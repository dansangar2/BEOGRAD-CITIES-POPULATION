from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url


class CityPopulationDoubleScraper:
    html_format = "double"

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._client = CityPopulationClient(debug=debug)

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        url = build_page_url(base_url, page.path)
        html = self._client.get(url)
        return self.scrape_html(html=html, url=url, country_code=country_code, level=page.lowest_level)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        base_for_urljoin = self._client.base_for_urljoin(url)

        entities: list[ScrapedAdminArea] = []
        parents_by_name: dict[str, ScrapedAdminArea] = {}

        tl = soup.find("table", id="tl")
        if tl:
            for entity in self._parse_table(
                table=tl,
                country_code=country_code,
                level=level,
                base_url=base_for_urljoin,
                parser="tl",
            ):
                entities.append(entity)
                parents_by_name[self._normalize_name(entity.name)] = entity

        ts = soup.find("table", id="ts")
        if ts:
            for entity in self._parse_table(
                table=ts,
                country_code=country_code,
                level=level + 1,
                base_url=base_for_urljoin,
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
        last_year = self._client.year_from_date(last_pop_date)
        tbody = table.find("tbody")
        if not tbody:
            return []

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
