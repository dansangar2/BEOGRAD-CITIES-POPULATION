from .admin import CityPopulationAdminScraper
from .auto import CityPopulationAutoScraper
from .cities import CityPopulationCitiesScraper
from .double import CityPopulationDoubleScraper
from .infosection import CityPopulationInfoSectionScraper
from .page_types import CityPopulationPageProfile, CityPopulationPageType, detect_citypopulation_page_profile
from .table import CityPopulationStructuredTableScraper

try:  # Keep parser modules importable in lightweight/test environments without Django.
    from .python_config_repository import PythonScrapingConfigRepository
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only when Django is not installed.
    if exc.name != "django":
        raise

    class PythonScrapingConfigRepository:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "PythonScrapingConfigRepository needs Django; install project dependencies "
                "or import the concrete scraper classes directly."
            ) from exc


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
