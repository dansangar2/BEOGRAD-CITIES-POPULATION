from __future__ import annotations

import time

from django.core.management import BaseCommand, CommandError, call_command
from django.db import OperationalError, close_old_connections

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.services.visual_assets import (
    ensure_visual_assets_for_config_slug,
    ensure_visual_assets_for_country_admin_areas,
    visual_asset_tables_exist,
)
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Ejecuta scrape_subdivisions y enriquece BBDD con bandera/escudo/sello locales/remotos."

    def _write(self, message):
        self.stdout.write(message)
        self.stdout.flush()

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug de la configuración/pais a popular.")
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
        parser.add_argument(
            "--skip-subdivision-assets",
            action="store_true",
            help="No buscar bandera/escudo/sello para subdivisiones AdminArea.",
        )
        parser.add_argument(
            "--subdivision-asset-levels",
            default="1,2",
            help="Niveles de AdminArea para buscar assets, separados por coma. Por defecto: 1,2.",
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
                    f"[sqlite] Base de datos ocupada; reintentando ({attempt}/{attempts - 1}) en {delay:.1f}s..."
                )
                close_old_connections()
                time.sleep(delay)
                delay = min(delay * 1.8, 8.0)

    def handle(self, *args, **options):
        slug = options["slug"]
        write_config_progress(slug, "populating")
        try:
            country_code = PythonScrapingConfigRepository().get(slug).country_code
            self._run_with_sqlite_retry(lambda: call_command("scrape_subdivisions", slug))

            if options.get("skip_assets"):
                self._write("[assets] omitido por --skip-assets")
                write_config_progress(slug, "populated")
                return
            if not visual_asset_tables_exist():
                raise CommandError("La tabla de assets visuales no existe. Ejecuta migraciones.")

            result = self._run_with_sqlite_retry(
                lambda: ensure_visual_assets_for_config_slug(slug, download_missing=not options.get("no_download_assets"))
            )
            self._write(result.as_log_line(slug))
            if not options.get("skip_subdivision_assets"):
                subdivision_result = self._run_with_sqlite_retry(
                    lambda: ensure_visual_assets_for_country_admin_areas(
                        country_code,
                        download_missing=not options.get("no_download_assets"),
                        levels=_parse_levels(options.get("subdivision_asset_levels") or ""),
                    )
                )
                self._write(subdivision_result.as_log_line(f"admin_areas:{country_code}"))
        except Exception as exc:
            write_config_progress(slug, "failed", detail=str(exc))
            raise
        write_config_progress(slug, "populated")


def _parse_levels(value: str) -> tuple[int, ...] | None:
    if not value.strip():
        return None
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())
