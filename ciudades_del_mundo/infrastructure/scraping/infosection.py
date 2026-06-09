"""Scraper for pages whose primary data is exposed in infosection blocks."""

from __future__ import annotations

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.page_config import apply_root_config


class CityPopulationInfoSectionScraper(BaseCityPopulationScraper):
    html_format = "infosection"

    def __init__(self, debug: bool = False):
        super().__init__(debug=debug)
        self._admin_scraper = CityPopulationAdminScraper(debug=debug)

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        return self._scrape_configured(html=html, url=url, country_code=country_code, level=level, page=None)

    def scrape_configured_html(self, html: str, url: str, country_code: str, page) -> list[ScrapedAdminArea]:
        return self._scrape_configured(
            html=html,
            url=url,
            country_code=country_code,
            level=page.lowest_level,
            page=page,
        )

    def _scrape_configured(
        self,
        *,
        html: str,
        url: str,
        country_code: str,
        level: int,
        page,
    ) -> list[ScrapedAdminArea]:
        soup, _profile = self._soup_and_profile(html)
        root = self._admin_scraper._parse_root(soup, country_code=country_code, level=level, url=url)
        if not root:
            root = self._parse_tfoot_root(soup=soup, url=url, country_code=country_code, level=level)
        if page is not None:
            root = apply_root_config(root, page=page, country_code=country_code, url=url, default_level=level)
        return [root] if root else []

    def _parse_tfoot_root(
        self,
        *,
        soup: BeautifulSoup,
        url: str,
        country_code: str,
        level: int,
    ) -> ScrapedAdminArea | None:
        table = soup.find("table", id="tl")
        tfoot = table.find("tfoot") if table else None
        tr = tfoot.find("tr") if tfoot else None
        if not table or not tr:
            return None

        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        parsed = self._client.parse_tr_tl(
            tr=tr,
            explicit_level=level,
            last_visible_pop_idx=last_pop_idx,
            last_visible_date=last_pop_date,
            default_last_census_year=self._client.year_from_date(last_pop_date),
            country_code=country_code,
            base_url=self._client.base_for_urljoin(url),
        )
        if not parsed:
            return None

        return ScrapedAdminArea(
            code=country_code,
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
            data_wd=parsed.data_wd,
        )
