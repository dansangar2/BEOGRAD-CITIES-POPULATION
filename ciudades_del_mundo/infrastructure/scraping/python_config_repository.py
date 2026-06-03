"""Repository that loads scrape configurations from SQL, falling back to TOML files."""

from __future__ import annotations

from importlib import resources
import tomllib

from django.db import OperationalError, ProgrammingError

from ciudades_del_mundo.domain import (
    RepresentationConfig,
    ScrapingJobConfig,
    parse_cities,
    parse_entity_merges,
    parse_pages,
)


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"


class PythonScrapingConfigRepository:
    """Load and validate scrape configs.

    SQL is the primary store. The bundled ``subdivisions/*.toml`` files remain
    a fallback and are used by migrations/first-run bootstrap to seed SQL.
    """

    def __init__(self, package: str = "ciudades_del_mundo.subdivisions"):
        self.package = package

    def list_configs(self) -> list[ScrapingJobConfig]:
        return [self.get(slug) for slug in self.list_slugs()]

    def list_slugs(self) -> list[str]:
        sql_slugs = self._list_sql_slugs()
        if sql_slugs:
            return sql_slugs
        return self._list_toml_slugs()

    def get(self, slug: str) -> ScrapingJobConfig:
        content = self._get_sql_content(slug)
        if content is None:
            resource = self._config_resource(slug)
            if resource is None:
                raise ModuleNotFoundError(f"No config found for slug '{slug}'.")
            content = resource.read_text(encoding="utf-8")
        return self._from_toml(slug, content)

    def _from_toml(self, slug: str, content: str) -> ScrapingJobConfig:
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

    def _list_toml_slugs(self) -> list[str]:
        names = set()
        for path in resources.files(self.package).iterdir():
            if path.name.startswith("_") or not path.name.endswith(".toml"):
                continue
            names.add(path.name.removesuffix(".toml"))
        return sorted(names)

    def _config_resource(self, slug: str):
        root = resources.files(self.package)
        resource = root / f"{slug}.toml"
        if resource.is_file():
            return resource
        return None

    def _list_sql_slugs(self) -> list[str]:
        try:
            from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists
            from ciudades_del_mundo.models import ScrapingConfig

            if not scraping_config_table_exists():
                return []
            return list(ScrapingConfig.objects.order_by("slug").values_list("slug", flat=True))
        except (OperationalError, ProgrammingError):
            return []

    def _get_sql_content(self, slug: str) -> str | None:
        try:
            from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists
            from ciudades_del_mundo.models import ScrapingConfig

            if not scraping_config_table_exists():
                return None
            return ScrapingConfig.objects.filter(slug=slug).values_list("content", flat=True).first()
        except (OperationalError, ProgrammingError):
            return None


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)
