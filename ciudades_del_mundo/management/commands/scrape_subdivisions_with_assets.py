from __future__ import annotations

import os
import time

from django.core.management import BaseCommand, CommandError, call_command
from django.db import OperationalError, close_old_connections

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.services.visual_assets import (
    visual_asset_tables_exist,
)
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = (
        "Ejecuta scrape_subdivisions y, tras popular los datos, registra bandera/escudo/sello "
        "como segunda fase reanudable."
    )

    def _write(self, message):
        self.stdout.write(message)
        self.stdout.flush()

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug de la configuración/pais a popular.")
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
            help="No registrar ni descargar bandera/escudo tras popular los datos.",
        )
        parser.add_argument(
            "--no-download-assets",
            action="store_true",
            help="Guardar solo URL/metadatos sin descargar ficheros a media/ (comportamiento por defecto).",
        )
        parser.add_argument(
            "--download-assets",
            action="store_true",
            help="Descargar los ficheros de Commons en la fase de assets. Más lento y puede provocar 429.",
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
            "--max-individual-wikidata-lookups",
            type=int,
            default=0,
            help="Fallbacks individuales de Wikidata por página. Por defecto 0 para evitar 429.",
        )

        parser.add_argument(
            "--resume",
            action="store_true",
            help="Continuar desde paginas ya completadas por una tarea web parada.",
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
            if options.get("skip_assets"):
                self._run_with_sqlite_retry(
                    lambda: call_command(
                        "scrape_subdivisions",
                        slug,
                        page_workers=max(1, int(options.get("page_workers") or 1)),
                        resume=bool(options.get("resume")),
                        ai_enrich=bool(options.get("ai_enrich")),
                        ai_languages=options.get("ai_languages") or "",
                        ai_translate_area_names=bool(options.get("ai_translate_area_names")),
                        ai_limit=max(1, int(options.get("ai_limit") or 1)),
                    )
                )
                self._write("[assets] omitido por --skip-assets")
                write_config_progress(slug, "populated")
                return
            if not visual_asset_tables_exist():
                raise CommandError("La tabla de assets visuales no existe. Ejecuta migraciones.")

            PythonScrapingConfigRepository().get(slug)
            self._run_with_sqlite_retry(
                lambda: call_command(
                    "scrape_subdivisions",
                    slug,
                    seed_assets_from_pages=True,
                    no_download_assets=not bool(options.get("download_assets")) or bool(options.get("no_download_assets")),
                    skip_subdivision_assets=bool(options.get("skip_subdivision_assets")),
                    asset_subdivision_levels=options.get("subdivision_asset_levels") or "",
                    max_individual_wikidata_lookups=int(options.get("max_individual_wikidata_lookups") or 0),
                    page_workers=max(1, int(options.get("page_workers") or 1)),
                    resume=bool(options.get("resume")),
                    ai_enrich=bool(options.get("ai_enrich")),
                    ai_languages=options.get("ai_languages") or "",
                    ai_translate_area_names=bool(options.get("ai_translate_area_names")),
                    ai_limit=max(1, int(options.get("ai_limit") or 1)),
                )
            )
        except Exception as exc:
            write_config_progress(slug, "failed", detail=str(exc))
            raise
        write_config_progress(slug, "populated")


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4
