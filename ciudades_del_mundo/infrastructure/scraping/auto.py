"""Auto-dispatching scraper for CityPopulation pages."""

from __future__ import annotations

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.admin import CityPopulationAdminScraper
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationDoubleScraper
from ciudades_del_mundo.infrastructure.scraping.infosection import CityPopulationInfoSectionScraper
from ciudades_del_mundo.infrastructure.scraping.page_types import CityPopulationPageProfile
from ciudades_del_mundo.infrastructure.scraping.table import CityPopulationStructuredTableScraper


class CityPopulationAutoScraper(BaseCityPopulationScraper):
    """Detect the CityPopulation page structure and delegate to a concrete scraper."""

    html_format = "auto"

    def __init__(self, debug: bool = False):
        super().__init__(debug=debug)
        self._scrapers = {
            "admin": CityPopulationAdminScraper(debug=debug),
            "double": CityPopulationDoubleScraper(debug=debug),
            "infosection": CityPopulationInfoSectionScraper(debug=debug),
            "table": CityPopulationStructuredTableScraper(debug=debug),
        }

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        _soup, profile = self._soup_and_profile(html)
        scraper = self._scraper_for_profile(profile)
        return list(scraper.scrape_html(html=html, url=url, country_code=country_code, level=level))

    def _scraper_for_profile(self, profile: CityPopulationPageProfile) -> BaseCityPopulationScraper:
        return self._scrapers[profile.preferred_html_format]
