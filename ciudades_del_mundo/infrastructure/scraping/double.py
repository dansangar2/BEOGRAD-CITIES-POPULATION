"""Scraper for pages that expose two stacked table sections per territory."""

from __future__ import annotations

import re
from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.page_config import (
    include_tables_for_page,
    should_include_table,
    status_levels_for_page,
    table_levels_for_page,
)
from ciudades_del_mundo.infrastructure.scraping.page_types import (
    CityPopulationPageProfile,
    detect_citypopulation_page_profile,
)


SHARED_POPULATION_ANNOTATION = "Comparte población con otras divisiones"


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

        entities: list[ScrapedAdminArea] = [root] if root else []
        parents_by_name: dict[str, ScrapedAdminArea] = {}
        default_tl_level = level + (first_table_offset if first_table_offset is not None else int(root is not None))
        tl_level = table_levels.get("tl", default_tl_level)

        tl = soup.find("table", id="tl") if profile.has_tl else None
        if tl:
            for entity in self._parse_table(
                table=tl,
                country_code=country_code,
                level=tl_level,
                base_url=url,
                parser="tl",
                status_levels=status_levels,
            ):
                if root:
                    entity = replace(entity, parent_code=root.code)
                elif entity.parent_code is None and entity.level > 0 and entity.code != country_code:
                    entity = replace(entity, parent_code=country_code)
                if should_include_tl:
                    entities.append(entity)
                for key in self._parent_lookup_keys(entity.name):
                    parents_by_name.setdefault(key, entity)

        ts = soup.find("table", id="ts") if profile.has_ts else None
        if ts:
            ts_has_radm = profile.ts_has_radm
            default_ts_level = tl_level if profile.ts_uses_first_child_level else tl_level + 1
            ts_level = table_levels.get("ts", default_ts_level)
            for entity in self._parse_table(
                table=ts,
                country_code=country_code,
                level=ts_level,
                base_url=url,
                parser="ts",
                parents_by_name=parents_by_name,
                status_levels=status_levels,
            ):
                if root and not ts_has_radm and entity.parent_code is None:
                    entity = replace(entity, parent_code=root.code)
                if should_include_ts:
                    entities.append(entity)

        return entities

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
                entities.append(
                    ScrapedAdminArea(
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
                        annotations=annotations,
                    )
                )

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

    def _parent_lookup_keys(self, value: str) -> tuple[str, ...]:
        """Return robust lookup aliases for parent names shown in ``radm`` cells.

        CityPopulation parent tables often include translations or legacy names
        in brackets/parentheses (for example ``České Budějovice [ Budweis ]``
        or ``Käerjeng ( Bascharage, Clemency )``), while child rows usually
        refer to the shorter administrative name.  The aliases stay generic and
        avoid country-specific branches by indexing both the full label and the
        leading canonical label before bracketed qualifiers.
        """
        candidates = [value]
        leading = re.split(r"\s*[\[(]", value, maxsplit=1)[0].strip()
        if leading and leading != value:
            candidates.append(leading)
        without_brackets = re.sub(r"\s*[\[(][^\])]*[\])]", "", value).strip()
        if without_brackets and without_brackets not in candidates:
            candidates.append(without_brackets)

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

