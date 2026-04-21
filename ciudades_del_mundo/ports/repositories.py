from __future__ import annotations

from typing import Protocol

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig


class AdminAreaRepository(Protocol):
    def reset_country(self, country_code: str) -> None:
        ...

    def save_many(self, country_code: str, entities: list[ScrapedAdminArea]) -> tuple[int, int]:
        ...


class ScrapingConfigRepository(Protocol):
    def list_configs(self) -> list[ScrapingJobConfig]:
        ...

    def get(self, slug: str) -> ScrapingJobConfig:
        ...
