"""Repository that loads scrape configurations from SQL rows."""

from __future__ import annotations

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
    """Load and validate SQL-backed scrape configs.

    The class name is kept for backwards compatibility with older imports. The
    repository no longer reads bundled ``subdivisions/*.toml`` files during
    normal operation; use ``sync_scraping_configs`` for the temporary seed bridge.
    """

    def __init__(self, package: str = "ciudades_del_mundo.subdivisions"):
        self.package = package  # Deprecated compatibility argument.

    def list_configs(self) -> list[ScrapingJobConfig]:
        return [self.get(slug) for slug in self.list_slugs()]

    def list_slugs(self) -> list[str]:
        return self._list_sql_slugs()

    def get(self, slug: str) -> ScrapingJobConfig:
        content = self._get_sql_content(slug)
        if content is None:
            raise ModuleNotFoundError(
                f"No SQL scraping config found for slug '{slug}'. "
                "Run 'py manage.py sync_scraping_configs' to import temporary TOML seeds."
            )
        return self._from_toml(slug, content)

    def _from_toml(self, slug: str, content: str) -> ScrapingJobConfig:
        data = tomllib.loads(content)
        pages = parse_pages(data.get("pages"), slug=slug)
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
