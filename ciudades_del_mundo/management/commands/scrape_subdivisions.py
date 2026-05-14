"""Run one or more configured scraping jobs from TOML definitions."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.application import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository, DjangoUnitOfWork
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationCitiesScraper,
    CityPopulationDoubleScraper,
    CityPopulationInfoSectionScraper,
    CityPopulationStructuredTableScraper,
    PythonScrapingConfigRepository,
)
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url


class Command(BaseCommand):
    help = "Runs a CityPopulation scraping job from ciudades_del_mundo/subdivisions/<country>.toml."

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help="Country module names. Examples: spain, spain france. If omitted, all subdivisions configs are used.",
        )
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--list-pages", action="store_true", help="Only print the pages that would be scraped.")

    def handle(self, *args, **options):
        countries = options["countries"]
        config_repository = PythonScrapingConfigRepository()
        configs = self._get_configs(config_repository, countries)

        use_case = ScrapeAdminAreas(
            repository=DjangoAdminAreaRepository(),
            unit_of_work=DjangoUnitOfWork(),
            scrapers=[
                CityPopulationAdminScraper(debug=options["debug"]),
                CityPopulationCitiesScraper(debug=options["debug"]),
                CityPopulationDoubleScraper(debug=options["debug"]),
                CityPopulationInfoSectionScraper(debug=options["debug"]),
                CityPopulationStructuredTableScraper(debug=options["debug"]),
            ],
            on_page_start=lambda page: self.stdout.write(
                f"SCRAPE {page.html_format} L{page.lowest_level}: {page.url}"
            ),
            on_page_complete=lambda page: self.stdout.write(
                f"FOUND {page.found} entities: {page.url}"
            ),
        )

        for config in configs:
            if options["list_pages"]:
                for page in config.pages:
                    self.stdout.write(
                        f"SCRAPE {config.slug} {page.html_format} L{page.lowest_level}: "
                        f"{build_page_url(config.base_url, page.path)}"
                    )
                continue

            try:
                result = use_case.run(config)
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            self.stdout.write(
                self.style.SUCCESS(
                    f"OK {config.slug}: found={result.found}, created={result.created}, "
                    f"updated={result.updated}, deleted={result.deleted}"
                )
            )

    def _get_configs(self, config_repository, countries):
        if not countries:
            configs = config_repository.list_configs()
            if not configs:
                raise CommandError("No subdivisions configs found.")
            return configs

        configs = []
        for country in countries:
            try:
                configs.append(config_repository.get(country))
            except ModuleNotFoundError as exc:
                raise CommandError(f"No subdivisions config found for country '{country}'.") from exc
        return configs
