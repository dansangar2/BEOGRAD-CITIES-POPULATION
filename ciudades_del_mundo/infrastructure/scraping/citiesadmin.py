"""Adapter for the CityPopulation ``citiesadmin`` system."""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.citypopulation_sections import CityPopulationSectionScraperMixin


class CityPopulationCitiesAdminScraper(CityPopulationSectionScraperMixin, BaseCityPopulationScraper):
    """Parse ``infosection -> major_subdivision -> minor_subdivision -> cities`` pages."""

    html_format = "citiesadmin"
