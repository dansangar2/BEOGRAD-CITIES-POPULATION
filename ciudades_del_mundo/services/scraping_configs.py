"""SQL-backed storage helpers and explicit TOML seed import/export for scraping configs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
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
CITY_MERGE_TOML_DIR = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "cities_merge"


def strip_config_base_url(content: str) -> str:
    """Remove the deprecated top-level base_url field from TOML content.

    CityPopulation is the only supported source for these configs, so the
    scraper always uses ``CITYPOPULATION_BASE_URL`` directly.  Keeping this
    field in editable/exported TOML only creates noise.
    """
    lines = str(content or "").splitlines(keepends=True)
    cleaned: list[str] = []
    before_first_table = True
    removed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("#"):
            before_first_table = False
        if before_first_table and stripped.startswith("base_url") and stripped.split("=", 1)[0].strip() == "base_url":
            removed = True
            continue
        cleaned.append(line)
    result = "".join(cleaned)
    if removed:
        result = result.replace("\n\n\n", "\n\n")
    return result



@dataclass(frozen=True)
class ConfigMetadata:
    country_code: str
    name: str
    pages_count: int
    cities_count: int
    has_representation: bool
    is_valid: bool
    validation_error: str = ""
    schema_version: int = 2
    scrape_types: str = ""
    pages_config: tuple[dict, ...] = ()


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
        schema_version = int(data.get("scrape_schema_version", 1))
        pages = parse_pages(
            data.get("pages"),
            slug=slug,
            schema_version=schema_version,
        )
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
            schema_version=schema_version,
            scrape_types=",".join(sorted({page.html_format for page in pages})),
            pages_config=tuple(_page_metadata(page) for page in pages),
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
            schema_version=int(data.get("scrape_schema_version", 2) or 2),
            scrape_types="",
            pages_config=(),
        )


def parse_scraping_job_config(slug: str, content: str) -> ScrapingJobConfig:
    """Build the domain config from persisted TOML content."""
    data = tomllib.loads(content)
    pages = parse_pages(
        data.get("pages"),
        slug=slug,
        schema_version=int(data.get("scrape_schema_version", 1)),
    )
    if not pages:
        raise ValueError(f"Config '{slug}' must define at least one page.")
    return ScrapingJobConfig(
        slug=slug,
        country_code=str(data.get("country_code") or slug),
        base_url=CITYPOPULATION_BASE_URL,
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
    content = strip_config_base_url(content)
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
            "schema_version": metadata.schema_version,
            "scrape_types": metadata.scrape_types,
            "pages_config": list(metadata.pages_config),
            "is_valid": metadata.is_valid,
            "validation_error": metadata.validation_error,
            "imported_at": timezone.now() if source_path else None,
        },
    )
    return obj


def bundled_toml_config_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return per-country subdivision TOML seed files available for explicit SQL import."""
    root = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions"
    if not root.is_dir():
        return []
    allowed = set(slugs or [])
    return sorted(
        path
        for path in root.glob("*.toml")
        if not path.name.startswith("_") and (not allowed or path.stem in allowed)
    )


def _page_metadata(page) -> dict:
    return {
        "path": page.path,
        "source": page.html_format,
        "lowest_level": page.lowest_level,
        "force_highest_level": page.force_highest_level,
        "parent_level": page.parent_level,
        "include_sections": list(page.include_sections),
        "include_tables": list(page.include_tables),
        "repeat": dict(page.repeat),
        "sum_to_root": bool(page.sum_to_root),
    }


def scraping_config_bootstrap_status(*, imported_count: int = 0) -> ConfigBootstrapStatus:
    """Return whether SQL contains every currently available TOML seed config."""
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
    """Synchronously seed SQL from per-country TOML files until no initial rows are missing.

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


def sync_scraping_configs_from_toml(
    *,
    force: bool = False,
    only_if_empty: bool = True,
    slugs: list[str] | tuple[str, ...] | None = None,
) -> int:
    """Import per-country TOML seed files into SQL.

    ``only_if_empty`` is kept for backwards compatibility. The import also
    repairs interrupted initial runs by inserting any missing seed TOML rows.
    """
    if not scraping_config_table_exists():
        return 0
    paths = bundled_toml_config_paths(slugs)
    if only_if_empty and not slugs and ScrapingConfig.objects.exists():
        status_before = scraping_config_bootstrap_status()
        if status_before.ready:
            return 0
    existing = set(ScrapingConfig.objects.filter(slug__in=[path.stem for path in paths]).values_list("slug", flat=True))
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
    return imported


def export_scraping_configs_to_toml(
    *,
    force: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
) -> int:
    """Export SQL scraping configs back to per-country TOML seed files.

    This is a development/bootstrap bridge only. Runtime scraping must keep
    using SQL rows, not these exported files.
    """
    if not scraping_config_table_exists():
        return 0
    root = Path(output_dir) if output_dir else Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions"
    root.mkdir(parents=True, exist_ok=True)
    records = ScrapingConfig.objects.order_by("slug")
    if slugs:
        records = records.filter(slug__in=slugs)
    exported = 0
    for record in records:
        path = root / f"{record.slug}.toml"
        content = strip_config_base_url(record.content)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                continue
            if not force:
                raise ValueError(f"{path} already exists and differs. Use --force to overwrite it.")
        path.write_text(content, encoding="utf-8")
        exported += 1
    return exported


def bundled_city_merge_toml_paths(
    slugs: list[str] | tuple[str, ...] | None = None,
    *,
    input_dir: str | Path | None = None,
) -> list[Path]:
    """Return per-country city merge TOML files available for explicit SQL import."""
    root = Path(input_dir) if input_dir else CITY_MERGE_TOML_DIR
    if not root.is_dir():
        return []
    allowed = set(slugs or [])
    return sorted(
        path
        for path in root.glob("*.toml")
        if not path.name.startswith("_") and (not allowed or path.stem in allowed)
    )


def export_city_merges_to_toml(
    *,
    force: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
) -> int:
    """Export only [[cities]] city unification blocks to cities_merge TOML files."""
    if not scraping_config_table_exists():
        return 0
    root = Path(output_dir) if output_dir else CITY_MERGE_TOML_DIR
    root.mkdir(parents=True, exist_ok=True)
    records = ScrapingConfig.objects.order_by("slug")
    if slugs:
        records = records.filter(slug__in=slugs)
    exported = 0
    for record in records:
        path = root / f"{record.slug}.toml"
        content = render_city_merge_toml(record.content)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                continue
            if not force:
                raise ValueError(f"{path} already exists and differs. Use --force to overwrite it.")
        path.write_text(content, encoding="utf-8")
        exported += 1
    return exported


def sync_city_merges_from_toml(
    *,
    force: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
    input_dir: str | Path | None = None,
) -> int:
    """Import cities_merge TOML files into existing SQL ScrapingConfig rows.

    This replaces only [[cities]] blocks. Pages, assets, scraping metadata and
    other config fragments remain in the SQL config content.
    """
    if not scraping_config_table_exists():
        return 0
    paths = bundled_city_merge_toml_paths(slugs, input_dir=input_dir)
    if not paths:
        return 0
    records = {record.slug: record for record in ScrapingConfig.objects.filter(slug__in=[path.stem for path in paths])}
    imported = 0
    with transaction.atomic():
        for path in paths:
            record = records.get(path.stem)
            if record is None:
                continue
            merge_content = path.read_text(encoding="utf-8")
            next_content = replace_city_merges_in_config(record.content, merge_content)
            if next_content == record.content and not force:
                continue
            upsert_scraping_config(record.slug, next_content, source_path=record.source_path)
            imported += 1
    return imported


def render_city_merge_toml(config_content: str) -> str:
    """Render the [[cities]] blocks from a full config as standalone TOML."""
    data = tomllib.loads(config_content or "")
    cities = _city_merge_blocks_from_data(data)
    return _render_city_merge_blocks(cities)


def replace_city_merges_in_config(config_content: str, merge_content: str) -> str:
    """Replace only [[cities]] blocks in a full config with standalone merge TOML."""
    merge_data = tomllib.loads(merge_content or "")
    cities = _city_merge_blocks_from_data(merge_data)
    base = _remove_toml_table_blocks(config_content, {"cities"}).rstrip()
    rendered = _render_city_merge_blocks(cities).rstrip()
    if rendered:
        return (base + "\n\n" + rendered).strip() + "\n"
    return base.strip() + "\n"


def _city_merge_blocks_from_data(data: dict) -> list[dict]:
    raw_cities = data.get("cities")
    if raw_cities is None:
        return []
    if not isinstance(raw_cities, list):
        raise ValueError("El TOML de cities_merge debe definir bloques [[cities]].")
    parse_cities(raw_cities)
    return [city for city in raw_cities if isinstance(city, dict)]


def _render_city_merge_blocks(cities: list[dict]) -> str:
    lines: list[str] = []
    for city in cities:
        lines.append("[[cities]]")
        lines.append(f"city = {_toml_string(city.get('city', ''))}")
        lines.append(f"id = {_toml_string(city.get('id', ''))}")
        lines.append(f"level = {int(city.get('level'))}")
        lines.append(f"type = {_toml_string(city.get('type', 'City'))}")
        if city.get("district_types"):
            lines.append(f"district_types = {_toml_array(city.get('district_types') or [])}")
        if city.get("parent_type"):
            lines.append(f"parent_type = {_toml_string(city.get('parent_type'))}")
        elif city.get("parent_entity_type"):
            lines.append(f"parent_type = {_toml_string(city.get('parent_entity_type'))}")
        lines.append(f"from = {_toml_inline_table(city.get('from') or {})}")
        if city.get("communes"):
            lines.append(f"communes = {_toml_array(city.get('communes') or [])}")
        lines.append(f"keep_communes = {_toml_bool(bool(city.get('keep_communes', True)))}")
        if city.get("child_id") is not None:
            lines.append(f"child_id = {_toml_string(city.get('child_id'))}")
        if city.get("child_level") is not None:
            lines.append(f"child_level = {int(city.get('child_level'))}")
        if city.get("child_type") is not None:
            lines.append(f"child_type = {_toml_string(city.get('child_type'))}")
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _remove_toml_table_blocks(content: str, table_names: set[str]) -> str:
    output: list[str] = []
    current_block: list[str] = []
    skip_current = False

    def flush_block() -> None:
        nonlocal current_block, skip_current
        if current_block and not skip_current:
            output.extend(current_block)
        current_block = []
        skip_current = False

    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("#"):
            flush_block()
            current_block = [raw_line]
            skip_current = _toml_header_name(stripped) in table_names
            continue
        if current_block:
            current_block.append(raw_line)
        else:
            output.append(raw_line)
    flush_block()
    return "\n".join(output).rstrip() + "\n"


def _toml_header_name(header: str) -> str:
    text = str(header or "").strip()
    while text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.split(".", 1)[0].strip()


def _toml_string(value) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _toml_array(values) -> str:
    return "[" + ", ".join(_toml_string(value) for value in (values or [])) + "]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_inline_table(mapping) -> str:
    if not isinstance(mapping, dict) or not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping, key=lambda value: int(value)):
        values = mapping.get(key) or []
        parts.append(f"{int(key)} = {_toml_array(values)}")
    return "{ " + ", ".join(parts) + " }"


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
