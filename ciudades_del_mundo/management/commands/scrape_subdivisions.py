from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.application import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationDoubleScraper,
    CityPopulationStructuredTableScraper,
    PythonScrapingConfigRepository,
)
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url


class Command(BaseCommand):
    help = "Runs a CityPopulation scraping job from ciudades_del_mundo/subdivisions/<country>.py."

    def add_arguments(self, parser):
        parser.add_argument("country", help="Country module name. Example: spain")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--list-pages", action="store_true", help="Only print the pages that would be scraped.")

    def handle(self, *args, **options):
        country = options["country"]
        try:
            config = PythonScrapingConfigRepository().get(country)
        except ModuleNotFoundError as exc:
            raise CommandError(f"No subdivisions config found for country '{country}'.") from exc

        if options["list_pages"]:
            for page in config.pages:
                self.stdout.write(
                    f"SCRAPE {page.html_format} L{page.lowest_level}: {build_page_url(config.base_url, page.path)}"
                )
            return

        use_case = ScrapeAdminAreas(
            repository=DjangoAdminAreaRepository(),
            scrapers=[
                CityPopulationAdminScraper(debug=options["debug"]),
                CityPopulationDoubleScraper(debug=options["debug"]),
                CityPopulationStructuredTableScraper(debug=options["debug"]),
            ],
            on_page_start=lambda page: self.stdout.write(
                f"SCRAPE {page.html_format} L{page.lowest_level}: {page.url}"
            ),
            on_page_complete=lambda page: self.stdout.write(
                f"FOUND {page.found} entities: {page.url}"
            ),
        )
        result = use_case.run(config)
        self.stdout.write(
            self.style.SUCCESS(
                f"OK {config.slug}: found={result.found}, created={result.created}, updated={result.updated}"
            )
        )
