"""Application service that orchestrates scraping and persistence.

This use case is intentionally small: it does not know CityPopulation HTML
quirks and it does not contain country-specific corrections.  The pipeline is:

1. ask the configured scraper to parse every page;
2. normalize/link equivalent CityPopulation blocks and assign most-populated city;
3. persist the resulting rows in the repository;
4. run remaining derived calculations such as representatives.

Keeping the application layer thin makes the scraping system easier for another
AI to read and safely modify.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import time
from typing import Callable
from urllib.parse import urljoin

from ciudades_del_mundo.application.citypopulation_linking import (
    apply_runtime_config_extensions,
    normalize_citypopulation_entities,
    unlinked_entities,
)
from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.application.entity_merges import apply_entity_merges
from ciudades_del_mundo.domain import (
    AdminAreaSummary,
    DivisionSourceType,
    ScrapedAdminArea,
    ScrapingJobConfig,
    calculate_most_populated_assignments,
)
from ciudades_del_mundo.ports import AdminAreaRepository, HtmlFetcher, HtmlScraper, UnitOfWork


SCRAPE_BLOCK_ERROR_CODE = "SCR-BLOCK-001"
SCRAPE_LINK_ERROR_CODE = "SCR-LINK-001"
SAVE_MAX_RETRIES = 10
_SECTION_ANNOTATION_PREFIX = "CityPopulation section:"
_CONFIGURED_BLOCK_FORMATS = {
    DivisionSourceType.CITIES.value,
    DivisionSourceType.ADMIN.value,
    DivisionSourceType.CITIESADMIN.value,
}
_SECTIONS_BY_FORMAT = {
    DivisionSourceType.CITIES.value: ("infosection", "major_subdivision", "cities"),
    DivisionSourceType.ADMIN.value: ("infosection", "major_subdivision", "minor_subdivision"),
    DivisionSourceType.CITIESADMIN.value: (
        "infosection",
        "major_subdivision",
        "minor_subdivision",
        "cities",
    ),
}


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
    block_index: int = 0
    path_index: int = 0
    required_sections: tuple[str, ...] = ()
    section_repeat_counts: tuple[tuple[str, int], ...] = ()
    found: int | None = None
    html: str = ""
    entities: tuple[ScrapedAdminArea, ...] = ()


@dataclass(frozen=True)
class CachedScrapePage:
    """Previously completed page payload reused by resumable scraping."""

    found: int
    html: str = ""
    entities: tuple[ScrapedAdminArea, ...] = ()


@dataclass(frozen=True)
class ScrapeBlockValidationProblem:
    """One issue that prevents a scraped block from advancing."""

    code: str
    message: str
    url: str = ""
    path: str = ""
    entity_code: str = ""
    entity_name: str = ""
    level: int | None = None
    parent_code: str | None = None


class ScrapeBlockValidationError(RuntimeError):
    """Raised when one configured block is not internally usable."""

    def __init__(
        self,
        *,
        config: ScrapingJobConfig,
        block_index: int,
        pages: tuple[ScrapePageProgress, ...],
        problems: tuple[ScrapeBlockValidationProblem, ...],
        most_populated_assignments: int = 0,
    ) -> None:
        self.code = SCRAPE_BLOCK_ERROR_CODE
        self.config = config
        self.block_index = block_index
        self.pages = pages
        self.problems = problems
        self.most_populated_assignments = most_populated_assignments
        super().__init__(
            f"{self.code}: bloque {block_index + 1} de {config.slug} no valido "
            f"({len(problems)} problema(s))."
        )

    def to_log_text(self) -> str:
        """Return a full diagnostic log, including raw page HTML."""

        lines = [
            self.code,
            f"Config slug: {self.config.slug}",
            f"Country code: {self.config.country_code}",
            f"Block index: {self.block_index}",
            f"Block number: {self.block_index + 1}",
            "Most populated assignment: deferred to step 2 after cross-block linking",
            "",
            "Problems:",
        ]
        for problem in self.problems:
            lines.extend(
                [
                    f"- code: {problem.code}",
                    f"  message: {problem.message}",
                    f"  url: {problem.url}",
                    f"  path: {problem.path}",
                    f"  entity_code: {problem.entity_code}",
                    f"  entity_name: {problem.entity_name}",
                    f"  level: {'' if problem.level is None else problem.level}",
                    f"  parent_code: {'' if problem.parent_code is None else problem.parent_code}",
                ]
            )
        lines.extend(["", "Pages:"])
        for page in self.pages:
            lines.extend(
                [
                    "=" * 80,
                    f"URL: {page.url}",
                    f"Path: {page.path}",
                    f"Format: {page.html_format}",
                    f"Lowest level: {page.lowest_level}",
                    f"Found entities: {page.found}",
                    f"Sections: {', '.join(sorted(_sections_in_entities(page.entities))) or '(none)'}",
                    "HTML BEGIN",
                    page.html or "",
                    "HTML END",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"


class ScrapeLinkValidationError(RuntimeError):
    """Raised when linked in-memory rows still have invalid parents."""

    def __init__(
        self,
        *,
        config: ScrapingJobConfig,
        pages: tuple[ScrapePageProgress, ...],
        entities: tuple[ScrapedAdminArea, ...],
    ) -> None:
        self.code = SCRAPE_LINK_ERROR_CODE
        self.config = config
        self.pages = pages
        self.entities = entities
        super().__init__(
            f"{self.code}: vinculacion de {config.slug} no valida "
            f"({len(entities)} entidad(es) sin padre valido)."
        )

    def to_log_text(self) -> str:
        lines = [
            self.code,
            f"Config slug: {self.config.slug}",
            f"Country code: {self.config.country_code}",
            f"Invalid linked entities: {len(self.entities)}",
            "",
            "Problems:",
        ]
        for entity in self.entities:
            page = _page_for_entity(entity, self.pages)
            lines.extend(
                [
                    "- code: SCR-LINK-PARENT",
                    "  message: Entidad final sin padre valido tras vincular bloques.",
                    f"  url: {entity.url or (page.url if page else '')}",
                    f"  path: {page.path if page else ''}",
                    f"  entity_code: {entity.code}",
                    f"  entity_name: {entity.name}",
                    f"  level: {entity.level}",
                    f"  entity_type: {entity.entity_type or ''}",
                    f"  parent_code: {entity.parent_code or ''}",
                    f"  data_wd: {entity.data_wd or ''}",
                ]
            )
        lines.extend(["", "Pages:"])
        for page in self.pages:
            lines.extend(
                [
                    "=" * 80,
                    f"URL: {page.url}",
                    f"Path: {page.path}",
                    f"Format: {page.html_format}",
                    f"Lowest level: {page.lowest_level}",
                    f"Found entities: {page.found}",
                    f"Sections: {', '.join(sorted(_sections_in_entities(page.entities))) or '(none)'}",
                    "HTML BEGIN",
                    page.html or "",
                    "HTML END",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class ScrapePersistenceProgress:
    """Progress event emitted while the scraped entities are normalized/saved."""

    phase: str
    status: str
    count: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0


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
        wikimedia_parent_resolver: Callable[
            [ScrapingJobConfig, list[ScrapedAdminArea]], list[ScrapedAdminArea]
        ]
        | None = None,
        page_workers: int = 1,
        html_fetcher: HtmlFetcher | None = None,
        on_unlinked_entities: Callable[[ScrapingJobConfig, tuple[ScrapedAdminArea, ...]], None] | None = None,
        on_persistence_progress: Callable[[ScrapingJobConfig, ScrapePersistenceProgress], None] | None = None,
        on_block_validation_error: Callable[[ScrapingJobConfig, ScrapeBlockValidationError], None] | None = None,
        on_link_validation_error: Callable[[ScrapingJobConfig, ScrapeLinkValidationError], None] | None = None,
    ):
        self.repository = repository
        self.scrapers = {scraper.html_format: scraper for scraper in scrapers}
        self.unit_of_work = unit_of_work
        self.on_page_start = on_page_start
        self.on_page_complete = on_page_complete
        self.cached_page_loader = cached_page_loader
        self.on_cached_page = on_cached_page
        self.entity_enricher = entity_enricher
        self.wikimedia_parent_resolver = wikimedia_parent_resolver
        # Accepted for CLI compatibility.  Parsing is kept ordered because the
        # new cross-page linker depends on configured block order.
        self.page_workers = max(1, int(page_workers or 1))
        self.html_fetcher = html_fetcher
        self.on_unlinked_entities = on_unlinked_entities
        self.on_persistence_progress = on_persistence_progress
        self.on_block_validation_error = on_block_validation_error
        self.on_link_validation_error = on_link_validation_error
        self._completed_scrape_pages: list[ScrapePageProgress] = []

    def run(self, config: ScrapingJobConfig) -> ScrapeResult:
        """Execute one complete scraping/import job."""
        self._completed_scrape_pages = []
        entities = self._scrape_pages(config)
        self._notify_persistence(config, "normalize", "start", count=len(entities))
        entities = normalize_citypopulation_entities(config.country_code, entities)
        self._notify_persistence(config, "normalize", "done", count=len(entities))
        if config.entity_merges:
            self._notify_persistence(config, "entity_merges", "start", count=len(entities))
            entities = apply_entity_merges(entities, config.entity_merges)
            entities = _keep_first_scraped_entity(entities)
            self._notify_persistence(config, "entity_merges", "done", count=len(entities))
        if config.cities:
            self._notify_persistence(config, "configured_cities", "start", count=len(entities))
            entities = apply_configured_cities(config.country_code, entities, config.cities)
            entities = _keep_first_scraped_entity(entities)
            self._notify_persistence(config, "configured_cities", "done", count=len(entities))
        self._notify_persistence(config, "runtime_extensions", "start", count=len(entities))
        entities = apply_runtime_config_extensions(config, entities)
        self._notify_persistence(config, "runtime_extensions", "done", count=len(entities))

        if self.wikimedia_parent_resolver:
            invalid_before = tuple(_invalid_linked_entities(config.country_code, entities))
            self._notify_persistence(config, "wikimedia_parent_links", "start", count=len(invalid_before))
            previous_entities = list(entities)
            entities = self.wikimedia_parent_resolver(config, entities)
            repaired = _count_parent_link_changes(previous_entities, entities)
            self._notify_persistence(
                config,
                "wikimedia_parent_links",
                "done",
                count=len(invalid_before),
                updated=repaired,
            )

        self._notify_persistence(config, "link_validation", "start", count=len(entities))
        self._validate_linked_entities(config, entities)
        self._notify_persistence(config, "link_validation", "done", count=len(entities))

        if self.on_unlinked_entities:
            unresolved = tuple(unlinked_entities(config.country_code, entities))
            if unresolved:
                self.on_unlinked_entities(config, unresolved)

        self._notify_persistence(config, "most_populated_assign", "start", count=len(entities))
        entities, most_populated_updated = _assign_most_populated_to_entities(config, entities)
        self._notify_persistence(
            config,
            "most_populated_assign",
            "done",
            count=most_populated_updated,
            updated=most_populated_updated,
        )

        if self.entity_enricher:
            self._notify_persistence(config, "entity_enrichment", "start", count=len(entities))
            entities = self.entity_enricher(config, entities)
            self._notify_persistence(config, "entity_enrichment", "done", count=len(entities))

        if config.reset_before_import:
            self._notify_persistence(config, "reset", "start", count=len(entities))
            with self._transaction():
                self.repository.reset_country(config.country_code)
            self._notify_persistence(config, "reset", "done", count=len(entities))

        self._notify_persistence(config, "save", "start", count=len(entities))
        created, updated = self._save_entities_with_retry(config, entities)
        self._notify_persistence(
            config,
            "save",
            "done",
            count=len(entities),
            created=created,
            updated=updated,
        )

        incoming_ids = {entity.id for entity in entities}
        self._notify_persistence(config, "delete_missing", "start", count=len(incoming_ids))
        with self._transaction():
            deleted = self.repository.delete_missing(config.country_code, incoming_ids)
        self._notify_persistence(config, "delete_missing", "done", count=len(incoming_ids), deleted=deleted)

        representatives_updated = 0
        if config.representation:
            self._notify_persistence(config, "representatives", "start", count=len(entities))
            with self._transaction():
                representatives_updated = self.repository.save_representatives(
                    config.country_code,
                    config.representation,
                )
            self._notify_persistence(
                config,
                "representatives",
                "done",
                count=len(entities),
                updated=representatives_updated,
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
        entities: list[ScrapedAdminArea] = []
        for block_index, indexed_pages in _iter_page_blocks(config.pages):
            if self.page_workers > 1 and self.html_fetcher:
                block_entities, completed_pages = self._scrape_block_with_prefetch(config, indexed_pages)
            else:
                block_entities, completed_pages = self._scrape_block_sequential(config, indexed_pages)
            self._validate_scraped_block(
                config,
                block_index=block_index,
                completed_pages=tuple(completed_pages),
                block_entities=block_entities,
                previous_entities=tuple(entities),
            )
            self._completed_scrape_pages.extend(completed_pages)
            entities.extend(block_entities)
        return entities

    def _scrape_block_sequential(
        self,
        config: ScrapingJobConfig,
        indexed_pages: list[tuple[int, object]],
    ) -> tuple[list[ScrapedAdminArea], list[ScrapePageProgress]]:
        entities: list[ScrapedAdminArea] = []
        completed_pages: list[ScrapePageProgress] = []
        for index, page in indexed_pages:
            progress = self._page_progress(config, page, index)
            cached = self._cached_page(progress)
            if cached:
                self._notify_cached_page(progress, cached)
                completed = replace(progress, found=cached.found, html=cached.html, entities=cached.entities)
                completed_pages.append(completed)
                entities.extend(cached.entities)
                continue

            if self.on_page_start:
                self.on_page_start(progress)
            page_entities, html, url = self._scrape_single_page(config, page, progress.url)
            page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
            page_entities = _apply_page_forced_highest_level_marker(page_entities, page)
            page_entities = _apply_page_sum_to_root_marker(page_entities, page)
            page_entities = _apply_page_parent_level_marker(page_entities, page)
            page_entities = _apply_page_root_parent_alias(page_entities)
            completed = replace(progress, url=url, found=len(page_entities), html=html, entities=tuple(page_entities))
            if self.on_page_complete:
                self.on_page_complete(completed)
            completed_pages.append(completed)
            entities.extend(page_entities)
        return entities, completed_pages

    def _scrape_block_with_prefetch(
        self,
        config: ScrapingJobConfig,
        indexed_pages: list[tuple[int, object]],
    ) -> tuple[list[ScrapedAdminArea], list[ScrapePageProgress]]:
        """Download independent URLs concurrently inside one configured block."""
        entities: list[ScrapedAdminArea] = []
        completed_pages: list[ScrapePageProgress] = []
        pending: list[tuple[object, ScrapePageProgress, CachedScrapePage | None]] = []
        unique_urls: list[str] = []
        seen_urls: set[str] = set()

        for index, page in indexed_pages:
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
                completed = replace(progress, found=cached.found, html=cached.html, entities=cached.entities)
                completed_pages.append(completed)
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
            page_entities = _apply_page_root_parent_alias(page_entities)
            completed = replace(
                progress,
                url=parsed[1],
                found=len(page_entities),
                html=html,
                entities=tuple(page_entities),
            )
            if self.on_page_complete:
                self.on_page_complete(completed)
            completed_pages.append(completed)
            entities.extend(page_entities)
        return entities, completed_pages

    def _validate_scraped_block(
        self,
        config: ScrapingJobConfig,
        *,
        block_index: int,
        completed_pages: tuple[ScrapePageProgress, ...],
        block_entities: list[ScrapedAdminArea],
        previous_entities: tuple[ScrapedAdminArea, ...],
    ) -> None:
        problems, assignment_count = _scrape_block_validation_problems(
            config,
            block_index=block_index,
            completed_pages=completed_pages,
            block_entities=block_entities,
            previous_entities=previous_entities,
        )
        if not problems:
            return
        error = ScrapeBlockValidationError(
            config=config,
            block_index=block_index,
            pages=completed_pages,
            problems=tuple(problems),
            most_populated_assignments=assignment_count,
        )
        if self.on_block_validation_error:
            self.on_block_validation_error(config, error)
        raise error

    def _validate_linked_entities(self, config: ScrapingJobConfig, entities: list[ScrapedAdminArea]) -> None:
        invalid = tuple(_invalid_linked_entities(config.country_code, entities))
        if not invalid:
            return
        if not _should_fail_link_validation(config):
            return
        error = ScrapeLinkValidationError(
            config=config,
            pages=tuple(self._completed_scrape_pages),
            entities=invalid,
        )
        if self.on_link_validation_error:
            self.on_link_validation_error(config, error)
        raise error

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
            block_index=int(getattr(page, "block_index", 0) or 0),
            path_index=int(getattr(page, "path_index", 0) or 0),
            required_sections=_required_sections_for_page(page),
            section_repeat_counts=tuple(
                (str(section), int(count))
                for section, count in (getattr(page, "repeat", None) or {}).items()
            ),
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

    def _save_entities_with_retry(
        self,
        config: ScrapingJobConfig,
        entities: list[ScrapedAdminArea],
    ) -> tuple[int, int]:
        delay = 0.25
        for attempt in range(1, SAVE_MAX_RETRIES + 1):
            try:
                with self._transaction():
                    return self.repository.save_many(config.country_code, entities)
            except Exception:
                if attempt >= SAVE_MAX_RETRIES:
                    raise
                self._notify_persistence(config, "save_retry", "start", count=attempt)
                time.sleep(delay)
                delay = min(delay * 1.5, 3.0)
        raise RuntimeError("save retry loop exhausted")

    def _notify_persistence(
        self,
        config: ScrapingJobConfig,
        phase: str,
        status: str,
        *,
        count: int = 0,
        created: int = 0,
        updated: int = 0,
        deleted: int = 0,
    ) -> None:
        if not self.on_persistence_progress:
            return
        self.on_persistence_progress(
            config,
            ScrapePersistenceProgress(
                phase=phase,
                status=status,
                count=count,
                created=created,
                updated=updated,
                deleted=deleted,
            ),
        )


def _iter_page_blocks(pages) -> list[tuple[int, list[tuple[int, object]]]]:
    blocks: list[tuple[int, list[tuple[int, object]]]] = []
    current_block_index: int | None = None
    current_pages: list[tuple[int, object]] = []
    for index, page in enumerate(pages):
        block_index = int(getattr(page, "block_index", index) or 0)
        if current_block_index is None:
            current_block_index = block_index
        if block_index != current_block_index:
            blocks.append((current_block_index, current_pages))
            current_block_index = block_index
            current_pages = []
        current_pages.append((index, page))
    if current_block_index is not None:
        blocks.append((current_block_index, current_pages))
    return blocks


def _count_parent_link_changes(before: list[ScrapedAdminArea], after: list[ScrapedAdminArea]) -> int:
    total = 0
    for old, new in zip(before, after, strict=False):
        if old.parent_code != new.parent_code or int(old.level) != int(new.level):
            total += 1
    return total


def _scrape_block_validation_problems(
    config: ScrapingJobConfig,
    *,
    block_index: int,
    completed_pages: tuple[ScrapePageProgress, ...],
    block_entities: list[ScrapedAdminArea],
    previous_entities: tuple[ScrapedAdminArea, ...],
) -> tuple[list[ScrapeBlockValidationProblem], int]:
    problems: list[ScrapeBlockValidationProblem] = []
    assignment_count = 0
    if not block_entities:
        for page in completed_pages:
            problems.append(
                ScrapeBlockValidationProblem(
                    code="SCR-BLOCK-EMPTY",
                    message="La pagina no produjo entidades para este bloque.",
                    url=page.url,
                    path=page.path,
                )
            )
        return problems, assignment_count

    validate_structure = _should_validate_block_structure(completed_pages)
    problems.extend(_section_validation_problems(completed_pages))
    if not validate_structure:
        return problems, assignment_count

    problems.extend(_parent_validation_problems(completed_pages, block_entities, previous_entities))

    return problems, assignment_count


def _should_validate_block_structure(completed_pages: tuple[ScrapePageProgress, ...]) -> bool:
    return any(str(page.html_format) in _CONFIGURED_BLOCK_FORMATS for page in completed_pages)


def _should_fail_link_validation(config: ScrapingJobConfig) -> bool:
    return any(str(page.html_format) in _CONFIGURED_BLOCK_FORMATS for page in config.pages)


def _invalid_linked_entities(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    scoped = [entity for entity in entities if entity.country_code == country_code]
    by_code = {str(entity.code) for entity in scoped}
    invalid: list[ScrapedAdminArea] = []
    for entity in scoped:
        if int(entity.level) == 0:
            continue
        parent_code = str(entity.parent_code or "").strip()
        if not parent_code or parent_code == str(entity.code) or parent_code not in by_code:
            invalid.append(entity)
    return invalid


def _section_validation_problems(
    completed_pages: tuple[ScrapePageProgress, ...],
) -> list[ScrapeBlockValidationProblem]:
    problems: list[ScrapeBlockValidationProblem] = []
    for page in completed_pages:
        if str(page.html_format) not in _CONFIGURED_BLOCK_FORMATS:
            continue
        if not page.required_sections:
            continue
        sections = _sections_in_entities(page.entities)
        repeat_counts = {section: count for section, count in page.section_repeat_counts}
        for section in page.required_sections:
            expected = max(1, int(repeat_counts.get(section, 1) or 1))
            actual = sections.get(section, 0)
            if actual >= expected:
                continue
            problems.append(
                ScrapeBlockValidationProblem(
                    code="SCR-BLOCK-SECTION",
                    message=(
                        f"No se scrapeo la seccion configurada {section!r} "
                        f"(esperadas={expected}, encontradas={actual})."
                    ),
                    url=page.url,
                    path=page.path,
                )
            )
    return problems


def _parent_validation_problems(
    completed_pages: tuple[ScrapePageProgress, ...],
    block_entities: list[ScrapedAdminArea],
    previous_entities: tuple[ScrapedAdminArea, ...],
) -> list[ScrapeBlockValidationProblem]:
    if not block_entities:
        return []
    allowed_parent_codes = {str(entity.code) for entity in previous_entities}
    allowed_parent_codes.update(str(entity.code) for entity in block_entities)
    min_level = min(int(entity.level) for entity in block_entities)
    problems: list[ScrapeBlockValidationProblem] = []
    for entity in block_entities:
        if int(entity.level) <= min_level:
            continue
        parent_code = str(entity.parent_code or "").strip()
        if not parent_code:
            page = _page_for_entity(entity, completed_pages)
            problems.append(
                ScrapeBlockValidationProblem(
                    code="SCR-BLOCK-PARENT",
                    message=(
                        "La entidad no tiene padre asignado dentro del bloque "
                        "ni en bloques anteriores."
                    ),
                    url=entity.url or (page.url if page else ""),
                    path=page.path if page else "",
                    entity_code=str(entity.code),
                    entity_name=entity.name,
                    level=int(entity.level),
                    parent_code=entity.parent_code,
                )
            )
            continue
        if parent_code in allowed_parent_codes:
            continue
        page = _page_for_entity(entity, completed_pages)
        problems.append(
            ScrapeBlockValidationProblem(
                code="SCR-BLOCK-PARENT",
                message=(
                    "La entidad apunta a un padre que no existe en el bloque "
                    "ni en bloques anteriores."
                ),
                url=entity.url or (page.url if page else ""),
                path=page.path if page else "",
                entity_code=str(entity.code),
                entity_name=entity.name,
                level=int(entity.level),
                parent_code=entity.parent_code,
            )
        )
    return problems


def _assign_most_populated_to_entities(
    config: ScrapingJobConfig,
    entities: list[ScrapedAdminArea],
) -> tuple[list[ScrapedAdminArea], int]:
    summaries = _summaries_from_scraped_entities(entities)
    assignments = calculate_most_populated_assignments(summaries, config.legal_subdivision_level)
    assignment_by_area_id = {assignment.area_id: assignment.most_populated_id for assignment in assignments}
    code_by_id = {entity.id: entity.code for entity in entities}

    updated = 0
    result: list[ScrapedAdminArea] = []
    for entity in entities:
        target_id = assignment_by_area_id.get(entity.id)
        target_code = code_by_id.get(target_id) if target_id else None
        if (entity.most_populated_city_code or None) == (target_code or None):
            result.append(entity)
            continue
        result.append(replace(entity, most_populated_city_code=target_code))
        updated += 1
    return result, updated


def _summaries_from_scraped_entities(entities: list[ScrapedAdminArea]) -> list[AdminAreaSummary]:
    summaries: list[AdminAreaSummary] = []
    seen_ids: set[str] = set()
    for entity in entities:
        if entity.id in seen_ids:
            continue
        seen_ids.add(entity.id)
        parent_code = str(entity.parent_code or "").strip()
        summaries.append(
            AdminAreaSummary(
                id=entity.id,
                level=int(entity.level),
                parent_id=f"{entity.country_code}_{parent_code}" if parent_code else None,
                pop_latest=entity.pop_latest,
                city_merge_status=entity.city_merge_status,
                most_populate_city_id=(
                    f"{entity.country_code}_{entity.most_populated_city_code}"
                    if entity.most_populated_city_code
                    else None
                ),
            )
        )
    return summaries


def _apply_page_root_parent_alias(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Rewrite same-page direct children from an HTML root id to the stored root.

    Some CityPopulation pages expose children with ``parent_code`` pointing to
    the raw page-root id while the parser persists the root using the configured
    country code.  This is not a cross-page guess: only direct children of a
    root row already present in the same parsed page are rewritten.
    """
    roots = [
        entity
        for entity in entities
        if int(entity.level) == 0 and not entity.parent_code
    ]
    if len(roots) != 1:
        return entities
    root = roots[0]
    root_code = str(root.code)
    codes = {str(entity.code) for entity in entities}
    missing_parent_codes = {
        str(entity.parent_code)
        for entity in entities
        if entity.parent_code
        and str(entity.parent_code) not in codes
        and int(entity.level) == int(root.level) + 1
    }
    if len(missing_parent_codes) != 1:
        return entities
    alias = next(iter(missing_parent_codes))
    return [
        replace(entity, parent_code=root_code)
        if str(entity.parent_code or "") == alias and int(entity.level) == int(root.level) + 1
        else entity
        for entity in entities
    ]


def _required_sections_for_page(page) -> tuple[str, ...]:
    html_format = str(getattr(page, "html_format", "") or "").strip().lower()
    sections = _SECTIONS_BY_FORMAT.get(html_format, ())
    if not sections:
        return ()
    return tuple(section for section in sections if _page_includes_section(page, section))


def _page_includes_section(page, section: str) -> bool:
    if section == "infosection" and getattr(page, "include_root", True) is False:
        return False
    property_name = {
        "infosection": "include_infosection",
        "major_subdivision": "include_major_subdivision",
        "minor_subdivision": "include_minor_subdivision",
        "cities": "include_cities",
    }[section]
    return bool(getattr(page, property_name, True))


def _sections_in_entities(entities) -> dict[str, int]:
    sections: dict[str, int] = {}
    for entity in entities:
        for chunk in str(getattr(entity, "annotations", "") or "").split(";"):
            text = chunk.strip()
            if not text.startswith(_SECTION_ANNOTATION_PREFIX):
                continue
            section = text.split(":", 1)[1].strip().casefold()
            if section:
                sections[section] = sections.get(section, 0) + 1
    return sections


def _page_for_entity(
    entity: ScrapedAdminArea | None,
    completed_pages: tuple[ScrapePageProgress, ...],
) -> ScrapePageProgress | None:
    if not completed_pages:
        return None
    if not entity or not entity.url:
        return completed_pages[0]
    entity_url = str(entity.url).rstrip("/")
    for page in completed_pages:
        page_url = str(page.url).rstrip("/")
        if entity_url.startswith(page_url) or page_url in entity_url:
            return page
    return completed_pages[0]



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
