"""Small, documented parser for the three CityPopulation scraping systems.

The scraper intentionally models CityPopulation pages as **sections** instead of
as many country-specific branches.  A configured page chooses one system:

* ``cities``:      infosection -> major_subdivision -> cities
* ``admin``:       infosection -> major_subdivision -> minor_subdivision
* ``citiesadmin``: infosection -> major_subdivision -> minor_subdivision -> cities

Only enabled sections are persisted.  Levels are sequential from the page base
level, so disabling ``infosection`` automatically moves the following section up
one level.  Repeating a section inserts an extra copy of that same entity and
pushes deeper sections down accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient, CityPopulationEntity

SECTION_INFOSECTION = "infosection"
SECTION_MAJOR = "major_subdivision"
SECTION_MINOR = "minor_subdivision"
SECTION_CITIES = "cities"

SYSTEM_SECTIONS: dict[str, tuple[str, ...]] = {
    "cities": (SECTION_INFOSECTION, SECTION_MAJOR, SECTION_CITIES),
    "admin": (SECTION_INFOSECTION, SECTION_MAJOR, SECTION_MINOR),
    "citiesadmin": (SECTION_INFOSECTION, SECTION_MAJOR, SECTION_MINOR, SECTION_CITIES),
}

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    SECTION_INFOSECTION: ("infosection", "root"),
    SECTION_MAJOR: ("major_subdivision", "admin1", "adm", "tl"),
    SECTION_MINOR: ("minor_subdivision", "admin2", "tl_child"),
    SECTION_CITIES: ("cities", "city", "ts"),
}

SECTION_DEFAULT_ENTITY_TYPE = {
    SECTION_INFOSECTION: None,
    SECTION_MAJOR: "Administrative Division",
    SECTION_MINOR: "Administrative Division",
    SECTION_CITIES: "City",
}

SECTION_ANNOTATION_PREFIX = "CityPopulation section"
REPEAT_ANNOTATION = "Duplicación explícita por página"


@dataclass(frozen=True)
class _TlParsedRow:
    row: Tag
    parsed: CityPopulationEntity


@dataclass(frozen=True)
class _TlBlock:
    index: int
    section: str
    rows: tuple[_TlParsedRow, ...]


@dataclass(frozen=True)
class _TlMajorEntry:
    block_index: int
    row: Tag
    chain: tuple[ScrapedAdminArea, ...]

    @property
    def entity(self) -> ScrapedAdminArea:
        return self.chain[-1]


class CityPopulationSectionScraperMixin:
    """Mixin used by concrete scraper adapters.

    Infrastructure adapters stay tiny: each one only declares ``html_format``.
    All table semantics live in ``CityPopulationSectionParser`` below.
    """

    html_format: str

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        return CityPopulationSectionParser(self._client).parse(
            html=html,
            url=url,
            country_code=country_code,
            system=self.html_format,
            base_level=level,
            page=None,
        )

    def scrape_configured_html(self, html: str, url: str, country_code: str, page) -> list[ScrapedAdminArea]:
        return CityPopulationSectionParser(self._client).parse(
            html=html,
            url=url,
            country_code=country_code,
            system=getattr(page, "html_format", self.html_format),
            base_level=_page_base_level(page, getattr(page, "lowest_level", 1)),
            page=page,
        )


class CityPopulationSectionParser:
    """Parse CityPopulation HTML into domain entities without saving anything.

    The parser does not know Django, SQL or assets.  Its only responsibility is
    translating one HTML page into ``ScrapedAdminArea`` rows with the most stable
    parent code available from that same page.  Cross-page deduplication/linking
    is done later in the application layer.
    """

    def __init__(self, client: CityPopulationClient):
        self.client = client

    def parse(
        self,
        *,
        html: str,
        url: str,
        country_code: str,
        system: str,
        base_level: int,
        page: Any | None,
    ) -> list[ScrapedAdminArea]:
        system = _normalize_system(system)
        soup = BeautifulSoup(html, self.client.parser)
        effective_system = "citiesadmin" if system == "cities" and _tl_has_grouped_minor(soup) else system
        include = _include_sections(page, effective_system)
        if effective_system == "citiesadmin" and system == "cities":
            include = _include_implicit_minor_for_grouped_cities(include)
        levels = _section_levels(system=effective_system, include=include, page=page, base_level=base_level)
        base_url = self.client.base_for_urljoin(url)

        entities: list[ScrapedAdminArea] = []
        parent_context = _ParentContext()

        root = self._parse_infosection_root(soup, country_code=country_code, level=levels.get(SECTION_INFOSECTION, base_level), url=url)
        root = _apply_root_hints(root, page=page, country_code=country_code, url=url, default_level=levels.get(SECTION_INFOSECTION, base_level))
        if root and SECTION_INFOSECTION in include:
            root_chain = _repeat_entity_chain(root, section=SECTION_INFOSECTION, page=page)
            entities.extend(root_chain)
            for item in root_chain:
                parent_context.register(item)

        if effective_system in {"cities", "admin", "citiesadmin"}:
            entities.extend(
                self._parse_tl_sections(
                    soup=soup,
                    system=effective_system,
                    include=include,
                    levels=levels,
                    base_level=base_level,
                    country_code=country_code,
                    base_url=base_url,
                    page=page,
                    parent_context=parent_context,
                )
            )

        if (
            effective_system == "cities"
            and SECTION_MAJOR in include
            and SECTION_CITIES not in include
            and not soup.find("table", id="tl")
        ):
            entities.extend(
                self._parse_ts_cities(
                    soup=soup,
                    section=SECTION_MAJOR,
                    level=levels[SECTION_MAJOR],
                    country_code=country_code,
                    base_url=base_url,
                    page=page,
                    parent_context=parent_context,
                )
            )

        if effective_system in {"cities", "citiesadmin"} and SECTION_CITIES in include:
            entities.extend(
                self._parse_ts_cities(
                    soup=soup,
                    section=SECTION_CITIES,
                    level=levels[SECTION_CITIES],
                    country_code=country_code,
                    base_url=base_url,
                    page=page,
                    parent_context=parent_context,
                )
            )

        parsed_entities = [entity for entity in entities if entity is not None]
        parsed_entities = _mark_forced_highest_level_entities(parsed_entities, page=page)
        parsed_entities = _mark_forced_parent_level_entities(parsed_entities, page=page)
        return _mark_sum_to_root_entities(parsed_entities, page=page)

    def _parse_tl_sections(
        self,
        *,
        soup: BeautifulSoup,
        system: str,
        include: tuple[str, ...],
        levels: dict[str, int],
        base_level: int,
        country_code: str,
        base_url: str,
        page: Any | None,
        parent_context: "_ParentContext",
    ) -> list[ScrapedAdminArea]:
        table = soup.find("table", id="tl")
        if not isinstance(table, Tag):
            return []

        last_pop_idx, last_pop_date = self.client.detect_last_visible_pop_column(table)
        visible_pop_columns = self.client.visible_pop_columns(table)
        last_year = self.client.year_from_date(last_pop_date)
        if system == "citiesadmin" and _tl_has_classed_major(table):
            return self._parse_grouped_tl_sections(
                table=table,
                include=include,
                levels=levels,
                base_level=base_level,
                country_code=country_code,
                base_url=base_url,
                page=page,
                parent_context=parent_context,
                last_pop_idx=last_pop_idx,
                last_pop_date=last_pop_date,
                visible_pop_columns=visible_pop_columns,
                last_year=last_year,
                system=system,
            )

        rows: list[ScrapedAdminArea] = []
        current_major: ScrapedAdminArea | None = parent_context.latest_for_section(SECTION_MAJOR)

        for tbody in table.find_all("tbody", recursive=False):
            section = _section_for_tl_tbody(system, tbody, current_major is not None)
            persist_section = section in include
            entity_level = _level_for_section(
                section=section,
                levels=levels,
                page=page,
                base_level=base_level,
                system=system,
            )
            for tr in tbody.find_all("tr", recursive=False):
                parsed = self.client.parse_tr_tl(
                    tr=tr,
                    explicit_level=entity_level,
                    last_visible_pop_idx=last_pop_idx,
                    last_visible_date=last_pop_date,
                    default_last_census_year=last_year,
                    country_code=country_code,
                    base_url=base_url,
                    default_entity_type=SECTION_DEFAULT_ENTITY_TYPE.get(section),
                    visible_pop_columns=visible_pop_columns,
                )
                if not parsed:
                    continue

                parent = self._parent_for_tl_row(
                    section=section,
                    tr=tr,
                    parent_context=parent_context,
                    current_major=current_major,
                )
                entity = _entity_from_parsed(
                    parsed,
                    section=section,
                    level=entity_level,
                    country_code=country_code,
                    parent_code=parent.code if parent else None,
                )
                chain = _repeat_entity_chain(entity, section=section, page=page)
                if persist_section:
                    rows.extend(chain)
                for item in chain:
                    parent_context.register(item, row=tr)
                if section == SECTION_MAJOR:
                    current_major = chain[-1]
        return rows

    def _parse_grouped_tl_sections(
        self,
        *,
        table: Tag,
        include: tuple[str, ...],
        levels: dict[str, int],
        base_level: int,
        country_code: str,
        base_url: str,
        page: Any | None,
        parent_context: "_ParentContext",
        last_pop_idx: int,
        last_pop_date: str | None,
        visible_pop_columns: list[tuple[int, str | None]],
        last_year: int | None,
        system: str,
    ) -> list[ScrapedAdminArea]:
        blocks = self._parse_tl_blocks(
            table=table,
            levels=levels,
            base_level=base_level,
            country_code=country_code,
            base_url=base_url,
            page=page,
            last_pop_idx=last_pop_idx,
            last_pop_date=last_pop_date,
            visible_pop_columns=visible_pop_columns,
            last_year=last_year,
            system=system,
        )
        major_entries = self._tl_major_entries(
            blocks=blocks,
            levels=levels,
            base_level=base_level,
            country_code=country_code,
            page=page,
            parent_context=parent_context,
            system=system,
        )
        major_lookup = _major_lookup(major_entries)
        emitted_major_codes: set[tuple[int, str]] = set()
        rows: list[ScrapedAdminArea] = []

        def emit_major(entry: _TlMajorEntry) -> None:
            marker = (entry.block_index, entry.entity.code)
            if marker in emitted_major_codes:
                return
            if SECTION_MAJOR in include:
                rows.extend(entry.chain)
            for item in entry.chain:
                parent_context.register(item, row=entry.row)
            emitted_major_codes.add(marker)

        for block in blocks:
            if block.section == SECTION_MAJOR:
                for entry in major_entries.get(block.index, ()):
                    emit_major(entry)
                continue

            parent_entry = _best_major_for_child_block(
                block=block,
                major_entries=major_entries,
                major_lookup=major_lookup,
            )
            if parent_entry is not None:
                emit_major(parent_entry)
            entity_level = _level_for_section(
                section=block.section,
                levels=levels,
                page=page,
                base_level=base_level,
                system=system,
            )
            persist_section = block.section in include
            for item in block.rows:
                row_parent_entry = _explicit_major_for_child_row(item.row, major_lookup) or parent_entry
                if row_parent_entry is not None:
                    emit_major(row_parent_entry)
                entity = _entity_from_parsed(
                    item.parsed,
                    section=block.section,
                    level=entity_level,
                    country_code=country_code,
                    parent_code=row_parent_entry.entity.code if row_parent_entry else None,
                )
                chain = tuple(_repeat_entity_chain(entity, section=block.section, page=page))
                if persist_section:
                    rows.extend(chain)
                for chained in chain:
                    parent_context.register(chained, row=item.row)
        return rows

    def _parse_tl_blocks(
        self,
        *,
        table: Tag,
        levels: dict[str, int],
        base_level: int,
        country_code: str,
        base_url: str,
        page: Any | None,
        last_pop_idx: int,
        last_pop_date: str | None,
        visible_pop_columns: list[tuple[int, str | None]],
        last_year: int | None,
        system: str,
    ) -> list[_TlBlock]:
        blocks: list[_TlBlock] = []
        for index, tbody in enumerate(table.find_all("tbody", recursive=False)):
            section = _section_for_grouped_tl_tbody(tbody)
            entity_level = _level_for_section(
                section=section,
                levels=levels,
                page=page,
                base_level=base_level,
                system=system,
            )
            parsed_rows: list[_TlParsedRow] = []
            for tr in tbody.find_all("tr", recursive=False):
                parsed = self.client.parse_tr_tl(
                    tr=tr,
                    explicit_level=entity_level,
                    last_visible_pop_idx=last_pop_idx,
                    last_visible_date=last_pop_date,
                    default_last_census_year=last_year,
                    country_code=country_code,
                    base_url=base_url,
                    default_entity_type=SECTION_DEFAULT_ENTITY_TYPE.get(section),
                    visible_pop_columns=visible_pop_columns,
                )
                if parsed:
                    parsed_rows.append(_TlParsedRow(row=tr, parsed=parsed))
            if parsed_rows:
                blocks.append(_TlBlock(index=index, section=section, rows=tuple(parsed_rows)))
        return blocks

    def _tl_major_entries(
        self,
        *,
        blocks: list[_TlBlock],
        levels: dict[str, int],
        base_level: int,
        country_code: str,
        page: Any | None,
        parent_context: "_ParentContext",
        system: str,
    ) -> dict[int, tuple[_TlMajorEntry, ...]]:
        root_parent = parent_context.latest_for_section(SECTION_INFOSECTION)
        entity_level = _level_for_section(
            section=SECTION_MAJOR,
            levels=levels,
            page=page,
            base_level=base_level,
            system=system,
        )
        result: dict[int, tuple[_TlMajorEntry, ...]] = {}
        for block in blocks:
            if block.section != SECTION_MAJOR:
                continue
            entries: list[_TlMajorEntry] = []
            for item in block.rows:
                entity = _entity_from_parsed(
                    item.parsed,
                    section=SECTION_MAJOR,
                    level=entity_level,
                    country_code=country_code,
                    parent_code=root_parent.code if root_parent else None,
                )
                chain = tuple(_repeat_entity_chain(entity, section=SECTION_MAJOR, page=page))
                entries.append(_TlMajorEntry(block_index=block.index, row=item.row, chain=chain))
            result[block.index] = tuple(entries)
        return result

    def _parse_ts_cities(
        self,
        *,
        soup: BeautifulSoup,
        section: str,
        level: int,
        country_code: str,
        base_url: str,
        page: Any | None,
        parent_context: "_ParentContext",
    ) -> list[ScrapedAdminArea]:
        table = soup.find("table", id="ts")
        if not isinstance(table, Tag):
            return []

        last_pop_idx, last_pop_date = self.client.detect_last_visible_pop_column(table)
        visible_pop_columns = self.client.visible_pop_columns(table)
        last_year = self.client.year_from_date(last_pop_date)
        has_radm = bool(table.select("td.radm, th.radm"))
        rows: list[ScrapedAdminArea] = []
        fallback_parent = parent_context.deepest()
        for tr in table.find_all("tr"):
            parsed = self.client.parse_tr_ts(
                tr=tr,
                last_visible_pop_idx=last_pop_idx,
                last_visible_date=last_pop_date,
                default_last_census_year=last_year,
                country_code=country_code,
                base_url=base_url,
                has_radm=has_radm,
                default_entity_type=SECTION_DEFAULT_ENTITY_TYPE[SECTION_CITIES],
                visible_pop_columns=visible_pop_columns,
            )
            if not parsed:
                continue

            parent = self._parent_for_ts_row(tr, parent_context) or fallback_parent
            entity = _entity_from_parsed(
                parsed,
                section=section,
                level=level,
                country_code=country_code,
                parent_code=parent.code if parent else None,
            )
            chain = _repeat_entity_chain(entity, section=section, page=page)
            rows.extend(chain)
            for item in chain:
                parent_context.register(item, row=tr)
        return rows

    def _parent_for_tl_row(
        self,
        *,
        section: str,
        tr: Tag,
        parent_context: "_ParentContext",
        current_major: ScrapedAdminArea | None,
    ) -> ScrapedAdminArea | None:
        if section == SECTION_MAJOR:
            return parent_context.latest_for_section(SECTION_INFOSECTION)
        explicit_parent = parent_context.lookup(_row_parent_key(tr))
        if explicit_parent:
            return explicit_parent
        if section == SECTION_MINOR:
            return current_major or parent_context.latest_for_section(SECTION_MAJOR)
        return parent_context.deepest_before_section(section)

    def _parent_for_ts_row(self, tr: Tag, parent_context: "_ParentContext") -> ScrapedAdminArea | None:
        explicit_parent = parent_context.lookup(_row_parent_key(tr))
        if explicit_parent:
            return explicit_parent
        for name in _row_parent_names(tr):
            parent = parent_context.lookup(name)
            if parent:
                return parent
        return None

    def _parse_infosection_root(
        self,
        soup: BeautifulSoup,
        *,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        section = soup.find(class_=lambda value: value and "infosection" in value and "mainsection" in value)
        if not isinstance(section, Tag):
            section = soup.find(class_=lambda value: value and "infosection" in value)
        if not isinstance(section, Tag):
            return self._parse_tfoot_root(soup=soup, country_code=country_code, level=level, url=url)

        name_node = section.find(class_="infoname")
        raw_name = name_node.get_text(" ", strip=True) if name_node else country_code
        name = _clean_root_name(raw_name)
        entity_type = _root_entity_type(section) or _root_entity_type_from_name(raw_name)
        pop_latest, pop_latest_date = _population_from_infosection(section, self.client)
        area_km2 = _metric_from_infosection(section, "area", self.client)
        density = _metric_from_infosection(section, "density", self.client)
        if country_code == "puertorico" and area_km2 is not None and area_km2 > 100000:
            area_km2 = area_km2 / 100
        if density is None and pop_latest is not None and area_km2 not in (None, 0):
            density = pop_latest / area_km2

        root = ScrapedAdminArea(
            code=country_code if int(level) == 0 else _infosection_code(section, country_code),
            name=name,
            level=int(level),
            country_code=country_code,
            entity_type=entity_type,
            raw_entity_type=entity_type,
            area_km2=area_km2,
            density=density,
            pop_latest=pop_latest,
            pop_latest_date=pop_latest_date,
            last_census_year=self.client.year_from_date(pop_latest_date),
            url=url,
            data_wd=_root_data_wd(section, name=name, country_code=country_code),
            annotations=_section_annotation(SECTION_INFOSECTION),
        )
        tfoot_root = self._parse_tfoot_root(soup=soup, country_code=country_code, level=level, url=url)
        if _same_visible_root(root, tfoot_root):
            return tfoot_root
        return root

    def _parse_tfoot_root(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        table = soup.find("table", id="tl")
        tfoot = table.find("tfoot") if isinstance(table, Tag) else None
        tr = tfoot.find("tr") if isinstance(tfoot, Tag) else None
        if not isinstance(table, Tag) or not isinstance(tr, Tag):
            return None
        last_pop_idx, last_pop_date = self.client.detect_last_visible_pop_column(table)
        visible_pop_columns = self.client.visible_pop_columns(table)
        parsed = self.client.parse_tr_tl(
            tr=tr,
            explicit_level=level,
            last_visible_pop_idx=last_pop_idx,
            last_visible_date=last_pop_date,
            default_last_census_year=self.client.year_from_date(last_pop_date),
            country_code=country_code,
            base_url=self.client.base_for_urljoin(url),
            visible_pop_columns=visible_pop_columns,
        )
        if not parsed:
            return None
        entity = _entity_from_parsed(
            parsed,
            section=SECTION_INFOSECTION,
            level=level,
            country_code=country_code,
            parent_code=None,
        )
        if int(level) == 0:
            return replace(entity, code=country_code)
        return entity


class _ParentContext:
    """Lookup table for parents found earlier on the same page."""

    def __init__(self):
        self._latest_by_section: dict[str, ScrapedAdminArea] = {}
        self._latest_by_level: dict[int, ScrapedAdminArea] = {}
        self._lookup: dict[str, ScrapedAdminArea] = {}

    def register(self, entity: ScrapedAdminArea, row: Tag | None = None) -> None:
        section = _annotation_section(entity.annotations)
        if section:
            self._latest_by_section[section] = entity
        self._latest_by_level[int(entity.level)] = entity
        for key in _entity_lookup_keys(entity, row=row):
            normalized = _lookup_key(key)
            if normalized:
                self._lookup.setdefault(normalized, entity)

    def latest_for_section(self, section: str) -> ScrapedAdminArea | None:
        return self._latest_by_section.get(section)

    def lookup(self, key: str | None) -> ScrapedAdminArea | None:
        return self._lookup.get(_lookup_key(key)) if key else None

    def deepest(self) -> ScrapedAdminArea | None:
        if not self._latest_by_level:
            return None
        return self._latest_by_level[max(self._latest_by_level)]

    def deepest_before_section(self, section: str) -> ScrapedAdminArea | None:
        order = (SECTION_INFOSECTION, SECTION_MAJOR, SECTION_MINOR, SECTION_CITIES)
        try:
            index = order.index(section)
        except ValueError:
            return self.deepest()
        for previous in reversed(order[:index]):
            item = self.latest_for_section(previous)
            if item:
                return item
        return None



def _mark_sum_to_root_entities(entities: list[ScrapedAdminArea], *, page: Any | None) -> list[ScrapedAdminArea]:
    """Mark rows from pages that must roll up to the country root.

    ``sum_to_root`` is a page-level import instruction, not a visual flag.  The
    linker uses this marker after all pages have been parsed, when it already
    knows the real level-0 country row.  Marking here keeps the HTML parser
    stateless and avoids guessing the root id while scraping an isolated page.
    """
    if not getattr(page, "sum_to_root", False):
        return entities
    return [
        replace(
            entity,
            contributes_to_root=True,
            annotations=_append_annotation(entity.annotations, "Suma al padre"),
        )
        for entity in entities
    ]


def _mark_forced_highest_level_entities(entities: list[ScrapedAdminArea], *, page: Any | None) -> list[ScrapedAdminArea]:
    forced_level = getattr(page, "force_highest_level", None)
    if forced_level in (None, ""):
        return entities
    annotation = f"Forced highest level: {int(forced_level)}"
    return [replace(entity, annotations=_append_annotation(entity.annotations, annotation)) for entity in entities]


def _mark_forced_parent_level_entities(entities: list[ScrapedAdminArea], *, page: Any | None) -> list[ScrapedAdminArea]:
    parent_level = getattr(page, "parent_level", None)
    if parent_level in (None, ""):
        return entities
    annotation = f"Forced parent level: {int(parent_level)}"
    return [replace(entity, annotations=_append_annotation(entity.annotations, annotation)) for entity in entities]


def _normalize_system(value: str) -> str:
    system = str(value or "cities").strip().casefold()
    if system not in SYSTEM_SECTIONS:
        raise ValueError(f"Sistema CityPopulation no soportado: {value!r}.")
    return system


def _page_base_level(page: Any | None, fallback: int) -> int:
    if page is not None:
        forced = getattr(page, "force_highest_level", None)
        if forced not in (None, ""):
            return int(forced)
    return int(fallback)


def _include_sections(page: Any | None, system: str) -> tuple[str, ...]:
    allowed = SYSTEM_SECTIONS[system]
    configured = tuple(str(value).strip().casefold() for value in getattr(page, "include_sections", ()) or ())
    if configured:
        return tuple(section for section in allowed if section in configured)

    # Backwards compatibility for older ScrapingPageConfig objects.
    include_tables = tuple(str(value).strip().casefold() for value in getattr(page, "include_tables", ()) or ())
    if "__none__" in include_tables:
        include_tables = ()
    include = []
    if getattr(page, "include_root", True):
        include.append(SECTION_INFOSECTION)
    if not include_tables:
        include.extend(section for section in allowed if section != SECTION_INFOSECTION)
    else:
        if any(table in include_tables for table in ("tl", "admin1", "1")):
            include.append(SECTION_MAJOR)
        if any(table in include_tables for table in ("admin2", "2", "tl_child")):
            include.append(SECTION_MINOR)
        if "ts" in include_tables:
            include.append(SECTION_CITIES)
    return tuple(section for section in allowed if section in include)


def _include_implicit_minor_for_grouped_cities(include: tuple[str, ...]) -> tuple[str, ...]:
    if SECTION_MINOR in include or SECTION_MAJOR not in include or SECTION_CITIES not in include:
        return include
    result: list[str] = []
    for section in include:
        if section == SECTION_CITIES:
            result.append(SECTION_MINOR)
        result.append(section)
    return tuple(result)


def _section_levels(*, system: str, include: tuple[str, ...], page: Any | None, base_level: int) -> dict[str, int]:
    explicit = {str(key).strip().casefold(): int(value) for key, value in getattr(page, "table_levels", {}).items()}
    levels: dict[str, int] = {}
    current = int(base_level)
    for section in SYSTEM_SECTIONS[system]:
        if section not in include:
            continue
        level = _explicit_level_for_section(section, explicit)
        levels[section] = int(level if level is not None else current)
        current = levels[section] + max(1, _repeat_count(page, section))
    return levels


def _level_for_section(
    *,
    section: str,
    levels: dict[str, int],
    page: Any | None,
    base_level: int,
    system: str,
) -> int:
    if section in levels:
        return int(levels[section])
    explicit = {str(key).strip().casefold(): int(value) for key, value in getattr(page, "table_levels", {}).items()}
    explicit_level = _explicit_level_for_section(section, explicit)
    if explicit_level is not None:
        return int(explicit_level)
    try:
        return int(base_level) + SYSTEM_SECTIONS[system].index(section)
    except ValueError:
        return int(base_level)


def _explicit_level_for_section(section: str, explicit: dict[str, int]) -> int | None:
    for key in SECTION_ALIASES.get(section, (section,)):
        normalized = str(key).strip().casefold()
        if normalized in explicit:
            return int(explicit[normalized])
    return None


def _section_for_tl_tbody(system: str, tbody: Tag, has_major_context: bool) -> str:
    classes = _tl_tbody_classes(tbody)
    if _is_classed_major_tbody(tbody):
        return SECTION_MAJOR
    if "admin2" in classes:
        return SECTION_MINOR
    # CityPopulation often uses a plain tbody for children immediately after a
    # classed admin/adm tbody.  If a major exists, plain tbody means minor.
    return SECTION_MINOR if has_major_context else SECTION_MAJOR


def _section_for_grouped_tl_tbody(tbody: Tag) -> str:
    return SECTION_MAJOR if _is_classed_major_tbody(tbody) else SECTION_MINOR


def _tl_tbody_classes(tbody: Tag) -> set[str]:
    return {str(value).strip().casefold() for value in (tbody.get("class") or [])}


def _is_classed_major_tbody(tbody: Tag) -> bool:
    classes = _tl_tbody_classes(tbody)
    return "admin1" in classes or "adm" in classes


def _tl_has_classed_major(table: Tag) -> bool:
    return any(_is_classed_major_tbody(tbody) for tbody in table.find_all("tbody", recursive=False))


def _tl_has_grouped_minor(soup: BeautifulSoup) -> bool:
    table = soup.find("table", id="tl")
    if not isinstance(table, Tag):
        return False
    has_classed_major = False
    has_child_body = False
    for tbody in table.find_all("tbody", recursive=False):
        classes = _tl_tbody_classes(tbody)
        if _is_classed_major_tbody(tbody):
            has_classed_major = True
            continue
        if "admin2" in classes or not classes:
            has_child_body = True
    return has_classed_major and has_child_body


def _major_lookup(major_entries: dict[int, tuple[_TlMajorEntry, ...]]) -> dict[str, _TlMajorEntry]:
    lookup: dict[str, _TlMajorEntry] = {}
    for entries in major_entries.values():
        for entry in entries:
            for key in _entity_lookup_keys(entry.entity, row=entry.row):
                normalized = _lookup_key(key)
                if normalized:
                    lookup.setdefault(normalized, entry)
    return lookup


def _best_major_for_child_block(
    *,
    block: _TlBlock,
    major_entries: dict[int, tuple[_TlMajorEntry, ...]],
    major_lookup: dict[str, _TlMajorEntry],
) -> _TlMajorEntry | None:
    explicit = _explicit_major_for_child_block(block=block, major_lookup=major_lookup)
    if explicit:
        return explicit
    candidates = _neighbor_major_entries(block=block, major_entries=major_entries)
    if not candidates:
        return None
    return _closest_major_by_metrics(block=block, candidates=candidates) or candidates[0]


def _explicit_major_for_child_block(
    *,
    block: _TlBlock,
    major_lookup: dict[str, _TlMajorEntry],
) -> _TlMajorEntry | None:
    hits: dict[tuple[int, str], int] = {}
    entries: dict[tuple[int, str], _TlMajorEntry] = {}
    for item in block.rows:
        entry = _explicit_major_for_child_row(item.row, major_lookup)
        if entry is not None:
            marker = (entry.block_index, entry.entity.code)
            hits[marker] = hits.get(marker, 0) + 1
            entries.setdefault(marker, entry)
    if not hits:
        return None
    best_marker = max(hits, key=lambda marker: hits[marker])
    return entries[best_marker]


def _explicit_major_for_child_row(row: Tag, major_lookup: dict[str, _TlMajorEntry]) -> _TlMajorEntry | None:
    for raw_key in (_row_parent_key(row), *_row_parent_names(row)):
        entry = major_lookup.get(_lookup_key(raw_key))
        if entry is not None:
            return entry
    return None


def _neighbor_major_entries(
    *,
    block: _TlBlock,
    major_entries: dict[int, tuple[_TlMajorEntry, ...]],
) -> list[_TlMajorEntry]:
    previous_indexes = [index for index in major_entries if index < block.index]
    next_indexes = [index for index in major_entries if index > block.index]
    result: list[_TlMajorEntry] = []
    if previous_indexes:
        result.extend(major_entries[max(previous_indexes)])
    if next_indexes:
        result.extend(major_entries[min(next_indexes)])
    return result


def _closest_major_by_metrics(*, block: _TlBlock, candidates: list[_TlMajorEntry]) -> _TlMajorEntry | None:
    child_pop = _parsed_rows_sum(block.rows, "pop_latest")
    child_area = _parsed_rows_sum(block.rows, "area_km2")
    best: tuple[float, _TlMajorEntry] | None = None
    for entry in candidates:
        pop_score = _relative_metric_distance(child_pop, entry.entity.pop_latest)
        area_score = _relative_metric_distance(child_area, entry.entity.area_km2)
        score = pop_score if pop_score is not None else area_score
        if score is None:
            continue
        if best is None or score < best[0]:
            best = (score, entry)
    return best[1] if best else None


def _parsed_rows_sum(rows: tuple[_TlParsedRow, ...], attr: str) -> float | None:
    values = [getattr(item.parsed, attr) for item in rows]
    numeric = [float(value) for value in values if value is not None]
    return sum(numeric) if numeric else None


def _relative_metric_distance(left: float | int | None, right: float | int | None) -> float | None:
    if left in (None, 0) or right in (None, 0):
        return None
    left_float = float(left)
    right_float = float(right)
    return abs(left_float - right_float) / max(abs(left_float), abs(right_float), 1.0)


def _entity_from_parsed(
    parsed: CityPopulationEntity,
    *,
    section: str,
    level: int,
    country_code: str,
    parent_code: str | None,
) -> ScrapedAdminArea:
    return ScrapedAdminArea(
        code=str(parsed.entity_id),
        name=parsed.name,
        level=int(level),
        country_code=country_code,
        entity_type=parsed.entity_type or SECTION_DEFAULT_ENTITY_TYPE.get(section),
        raw_entity_type=parsed.entity_type,
        parent_code=parent_code,
        area_km2=parsed.area_km2,
        density=parsed.density,
        pop_latest=parsed.pop_latest,
        pop_latest_date=parsed.pop_latest_date,
        last_census_year=parsed.last_census_year,
        url=parsed.url,
        data_wd=parsed.data_wd,
        annotations=_section_annotation(section),
    )


def _repeat_entity_chain(entity: ScrapedAdminArea, *, section: str, page: Any | None) -> list[ScrapedAdminArea]:
    count = _repeat_count(page, section)
    if count <= 1:
        return [entity]
    chain: list[ScrapedAdminArea] = []
    repeat_scope = _repeat_scope(page)
    for index in range(count):
        previous = chain[-1] if chain else None
        if index == 0:
            code = entity.code
        else:
            # Repeated infosections from different pages may have the same
            # fallback code (for example Spain/Ceuta and Spain/Melilla both
            # without an ``ir...`` id).  Scope the synthetic repeat id by page
            # path so equivalent pages do not collapse into one another.
            code = f"{entity.code}__{repeat_scope}__repeat{index + 1}"
        chain.append(
            replace(
                entity,
                code=code,
                level=int(entity.level) + index,
                parent_code=previous.code if previous else entity.parent_code,
                annotations=_append_annotation(entity.annotations, REPEAT_ANNOTATION if index else _section_annotation(section)),
            )
        )
    return chain


def _repeat_scope(page: Any | None) -> str:
    path = getattr(page, "path", None) or getattr(page, "paths", None) or "page"
    if isinstance(path, (list, tuple)):
        path = "_".join(str(item) for item in path)
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(path or "page")).strip("_").casefold()
    return text or "page"


def _repeat_count(page: Any | None, section: str) -> int:
    repeat = getattr(page, "repeat", None) or {}
    for key in SECTION_ALIASES.get(section, (section,)):
        raw = repeat.get(key)
        if raw not in (None, ""):
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                return 1
    return 1


def _apply_root_hints(
    root: ScrapedAdminArea | None,
    *,
    page: Any | None,
    country_code: str,
    url: str,
    default_level: int,
) -> ScrapedAdminArea | None:
    if page is None:
        return root
    root_code = _optional_string(getattr(page, "root_code", None))
    root_name = _optional_string(getattr(page, "root_name", None))
    root_parent_code = _optional_string(getattr(page, "root_parent_code", None))
    root_entity_type = _optional_string(getattr(page, "root_entity_type", None))
    root_level = getattr(page, "root_level", None)
    level = int(root_level) if root_level is not None else int(default_level)
    if root is None:
        if not (root_code or root_name):
            return None
        return ScrapedAdminArea(
            code=root_code or country_code,
            name=root_name or root_code or country_code,
            level=level,
            country_code=country_code,
            entity_type=root_entity_type,
            parent_code=root_parent_code,
            url=url,
            annotations=_section_annotation(SECTION_INFOSECTION),
        )
    return replace(
        root,
        code=root_code or root.code,
        name=root_name or root.name,
        level=level,
        entity_type=root_entity_type or root.entity_type,
        parent_code=root_parent_code if root_parent_code is not None else root.parent_code,
        url=root.url or url,
    )


def _population_from_infosection(section: Tag, client: CityPopulationClient) -> tuple[int | None, str | None]:
    pop_node = section.find(attrs={"data-newpop": True}) or section.find(attrs={"data-oldpop": True})
    if pop_node:
        raw = pop_node.get("data-newpop") or pop_node.get("data-oldpop")
        date = pop_node.get("data-newdate") or pop_node.get("data-olddate")
        try:
            return int(str(raw)), date
        except (TypeError, ValueError):
            pass
    best_value = None
    best_date = None
    for node in section.find_all(class_="infotext", recursive=False):
        text = node.get_text(" ", strip=True)
        lowered = text.casefold()
        if "population" not in lowered or "annual population" in lowered or "density" in lowered:
            continue
        value_node = node.find(class_="val")
        value = client.safe_float_text(value_node.get_text(" ", strip=True) if value_node else text)
        if value is None:
            continue
        match = re.search(r"(\d{4})", text)
        best_value = int(value)
        best_date = f"{match.group(1)}-01-01" if match else None
    return best_value, best_date


def _metric_from_infosection(section: Tag, label: str, client: CityPopulationClient) -> float | None:
    data_key = "data-area" if label == "area" else "data-density"
    node = section.find(attrs={data_key: True})
    if node:
        value = client.safe_float(node.get(data_key))
        if value is not None:
            return value
    for item in section.find_all(class_="infotext", recursive=False):
        text = item.get_text(" ", strip=True)
        lowered = text.casefold()
        if label == "area" and "area" not in lowered:
            continue
        if label == "density" and "density" not in lowered:
            continue
        value_node = item.find(class_="val")
        value = client.safe_float_text(value_node.get_text(" ", strip=True) if value_node else text)
        if value is not None:
            return value
    return None



def _infosection_code(section: Tag, country_code: str) -> str:
    """Return CityPopulation's internal id for an infosection when present.

    Country-level pages expose ids like ``ir161`` / ``wd161``.  Keeping ``161``
    as the code makes the root id consistent with the rest of the scraper and
    lets later blocks link using the same internal CityPopulation identifiers.
    Sub-pages sometimes omit that id; then the country slug remains the safe
    fallback and can still be overridden with ``root_code`` in TOML.
    """
    for raw in (section.get("id"),):
        match = re.fullmatch(r"ir(.+)", str(raw or "").strip())
        if match:
            return match.group(1)
    wd_node = section.find(class_="wd")
    raw_wd_id = str(wd_node.get("id") or "") if isinstance(wd_node, Tag) else ""
    match = re.fullmatch(r"wd(.+)", raw_wd_id.strip())
    if match:
        return match.group(1)
    return country_code

def _root_entity_type(section: Tag) -> str | None:
    info = section.find(class_="infotext")
    if info:
        text = info.get_text(" ", strip=True)
        if text and "population" not in text.casefold() and "capital" not in text.casefold():
            return text
    return None


def _root_entity_type_from_name(value: str) -> str | None:
    """Return the trailing parenthetical type from an infosection title.

    CityPopulation sub-pages often encode the root type in the visible title,
    for example ``Mayotte (Overseas Department)`` or ``Antwerpen (Province)``,
    while the following info rows only contain population/area metrics.  Without
    this fallback, pages marked with ``sum_to_root`` keep their root entity with
    an empty type.
    """
    text = re.sub(r"^Contents:\s*", "", str(value or "").strip(), flags=re.I)
    match = re.search(r"\(([^()]*)\)\s*$", text)
    if not match:
        return None
    candidate = match.group(1).strip()
    if not candidate:
        return None
    if any(token in candidate.casefold() for token in ("population", "capital", "density")):
        return None
    return candidate


def _root_data_wd(section: Tag, *, name: str, country_code: str) -> str:
    for node in section.find_all(attrs={"data-wd": True}):
        qid = _normalize_qid(node.get("data-wd"))
        if qid:
            return qid
    wd_node = section.find(class_="wd")
    text = wd_node.get_text(" ", strip=True) if wd_node else ""
    match = re.search(r"\b(Q\d+)\b", text)
    return _normalize_qid(match.group(1) if match else "")


def _same_visible_root(left: ScrapedAdminArea | None, right: ScrapedAdminArea | None) -> bool:
    if not left or not right:
        return False
    if int(left.level) != int(right.level):
        return False
    return _lookup_key(left.name) == _lookup_key(right.name)


def _clean_root_name(value: str) -> str:
    text = re.sub(r"^Contents:\s*", "", str(value or "").strip(), flags=re.I)
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    return text or value


def _row_parent_key(tr: Tag) -> str:
    main = _main_cell(tr)
    if main:
        for attr in ("data-adm", "data-parent", "data-parentid"):
            if main.get(attr):
                return str(main.get(attr))
    radm = tr.find(["td", "th"], class_=lambda value: value and "radm" in value)
    if isinstance(radm, Tag):
        for attr in ("data-admid", "data-adm", "data-parent", "data-parentid"):
            if radm.get(attr):
                return str(radm.get(attr))
    return ""


def _row_parent_name(tr: Tag) -> str:
    radm = tr.find(["td", "th"], class_=lambda value: value and "radm" in value)
    return radm.get_text(" ", strip=True) if isinstance(radm, Tag) else ""


def _row_parent_names(tr: Tag) -> tuple[str, ...]:
    radm = tr.find(["td", "th"], class_=lambda value: value and "radm" in value)
    if not isinstance(radm, Tag):
        return ()
    return tuple(_name_aliases(radm))


def _main_cell(tr: Tag) -> Tag | None:
    for cell in tr.find_all(["td", "th"], recursive=False):
        if "rname" in (cell.get("class") or []):
            return cell
    return None


def _entity_lookup_keys(entity: ScrapedAdminArea, row: Tag | None) -> Iterable[str]:
    yield entity.code
    yield entity.data_wd
    yield from _text_aliases(entity.name)
    if row is None:
        return
    main = _main_cell(row)
    if main:
        raw_id = str(main.get("id") or "")
        yield raw_id
        yield raw_id[1:] if raw_id.startswith("i") else raw_id
        yield main.get("data-wd")
        yield main.get("data-adm")
        yield from _name_aliases(main)
    abbr = row.find(["td", "th"], class_=lambda value: value and "rabbr" in value)
    if isinstance(abbr, Tag):
        yield abbr.get_text(" ", strip=True)


def _name_aliases(node: Tag) -> Iterable[str]:
    text = node.get_text(" ", strip=True)
    yield from _text_aliases(text)
    for span in node.find_all(attrs={"itemprop": "name"}):
        yield from _text_aliases(span.get_text(" ", strip=True))


def _text_aliases(value: str | None) -> Iterable[str]:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return
    seen: set[str] = set()

    def emit(candidate: str | None):
        candidate_text = re.sub(r"\s+", " ", str(candidate or "").strip(" ,;"))
        if not candidate_text:
            return
        key = _lookup_key(candidate_text)
        if not key or key in seen:
            return
        seen.add(key)
        yield candidate_text

    yield from emit(text)
    without_brackets = re.sub(r"\s*[\[(][^\])]*[\])]\s*", " ", text).strip()
    if without_brackets and without_brackets != text:
        yield from emit(without_brackets)
    for bracketed in re.findall(r"[\[(]([^\])]+)[\])]", text):
        yield from emit(bracketed)
    for part in re.split(r"\s*(?:/|\||·|;)\s*", text):
        if part != text:
            yield from emit(part)


def _lookup_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalize_qid(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"Q\d+", text) else ""


def _optional_string(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _section_annotation(section: str) -> str:
    return f"{SECTION_ANNOTATION_PREFIX}: {section}"


def _annotation_section(value: str | None) -> str:
    match = re.search(rf"{re.escape(SECTION_ANNOTATION_PREFIX)}:\s*([a-z_]+)", str(value or ""))
    return match.group(1) if match else ""


def _append_annotation(value: str | None, annotation: str) -> str:
    current = str(value or "").strip()
    if not current:
        return annotation
    if annotation in current:
        return current
    return f"{current}; {annotation}"
