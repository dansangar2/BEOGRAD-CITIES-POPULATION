from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import threading
import time

from django.core.management import BaseCommand, CommandError, call_command
from django.db import OperationalError, close_old_connections

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Valida y popula configuraciones con guardado incremental."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="+", help="Slugs de las configuraciones/paises a popular o re-popular.")
        parser.add_argument(
            "--page-workers",
            type=int,
            default=_default_page_workers(),
            help=(
                "Numero de paginas CityPopulation que se descargan en paralelo durante el scrapeo. "
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
            help="Descargar los ficheros de Commons durante el scraping. Mas lento y puede provocar 429.",
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
        self._stdout_lock = threading.Lock()
        slugs = [str(slug).strip() for slug in options.get("slugs") or [] if str(slug).strip()]
        if not slugs:
            self.stdout.write(self.style.WARNING("No se indico ninguna configuracion para popular."))
            return

        country_workers = max(1, int(options.get("country_workers") or 1))
        if country_workers <= 1 or len(slugs) <= 1:
            for slug in slugs:
                self._repopulate_slug(slug, options=options)
            return

        self._write(
            f"[popular] Ejecutando {len(slugs)} configuracion(es) con country-workers={country_workers} "
            f"y page-workers={max(1, int(options.get('page_workers') or 1))}."
        )
        with ThreadPoolExecutor(max_workers=min(country_workers, len(slugs))) as executor:
            futures = {executor.submit(self._repopulate_slug, slug, options=options): slug for slug in slugs}
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise CommandError(f"{slug}: {exc}") from exc

    def _repopulate_slug(self, slug: str, *, options: dict) -> None:
        close_old_connections()
        try:
            repository = PythonScrapingConfigRepository()
            repository.get(slug)

            write_config_progress(slug, "validating", detail="Validando configuracion")
            self._write(f"[validar] {slug}: validando configuracion antes de popular...")
            self._run_with_sqlite_retry(lambda: call_command("validate_subdivision_configs", slug))

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
        finally:
            close_old_connections()

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
        lock = getattr(self, "_stdout_lock", None)
        if lock is None:
            self.stdout.write(message)
            self.stdout.flush()
            return
        with lock:
            self.stdout.write(message)
            self.stdout.flush()


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
