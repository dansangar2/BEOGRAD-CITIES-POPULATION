from __future__ import annotations

import os
import time

from django.core.management import BaseCommand, call_command
from django.db import OperationalError, close_old_connections

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Valida y popula configuraciones; si ya tienen datos, los limpia antes de re-popular."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="+", help="Slugs de las configuraciones/paises a popular o re-popular.")
        parser.add_argument(
            "--page-workers",
            type=int,
            default=_default_page_workers(),
            help=(
                "Número de páginas CityPopulation que se descargan en paralelo durante el scrapeo. "
                "Usa 1 para modo secuencial exacto. Por defecto: %(default)s."
            ),
        )
        parser.add_argument(
            "--skip-assets",
            action="store_true",
            help="No registrar ni descargar bandera/escudo durante el scraping.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Guardar solo URL/metadatos sin descargar ficheros a media/ (comportamiento por defecto).",
        )
        parser.add_argument(
            "--download-assets",
            action="store_true",
            help="Descargar los ficheros de Commons durante el scraping. Más lento y puede provocar 429.",
        )
        parser.add_argument(
            "--skip-subdivision-assets",
            action="store_true",
            help="No registrar bandera/escudo/sello para subdivisiones AdminArea.",
        )
        parser.add_argument(
            "--subdivision-asset-levels",
            default="",
            help="Niveles de AdminArea para registrar assets desde paginas scrapeadas, separados por coma. Por defecto: todos los niveles scrapeados.",
        )
        parser.add_argument(
            "--ai-enrich",
            action="store_true",
            help="Activar enriquecimiento IA de textos dinamicos al terminar el scrapeo.",
        )
        parser.add_argument(
            "--ai-languages",
            default="es,en,fr,de,it,ru,sr,sr-latn,ar",
            help="Idiomas separados por coma para --ai-enrich.",
        )
        parser.add_argument(
            "--ai-translate-area-names",
            action="store_true",
            help="Traducir tambien nombres de AdminArea existentes.",
        )
        parser.add_argument(
            "--ai-limit",
            type=int,
            default=100,
            help="Limite de textos/assets procesados por IA.",
        )

    def handle(self, *args, **options):
        slugs = [str(slug).strip() for slug in options.get("slugs") or [] if str(slug).strip()]
        if not slugs:
            self.stdout.write(self.style.WARNING("No se indicó ninguna configuración para popular."))
            return
        repository = PythonScrapingConfigRepository()
        for slug in slugs:
            self._repopulate_slug(slug, repository=repository, options=options)

    def _repopulate_slug(self, slug: str, *, repository, options: dict) -> None:
        try:
            config = repository.get(slug)
            country_code = str(config.country_code or slug)

            write_config_progress(slug, "validating", detail="Validando configuración")
            self._write(f"[validar] {slug}: validando configuración antes de popular...")
            self._run_with_sqlite_retry(lambda: call_command("validate_subdivision_configs", slug))

            if _country_has_scraped_data(country_code):
                write_config_progress(slug, "clearing", detail="Limpiando datos anteriores")
                self._write(f"[limpiar] {slug}: ya tenía datos; limpiando antes de re-popular...")
                self._run_with_sqlite_retry(lambda: call_command("clear_config_data_with_assets", country_code))
            else:
                self._write(f"[limpiar] {slug}: no había datos previos que limpiar.")

            write_config_progress(slug, "populating", detail="Populando datos")
            scrape_options = {
                "page_workers": max(1, int(options.get("page_workers") or 1)),
                "skip_assets": bool(options.get("skip_assets")),
                "no_download_assets": bool(options.get("no_download_assets")),
                "download_assets": bool(options.get("download_assets")),
                "skip_subdivision_assets": bool(options.get("skip_subdivision_assets")),
                "subdivision_asset_levels": options.get("subdivision_asset_levels") or "",
                "ai_enrich": bool(options.get("ai_enrich")),
                "ai_languages": options.get("ai_languages") or "",
                "ai_translate_area_names": bool(options.get("ai_translate_area_names")),
                "ai_limit": max(1, int(options.get("ai_limit") or 1)),
            }
            self._run_with_sqlite_retry(lambda: call_command("scrape_subdivisions_with_assets", slug, **scrape_options))
            write_config_progress(slug, "populated")
        except Exception as exc:
            write_config_progress(slug, "failed", detail=str(exc))
            raise

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

    def _write(self, message: str) -> None:
        self.stdout.write(message)
        self.stdout.flush()


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4


def _country_has_scraped_data(country_code: str) -> bool:
    country_code = str(country_code or "").strip()
    if not country_code:
        return False
    try:
        from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
    except Exception:
        return True

    querysets = (
        AdminArea.objects.filter(country_code=country_code),
        NuevoAdminArea.objects.filter(country_code=country_code),
    )
    for queryset in querysets:
        try:
            if queryset.exists():
                return True
        except Exception:
            return True
    return False
