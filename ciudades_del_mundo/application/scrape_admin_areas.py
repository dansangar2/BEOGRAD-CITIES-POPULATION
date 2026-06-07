"""Application service that orchestrates a full scraping/import job."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re
from typing import Callable
from urllib.parse import unquote, urlparse

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
    """Coordinates scrapers, post-processing and repository persistence."""

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
    ):
        self.repository = repository
        self.scrapers = {scraper.html_format: scraper for scraper in scrapers}
        self.unit_of_work = unit_of_work
        self.on_page_start = on_page_start
        self.on_page_complete = on_page_complete
        self.cached_page_loader = cached_page_loader
        self.on_cached_page = on_cached_page
        self.entity_enricher = entity_enricher
        self.page_workers = max(1, int(page_workers or 1))
        self.html_fetcher = html_fetcher

    def run(self, config: ScrapingJobConfig) -> ScrapeResult:
        """Execute the scraping pipeline for a single country/job config."""
        entities = self._scrape_pages(config)
        entities = _rewrite_synthetic_page_roots(config.country_code, entities)
        entities = _apply_runtime_config_extensions(config, entities)
        entities = self._post_process_entities(config, entities)
        if self.entity_enricher:
            entities = self.entity_enricher(config, entities)

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
        if (
            self.html_fetcher
            and self.page_workers > 1
            and len(config.pages) > 1
            and self._all_pages_support_html_prefetch(config)
        ):
            return self._scrape_pages_prefetched(config)

        entities = []
        for index, page in enumerate(config.pages):
            progress = self._page_progress(config, page, index)
            cached = self._cached_page(progress)
            if cached:
                self._notify_cached_page(progress, cached)
                entities.extend(cached.entities)
                continue
            if self.on_page_start:
                self.on_page_start(progress)
            page_entities, page_html, page_url = self._scrape_single_page(config, page, progress.url)
            if page_url != progress.url:
                progress = ScrapePageProgress(
                    path=progress.path,
                    html_format=progress.html_format,
                    lowest_level=progress.lowest_level,
                    url=page_url,
                    index=progress.index,
                )
            page_entities = _apply_page_area_overrides(page_entities, page.area_km2, page.area_overrides)
            self._notify_page_complete(progress, len(page_entities), page_html, page_entities)
            entities.extend(page_entities)
        return entities

    def _scrape_pages_prefetched(self, config: ScrapingJobConfig) -> list[ScrapedAdminArea]:
        """Fetch page HTML concurrently, then parse/persist in configured order.

        This keeps the extracted data and page-complete side effects equivalent
        to the sequential scraper: ``on_page_complete`` is emitted once per
        configured page in TOML/SQL order, and the same layout-specific parser
        handles the HTML. Only independent page downloads are overlapped;
        duplicate URL/format/level pages also reuse the parsed entities.
        """
        entities: list[ScrapedAdminArea] = []
        page_progress = [self._page_progress(config, page, index) for index, page in enumerate(config.pages)]
        cached_by_index: dict[int, CachedScrapePage] = {}
        for index, progress in enumerate(page_progress):
            cached = self._cached_page(progress)
            if cached:
                cached_by_index[index] = cached
                self._notify_cached_page(progress, cached)
                continue
            if self.on_page_start:
                self.on_page_start(progress)

        futures_by_url: dict[str, Future[str]] = {}
        parsed_by_page_key: dict[tuple[str, str, int], tuple[ScrapedAdminArea, ...]] = {}
        with ThreadPoolExecutor(max_workers=min(self.page_workers, len(config.pages))) as executor:
            for index, progress in enumerate(page_progress):
                if index in cached_by_index:
                    continue
                if progress.url not in futures_by_url:
                    futures_by_url[progress.url] = executor.submit(self.html_fetcher.get, progress.url)

            for index, (page, progress) in enumerate(zip(config.pages, page_progress, strict=True)):
                cached = cached_by_index.get(index)
                if cached:
                    entities.extend(cached.entities)
                    continue
                scraper = self._scraper_for(page.html_format)
                scrape_html = getattr(scraper, "scrape_html", None)
                if not callable(scrape_html):
                    page_entities, page_html, page_url = self._scrape_single_page(config, page, progress.url)
                else:
                    page_html = futures_by_url[progress.url].result()
                    page_url = progress.url
                    page_key = (
                        page_url,
                        page.html_format,
                        page.lowest_level,
                        tuple(sorted(getattr(page, "table_levels", {}).items())),
                        tuple(getattr(page, "include_tables", ())),
                        getattr(page, "include_root", True),
                        getattr(page, "root_level", None),
                        getattr(page, "root_code", None),
                        getattr(page, "root_name", None),
                        getattr(page, "root_parent_code", None),
                        getattr(page, "root_entity_type", None),
                    )
                    cached_entities = parsed_by_page_key.get(page_key)
                    if cached_entities is None:
                        scrape_configured_html = getattr(scraper, "scrape_configured_html", None)
                        if callable(scrape_configured_html):
                            cached_entities = tuple(
                                scrape_configured_html(
                                    html=page_html,
                                    url=page_url,
                                    country_code=config.country_code,
                                    page=page,
                                )
                            )
                        else:
                            cached_entities = tuple(
                                scrape_html(
                                    html=page_html,
                                    url=page_url,
                                    country_code=config.country_code,
                                    level=page.lowest_level,
                                )
                            )
                        parsed_by_page_key[page_key] = cached_entities
                    page_entities = list(cached_entities)
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

    def _cached_page(self, progress: ScrapePageProgress) -> CachedScrapePage | None:
        if not self.cached_page_loader:
            return None
        cached = self.cached_page_loader(progress)
        if not cached or not cached.entities:
            return None
        return cached

    def _page_progress(self, config: ScrapingJobConfig, page, index: int) -> ScrapePageProgress:
        return ScrapePageProgress(
            path=page.path,
            html_format=page.html_format,
            lowest_level=page.lowest_level,
            url=_page_url(config.base_url, page.path),
            index=index,
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
                index=progress.index,
                found=found,
                html=html,
                entities=tuple(entities),
            )
        )

    def _notify_cached_page(self, progress: ScrapePageProgress, cached: CachedScrapePage) -> None:
        if not self.on_cached_page:
            return
        self.on_cached_page(
            ScrapePageProgress(
                path=progress.path,
                html_format=progress.html_format,
                lowest_level=progress.lowest_level,
                url=progress.url,
                index=progress.index,
                found=cached.found,
                html=cached.html,
                entities=cached.entities,
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



def _apply_runtime_config_extensions(
    config: ScrapingJobConfig,
    entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    """Apply optional SQL/TOML runtime extensions not needed by basic configs.

    The domain config model intentionally keeps common CityPopulation fields
    small.  A few countries need data-only post-processing such as synthetic
    grouping rows or additive country totals.  Composition roots attach those
    parsed extension dictionaries to the otherwise typed config so the use case
    can keep the transformation deterministic and reusable.
    """
    if not entities:
        return entities

    extended = list(entities)
    extended = _apply_synthetic_entities(
        config.country_code,
        extended,
        getattr(config, "runtime_synthetic_entities", ()),
    )
    extended = _apply_parent_overrides(
        extended,
        getattr(config, "runtime_parent_overrides", ()),
    )
    extended = _refresh_synthetic_entity_metrics(
        extended,
        getattr(config, "runtime_synthetic_entities", ()),
    )
    extended = _fill_missing_root_metrics_from_children(config.country_code, extended)
    extended = _apply_root_metric_sources(
        config.country_code,
        extended,
        getattr(config, "runtime_root_metric_sources", ()),
    )
    return extended


def _apply_synthetic_entities(
    country_code: str,
    entities: list[ScrapedAdminArea],
    specs,
) -> list[ScrapedAdminArea]:
    if not specs:
        return entities

    original_by_code = _first_entity_by_code(entities)
    existing_codes = {entity.code for entity in entities}
    updated = list(entities)
    for spec in specs:
        code = str(spec.get("code") or "").strip()
        name = str(spec.get("name") or code).strip()
        if not code or code in existing_codes:
            continue

        area_km2 = _metric_decimal(spec.get("area_km2"))
        pop_latest = _metric_int(spec.get("pop_latest"))
        pop_latest_date = spec.get("pop_latest_date") or None
        density = None

        copy_code = str(spec.get("copy_metrics_from") or "").strip()
        if copy_code:
            source = original_by_code.get(copy_code)
            if source:
                area_km2 = source.area_km2
                pop_latest = source.pop_latest
                pop_latest_date = source.pop_latest_date
                density = source.density

        metric_sources = _metric_sources_for_synthetic_spec(entities, spec)
        if metric_sources:
            area_km2 = _sum_decimals(source.area_km2 for source in metric_sources)
            pop_latest = _sum_ints(source.pop_latest for source in metric_sources)
            pop_latest_date = _latest_metric_date(source.pop_latest_date for source in metric_sources)
            density = _density(pop_latest, area_km2)

        entity_type = str(spec.get("entity_type") or "").strip()
        raw_entity_type = str(spec.get("raw_entity_type") or entity_type).strip()
        updated.append(
            ScrapedAdminArea(
                code=code,
                name=name,
                level=int(spec.get("level") or 0),
                country_code=country_code,
                parent_code=str(spec.get("parent_code") or "").strip() or None,
                entity_type=entity_type,
                raw_entity_type=raw_entity_type,
                area_km2=area_km2,
                density=density,
                pop_latest=pop_latest,
                pop_latest_date=pop_latest_date,
                url=str(spec.get("url") or "").strip(),
            )
        )
        existing_codes.add(code)
    return updated



def _refresh_synthetic_entity_metrics(
    entities: list[ScrapedAdminArea],
    specs,
) -> list[ScrapedAdminArea]:
    if not specs:
        return entities

    specs_by_code = {str(spec.get("code") or "").strip(): spec for spec in specs if str(spec.get("code") or "").strip()}
    if not specs_by_code:
        return entities

    updated = []
    for entity in entities:
        spec = specs_by_code.get(entity.code)
        if not spec:
            updated.append(entity)
            continue
        sources = _metric_sources_for_synthetic_spec(entities, spec, exclude_codes={entity.code})
        if not sources:
            updated.append(entity)
            continue
        area_km2 = _sum_decimals(source.area_km2 for source in sources)
        pop_latest = _sum_ints(source.pop_latest for source in sources)
        pop_latest_date = _latest_metric_date(source.pop_latest_date for source in sources)
        updated.append(
            replace(
                entity,
                area_km2=area_km2,
                pop_latest=pop_latest,
                pop_latest_date=pop_latest_date,
                density=_density(pop_latest, area_km2),
            )
        )
    return updated


def _metric_sources_for_synthetic_spec(
    entities: list[ScrapedAdminArea],
    spec: dict,
    *,
    exclude_codes: set[str] | None = None,
) -> list[ScrapedAdminArea]:
    exclude_codes = exclude_codes or set()
    by_code = _first_entity_by_code(entities)
    sources: list[ScrapedAdminArea] = []
    seen: set[str] = set()

    for raw_code in spec.get("metric_source_codes") or ():
        code = str(raw_code).strip()
        source = by_code.get(code)
        if source and source.code not in exclude_codes and source.code not in seen:
            sources.append(source)
            seen.add(source.code)

    parent_code = str(spec.get("metric_source_parent_code") or "").strip()
    raw_level = spec.get("metric_source_level")
    metric_level = int(raw_level) if raw_level not in (None, "") else None
    if parent_code or metric_level is not None:
        for entity in entities:
            if entity.code in exclude_codes or entity.code in seen:
                continue
            if parent_code and entity.parent_code != parent_code:
                continue
            if metric_level is not None and entity.level != metric_level:
                continue
            sources.append(entity)
            seen.add(entity.code)

    return sources


def _fill_missing_root_metrics_from_children(
    country_code: str,
    entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    roots = [entity for entity in entities if entity.code == country_code and entity.level == 0]
    if not roots:
        return entities

    root = roots[0]
    if root.pop_latest is not None and root.area_km2 is not None:
        return entities

    child_levels = [entity.level for entity in entities if entity.parent_code == root.code and entity.level > root.level]
    if not child_levels:
        return entities
    highest_child_level = min(child_levels)
    sources = [
        entity
        for entity in entities
        if entity.parent_code == root.code and entity.level == highest_child_level
    ]
    if not sources:
        return entities

    area_km2 = root.area_km2 if root.area_km2 is not None else _sum_decimals(source.area_km2 for source in sources)
    pop_latest = root.pop_latest if root.pop_latest is not None else _sum_ints(source.pop_latest for source in sources)
    pop_latest_date = root.pop_latest_date or _latest_metric_date(source.pop_latest_date for source in sources)
    updated_root = replace(
        root,
        area_km2=area_km2,
        pop_latest=pop_latest,
        pop_latest_date=pop_latest_date,
        density=_density(pop_latest, area_km2),
    )
    return [updated_root if entity is root else entity for entity in entities]

def _apply_parent_overrides(entities: list[ScrapedAdminArea], overrides) -> list[ScrapedAdminArea]:
    if not overrides:
        return entities

    updated = []
    for entity in entities:
        replacement = entity
        for override in overrides:
            if not _matches_parent_override(entity, override):
                continue
            values = {}
            if "parent_code" in override:
                values["parent_code"] = str(override.get("parent_code") or "").strip() or None
            if "level" in override:
                values["level"] = int(override["level"])
            if values:
                replacement = replace(replacement, **values)
        updated.append(replacement)
    return updated


def _matches_parent_override(entity: ScrapedAdminArea, override: dict) -> bool:
    codes = {str(value).strip() for value in override.get("codes") or () if str(value).strip()}
    if codes and entity.code not in codes:
        return False

    names = {_normalize_entity_name(value) for value in override.get("names") or () if str(value).strip()}
    if names and _normalize_entity_name(entity.name) not in names:
        return False

    levels = {int(value) for value in override.get("match_levels") or ()}
    if levels and entity.level not in levels:
        return False

    # Avoid reparenting synthetic containers by default when an override targets
    # a broad scraped level such as all French regions.
    exclude_codes = {str(value).strip() for value in override.get("exclude_codes") or () if str(value).strip()}
    if entity.code in exclude_codes:
        return False
    return bool(codes or names or levels)


def _apply_root_metric_sources(
    country_code: str,
    entities: list[ScrapedAdminArea],
    specs,
) -> list[ScrapedAdminArea]:
    if not specs:
        return entities

    additions = [_metric_source_entity(entities, spec) for spec in specs]
    additions = [entity for entity in additions if entity]
    if not additions:
        return entities

    updated = []
    for entity in entities:
        if entity.code != country_code or entity.level != 0:
            updated.append(entity)
            continue
        area_km2 = _sum_decimals([entity.area_km2, *[source.area_km2 for source in additions]])
        pop_latest = _sum_ints([entity.pop_latest, *[source.pop_latest for source in additions]])
        pop_latest_date = _latest_metric_date([entity.pop_latest_date, *[source.pop_latest_date for source in additions]])
        updated.append(
            replace(
                entity,
                area_km2=area_km2,
                pop_latest=pop_latest,
                pop_latest_date=pop_latest_date,
                density=_density(pop_latest, area_km2),
            )
        )
    return updated


def _metric_source_entity(entities: list[ScrapedAdminArea], spec: dict) -> ScrapedAdminArea | None:
    code = str(spec.get("code") or "").strip()
    if code:
        for entity in entities:
            if entity.code == code:
                return entity

    name = str(spec.get("name") or "").strip()
    if name:
        normalized = _normalize_entity_name(name)
        for entity in entities:
            if _normalize_entity_name(entity.name) == normalized:
                return entity

    path = str(spec.get("path") or spec.get("url") or "").strip().strip("/")
    if path:
        for entity in entities:
            url_path = urlparse(entity.url or "").path.strip("/")
            if url_path.endswith(path):
                return entity
    return None


def _first_entity_by_code(entities: list[ScrapedAdminArea]) -> dict[str, ScrapedAdminArea]:
    by_code: dict[str, ScrapedAdminArea] = {}
    for entity in entities:
        by_code.setdefault(entity.code, entity)
    return by_code


def _metric_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _metric_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sum_decimals(values):
    total = Decimal("0")
    found = False
    for value in values:
        if value in (None, ""):
            continue
        decimal_value = _metric_decimal(value)
        if decimal_value is None:
            continue
        total += decimal_value
        found = True
    return total if found else None


def _sum_ints(values):
    total = 0
    found = False
    for value in values:
        if value in (None, ""):
            continue
        try:
            total += int(value)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _latest_metric_date(values):
    present = [value for value in values if value]
    if not present:
        return None
    return max(present)


def _density(pop_latest, area_km2):
    if not pop_latest or not area_km2:
        return None
    decimal_area = _metric_decimal(area_km2)
    if not decimal_area:
        return None
    return Decimal(str(pop_latest)) / decimal_area

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
