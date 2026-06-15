"""Validate and populate SQL scraping configs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import threading

from django.core.management import BaseCommand, CommandError, call_command
from django.db import close_old_connections

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Valida y popula configuraciones SQL."

    def _write(self, message, *, style=None):
        lock = getattr(self, "_stdout_lock", None)
        if lock is None:
            self.stdout.write(style(message) if style else message)
            self.stdout.flush()
            return
        with lock:
            self.stdout.write(style(message) if style else message)
            self.stdout.flush()

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help="Slugs a validar y popular. Si no se indican, se procesan todas las configuraciones.",
        )
        parser.add_argument(
            "--page-workers",
            type=int,
            default=_default_page_workers(),
            help=(
                "Numero de paginas CityPopulation que se descargan en paralelo al popular. "
                "Usa 1 para modo secuencial exacto. Por defecto: %(default)s."
            ),
        )
        parser.add_argument(
            "--country-workers",
            type=int,
            default=_default_country_workers(),
            help=(
                "Numero de paises que se validan/populan en paralelo. "
                "Las escrituras SQLite del scrapeo se serializan. Por defecto: %(default)s."
            ),
        )
        parser.add_argument(
            "--skip-assets",
            action="store_true",
            help="No buscar ni descargar bandera/escudo despues del scraping.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Guardar solo URL/metadatos sin descargar ficheros a media/ (comportamiento por defecto).",
        )
        parser.add_argument(
            "--download-assets",
            action="store_true",
            help="Descargar ficheros de Commons durante el scraping. Mas lento y puede provocar 429.",
        )

    def handle(self, *args, **options):
        self._stdout_lock = threading.Lock()
        repository = PythonScrapingConfigRepository()
        slugs = list(options["countries"] or repository.list_slugs())
        if not slugs:
            self._write("No hay configuraciones para popular.", style=self.style.WARNING)
            return

        self._write(f"[popular] Validando y populando {len(slugs)} configuracion(es)...")

        country_workers = max(1, int(options.get("country_workers") or 1))
        scrape_options = self._scrape_options(options)
        if country_workers <= 1 or len(slugs) <= 1:
            for index, slug in enumerate(slugs, start=1):
                self._populate_slug(slug, index=index, total=len(slugs), scrape_options=scrape_options)
        else:
            self._write(
                f"[popular] Ejecutando con country-workers={country_workers} "
                f"y page-workers={scrape_options['page_workers']}."
            )
            with ThreadPoolExecutor(max_workers=min(country_workers, len(slugs))) as executor:
                futures = {
                    executor.submit(
                        self._populate_slug,
                        slug,
                        index=index,
                        total=len(slugs),
                        scrape_options=scrape_options,
                    ): slug
                    for index, slug in enumerate(slugs, start=1)
                }
                for future in as_completed(futures):
                    slug = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        raise CommandError(f"{slug}: {exc}") from exc

        self._write(f"[popular] Completadas {len(slugs)} configuracion(es).", style=self.style.SUCCESS)

    def _populate_slug(self, slug: str, *, index: int, total: int, scrape_options: dict) -> None:
        close_old_connections()
        try:
            self._write(f"[popular] ({index}/{total}) {slug}")
            repository = PythonScrapingConfigRepository()
            write_config_progress(slug, "validating")
            try:
                repository.get(slug)
                call_command("validate_subdivision_configs", slug)
                write_config_progress(slug, "populating")
                call_command("scrape_subdivisions_with_assets", slug, **scrape_options)
            except Exception as exc:
                write_config_progress(slug, "failed", detail=str(exc))
                raise
            write_config_progress(slug, "populated")
        finally:
            close_old_connections()

    def _scrape_options(self, options: dict) -> dict:
        scrape_options = {}
        if options.get("skip_assets"):
            scrape_options["skip_assets"] = True
        if options.get("no_download_assets") or not options.get("download_assets"):
            scrape_options["no_download_assets"] = True
        if options.get("download_assets"):
            scrape_options["download_assets"] = True
        scrape_options["page_workers"] = max(1, int(options.get("page_workers") or 1))
        return scrape_options


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4


def _default_country_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_COUNTRY_WORKERS", "1")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1
