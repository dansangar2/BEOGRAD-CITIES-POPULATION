from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig, calculate_most_populated_assignments
from ciudades_del_mundo.ports import AdminAreaRepository, HtmlScraper, UnitOfWork


@dataclass(frozen=True)
class ScrapeResult:
    created: int
    updated: int
    found: int
    most_populated_updated: int = 0
    representatives_updated: int = 0


@dataclass(frozen=True)
class ScrapePageProgress:
    path: str
    html_format: str
    lowest_level: int
    url: str
    found: int | None = None


class ScrapeAdminAreas:
    def __init__(
        self,
        repository: AdminAreaRepository,
        scrapers: list[HtmlScraper],
        unit_of_work: UnitOfWork | None = None,
        on_page_start: Callable[[ScrapePageProgress], None] | None = None,
        on_page_complete: Callable[[ScrapePageProgress], None] | None = None,
    ):
        self.repository = repository
        self.scrapers = {scraper.html_format: scraper for scraper in scrapers}
        self.unit_of_work = unit_of_work
        self.on_page_start = on_page_start
        self.on_page_complete = on_page_complete

    def run(self, config: ScrapingJobConfig) -> ScrapeResult:
        entities = []
        for page in config.pages:
            scraper = self.scrapers.get(page.html_format)
            if not scraper:
                known = ", ".join(sorted(self.scrapers)) or "none"
                raise ValueError(f"Unknown html_format '{page.html_format}'. Known formats: {known}.")
            progress = ScrapePageProgress(
                path=page.path,
                html_format=page.html_format,
                lowest_level=page.lowest_level,
                url=_page_url(config.base_url, page.path),
            )
            if self.on_page_start:
                self.on_page_start(progress)
            page_entities = scraper.scrape(config.base_url, config.country_code, page)
            if self.on_page_complete:
                self.on_page_complete(
                    ScrapePageProgress(
                        path=progress.path,
                        html_format=progress.html_format,
                        lowest_level=progress.lowest_level,
                        url=progress.url,
                        found=len(page_entities),
                    )
                )
            entities.extend(page_entities)

        entities = _keep_first_scraped_entity(entities)
        if config.cities:
            entities = apply_configured_cities(config.country_code, entities, config.cities)
            entities = _keep_first_scraped_entity(entities)

        with self._transaction():
            if config.reset_before_import:
                self.repository.reset_country(config.country_code)
            created, updated = self.repository.save_many(config.country_code, entities)
            assignments = calculate_most_populated_assignments(
                self.repository.list_summaries(config.country_code),
                config.legal_subdivision_level,
            )
            most_populated_updated = self.repository.save_most_populated_assignments(assignments)
            representatives_updated = 0
            if config.representation:
                representatives_updated = self.repository.save_representatives(
                    config.country_code,
                    config.representation,
                )
        return ScrapeResult(
            created=created,
            updated=updated,
            found=len(entities),
            most_populated_updated=most_populated_updated,
            representatives_updated=representatives_updated,
        )

    def _transaction(self):
        if self.unit_of_work:
            return self.unit_of_work.transaction()
        return nullcontext()


def _keep_first_scraped_entity(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    seen = set()
    deduplicated = []
    for entity in entities:
        key = (entity.country_code, entity.code)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(entity)
    return deduplicated


def _page_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path.rstrip("/") + "/"
    return f"{base_url.rstrip('/')}/{path.strip('/')}/"
