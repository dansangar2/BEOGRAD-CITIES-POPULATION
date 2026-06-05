"""Application service that orchestrates a full scraping/import job."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
import re
from typing import Callable
from urllib.parse import unquote, urlparse

from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.application.entity_merges import apply_entity_merges
from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig, calculate_most_populated_assignments
from ciudades_del_mundo.ports import AdminAreaRepository, HtmlScraper, UnitOfWork


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
    found: int | None = None
    html: str = ""
    entities: tuple[ScrapedAdminArea, ...] = ()


class ScrapeAdminAreas:
    """Coordinates scrapers, post-processing and repository persistence."""

    def __init__(
        self,
        repository: AdminAreaRepository,
        scrapers: list[HtmlScraper],
        unit_of_work: UnitOfWork | None = None,
        on_page_start: Callable[[ScrapePageProgress], None] | None = None,
        on_page_complete: Callable[[ScrapePageProgress], None] | None = None,
        page_workers: int = 1,
    ):
        self.repository = repository
        self.scrapers = {scraper.html_format: scraper for scraper in scrapers}
        self.unit_of_work = unit_of_work
        self.on_page_start = on_page_start
        self.on_page_complete = on_page_complete
        self.page_workers = max(1, int(page_workers or 1))

    def run(self, config: ScrapingJobConfig) -> ScrapeResult:
        """Execute the scraping pipeline for a single country/job config."""
        entities = self._scrape_pages(config)
        entities = _rewrite_synthetic_page_roots(config.country_code, entities)
        entities = self._post_process_entities(config, entities)

        with self._transaction():
            if config.reset_before_import:
                self.repository.reset_country(config.country_code)
            created, updated = self.repository.save_many(config.country_code, entities)
            deleted = self.repository.delete_missing(
                config.country_code,
                {entity.id for entity in entities},
            )
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
        if self.page_workers > 1 and len(config.pages) > 1 and self._all_pages_support_html_prefetch(config):
            return self._scrape_pages_prefetched(config)

        entities = []
        for page in config.pages:
            progress = self._page_progress(config, page)
            if self.on_page_start:
                self.on_page_start(progress)
            page_entities, page_html, page_url = self._scrape_single_page(config, page, progress.url)
            if page_url != progress.url:
                progress = ScrapePageProgress(
                    path=progress.path,
                    html_format=progress.html_format,
                    lowest_level=progress.lowest_level,
                    url=page_url,
                )
            page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
            self._notify_page_complete(progress, len(page_entities), page_html, page_entities)
            entities.extend(page_entities)
        return entities

    def _scrape_pages_prefetched(self, config: ScrapingJobConfig) -> list[ScrapedAdminArea]:
        """Fetch page HTML concurrently, then parse/persist in configured order.

        This keeps the extracted data and page-complete side effects equivalent
        to the sequential scraper: each configured page is still parsed exactly
        once, ``on_page_complete`` is emitted in TOML/SQL order, and the same
        layout-specific parser handles the HTML. Only the network wait for
        independent CityPopulation pages is overlapped.
        """
        entities: list[ScrapedAdminArea] = []
        page_progress = [self._page_progress(config, page) for page in config.pages]
        for progress in page_progress:
            if self.on_page_start:
                self.on_page_start(progress)

        futures_by_url: dict[str, Future[str]] = {}
        with ThreadPoolExecutor(max_workers=min(self.page_workers, len(config.pages))) as executor:
            for progress in page_progress:
                if progress.url not in futures_by_url:
                    futures_by_url[progress.url] = executor.submit(_fetch_citypopulation_html, progress.url)

            for page, progress in zip(config.pages, page_progress, strict=True):
                scraper = self._scraper_for(page.html_format)
                scrape_html = getattr(scraper, "scrape_html", None)
                if not callable(scrape_html):
                    page_entities, page_html, page_url = self._scrape_single_page(config, page, progress.url)
                else:
                    page_html = futures_by_url[progress.url].result()
                    page_url = progress.url
                    page_entities = list(
                        scrape_html(
                            html=page_html,
                            url=page_url,
                            country_code=config.country_code,
                            level=page.lowest_level,
                        )
                    )
                page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
                self._notify_page_complete(progress, len(page_entities), page_html, page_entities)
                entities.extend(page_entities)
        return entities

    def _all_pages_support_html_prefetch(self, config: ScrapingJobConfig) -> bool:
        for page in config.pages:
            scraper = self._scraper_for(page.html_format)
            if not callable(getattr(scraper, "scrape_html", None)):
                return False
        return True

    def _page_progress(self, config: ScrapingJobConfig, page) -> ScrapePageProgress:
        return ScrapePageProgress(
            path=page.path,
            html_format=page.html_format,
            lowest_level=page.lowest_level,
            url=_page_url(config.base_url, page.path),
        )

    def _scrape_single_page(
        self,
        config: ScrapingJobConfig,
        page,
        expected_url: str,
    ) -> tuple[list[ScrapedAdminArea], str, str]:
        scraper = self._scraper_for(page.html_format)
        scraped_page = getattr(scraper, "scrape_page", None)
        if callable(scraped_page):
            page_result = scraped_page(config.base_url, config.country_code, page)
            return list(page_result.entities), str(page_result.html or ""), str(page_result.url or expected_url)
        return list(scraper.scrape(config.base_url, config.country_code, page)), "", expected_url

    def _scraper_for(self, html_format: str) -> HtmlScraper:
        scraper = self.scrapers.get(html_format)
        if scraper:
            return scraper

        known = ", ".join(sorted(self.scrapers)) or "none"
        raise ValueError(f"Unknown html_format '{html_format}'. Known formats: {known}.")

    def _notify_page_complete(
        self,
        progress: ScrapePageProgress,
        found: int,
        html: str,
        entities: list[ScrapedAdminArea],
    ) -> None:
        if not self.on_page_complete:
            return

        self.on_page_complete(
            ScrapePageProgress(
                path=progress.path,
                html_format=progress.html_format,
                lowest_level=progress.lowest_level,
                url=progress.url,
                found=found,
                html=html,
                entities=tuple(entities),
            )
        )

    def _post_process_entities(
        self,
        config: ScrapingJobConfig,
        entities: list[ScrapedAdminArea],
    ) -> list[ScrapedAdminArea]:
        entities = _keep_first_scraped_entity(entities)
        entities = _normalize_synthetic_country_parent_codes(config.country_code, entities)
        entities = _infer_parent_codes_from_url_path(entities)
        if config.entity_merges:
            entities = apply_entity_merges(entities, config.entity_merges)
            entities = _keep_first_scraped_entity(entities)
        if not config.cities:
            return entities

        configured = apply_configured_cities(config.country_code, entities, config.cities)
        return _keep_first_scraped_entity(configured)

    def _transaction(self):
        if self.unit_of_work:
            return self.unit_of_work.transaction()
        return nullcontext()



def _fetch_citypopulation_html(url: str) -> str:
    # Imported lazily to keep the application service importable in pure unit
    # tests that do not configure Django or load infrastructure repositories.
    from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient

    return CityPopulationClient().get(url)


def _rewrite_synthetic_page_roots(
    country_code: str,
    entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    """Attach children of page-local synthetic roots to the real scraped root.

    Some CityPopulation pages for Spanish autonomous cities expose an
    infosection root but no own admin code in the table. The generic root
    parser therefore emits code == country_code at level > 0 (for example a
    transient ``spain_spain`` Ceuta root). When the admin page already provided
    the real same-level Ceuta/Melilla entity, keep that real code and point the
    page children to it.
    """
    synthetic_roots = [
        entity
        for entity in entities
        if entity.code == country_code and entity.level > 0
    ]
    if not synthetic_roots:
        return entities

    real_by_level_name: dict[tuple[int, str], ScrapedAdminArea] = {}
    for entity in entities:
        if entity.code == country_code or entity.level <= 0:
            continue
        real_by_level_name.setdefault((entity.level, _normalize_entity_name(entity.name)), entity)

    aliases = []
    for synthetic in synthetic_roots:
        real = real_by_level_name.get((synthetic.level, _normalize_entity_name(synthetic.name)))
        if not real:
            continue
        aliases.append((synthetic, real))
    if not aliases:
        return entities

    updated = []
    for entity in entities:
        replacement = entity
        for synthetic, real in aliases:
            if entity is synthetic:
                replacement = replace(entity, code=real.code, parent_code=real.parent_code)
                break
            if (
                entity.parent_code == country_code
                and entity.level > synthetic.level
                and _entity_belongs_to_page_root(entity, synthetic)
            ):
                replacement = replace(entity, parent_code=real.code)
                break
        updated.append(replacement)
    return updated


def _entity_belongs_to_page_root(entity: ScrapedAdminArea, root: ScrapedAdminArea) -> bool:
    root_slug = _page_root_slug(root.url) or _normalize_url_slug(root.name)
    if not root_slug:
        return False
    path = urlparse(entity.url or "").path.casefold()
    return f"/{root_slug}/" in path or path.rstrip("/").endswith(f"/{root_slug}")


def _page_root_slug(url: str | None) -> str | None:
    if not url:
        return None
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    if len(segments) < 2:
        return None
    # /en/spain/ceuta/ -> ceuta
    return _normalize_url_slug(segments[-1])


def _normalize_entity_name(value: str | None) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "").strip())
    return " ".join(text.casefold().split())


def _keep_first_scraped_entity(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Deduplicate by `(country_code, code)` while preserving first appearance."""
    seen = set()
    deduplicated = []
    for entity in entities:
        key = (entity.country_code, entity.code)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(entity)
    return deduplicated


def _normalize_synthetic_country_parent_codes(
    country_code: str,
    entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    if any(entity.code == country_code for entity in entities):
        return entities

    roots = [entity for entity in entities if entity.level == 0]
    if len(roots) != 1:
        return entities

    root_code = roots[0].code
    return [
        replace(entity, parent_code=root_code)
        if entity.parent_code == country_code
        else entity
        for entity in entities
    ]


def _infer_parent_codes_from_url_path(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Use CityPopulation URL path slugs to fill missing parent codes when unique."""
    slug_index: dict[tuple[str, int, str], set[str]] = {}
    for entity in entities:
        slug = _entity_url_slug(entity.url)
        if not slug:
            continue
        key = (entity.country_code, entity.level, slug)
        slug_index.setdefault(key, set()).add(entity.code)

    unique_codes = {
        key: next(iter(codes))
        for key, codes in slug_index.items()
        if len(codes) == 1
    }

    updated = []
    for entity in entities:
        if entity.parent_code or entity.level <= 0:
            updated.append(entity)
            continue

        parent_slug = _url_parent_slug(entity.url)
        if not parent_slug:
            updated.append(entity)
            continue

        parent_code = unique_codes.get((entity.country_code, entity.level - 1, parent_slug))
        if not parent_code or parent_code == entity.code:
            updated.append(entity)
            continue

        updated.append(replace(entity, parent_code=parent_code))
    return updated


def _entity_url_slug(url: str | None) -> str | None:
    segment = _last_url_path_segment(url)
    if not segment or "__" not in segment:
        return None
    return _normalize_url_slug(segment.split("__", 1)[1])


def _url_parent_slug(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return None

    parent_segment = segments[-2]
    if "__" in parent_segment:
        parent_segment = parent_segment.split("__", 1)[1]
    return _normalize_url_slug(parent_segment)


def _last_url_path_segment(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path
    segments = [segment for segment in path.split("/") if segment]
    return segments[-1] if segments else None


def _normalize_url_slug(value: str) -> str:
    return unquote(value).strip().casefold()


def _apply_page_area_overrides(
    entities: list[ScrapedAdminArea],
    root_area_km2,
    area_overrides: dict[str, object],
) -> list[ScrapedAdminArea]:
    if root_area_km2 is None and not area_overrides:
        return entities

    updated = []
    for entity in entities:
        area_km2 = _custom_area_for(entity, root_area_km2, area_overrides)
        if area_km2 is None:
            updated.append(entity)
            continue

        density = entity.density
        if entity.pop_latest is not None and area_km2 != 0:
            density = entity.pop_latest / area_km2
        updated.append(replace(entity, area_km2=area_km2, density=density))
    return updated


def _custom_area_for(
    entity: ScrapedAdminArea,
    root_area_km2,
    area_overrides: dict[str, object],
):
    for key in (entity.id, entity.code, entity.name):
        if key in area_overrides:
            return area_overrides[key]
    if root_area_km2 is not None and entity.code == entity.country_code:
        return root_area_km2
    return None


def _page_url(base_url: str, path: str) -> str:
    """Build a display URL for progress logs."""
    if path.startswith(("http://", "https://")):
        return path.rstrip("/") + "/"
    return f"{base_url.rstrip('/')}/{path.strip('/')}/"
