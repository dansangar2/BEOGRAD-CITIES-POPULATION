"""Compatibility module for the unified CityPopulation table scraper."""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.scraping.double import CityPopulationTableScraper

# Backwards-compatible alias used by auto.py and older code paths.
CityPopulationStructuredTableScraper = CityPopulationTableScraper

__all__ = ["CityPopulationTableScraper", "CityPopulationStructuredTableScraper"]
