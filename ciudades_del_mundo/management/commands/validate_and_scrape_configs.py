"""Populate configs that have already passed validation."""

from __future__ import annotations

import os

from django.core.management import BaseCommand, call_command

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Popula configuraciones ya validadas, una por una."

    def _write(self, message, *, style=None):
        self.stdout.write(style(message) if style else message)
        self.stdout.flush()

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help="Slugs a popular. Si no se indican, se procesan todas las configuraciones.",
        )
        parser.add_argument(
            "--page-workers",
            type=int,
            default=_default_page_workers(),
            help=(
                "Número de páginas CityPopulation que se descargan en paralelo al popular. "
                "Usa 1 para modo secuencial exacto. Por defecto: %(default)s."
            ),
        )
        parser.add_argument(
            "--skip-assets",
            action="store_true",
            help="No buscar ni descargar bandera/escudo después del scraping.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Guardar solo URL/metadatos sin descargar ficheros a media/ (comportamiento por defecto).",
        )
        parser.add_argument(
            "--download-assets",
            action="store_true",
            help="Descargar ficheros de Commons durante el scraping. Más lento y puede provocar 429.",
        )

    def handle(self, *args, **options):
        repository = PythonScrapingConfigRepository()
        slugs = list(options["countries"] or repository.list_slugs())
        if not slugs:
            self._write("No hay configuraciones para popular.", style=self.style.WARNING)
            return

        self._write(f"[popular] Populando {len(slugs)} configuración(es) validada(s)...")

        scrape_options = {}
        if options.get("skip_assets"):
            scrape_options["skip_assets"] = True
        if options.get("no_download_assets") or not options.get("download_assets"):
            scrape_options["no_download_assets"] = True
        if options.get("download_assets"):
            scrape_options["download_assets"] = True
        scrape_options["page_workers"] = max(1, int(options.get("page_workers") or 1))

        for index, slug in enumerate(slugs, start=1):
            self._write(f"[popular] ({index}/{len(slugs)}) {slug}")
            write_config_progress(slug, "populating")
            try:
                call_command("scrape_subdivisions_with_assets", slug, **scrape_options)
            except Exception as exc:
                write_config_progress(slug, "failed", detail=str(exc))
                raise
            write_config_progress(slug, "populated")

        self._write(f"[popular] Completadas {len(slugs)} configuración(es).", style=self.style.SUCCESS)


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4
