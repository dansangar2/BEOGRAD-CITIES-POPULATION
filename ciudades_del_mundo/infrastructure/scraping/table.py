from __future__ import annotations

from dataclasses import replace

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingPageConfig
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationDoubleScraper
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url
from ciudades_del_mundo.utils.citypop import CityPopulationScraper


class CityPopulationStructuredTableScraper:
    html_format = "table"

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._scraper = CityPopulationScraper(debug=debug)
        self._admin_scraper = CityPopulationAdminScraper(debug=debug)
        self._double_scraper = CityPopulationDoubleScraper(debug=debug)

    def scrape(self, base_url: str, country_code: str, page: ScrapingPageConfig) -> list[ScrapedAdminArea]:
        url = build_page_url(base_url, page.path)
        html = self._scraper._get(url)
        return self.scrape_html(html=html, url=url, country_code=country_code, level=page.lowest_level)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._scraper.parser)
        base_for_urljoin = self._scraper._base_for_urljoin(url)
        root = self._parse_root(soup=soup, country_code=country_code, level=level, url=url)

        entities = [root] if root else []
        parents_by_name: dict[str, ScrapedAdminArea] = {}

        tl = soup.find("table", id="tl")
        if tl:
            for entity in self._double_scraper._parse_table(
                table=tl,
                country_code=country_code,
                level=level + 1,
                base_url=base_for_urljoin,
                parser="tl",
            ):
                entity = replace(entity, parent_code=root.code if root else None)
                entities.append(entity)
                parents_by_name[self._double_scraper._normalize_name(entity.name)] = entity

        ts = soup.find("table", id="ts")
        if ts:
            entities.extend(
                self._double_scraper._parse_table(
                    table=ts,
                    country_code=country_code,
                    level=level + 2,
                    base_url=base_for_urljoin,
                    parser="ts",
                    parents_by_name=parents_by_name,
                )
            )

        return entities

    def _parse_root(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        root = self._admin_scraper._parse_root(soup, country_code=country_code, level=level, url=url)
        code = self._root_code_from_tfoot(soup=soup, country_code=country_code, level=level, url=url)
        if root and code:
            return replace(root, code=code)
        return root

    def _root_code_from_tfoot(self, *, soup: BeautifulSoup, country_code: str, level: int, url: str) -> str | None:
        table = soup.find("table", id="tl")
        tfoot = table.find("tfoot") if table else None
        tr = tfoot.find("tr") if tfoot else None
        if not tr:
            return None

        last_pop_idx, last_pop_date = self._scraper._detect_last_visible_pop_column(table)
        parsed = self._scraper._parse_tr_tl(
            tr=tr,
            explicit_level=level,
            last_visible_pop_idx=last_pop_idx,
            last_visible_date=last_pop_date,
            default_last_census_year=self._scraper._year_from_date(last_pop_date),
            country_code=country_code,
            base_url=self._scraper._base_for_urljoin(url),
        )
        return parsed.entity_id if parsed else None
