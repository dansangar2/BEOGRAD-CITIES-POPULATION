"""Repository that loads scrape configurations from TOML files."""

from __future__ import annotations

from importlib import resources
import tomllib

from ciudades_del_mundo.domain import (
    RepresentationConfig,
    ScrapingJobConfig,
    parse_cities,
    parse_pages,
)


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"


class PythonScrapingConfigRepository:
    """Load and validate scrape configs stored under `subdivisions/*.toml`."""

    def __init__(self, package: str = "ciudades_del_mundo.subdivisions"):
        self.package = package

    def list_configs(self) -> list[ScrapingJobConfig]:
        return [self.get(slug) for slug in self.list_slugs()]

    def list_slugs(self) -> list[str]:
        names = set()
        for path in resources.files(self.package).iterdir():
            if path.name.startswith("_") or not path.name.endswith(".toml"):
                continue
            names.add(path.name.removesuffix(".toml"))
        return sorted(names)

    def get(self, slug: str) -> ScrapingJobConfig:
        resource = self._config_resource(slug)
        if resource is None:
            raise ModuleNotFoundError(f"No config found for slug '{slug}'.")

        data = tomllib.loads(resource.read_text(encoding="utf-8"))
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
        )

    def _config_resource(self, slug: str):
        root = resources.files(self.package)
        resource = root / f"{slug}.toml"
        if resource.is_file():
            return resource
        return None


def _int_or_none(value):
    if value in (None, ""):
        return None
    return int(value)
