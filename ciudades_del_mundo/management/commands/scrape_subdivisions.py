"""Run one or more SQL-configured scraping jobs."""

from __future__ import annotations

import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from ciudades_del_mundo.application import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository, DjangoUnitOfWork
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationAutoScraper,
    CityPopulationCitiesScraper,
    CityPopulationDoubleScraper,
    CityPopulationInfoSectionScraper,
    CityPopulationStructuredTableScraper,
    PythonScrapingConfigRepository,
)
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url
from ciudades_del_mundo.services.visual_assets import (
    seed_visual_assets_from_scraped_page,
    visual_asset_tables_exist,
)


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
        parser.add_argument(
            "--page-workers",
            type=int,
            default=_default_page_workers(),
            help=(
                "Número de páginas CityPopulation que se descargan en paralelo. "
                "Usa 1 para modo secuencial exacto. Por defecto: %(default)s."
            ),
        )
        parser.add_argument(
            "--seed-assets-from-pages",
            action="store_true",
            help="Persist flag/coat/seal images found in each already-downloaded CityPopulation page.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Compatibility flag: asset downloads are disabled by default.",
        )
        parser.add_argument(
            "--download-assets",
            action="store_true",
            help="Download media files to media/visual_assets. Disabled by default; URLs are used directly.",
        )
        parser.add_argument(
            "--skip-subdivision-assets",
            action="store_true",
            help="When seeding page assets, process only the country/root asset.",
        )
        parser.add_argument(
            "--asset-subdivision-levels",
            default="",
            help="AdminArea levels to seed from scraped pages, comma-separated. Default: all scraped levels.",
        )
        parser.add_argument(
            "--max-individual-wikidata-lookups",
            type=int,
            default=0,
            help=(
                "Maximum per-entity wbsearch fallback calls per page. Default: 0; "
                "page data-wd and one country SPARQL batch are used instead."
            ),
        )

    def handle(self, *args, **options):
        countries = options["countries"]
        config_repository = PythonScrapingConfigRepository()
        configs = self._get_configs(config_repository, countries)
        seed_assets = bool(options.get("seed_assets_from_pages"))
        if seed_assets and not visual_asset_tables_exist():
            raise CommandError("La tabla de assets visuales no existe. Ejecuta 'py manage.py migrate'.")
        subdivision_levels = (
            ()
            if options.get("skip_subdivision_assets")
            else _parse_levels(options.get("asset_subdivision_levels") or "")
        )

        for config in configs:
            if options["list_pages"]:
                for page in config.pages:
                    self._write(
                        f"SCRAPE {config.slug} {page.html_format} L{page.lowest_level}: "
                        f"{build_page_url(config.base_url, page.path)}"
                    )
                continue

            use_case = ScrapeAdminAreas(
                repository=DjangoAdminAreaRepository(),
                unit_of_work=DjangoUnitOfWork(),
                scrapers=[
                    CityPopulationAdminScraper(debug=options["debug"]),
                    CityPopulationAutoScraper(debug=options["debug"]),
                    CityPopulationCitiesScraper(debug=options["debug"]),
                    CityPopulationDoubleScraper(debug=options["debug"]),
                    CityPopulationInfoSectionScraper(debug=options["debug"]),
                    CityPopulationStructuredTableScraper(debug=options["debug"]),
                ],
                on_page_start=lambda page: self._write(
                    f"SCRAPE {page.html_format} L{page.lowest_level}: {page.url}"
                ),
                on_page_complete=lambda page, current_config=config: self._on_page_complete(
                    page,
                    current_config,
                    seed_assets=seed_assets,
                    download_assets=bool(options.get("download_assets")) and not bool(options.get("no_download_assets")),
                    subdivision_levels=subdivision_levels,
                    max_individual_wikidata_lookups=int(options.get("max_individual_wikidata_lookups") or 0),
                ),
                page_workers=max(1, int(options.get("page_workers") or 1)),
            )

            try:
                result = self._run_with_sqlite_retry(lambda: use_case.run(config))
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            self._write(
                f"OK {config.slug}: found={result.found}, created={result.created}, "
                f"updated={result.updated}, deleted={result.deleted}",
                style=self.style.SUCCESS,
            )

    def _on_page_complete(
        self,
        page,
        config,
        *,
        seed_assets: bool,
        download_assets: bool,
        subdivision_levels: tuple[int, ...] | None,
        max_individual_wikidata_lookups: int,
    ) -> None:
        self._write(f"FOUND {page.found} entities: {page.url}")
        if not seed_assets:
            return
        result = seed_visual_assets_from_scraped_page(
            country_code=config.country_code,
            page_url=page.url,
            html=page.html,
            entities=list(page.entities),
            download_missing=download_assets,
            subdivision_levels=subdivision_levels,
            fill_missing_with_wikidata=True,
            max_individual_wikidata_lookups=max_individual_wikidata_lookups,
            logger=self._write,
        )
        if result.found or result.downloaded or result.missing or result.errors:
            self._write(result.as_log_line(f"{config.slug}:page-assets"))

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


def _parse_levels(value: str) -> tuple[int, ...] | None:
    if not value.strip():
        return None
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4
