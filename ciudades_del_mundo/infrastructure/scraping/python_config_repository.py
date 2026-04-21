from __future__ import annotations

import importlib
from importlib import resources

from ciudades_del_mundo.domain import ScrapingJobConfig, ScrapingPageConfig, parse_divisions


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"


class PythonScrapingConfigRepository:
    def __init__(self, package: str = "ciudades_del_mundo.subdivisions"):
        self.package = package

    def list_configs(self) -> list[ScrapingJobConfig]:
        configs = []
        for path in sorted(resources.files(self.package).iterdir(), key=lambda item: item.name):
            if not path.name.endswith(".py") or path.name.startswith("_"):
                continue
            slug = path.name.removesuffix(".py")
            try:
                configs.append(self.get(slug))
            except (KeyError, TypeError):
                continue
        return configs

    def get(self, slug: str) -> ScrapingJobConfig:
        module = importlib.import_module(f"{self.package}.{slug}")
        divisions = parse_divisions(module.DIVISIONS)
        pages = [
            ScrapingPageConfig(
                path=self._path_for(slug, url),
                html_format=division.source_type,
                lowest_level=division.lowest_level,
            )
            for division in divisions
            for url in division.urls
        ]
        return ScrapingJobConfig(
            slug=slug,
            country_code=slug,
            base_url=CITYPOPULATION_BASE_URL,
            pages=pages,
        )

    def _path_for(self, country_code: str, configured_url: str) -> str:
        if configured_url.startswith(("http://", "https://")):
            return configured_url
        if configured_url == "admin":
            return country_code
        return f"{country_code}/{configured_url.strip('/')}"
