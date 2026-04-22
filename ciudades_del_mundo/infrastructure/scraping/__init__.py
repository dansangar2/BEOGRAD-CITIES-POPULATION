from .admin import CityPopulationAdminScraper
from .cities import CityPopulationCitiesScraper
from .double import CityPopulationDoubleScraper
from .infosection import CityPopulationInfoSectionScraper
from .python_config_repository import PythonScrapingConfigRepository
from .table import CityPopulationStructuredTableScraper

__all__ = [
    "CityPopulationAdminScraper",
    "CityPopulationCitiesScraper",
    "CityPopulationDoubleScraper",
    "CityPopulationInfoSectionScraper",
    "CityPopulationStructuredTableScraper",
    "PythonScrapingConfigRepository",
]
