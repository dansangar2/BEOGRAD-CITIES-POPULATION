from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from ciudades_del_mundo.domain import (
    AdminAreaSummary,
    MostPopulatedAssignment,
    RepresentationConfig,
    ScrapedAdminArea,
    ScrapingJobConfig,
)


class AdminAreaRepository(Protocol):
    def reset_country(self, country_code: str) -> None:
        ...

    def save_many(self, country_code: str, entities: list[ScrapedAdminArea]) -> tuple[int, int]:
        ...

    def list_summaries(self, country_code: str) -> list[AdminAreaSummary]:
        ...

    def save_most_populated_assignments(self, assignments: list[MostPopulatedAssignment]) -> int:
        ...

    def save_representatives(self, country_code: str, config: RepresentationConfig) -> int:
        ...


class ScrapingConfigRepository(Protocol):
    def list_configs(self) -> list[ScrapingJobConfig]:
        ...

    def get(self, slug: str) -> ScrapingJobConfig:
        ...


class UnitOfWork(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        ...
