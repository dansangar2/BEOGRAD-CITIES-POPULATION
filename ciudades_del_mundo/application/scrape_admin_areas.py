"""Application service that orchestrates scraping and persistence.

This use case is intentionally small: it does not know CityPopulation HTML
quirks and it does not contain country-specific corrections.  The pipeline is:

1. ask the configured scraper to parse every page;
2. normalize/link equivalent CityPopulation blocks;
3. persist the resulting rows in the repository;
4. run the existing derived calculations (most populated city/representatives).

Keeping the application layer thin makes the scraping system easier for another
AI to read and safely modify.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import urljoin

from ciudades_del_mundo.application.citypopulation_linking import (
    apply_runtime_config_extensions,
    normalize_citypopulation_entities,
    unlinked_entities,
)
from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.application.entity_merges import apply_entity_merges
from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig, calculate_most_populated_assignments
from ciudades_del_mundo.ports import AdminAreaRepository, HtmlFetcher, HtmlScraper, UnitOfWork


@dataclass(frozen=True)
class ScrapeResult:
    """Summary of persistence side effects for a scraping run."""

    created: int
    updated: int
    deleted: int
    found: int
    most_populated_updated: int = 0
    representatives_updated: int = 0


@dataclass(frozen=True)
class ScrapePageProgress:
    """Progress event emitted before and after scraping each configured page."""

    path: str
    html_format: str
    lowest_level: int
    url: str
    index: int = 0
    found: int | None = None
    html: str = ""
    entities: tuple[ScrapedAdminArea, ...] = ()


@dataclass(frozen=True)
class CachedScrapePage:
    """Previously completed page payload reused by resumable scraping."""

    found: int
    html: str = ""
    entities: tuple[ScrapedAdminArea, ...] = ()


class ScrapeAdminAreas:
    """Coordinates scraper adapters and repository persistence."""

    def __init__(
        self,
        repository: AdminAreaRepository,
        scrapers: list[HtmlScraper],
        unit_of_work: UnitOfWork | None = None,
        on_page_start: Callable[[ScrapePageProgress], None] | None = None,
        on_page_complete: Callable[[ScrapePageProgress], None] | None = None,
        cached_page_loader: Callable[[ScrapePageProgress], CachedScrapePage | None] | None = None,
        on_cached_page: Callable[[ScrapePageProgress], None] | None = None,
        entity_enricher: Callable[[ScrapingJobConfig, list[ScrapedAdminArea]], list[ScrapedAdminArea]] | None = None,
        page_workers: int = 1,
        html_fetcher: HtmlFetcher | None = None,
        on_unlinked_entities: Callable[[ScrapingJobConfig, tuple[ScrapedAdminArea, ...]], None] | None = None,
    ):
        self.repository = repository
        self.scrapers = {scraper.html_format: scraper for scraper in scrapers}
        self.unit_of_work = unit_of_work
        self.on_page_start = on_page_start
        self.on_page_complete = on_page_complete
        self.cached_page_loader = cached_page_loader
        self.on_cached_page = on_cached_page
        self.entity_enricher = entity_enricher
        # Accepted for CLI compatibility.  Parsing is kept ordered because the
        # new cross-page linker depends on configured block order.
        self.page_workers = max(1, int(page_workers or 1))
        self.html_fetcher = html_fetcher
        self.on_unlinked_entities = on_unlinked_entities

    def run(self, config: ScrapingJobConfig) -> ScrapeResult:
        """Execute one complete scraping/import job."""
        entities = self._scrape_pages(config)
        entities = normalize_citypopulation_entities(config.country_code, entities)
        if config.entity_merges:
            entities = apply_entity_merges(entities, config.entity_merges)
            entities = _keep_first_scraped_entity(entities)
        if config.cities:
            entities = apply_configured_cities(config.country_code, entities, config.cities)
            entities = _keep_first_scraped_entity(entities)
        entities = apply_runtime_config_extensions(config, entities)

        if self.on_unlinked_entities:
            unresolved = tuple(unlinked_entities(config.country_code, entities))
            if unresolved:
                self.on_unlinked_entities(config, unresolved)

        if self.entity_enricher:
            entities = self.entity_enricher(config, entities)

        with self._transaction():
            if config.reset_before_import:
                self.repository.reset_country(config.country_code)
            created, updated = self.repository.save_many(config.country_code, entities)
            deleted = self.repository.delete_missing(config.country_code, {entity.id for entity in entities})
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
            deleted=deleted,
            found=len(entities),
            most_populated_updated=most_populated_updated,
            representatives_updated=representatives_updated,
        )

    def _scrape_pages(self, config: ScrapingJobConfig) -> list[ScrapedAdminArea]:
        if self.page_workers > 1 and self.html_fetcher:
            return self._scrape_pages_with_prefetch(config)

        entities: list[ScrapedAdminArea] = []
        for index, page in enumerate(config.pages):
            progress = self._page_progress(config, page, index)
            cached = self._cached_page(progress)
            if cached:
                self._notify_cached_page(progress, cached)
                entities.extend(cached.entities)
                continue

            if self.on_page_start:
                self.on_page_start(progress)
            page_entities, html, url = self._scrape_single_page(config, page, progress.url)
            page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
            page_entities = _apply_page_forced_highest_level_marker(page_entities, page)
            page_entities = _apply_page_sum_to_root_marker(page_entities, page)
            page_entities = _apply_page_parent_level_marker(page_entities, page)
            completed = replace(progress, url=url, found=len(page_entities), html=html, entities=tuple(page_entities))
            if self.on_page_complete:
                self.on_page_complete(completed)
            entities.extend(page_entities)
        return entities

    def _scrape_pages_with_prefetch(self, config: ScrapingJobConfig) -> list[ScrapedAdminArea]:
        """Download independent URLs concurrently and emit page events in order."""
        entities: list[ScrapedAdminArea] = []
        pending: list[tuple[object, ScrapePageProgress, CachedScrapePage | None]] = []
        unique_urls: list[str] = []
        seen_urls: set[str] = set()

        for index, page in enumerate(config.pages):
            progress = self._page_progress(config, page, index)
            cached = self._cached_page(progress)
            if cached:
                pending.append((page, progress, cached))
                continue

            if self.on_page_start:
                self.on_page_start(progress)
            pending.append((page, progress, None))
            if progress.url not in seen_urls:
                seen_urls.add(progress.url)
                unique_urls.append(progress.url)

        html_by_url: dict[str, str] = {}
        if unique_urls:
            max_workers = min(self.page_workers, len(unique_urls))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures: dict[str, Future[str]] = {
                    url: executor.submit(self.html_fetcher.get, url) for url in unique_urls
                }
                for url in unique_urls:
                    html_by_url[url] = futures[url].result()

        parsed_by_key: dict[tuple[object, ...], tuple[list[ScrapedAdminArea], str]] = {}
        for page, progress, cached in pending:
            if cached:
                self._notify_cached_page(progress, cached)
                entities.extend(cached.entities)
                continue

            html = html_by_url[progress.url]
            parse_key = (progress.url,)
            parsed = parsed_by_key.get(parse_key)
            if parsed is None:
                page_entities = self._parse_prefetched_page(config, page, html, progress.url)
                parsed = (page_entities, progress.url)
                parsed_by_key[parse_key] = parsed
            else:
                page_entities = list(parsed[0])

            page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
            page_entities = _apply_page_forced_highest_level_marker(page_entities, page)
            page_entities = _apply_page_sum_to_root_marker(page_entities, page)
            page_entities = _apply_page_parent_level_marker(page_entities, page)
            completed = replace(
                progress,
                url=parsed[1],
                found=len(page_entities),
                html=html,
                entities=tuple(page_entities),
            )
            if self.on_page_complete:
                self.on_page_complete(completed)
            entities.extend(page_entities)
        return entities

    def _scrape_single_page(self, config: ScrapingJobConfig, page, expected_url: str) -> tuple[list[ScrapedAdminArea], str, str]:
        scraper = self._scraper_for(page.html_format)
        scrape_page = getattr(scraper, "scrape_page", None)
        if callable(scrape_page):
            result = scrape_page(config.base_url, config.country_code, page)
            return list(result.entities), result.html, result.url or expected_url
        return list(scraper.scrape(config.base_url, config.country_code, page)), "", expected_url

    def _parse_prefetched_page(self, config: ScrapingJobConfig, page, html: str, url: str) -> list[ScrapedAdminArea]:
        scraper = self._scraper_for(page.html_format)
        scrape_configured_html = getattr(scraper, "scrape_configured_html", None)
        if callable(scrape_configured_html):
            return list(scrape_configured_html(html=html, url=url, country_code=config.country_code, page=page))
        scrape_html = getattr(scraper, "scrape_html", None)
        if callable(scrape_html):
            return list(scrape_html(html=html, url=url, country_code=config.country_code, level=page.lowest_level))
        scrape = getattr(scraper, "scrape", None)
        if callable(scrape):
            return list(scrape(config.base_url, config.country_code, page))
        raise ValueError(f"Unknown html_format: {page.html_format!r}")

    def _page_progress(self, config: ScrapingJobConfig, page, index: int) -> ScrapePageProgress:
        return ScrapePageProgress(
            path=page.path,
            html_format=page.html_format,
            lowest_level=page.force_highest_level if page.force_highest_level is not None else page.lowest_level,
            url=_build_page_url(config.base_url, page.path),
            index=index,
        )

    def _cached_page(self, progress: ScrapePageProgress) -> CachedScrapePage | None:
        if not self.cached_page_loader:
            return None
        cached = self.cached_page_loader(progress)
        if cached and cached.entities:
            return cached
        return None

    def _notify_cached_page(self, progress: ScrapePageProgress, cached: CachedScrapePage) -> None:
        if self.on_cached_page:
            self.on_cached_page(replace(progress, found=cached.found, html=cached.html, entities=cached.entities))

    def _scraper_for(self, html_format: str) -> HtmlScraper:
        scraper = self.scrapers.get(str(html_format).strip().lower())
        if not scraper:
            allowed = ", ".join(sorted(self.scrapers))
            raise ValueError(f"Unknown html_format: {html_format!r}. Available: {allowed}.")
        return scraper

    def _transaction(self):
        if self.unit_of_work:
            return self.unit_of_work.transaction()
        return nullcontext()



def _apply_page_forced_highest_level_marker(entities: list[ScrapedAdminArea], page) -> list[ScrapedAdminArea]:
    forced_level = getattr(page, "force_highest_level", None)
    if forced_level in (None, ""):
        return entities
    marker = f"Forced highest level: {int(forced_level)}"
    return [replace(entity, annotations=_append_annotation(entity.annotations, marker)) for entity in entities]


def _apply_page_sum_to_root_marker(entities: list[ScrapedAdminArea], page) -> list[ScrapedAdminArea]:
    """Mark all rows parsed from a ``sum_to_root``/``sumar al padre`` page.

    The linker later knows the canonical parent chain and can safely attach or
    roll up the marked branch.  Doing this in the application layer keeps both
    real HTML scrapers and test/dummy scrapers consistent.
    """
    if not getattr(page, "sum_to_root", False):
        return entities
    return [replace(entity, contributes_to_root=True) for entity in entities]


def _apply_page_parent_level_marker(entities: list[ScrapedAdminArea], page) -> list[ScrapedAdminArea]:
    parent_level = getattr(page, "parent_level", None)
    if parent_level in (None, ""):
        return entities
    marker = f"Forced parent level: {int(parent_level)}"
    return [replace(entity, annotations=_append_annotation(entity.annotations, marker)) for entity in entities]


def _build_page_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path.rstrip("/") + "/"
    return urljoin(base_url.rstrip("/") + "/", path.strip("/") + "/")


def _apply_page_area_overrides(
    entities: list[ScrapedAdminArea],
    root_area_km2,
    area_overrides: dict[str, object],
) -> list[ScrapedAdminArea]:
    if root_area_km2 is None and not area_overrides:
        return entities
    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        area_km2 = _custom_area_for(entity, root_area_km2, area_overrides)
        if area_km2 is None:
            updated.append(entity)
            continue
        density = entity.density
        if entity.pop_latest is not None and area_km2 != 0:
            density = Decimal(entity.pop_latest) / Decimal(str(area_km2))
        updated.append(replace(entity, area_km2=area_km2, density=density))
    return updated


def _custom_area_for(entity: ScrapedAdminArea, root_area_km2, area_overrides: dict[str, object]):
    for key in (entity.id, entity.code, entity.name):
        if key in area_overrides:
            return _decimal_or_original(area_overrides[key])
    if root_area_km2 is not None and entity.code == entity.country_code:
        return _decimal_or_original(root_area_km2)
    return None


def _decimal_or_original(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value


def _keep_first_scraped_entity(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    seen: set[tuple[str, str]] = set()
    kept: list[ScrapedAdminArea] = []
    for entity in entities:
        key = (str(entity.country_code), str(entity.code))
        if key in seen:
            continue
        seen.add(key)
        kept.append(entity)
    return kept


def _append_annotation(value: str | None, annotation: str) -> str:
    current = str(value or "").strip()
    if not current:
        return annotation
    if annotation in current:
        return current
    return f"{current}; {annotation}"
