"""Scraper for structured table pages without explicit nested admin sections."""

from __future__ import annotations

import re
from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationDoubleScraper


class CityPopulationStructuredTableScraper(BaseCityPopulationScraper):
    html_format = "table"

    def __init__(self, debug: bool = False):
        super().__init__(debug=debug)
        self._admin_scraper = CityPopulationAdminScraper(debug=debug)
        self._double_scraper = CityPopulationDoubleScraper(debug=debug)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        root = self._parse_root(soup=soup, country_code=country_code, level=level, url=url)
        return self._double_scraper.parse_hierarchical_tables(
            soup=soup,
            url=url,
            country_code=country_code,
            level=level,
            root=root,
            first_table_offset=1,
        )

    def _parse_root(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        root = self._admin_scraper._parse_root(soup, country_code=country_code, level=level, url=url)
        if not root:
            root = self._root_from_cpage(soup=soup, country_code=country_code, level=level, url=url)
        if root and not root.entity_type:
            root = replace(root, entity_type=self._root_entity_type_from_cpage(soup=soup, root_name=root.name))
        tfoot_root = self._root_from_tfoot(soup=soup, country_code=country_code, level=level, url=url)
        if root and tfoot_root:
            return replace(
                tfoot_root,
                entity_type=tfoot_root.entity_type or root.entity_type,
                url=tfoot_root.url or root.url,
            )
        if tfoot_root:
            return tfoot_root
        return root

    def _root_from_cpage(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        header = soup.find("header", class_=lambda value: value and "cpage" in value.split())
        if not header:
            return None

        name_node = header.find(attrs={"itemprop": "name"}) or header.find("h1")
        name = name_node.get_text(" ", strip=True) if name_node else country_code
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            return None

        return ScrapedAdminArea(
            code=country_code,
            name=name,
            level=level,
            country_code=country_code,
            entity_type=self._root_entity_type_from_cpage(soup=soup, root_name=name),
            area_km2=None,
            density=None,
            pop_latest=None,
            pop_latest_date=None,
            last_census_year=None,
            url=url,
        )

    def _root_entity_type_from_cpage(self, *, soup: BeautifulSoup, root_name: str) -> str | None:
        header = soup.find("header", class_=lambda value: value and "cpage" in value.split())
        if not header:
            return None

        description = header.find("p", attrs={"itemprop": "description"})
        if not description:
            return None

        text = description.get_text(" ", strip=True)
        if not text:
            return None

        root_name = re.sub(r"\s+", " ", root_name).strip()
        if root_name:
            match = re.fullmatch(
                rf"(?P<entity_type>.+?)\s+of\s+{re.escape(root_name)}",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group("entity_type").strip()

        return text.strip()

    def _root_from_tfoot(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        table = soup.find("table", id="tl")
        tfoot = table.find("tfoot") if table else None
        tr = tfoot.find("tr") if tfoot else None
        if not tr:
            return None

        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        parsed = self._client.parse_tr_tl(
            tr=tr,
            explicit_level=level,
            last_visible_pop_idx=last_pop_idx,
            last_visible_date=last_pop_date,
            default_last_census_year=self._client.year_from_date(last_pop_date),
            country_code=country_code,
            base_url=self._client.base_for_urljoin(url),
            area_divisor=self._double_scraper._area_divisor(table),
            visible_pop_columns=visible_pop_columns,
        )
        if not parsed:
            return None

        return ScrapedAdminArea(
            code=parsed.entity_id,
            name=parsed.name,
            level=level,
            country_code=country_code,
            entity_type=parsed.entity_type,
            area_km2=parsed.area_km2,
            density=parsed.density,
            pop_latest=parsed.pop_latest,
            pop_latest_date=parsed.pop_latest_date,
            last_census_year=parsed.last_census_year,
            url=parsed.url,
        )
