"""Populate configs that have already passed validation."""

from __future__ import annotations

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
            "--skip-assets",
            action="store_true",
            help="No buscar ni descargar bandera/escudo después del scraping.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Guardar solo URL/metadatos sin descargar ficheros a media/.",
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
        if options.get("no_download_assets"):
            scrape_options["no_download_assets"] = True

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
