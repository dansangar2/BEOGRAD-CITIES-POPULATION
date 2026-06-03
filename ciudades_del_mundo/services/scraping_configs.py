"""SQL-backed storage helpers for CityPopulation scraping configs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import sys
import threading
import tomllib

from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.utils import timezone

from ciudades_del_mundo.domain import (
    RepresentationConfig,
    ScrapingJobConfig,
    parse_cities,
    parse_entity_merges,
    parse_pages,
)
from ciudades_del_mundo.models import ScrapingConfig


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"
_BOOTSTRAP_LOCK = threading.RLock()


@dataclass(frozen=True)
class ConfigMetadata:
    country_code: str
    name: str
    pages_count: int
    cities_count: int
    has_representation: bool
    is_valid: bool
    validation_error: str = ""


@dataclass(frozen=True)
class ConfigBootstrapStatus:
    table_ready: bool
    expected_count: int
    stored_count: int
    missing_count: int
    imported_count: int = 0

    @property
    def ready(self) -> bool:
        return self.table_ready and self.missing_count == 0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["ready"] = self.ready
        return data


def scraping_config_table_exists() -> bool:
    """Return whether the SQL config table is available for safe use."""
    try:
        return ScrapingConfig._meta.db_table in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def parse_config_metadata(slug: str, content: str) -> ConfigMetadata:
    """Parse a TOML config once and return the fields needed by list views."""
    try:
        data = tomllib.loads(content)
        pages = parse_pages(data.get("pages"), slug=slug)
        if not pages:
            raise ValueError("La configuración debe definir al menos una página.")
        cities = parse_cities(data.get("cities"))
        parse_entity_merges(data.get("entity_merges", data.get("merge_entities")))
        representation = RepresentationConfig.from_mapping(data.get("representation"))
        return ConfigMetadata(
            country_code=str(data.get("country_code") or slug),
            name=str(data.get("name") or ""),
            pages_count=len(pages),
            cities_count=len(cities),
            has_representation=bool(representation),
            is_valid=True,
            validation_error="",
        )
    except Exception as exc:  # noqa: BLE001 - stored for the web UI.
        try:
            data = tomllib.loads(content)
        except Exception:  # noqa: BLE001
            data = {}
        return ConfigMetadata(
            country_code=str(data.get("country_code") or slug),
            name=str(data.get("name") or ""),
            pages_count=0,
            cities_count=0,
            has_representation=False,
            is_valid=False,
            validation_error=str(exc),
        )


def parse_scraping_job_config(slug: str, content: str) -> ScrapingJobConfig:
    """Build the domain config from persisted TOML content."""
    data = tomllib.loads(content)
    pages = parse_pages(data.get("pages"), slug=slug)
    if not pages:
        raise ValueError(f"Config '{slug}' must define at least one page.")
    return ScrapingJobConfig(
        slug=slug,
        country_code=str(data.get("country_code") or slug),
        base_url=str(data.get("base_url") or CITYPOPULATION_BASE_URL),
        legal_subdivision_level=_int_or_none(data.get("LEGAL_SUBDIVISION")),
        name=data.get("name"),
        reset_before_import=bool(data.get("reset_before_import", False)),
        representation=RepresentationConfig.from_mapping(data.get("representation")),
        pages=pages,
        cities=parse_cities(data.get("cities")),
        entity_merges=parse_entity_merges(data.get("entity_merges", data.get("merge_entities"))),
    )


def upsert_scraping_config(slug: str, content: str, *, source_path: str = "") -> ScrapingConfig:
    """Create/update one SQL config and refresh its cached metadata."""
    metadata = parse_config_metadata(slug, content)
    digest = sha256(content.encode("utf-8")).hexdigest()
    obj, _ = ScrapingConfig.objects.update_or_create(
        slug=slug,
        defaults={
            "country_code": metadata.country_code,
            "name": metadata.name,
            "content": content,
            "content_hash": digest,
            "source_path": source_path,
            "pages_count": metadata.pages_count,
            "cities_count": metadata.cities_count,
            "has_representation": metadata.has_representation,
            "is_valid": metadata.is_valid,
            "validation_error": metadata.validation_error,
            "imported_at": timezone.now() if source_path else None,
        },
    )
    return obj


def bundled_toml_config_paths() -> list[Path]:
    """Return the bundled subdivision TOML files that must exist in SQL."""
    root = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.toml") if not path.name.startswith("_"))


def scraping_config_bootstrap_status(*, imported_count: int = 0) -> ConfigBootstrapStatus:
    """Return whether the SQL config table contains every bundled TOML config."""
    paths = bundled_toml_config_paths()
    expected_slugs = {path.stem for path in paths}
    if not scraping_config_table_exists():
        return ConfigBootstrapStatus(
            table_ready=False,
            expected_count=len(expected_slugs),
            stored_count=0,
            missing_count=len(expected_slugs),
            imported_count=imported_count,
        )
    try:
        stored_slugs = set(ScrapingConfig.objects.filter(slug__in=expected_slugs).values_list("slug", flat=True))
    except (OperationalError, ProgrammingError):
        return ConfigBootstrapStatus(
            table_ready=False,
            expected_count=len(expected_slugs),
            stored_count=0,
            missing_count=len(expected_slugs),
            imported_count=imported_count,
        )
    missing_count = max(0, len(expected_slugs - stored_slugs))
    return ConfigBootstrapStatus(
        table_ready=True,
        expected_count=len(expected_slugs),
        stored_count=len(stored_slugs),
        missing_count=missing_count,
        imported_count=imported_count,
    )


def ensure_initial_scraping_configs(*, force: bool = False) -> ConfigBootstrapStatus:
    """Synchronously seed SQL from bundled TOML files until no initial rows are missing.

    This is safe after an interrupted first run: existing rows are left intact by
    default and only missing TOML configs are inserted. Pass ``force=True`` from
    the management command to refresh every SQL row from the current TOML files.
    """
    with _BOOTSTRAP_LOCK:
        if not scraping_config_table_exists():
            return scraping_config_bootstrap_status()
        paths = bundled_toml_config_paths()
        if not paths:
            return scraping_config_bootstrap_status()
        slugs = [path.stem for path in paths]
        existing = set(ScrapingConfig.objects.filter(slug__in=slugs).values_list("slug", flat=True))
        imported = 0
        with transaction.atomic():
            for path in paths:
                slug = path.stem
                if not force and slug in existing:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                upsert_scraping_config(slug, content, source_path=str(path.relative_to(settings.BASE_DIR)))
                imported += 1
        return scraping_config_bootstrap_status(imported_count=imported)


def sync_scraping_configs_from_toml(*, force: bool = False, only_if_empty: bool = True) -> int:
    """Import bundled TOML files into SQL.

    ``only_if_empty`` is kept for backwards compatibility. The import now also
    repairs interrupted initial runs by inserting any missing bundled TOML rows.
    """
    if not scraping_config_table_exists():
        return 0
    if only_if_empty and ScrapingConfig.objects.exists():
        status_before = scraping_config_bootstrap_status()
        if status_before.ready:
            return 0
    return ensure_initial_scraping_configs(force=force).imported_count


def maybe_sync_scraping_configs_on_startup() -> None:
    """Populate missing SQL configs on the first web/command run after migrations."""
    if any(command in sys.argv for command in {"makemigrations", "migrate", "collectstatic"}):
        return
    try:
        ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError):
        return


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)
