from .admin import CityPopulationAdminScraper
from .auto import CityPopulationAutoScraper
from .cities import CityPopulationCitiesScraper
from .double import CityPopulationDoubleScraper
from .infosection import CityPopulationInfoSectionScraper
from .page_types import CityPopulationPageProfile, CityPopulationPageType, detect_citypopulation_page_profile
from .python_config_repository import PythonScrapingConfigRepository
from .table import CityPopulationStructuredTableScraper

__all__ = [
    "CityPopulationAdminScraper",
    "CityPopulationAutoScraper",
    "CityPopulationCitiesScraper",
    "CityPopulationDoubleScraper",
    "CityPopulationInfoSectionScraper",
    "CityPopulationPageProfile",
    "CityPopulationPageType",
    "CityPopulationStructuredTableScraper",
    "PythonScrapingConfigRepository",
    "detect_citypopulation_page_profile",
]
