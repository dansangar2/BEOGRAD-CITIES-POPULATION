"""Run one or more SQL-configured scraping jobs."""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

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
    help = "Runs a CityPopulation scraping job from SQL ScrapingConfig rows."

    def _write(self, message, *, style=None):
        self.stdout.write(style(message) if style else message)
        self.stdout.flush()

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help="Config slugs. Examples: spain, spain france. If omitted, all SQL configs are used.",
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
            on_page_start=lambda page: self._write(
                f"SCRAPE {page.html_format} L{page.lowest_level}: {page.url}"
            ),
            on_page_complete=lambda page: self._write(
                f"FOUND {page.found} entities: {page.url}"
            ),
        )

        for config in configs:
            if options["list_pages"]:
                for page in config.pages:
                    self._write(
                        f"SCRAPE {config.slug} {page.html_format} L{page.lowest_level}: "
                        f"{build_page_url(config.base_url, page.path)}"
                    )
                continue

            try:
                result = self._run_with_sqlite_retry(lambda: use_case.run(config))
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            self._write(
                f"OK {config.slug}: found={result.found}, created={result.created}, "
                f"updated={result.updated}, deleted={result.deleted}",
                style=self.style.SUCCESS,
            )


    def _run_with_sqlite_retry(self, callback, *, attempts: int = 8):
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                return callback()
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt >= attempts:
                    raise
                self._write(
                    f"[sqlite] Base de datos ocupada; reintentando ({attempt}/{attempts - 1}) en {delay:.1f}s...",
                    style=self.style.WARNING,
                )
                close_old_connections()
                time.sleep(delay)
                delay = min(delay * 1.8, 8.0)

    def _get_configs(self, config_repository, countries):
        if not countries:
            configs = config_repository.list_configs()
            if not configs:
                raise CommandError(
                    "No SQL scraping configs found. Run 'py manage.py sync_scraping_configs' to import temporary TOML seeds."
                )
            return configs

        configs = []
        for country in countries:
            try:
                configs.append(config_repository.get(country))
            except ModuleNotFoundError as exc:
                raise CommandError(f"No SQL scraping config found for slug '{country}'.") from exc
        return configs
