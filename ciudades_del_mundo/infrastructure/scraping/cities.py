"""Adapter for the CityPopulation ``cities`` system."""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.citypopulation_sections import CityPopulationSectionScraperMixin


class CityPopulationCitiesScraper(CityPopulationSectionScraperMixin, BaseCityPopulationScraper):
    """Parse ``infosection -> major_subdivision -> cities`` pages."""

    html_format = "cities"
