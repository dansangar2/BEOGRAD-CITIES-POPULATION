"""Unified scraper for CityPopulation table-like pages.

The historical ``table`` and ``double`` modes both parse the same CityPopulation
``tl``/``ts`` tables.  The difference is now handled by page profile and
configuration instead of separate parent-assignment code paths.
"""

from __future__ import annotations

import re
from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.page_config import (
    include_tables_for_page,
    repeated_infosection_roots,
    repeat_count_for_page,
    should_include_table,
    status_levels_for_page,
    table_levels_for_page,
)
from ciudades_del_mundo.infrastructure.scraping.page_types import (
    CityPopulationPageProfile,
    detect_citypopulation_page_profile,
)


SHARED_POPULATION_ANNOTATION = "Comparte población con otras divisiones"


def _normalize_data_wd(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"Q\d+", text) else ""


def _normalize_name_for_data_wd(value: str | None) -> str:
    text = str(value or "").casefold().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _data_wd_name_score(value: str | None, expected: set[str]) -> int:
    normalized = _normalize_name_for_data_wd(value)
    if not normalized or not expected:
        return 0
    if normalized in expected:
        return 100
    for item in expected:
        if normalized in {f"{item} republic", f"republic of {item}", f"state of {item}", f"kingdom of {item}"}:
            return 95
        if normalized.startswith(f"{item} ") or normalized.startswith(f"{item}:"):
            return 70
    return 0


def _root_data_wd_from_soup(soup: BeautifulSoup, *, name: str, country_code: str) -> str:
    expected = {_normalize_name_for_data_wd(name), _normalize_name_for_data_wd(country_code)} - {""}
    candidates: list[tuple[int, str]] = []
    for element in soup.select(".infosection [data-wd], header [data-wd], h1 [data-wd]"):
        qid = _normalize_data_wd(element.get("data-wd"))
        if not qid:
            continue
        score = max(
            _data_wd_name_score(element.get_text(" ", strip=True), expected),
            _data_wd_name_score(element.get("data-wiki"), expected),
        )
        if score:
            candidates.append((score, qid))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    section = soup.find(class_=lambda value: value and "infosection" in value)
    return _normalize_data_wd(section.get("data-wd") if section else "")


class CityPopulationDoubleScraper(BaseCityPopulationScraper):
    html_format = "double"

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup, profile = self._soup_and_profile(html)
        return self.parse_hierarchical_tables(
            soup=soup,
            url=url,
            country_code=country_code,
            level=level,
            profile=profile,
        )

    def scrape_configured_html(self, html: str, url: str, country_code: str, page) -> list[ScrapedAdminArea]:
        soup, profile = self._soup_and_profile(html)
        return self.parse_hierarchical_tables(
            soup=soup,
            url=url,
            country_code=country_code,
            level=page.lowest_level,
            profile=profile,
            page=page,
        )

    def parse_hierarchical_tables(
        self,
        *,
        soup: BeautifulSoup,
        url: str,
        country_code: str,
        level: int,
        root: ScrapedAdminArea | None = None,
        first_table_offset: int | None = None,
        profile: CityPopulationPageProfile | None = None,
        page=None,
    ) -> list[ScrapedAdminArea]:
        profile = profile or detect_citypopulation_page_profile(soup)
        if root and root.parent_code is None and root.level > 0 and root.code != country_code:
            root = replace(root, parent_code=country_code)

        include_tables = include_tables_for_page(page)
        table_levels = table_levels_for_page(page)
        status_levels = status_levels_for_page(page)
        should_include_tl = should_include_table(include_tables, "tl")
        should_include_ts = should_include_table(include_tables, "ts")

        page_parent_context_slug = self._page_parent_context_slug(url, country_code)
        tl = soup.find("table", id="tl") if profile.has_tl else None
        ts = soup.find("table", id="ts") if profile.has_ts else None
        ts_has_radm = bool(profile.ts_has_radm) if ts else False
        grouped_tl = self._is_grouped_adm_tl(tl) if tl else False
        context_root = root or self._infosection_context_root(
            soup=soup,
            url=url,
            country_code=country_code,
            level=level,
            page=page,
            # Page-local roots are only needed when CityPopulation omits an
            # administrative parent column.  Pages with both tl and ts usually
            # carry the parent in the ts radm column and are linked later from
            # the URL, so they must not be treated as single-root pages.
            enabled=bool(ts and not tl and page_parent_context_slug),
        )

        parents_by_name: dict[str, ScrapedAdminArea] = {}
        if first_table_offset is not None:
            default_tl_level = level + first_table_offset
        elif context_root is not None and tl is not None:
            default_tl_level = int(context_root.level or level) + 1
        elif tl is not None and page_parent_context_slug:
            # ``source = table`` now also covers old double-like pages.  On
            # contextual CityPopulation pages such as /spain/aragon/, the page
            # itself represents the configured lowest_level and the first table
            # contains its children, not another copy of the same level.
            default_tl_level = int(level or 0) + 1
        else:
            default_tl_level = int(level or 0)
        tl_level = table_levels.get("tl", default_tl_level)
        ts_level: int | None = None
        context_roots: list[ScrapedAdminArea] = (
            repeated_infosection_roots(context_root, page=page, country_code=country_code, url=url)
            if context_root
            else []
        )
        if ts:
            if context_root and not tl:
                repeated_root_count = max(1, repeat_count_for_page(page, "infosection"))
                default_ts_level = int(context_root.level or 0) + repeated_root_count
            else:
                if grouped_tl:
                    # Hierarchical first table: infosection -> tl tbody.adm ->
                    # tl tbody -> ts.  Therefore ``ts`` is two levels below
                    # the first ``tl`` administrative group.
                    default_ts_level = tl_level + 2
                else:
                    default_ts_level = tl_level if profile.ts_uses_first_child_level else tl_level + 1
            ts_level = table_levels.get("ts", default_ts_level)
            # Level bridging is now explicit through repeat = { infosection = N }.

        include_context_root = bool(context_root and (root or getattr(page, "include_root", True)))
        # Even when include_root=false, emit page-local synthetic roots if they
        # are necessary to bridge a level gap before the first table row.  The
        # application post-processor rewrites these transient roots to existing
        # same-name entities or deterministic equivalent-level rows.
        emit_context_chain = bool(
            context_roots
            and context_roots[-1].level < (ts_level or context_roots[-1].level)
            and not ts_has_radm
            and len(context_roots) > 1
        )

        entities: list[ScrapedAdminArea] = list(context_roots) if (include_context_root or emit_context_chain) else []

        if tl:
            if grouped_tl:
                tl_entities = self._parse_grouped_adm_tl(
                    table=tl,
                    country_code=country_code,
                    adm_level=tl_level,
                    child_level=table_levels.get("tl_child", tl_level + 1),
                    root=context_root,
                    base_url=url,
                    status_levels=status_levels,
                    parent_lookup_sink=parents_by_name,
                )
            else:
                tl_entities = self._parse_table(
                    table=tl,
                    country_code=country_code,
                    level=tl_level,
                    base_url=url,
                    parser="tl",
                    status_levels=status_levels,
                    parent_lookup_sink=parents_by_name,
                )

            for entity in tl_entities:
                if not grouped_tl:
                    if context_root:
                        entity = replace(entity, parent_code=context_root.code)
                    elif self._should_fallback_to_country_parent(
                        entity,
                        country_code=country_code,
                        page_parent_context_slug=page_parent_context_slug,
                    ):
                        entity = replace(entity, parent_code=country_code)
                elif self._should_fallback_to_country_parent(
                    entity,
                    country_code=country_code,
                    page_parent_context_slug=page_parent_context_slug,
                ):
                    entity = replace(entity, parent_code=country_code)

                if should_include_tl:
                    entities.append(entity)
                for key in self._parent_lookup_keys(entity.name):
                    parents_by_name.setdefault(key, entity)

        if ts:
            assert ts_level is not None
            context_parent = context_roots[-1] if context_roots else context_root
            for entity in self._parse_table(
                table=ts,
                country_code=country_code,
                level=ts_level,
                base_url=url,
                parser="ts",
                parents_by_name=parents_by_name,
                status_levels=status_levels,
            ):
                if context_parent and not ts_has_radm and entity.parent_code is None:
                    entity = replace(entity, parent_code=context_parent.code)
                elif self._should_fallback_to_country_parent(
                    entity,
                    country_code=country_code,
                    page_parent_context_slug=page_parent_context_slug,
                ):
                    entity = replace(entity, parent_code=country_code)
                if should_include_ts:
                    entities.append(entity)

        return entities

    def _is_grouped_adm_tl(self, table) -> bool:
        """Return true for country ``cities`` pages whose first table is hierarchical.

        Belgium-like pages expose the hierarchy inside one ``table#tl`` using
        ``tbody.adm`` as the upper administrative group and the following plain
        ``tbody`` blocks as its children.  This is deliberately structural, not
        country-specific, so other countries with the same CityPopulation layout
        work without changing their TOML, while ordinary ``tl``/``ts`` pages keep
        the generic parser.
        """
        if not table:
            return False
        tbodies = table.find_all("tbody", recursive=False)
        has_adm = any(self._tbody_is_adm_group(tbody) for tbody in tbodies)
        has_child = any(not self._tbody_is_adm_group(tbody) for tbody in tbodies)
        return bool(has_adm and has_child)

    def _tbody_is_adm_group(self, tbody) -> bool:
        return "adm" in {str(value).strip().casefold() for value in (tbody.get("class") or [])}

    def _parse_grouped_adm_tl(
        self,
        *,
        table,
        country_code: str,
        adm_level: int,
        child_level: int,
        root: ScrapedAdminArea | None,
        base_url: str,
        status_levels: dict[str, int] | None = None,
        parent_lookup_sink: dict[str, ScrapedAdminArea] | None = None,
    ) -> list[ScrapedAdminArea]:
        """Parse ``infosection > tl tbody.adm > tl tbody`` hierarchies.

        The method always builds the lookup context, even when ``tl`` is not
        persisted by the page config, because ``table#ts`` may reference these
        parents through ``data-admid``/``radm``.
        """
        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        last_year = self._client.year_from_date(last_pop_date)
        default_entity_type = self._default_entity_type(table)
        area_divisor = self._area_divisor(table)
        status_levels = status_levels or {}

        entities: list[ScrapedAdminArea] = []
        reference_lookup: dict[str, ScrapedAdminArea] = {}
        current_adm_parent: ScrapedAdminArea | None = None

        def remember(entity: ScrapedAdminArea, tr) -> None:
            for reference in self._tl_row_reference_values(entity, tr):
                for key in self._reference_lookup_keys(reference):
                    reference_lookup.setdefault(key, entity)
                    if parent_lookup_sink is not None:
                        parent_lookup_sink.setdefault(key, entity)
            if parent_lookup_sink is not None:
                for key in self._parent_lookup_keys(entity.name):
                    parent_lookup_sink.setdefault(key, entity)

        for tbody in table.find_all("tbody", recursive=False):
            is_adm_group = self._tbody_is_adm_group(tbody)
            row_level = adm_level if is_adm_group else child_level
            for tr in tbody.find_all("tr", recursive=False):
                parsed = self._client.parse_tr_tl(
                    tr=tr,
                    explicit_level=row_level,
                    last_visible_pop_idx=last_pop_idx,
                    last_visible_date=last_pop_date,
                    default_last_census_year=last_year,
                    country_code=country_code,
                    base_url=base_url,
                    default_entity_type=default_entity_type,
                    area_divisor=area_divisor,
                    visible_pop_columns=visible_pop_columns,
                )
                if not parsed:
                    continue

                entity_level = status_levels.get(str(parsed.entity_type or "").strip().casefold(), row_level)
                parent_code = None
                if is_adm_group:
                    parent_code = root.code if root else None
                else:
                    parent = self._parent_from_tl_data_adm(tr, reference_lookup) or current_adm_parent
                    parent_code = parent.code if parent else (root.code if root else None)

                entity = ScrapedAdminArea(
                    code=parsed.entity_id,
                    name=parsed.name,
                    level=entity_level,
                    country_code=country_code,
                    entity_type=parsed.entity_type,
                    parent_code=parent_code,
                    area_km2=parsed.area_km2,
                    density=parsed.density,
                    pop_latest=parsed.pop_latest,
                    pop_latest_date=parsed.pop_latest_date,
                    last_census_year=parsed.last_census_year,
                    url=parsed.url,
                    data_wd=parsed.data_wd,
                )
                entities.append(entity)
                remember(entity, tr)
                if is_adm_group:
                    current_adm_parent = entity

        return entities

    def _parent_from_tl_data_adm(
        self,
        tr,
        reference_lookup: dict[str, ScrapedAdminArea],
    ) -> ScrapedAdminArea | None:
        main_cell = self._client._main_name_cell(tr)
        references = []
        if main_cell and main_cell.get("data-adm"):
            references.append(main_cell.get("data-adm"))
        adm_cell = tr.find(["td", "th"], class_=lambda value: value and "radm" in value.split())
        if adm_cell:
            references.extend([adm_cell.get("data-admid"), adm_cell.get_text(" ", strip=True)])

        for reference in references:
            for key in self._reference_lookup_keys(reference):
                parent = reference_lookup.get(key)
                if parent:
                    return parent
        return None

    def _tl_row_reference_values(self, entity: ScrapedAdminArea, tr) -> list[str]:
        values = [entity.code, entity.name]
        main_cell = self._client._main_name_cell(tr)
        if main_cell:
            raw_id = str(main_cell.get("id") or "").strip()
            values.extend([raw_id, raw_id[1:] if raw_id.startswith("i") else raw_id])
            values.extend([main_cell.get("data-adm"), main_cell.get("data-wiki")])
            values.append(main_cell.get_text(" ", strip=True))
        abbr_cell = tr.find(["td", "th"], class_=lambda value: value and "rabbr" in value.split())
        if abbr_cell:
            values.append(abbr_cell.get_text(" ", strip=True))
        return [str(value).strip() for value in values if str(value or "").strip()]

    def _reference_lookup_keys(self, value: str | None) -> tuple[str, ...]:
        text = str(value or "").strip()
        if not text:
            return ()
        keys = [text, text.casefold()]
        normalized = self._normalize_name(text)
        compact = re.sub(r"[\W_]+", "", normalized)
        keys.extend([normalized, compact])
        if text.startswith("i") and text[1:]:
            keys.extend([text[1:], text[1:].casefold()])
        unique: list[str] = []
        for key in keys:
            if key and key not in unique:
                unique.append(key)
        return tuple(unique)

    def _context_root_chain(self, context_root: ScrapedAdminArea, child_level: int | None) -> list[ScrapedAdminArea]:
        """Return page-local roots needed to bridge missing equivalent levels.

        Some CityPopulation pages describe a territory once in the infosection
        but the only table row represents a deeper same-name administrative
        role.  Keeping this generic lets pages like autonomous cities, island
        territories or province-equivalent cities produce a homogeneous tree
        without country-specific branches.
        """
        if child_level is None:
            return [context_root]
        try:
            root_level = int(context_root.level or 0)
            child_level = int(child_level)
        except (TypeError, ValueError):
            return [context_root]
        if child_level <= root_level + 1:
            return [context_root]

        chain = [context_root]
        for equivalent_level in range(root_level + 1, child_level):
            previous = chain[-1]
            chain.append(
                replace(
                    context_root,
                    level=equivalent_level,
                    parent_code=previous.code,
                    entity_type=self._equivalent_entity_type(context_root.entity_type, equivalent_level),
                    raw_entity_type=self._equivalent_entity_type(context_root.raw_entity_type or context_root.entity_type, equivalent_level),
                )
            )
        return chain

    def _equivalent_entity_type(self, value: str | None, level: int) -> str:
        base = str(value or "Administrative area").strip() or "Administrative area"
        if "equivalent" in base.casefold():
            return base
        return f"{base} equivalent L{level}"

    def _table_has_single_self_child(self, table, root_name: str) -> bool:
        """Return true when one row repeats the page root as a deeper child."""
        root_key = self._normalize_name(root_name)
        if not root_key:
            return False
        rows = []
        for tbody in table.find_all("tbody", recursive=False):
            rows.extend(tbody.find_all("tr", recursive=False))
        if len(rows) != 1:
            return False
        name_cell = rows[0].find("td", class_=lambda value: value and "rname" in value.split())
        if not name_cell:
            return False
        name_node = name_cell.find(attrs={"itemprop": "name"}) or name_cell.find("a") or name_cell
        child_name = name_node.get_text(" ", strip=True)
        return self._normalize_name(child_name) == root_key

    def _infosection_context_root(
        self,
        *,
        soup: BeautifulSoup,
        url: str,
        country_code: str,
        level: int,
        page=None,
        enabled: bool = False,
    ) -> ScrapedAdminArea | None:
        """Return a page-local root parsed from CityPopulation's infosection.

        Some pages, notably Spanish autonomous cities, expose a single ``ts``
        table without a parent column.  The parent is the page itself and is
        described in ``div.infosection`` as e.g. ``Ceuta (Autonomous City)``.
        The root is intentionally emitted with ``code == country_code`` so the
        application post-processor can replace it with the real same-name row
        already scraped from the national admin page.
        """
        if not enabled:
            return None
        infoname = soup.select_one(".infosection .infoname")
        text = infoname.get_text(" ", strip=True) if infoname else ""
        text = re.sub(r"^Contents:\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            header = soup.select_one("header.citypage h1, header.cpage h1")
            text = header.get_text(" ", strip=True) if header else ""
            if ":" in text:
                text = text.split(":", 1)[-1].strip()
        if not text:
            return None

        entity_type = ""
        match = re.match(r"^(?P<name>.*?)\s*\((?P<type>[^)]*)\)\s*$", text)
        if match:
            name = match.group("name").strip()
            entity_type = match.group("type").strip()
        else:
            name = text.strip()
        if not name:
            return None
        if not entity_type:
            description = soup.select_one("header.citypage [itemprop='description'], header.cpage [itemprop='description']")
            description_text = description.get_text(" ", strip=True) if description else ""
            suffix = f" of {name}"
            if description_text.casefold().endswith(suffix.casefold()):
                description_text = description_text[: -len(suffix)].strip()
            entity_type = description_text

        root_level = getattr(page, "root_level", None)
        try:
            if root_level is not None:
                root_level = int(root_level)
            else:
                table_levels = table_levels_for_page(page)
                if "ts" in table_levels:
                    root_level = max(0, int(table_levels["ts"]) - 1)
                elif "/localities/" in str(url or "").casefold():
                    root_level = max(0, int(level or 0) - 1)
                else:
                    root_level = int(level or 0)
        except (TypeError, ValueError):
            root_level = int(level or 0)
        return ScrapedAdminArea(
            code=country_code,
            name=name,
            level=root_level,
            country_code=country_code,
            entity_type=entity_type or None,
            raw_entity_type=entity_type or None,
            parent_code=country_code if root_level > 0 else None,
            url=url,
            data_wd=_root_data_wd_from_soup(soup, name=name, country_code=country_code),
        )


    def _parse_table(
        self,
        *,
        table,
        country_code: str,
        level: int,
        base_url: str,
        parser: str,
        parents_by_name: dict[str, ScrapedAdminArea] | None = None,
        status_levels: dict[str, int] | None = None,
        parent_lookup_sink: dict[str, ScrapedAdminArea] | None = None,
    ) -> list[ScrapedAdminArea]:
        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        last_year = self._client.year_from_date(last_pop_date)
        tbodies = table.find_all("tbody", recursive=False)
        if not tbodies:
            return []
        default_entity_type = self._default_entity_type(table)
        area_divisor = self._area_divisor(table)
        status_levels = status_levels or {}

        entities = []
        for tbody in tbodies:
            for tr in tbody.find_all("tr", recursive=False):
                if parser == "ts":
                    parsed = self._client.parse_tr_ts(
                        tr=tr,
                        last_visible_pop_idx=last_pop_idx,
                        last_visible_date=last_pop_date,
                        default_last_census_year=last_year,
                        country_code=country_code,
                        base_url=base_url,
                        has_radm=bool(table.find("th", class_=lambda value: value and "radm" in value.split())),
                        default_entity_type=default_entity_type,
                        visible_pop_columns=visible_pop_columns,
                    )
                    parent_code = None
                    annotations = ""
                    if parsed:
                        parent_code, annotations = self._parent_code_from_radm(
                            tr,
                            parents_by_name or {},
                            child_name=parsed.name,
                        )
                else:
                    parsed = self._client.parse_tr_tl(
                        tr=tr,
                        explicit_level=level,
                        last_visible_pop_idx=last_pop_idx,
                        last_visible_date=last_pop_date,
                        default_last_census_year=last_year,
                        country_code=country_code,
                        base_url=base_url,
                        default_entity_type=default_entity_type,
                        area_divisor=area_divisor,
                        visible_pop_columns=visible_pop_columns,
                    )
                    parent_code = None
                    annotations = ""

                if not parsed:
                    continue

                entity_level = status_levels.get(str(parsed.entity_type or "").strip().casefold(), level)
                entity = ScrapedAdminArea(
                    code=parsed.entity_id,
                    name=parsed.name,
                    level=entity_level,
                    country_code=country_code,
                    entity_type=parsed.entity_type,
                    parent_code=parent_code,
                    area_km2=parsed.area_km2,
                    density=parsed.density,
                    pop_latest=parsed.pop_latest,
                    pop_latest_date=parsed.pop_latest_date,
                    last_census_year=parsed.last_census_year,
                    url=parsed.url,
                    data_wd=parsed.data_wd,
                    annotations=annotations,
                )
                if parser != "ts" and parent_lookup_sink is not None:
                    lookup_name = getattr(parsed, "lookup_name", None) or parsed.name
                    for alias in (lookup_name, parsed.name):
                        for key in self._parent_lookup_keys(alias):
                            parent_lookup_sink.setdefault(key, entity)
                entities.append(entity)

        return entities

    def _default_entity_type(self, table) -> str | None:
        heading = table.find_previous("h2")
        if not heading:
            return None

        text = heading.get_text(" ", strip=True)
        text = re.sub(r"^Contents:\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            return None

        words = text.split()
        if not words:
            return None

        last = words[-1]
        singular = self._singularize(last)
        words[-1] = singular
        return " ".join(words).strip()

    def _area_divisor(self, table) -> float:
        area_header = table.find("th", class_=lambda value: value and "rarea" in value.split())
        unit = area_header.find(class_="unit") if area_header else None
        text = unit.get_text(" ", strip=True) if unit else ""
        data_inv = unit.get("data-inv", "") if unit else ""
        return 100 if "hect" in f"{text} {data_inv}".casefold() else 1

    def _singularize(self, value: str) -> str:
        lowered = value.casefold()
        if lowered.endswith("ies") and len(value) > 3:
            return value[:-3] + "y"
        if lowered.endswith("ses") and len(value) > 3:
            return value[:-2]
        if lowered.endswith("s") and not lowered.endswith("ss") and len(value) > 1:
            return value[:-1]
        return value

    def _parent_code_from_radm(
        self,
        tr,
        parents_by_name: dict[str, ScrapedAdminArea],
        *,
        child_name: str,
    ) -> tuple[str | None, str]:
        parent_cell = tr.find("td", class_=lambda value: value and "radm" in value.split())
        if not parent_cell:
            return None, ""
        parent_id = parent_cell.get("data-admid")
        if parent_id:
            return parent_id, ""

        raw_parent_text = parent_cell.get_text(" ", strip=True)
        parent_texts, shared_population = self._parent_reference_candidates(
            raw_parent_text,
            child_name=child_name,
        )
        for parent_text in parent_texts:
            parent = self._resolve_parent_by_name(parent_text, parents_by_name)
            if parent:
                return parent.code, SHARED_POPULATION_ANNOTATION if shared_population else ""
        return None, SHARED_POPULATION_ANNOTATION if shared_population else ""

    def _parent_reference_candidates(self, raw_parent_text: str, *, child_name: str) -> tuple[list[str], bool]:
        """Return ordered parent names and whether the row shares population.

        Some CityPopulation ``ts`` tables leave ``radm`` blank for rows that
        repeat a parent with the same name, or put several candidate parents in
        a slash-separated label.  In those cases, prefer the same-name parent;
        otherwise fall back to the first slash segment and mark the child as
        sharing population with other divisions.
        """
        text = re.sub(r"\s+", " ", raw_parent_text or "").strip()
        child = re.sub(r"\s+", " ", child_name or "").strip()
        if not text:
            return ([child] if child else []), bool(child)

        parts = [part.strip() for part in re.split(r"\s*/\s*", text) if part.strip()]
        if len(parts) <= 1:
            return ([text] if text else []), False

        child_keys = set(self._parent_lookup_keys(child))
        matching_parts = [part for part in parts if child_keys.intersection(self._parent_lookup_keys(part))]
        if matching_parts:
            ordered = matching_parts + [part for part in parts if part not in matching_parts]
        else:
            ordered = parts
        return ordered, True

    def _resolve_parent_by_name(
        self,
        parent_text: str,
        parents_by_name: dict[str, ScrapedAdminArea],
    ) -> ScrapedAdminArea | None:
        for parent_name in self._parent_lookup_keys(parent_text):
            parent = parents_by_name.get(parent_name)
            if parent:
                return parent
            parent = self._unique_parent_by_prefix(parent_name, parents_by_name)
            if parent:
                return parent
        return None

    def _unique_parent_by_prefix(
        self,
        parent_name: str,
        parents_by_name: dict[str, ScrapedAdminArea],
    ) -> ScrapedAdminArea | None:
        """Resolve abbreviated ``radm`` labels when they uniquely prefix a parent.

        Examples include Austrian communes where the child row says
        ``Breitenbrunn`` but the parent table says ``Breitenbrunn am
        Neusiedler See``.  The match is only accepted when it resolves to one
        unique parent code to avoid accidental cross-parent assignments.
        """
        if not parent_name:
            return None
        matches: dict[str, ScrapedAdminArea] = {}
        for key, candidate in parents_by_name.items():
            if key.startswith(f"{parent_name} "):
                matches[candidate.code] = candidate
        if len(matches) == 1:
            return next(iter(matches.values()))
        return None

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    def _should_fallback_to_country_parent(
        self,
        entity: ScrapedAdminArea,
        *,
        country_code: str,
        page_parent_context_slug: str,
    ) -> bool:
        """Return whether an unparented row should point to the country.

        ``table`` and ``double`` pages are now parsed by the same logic.  When a
        CityPopulation page is contextual, for example ``/spain/aragon/`` or
        ``/spain/localities/ceuta/``, the correct parent is encoded in the URL
        rather than in every row.  In those cases we deliberately leave
        ``parent_code`` empty so the application-level URL inference can attach
        the row to the matching previous-level entity.  Only non-contextual
        pages, such as the country ``admin`` page, fall back to the country root.
        """
        return (
            entity.parent_code is None
            and entity.level > 0
            and entity.code != country_code
            and not page_parent_context_slug
        )

    def _page_parent_context_slug(self, url: str, country_code: str) -> str:
        """Return the URL slug that identifies a page-local parent context.

        This keeps ``table`` and ``double`` consistent: if a page path is scoped
        to an already-scraped area, children without a ``radm`` parent cell are
        not attached to the country prematurely.  Examples:

        * ``/en/spain/aragon/`` -> ``aragon``
        * ``/en/spain/localities/ceuta/`` -> ``ceuta``
        * ``/en/spain/admin/`` -> ``""``
        """
        path_segments = [segment.strip() for segment in str(url or "").split("?")[0].rstrip("/").split("/") if segment.strip()]
        if not path_segments:
            return ""

        normalized_country = str(country_code or "").strip().casefold()
        segments = [segment.casefold() for segment in path_segments]
        generic_segments = {"admin", "cities"}

        # Prefer segments after the country code when the full URL is available.
        start = 0
        if normalized_country in segments:
            start = segments.index(normalized_country) + 1
        scoped_segments = segments[start:]
        if not scoped_segments:
            return ""

        for segment in reversed(scoped_segments):
            if segment and segment not in generic_segments and segment != normalized_country:
                return segment
        return ""

    def _parent_lookup_keys(self, value: str) -> tuple[str, ...]:
        """Return robust lookup aliases for parent names shown in ``radm`` cells.

        CityPopulation parent tables often include co-official names or
        translations in brackets/parentheses (for example ``Xàbia (Jávea)``,
        ``České Budějovice [ Budweis ]`` or ``Käerjeng (Bascharage, Clemency)``),
        while child rows may reference any of those labels.  Index all visible
        alternatives so locality rows can attach to their municipality before
        falling back to URL/code inference.
        """
        candidates = self._parent_name_aliases(value)

        keys: list[str] = []
        for candidate in candidates:
            normalized = self._normalize_name(candidate)
            if not normalized:
                continue
            compact = re.sub(r"[\W_]+", "", normalized)
            for key in (normalized, compact):
                if key and key not in keys:
                    keys.append(key)
        return tuple(keys)

    def _parent_name_aliases(self, value: str) -> list[str]:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return []

        aliases: list[str] = []

        def add(raw: str) -> None:
            item = re.sub(r"\s+", " ", raw).strip(" ,;/-")
            if item and item not in aliases:
                aliases.append(item)

        add(text)

        leading = re.split(r"\s*[\[(]", text, maxsplit=1)[0].strip()
        add(leading)

        # Keep the canonical label with all bracketed qualifiers removed.
        add(re.sub(r"\s*[\[(][^\])]*[\])]", "", text).strip())

        # Also index the qualifiers themselves.  This covers Spanish/Valencian
        # co-official names such as ``Xàbia (Jávea)`` where the locality table
        # uses ``Jávea`` in the hidden parent column.
        for match in re.finditer(r"[\[(]([^\])]+)[\])]", text):
            content = match.group(1).strip()
            add(content)
            for part in re.split(r"\s*(?:/|,|;)\s*", content):
                add(part)

        for part in re.split(r"\s*/\s*", text):
            add(part)

        return aliases



class CityPopulationTableScraper(CityPopulationDoubleScraper):
    """Compatibility alias for the unified CityPopulation table scraper.

    ``source = "table"`` and ``source = "double"`` now share the same
    parser.  Page profile detection, configured ``table_levels`` and URL-based
    parent inference decide how each page is interpreted.
    """

    html_format = "table"
