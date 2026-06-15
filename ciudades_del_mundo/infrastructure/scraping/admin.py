"""Adapter for the CityPopulation ``admin`` system.

The concrete adapter is deliberately small.  The shared section parser contains
all HTML rules so an AI can modify the scraping model in one place:
``citypopulation_sections.py``.
"""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.citypopulation_sections import CityPopulationSectionScraperMixin


class CityPopulationAdminScraper(CityPopulationSectionScraperMixin, BaseCityPopulationScraper):
    """Parse ``infosection -> major_subdivision -> minor_subdivision`` pages."""

    html_format = "admin"
