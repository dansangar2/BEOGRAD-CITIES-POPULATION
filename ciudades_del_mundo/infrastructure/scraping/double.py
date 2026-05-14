"""Scraper for pages that expose two stacked table sections per territory."""

from __future__ import annotations

import re
from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper


class CityPopulationDoubleScraper(BaseCityPopulationScraper):
    html_format = "double"

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        return self.parse_hierarchical_tables(
            soup=soup,
            url=url,
            country_code=country_code,
            level=level,
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
    ) -> list[ScrapedAdminArea]:
        if root and root.parent_code is None and root.level > 0 and root.code != country_code:
            root = replace(root, parent_code=country_code)

        entities: list[ScrapedAdminArea] = [root] if root else []
        parents_by_name: dict[str, ScrapedAdminArea] = {}
        tl_level = level + (first_table_offset if first_table_offset is not None else int(root is not None))

        tl = soup.find("table", id="tl")
        if tl:
            for entity in self._parse_table(
                table=tl,
                country_code=country_code,
                level=tl_level,
                base_url=url,
                parser="tl",
            ):
                if root:
                    entity = replace(entity, parent_code=root.code)
                elif entity.parent_code is None and entity.level > 0 and entity.code != country_code:
                    entity = replace(entity, parent_code=country_code)
                entities.append(entity)
                parents_by_name[self._normalize_name(entity.name)] = entity

        ts = soup.find("table", id="ts")
        if ts:
            for entity in self._parse_table(
                table=ts,
                country_code=country_code,
                level=tl_level + 1,
                base_url=url,
                parser="ts",
                parents_by_name=parents_by_name,
            ):
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
    ) -> list[ScrapedAdminArea]:
        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        last_year = self._client.year_from_date(last_pop_date)
        tbody = table.find("tbody")
        if not tbody:
            return []
        default_entity_type = self._default_entity_type(table)
        area_divisor = self._area_divisor(table)

        entities = []
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
                parent_code = self._parent_code_from_radm(tr, parents_by_name or {})
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

            if not parsed:
                continue

            entities.append(
                ScrapedAdminArea(
                    code=parsed.entity_id,
                    name=parsed.name,
                    level=level,
                    country_code=country_code,
                    entity_type=parsed.entity_type,
                    parent_code=parent_code,
                    area_km2=parsed.area_km2,
                    density=parsed.density,
                    pop_latest=parsed.pop_latest,
                    pop_latest_date=parsed.pop_latest_date,
                    last_census_year=parsed.last_census_year,
                    url=parsed.url,
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

    def _parent_code_from_radm(self, tr, parents_by_name: dict[str, ScrapedAdminArea]) -> str | None:
        parent_cell = tr.find("td", class_=lambda value: value and "radm" in value.split())
        if not parent_cell:
            return None
        parent_id = parent_cell.get("data-admid")
        if parent_id:
            return parent_id
        parent_name = self._normalize_name(parent_cell.get_text(" ", strip=True))
        parent = parents_by_name.get(parent_name)
        return parent.code if parent else None

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()
