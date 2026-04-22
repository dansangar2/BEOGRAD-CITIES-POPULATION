from __future__ import annotations

import importlib
from importlib import resources

from ciudades_del_mundo.domain import RepresentationConfig, ScrapingJobConfig, ScrapingPageConfig, parse_divisions


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"


class PythonScrapingConfigRepository:
    def __init__(self, package: str = "ciudades_del_mundo.subdivisions"):
        self.package = package

    def list_configs(self) -> list[ScrapingJobConfig]:
        configs = []
        for path in sorted(resources.files(self.package).iterdir(), key=lambda item: item.name):
            if path.name.startswith("_") or not path.name.endswith(".py"):
                continue
            slug = path.name.removesuffix(".py")
            try:
                configs.append(self.get(slug))
            except (KeyError, TypeError):
                continue
        return configs

    def get(self, slug: str) -> ScrapingJobConfig:
        module = self._load_module(slug)
        divisions = parse_divisions(module.DIVISIONS)
        pages = [
            ScrapingPageConfig(
                path=self._path_for(slug, url),
                html_format=division.source_type.value,
                lowest_level=division.lowest_level,
            )
            for division in divisions
            for url in division.urls
        ]
        return ScrapingJobConfig(
            slug=slug,
            country_code=slug,
            base_url=CITYPOPULATION_BASE_URL,
            legal_subdivision_level=(
                getattr(module, "LEGAL_SUBDIVISION", None)
                or getattr(module, "LEGAL_SUBDIVISIONS", None)
            ),
            representation=RepresentationConfig.from_mapping(getattr(module, "REPRESENTATION", None)),
            pages=pages,
        )

    def _path_for(self, country_code: str, configured_url: str) -> str:
        if configured_url.startswith(("http://", "https://")):
            return configured_url
        if configured_url == "infosection":
            return country_code
        if configured_url == "admin":
            return f"{country_code}/admin"
        return f"{country_code}/{configured_url.strip('/')}"

    def _load_module(self, slug: str):
        return importlib.import_module(f"{self.package}.{slug}")
