"""Run one or more SQL-configured scraping jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import ProgrammingError
from django.utils.translation import gettext as _

from ciudades_del_mundo.application import (
    CachedScrapePage,
    ScrapeAdminAreas,
    ScrapeBlockValidationError,
    ScrapeLinkValidationError,
)
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository, DjangoUnitOfWork
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationCitiesScraper,
    CityPopulationCitiesAdminScraper,
    PythonScrapingConfigRepository,
)
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationHtmlFetcher
from ciudades_del_mundo.infrastructure.scraping.urls import build_page_url
from ciudades_del_mundo.models import AdminArea, ScrapingConfig
from ciudades_del_mundo.services.ai_text_enrichment import (
    DEFAULT_AI_LANGUAGES,
    AiTextEnrichmentService,
    apply_stored_entity_type_inferences,
)
from ciudades_del_mundo.services.ai_text_provider import (
    AiProviderConfigurationError,
    OpenAICompatibleJsonProvider,
)
from ciudades_del_mundo.services.scrape_resume import ScrapeResumeStore
from ciudades_del_mundo.services.scraped_page_logs import ScrapedPageLogWriter
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.visual_assets import (
    seed_visual_assets_from_scraped_page,
    share_visual_assets_for_country_admin_areas,
    visual_asset_tables_exist,
)
from ciudades_del_mundo.services.wikidata_parent_links import repair_entities_with_wikidata_parent_links
from ciudades_del_mundo.web.log_retention import cleanup_old_logs
from ciudades_del_mundo.web.task_progress import current_task_id


class Command(BaseCommand):
    help = "Runs a CityPopulation scraping job from SQL ScrapingConfig rows."

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
            "--country-workers",
            type=int,
            default=_default_country_workers(),
            help=(
                "Numero de paises que se scrapean en paralelo. "
                "Las escrituras SQLite se serializan. Por defecto: %(default)s."
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
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Reuse completed page checkpoints from a stopped web scraping task.",
        )
        parser.add_argument(
            "--ai-enrich",
            action="store_true",
            help="Infer incomplete entity types and generate missing dynamic AI text after scraping.",
        )
        parser.add_argument(
            "--ai-languages",
            default=",".join(DEFAULT_AI_LANGUAGES),
            help="Comma-separated dynamic text languages used with --ai-enrich.",
        )
        parser.add_argument(
            "--ai-translate-area-names",
            action="store_true",
            help="Also translate individual AdminArea names. Off by default to avoid large AI jobs.",
        )
        parser.add_argument(
            "--ai-limit",
            type=int,
            default=100,
            help="Maximum existing text/assets processed by post-scrape AI enrichment.",
        )

    def handle(self, *args, **options):
        self._stdout_lock = threading.Lock()
        self._sqlite_write_lock = sqlite_write_lock_if_needed() or threading.Lock()
        cleanup_old_logs()
        countries = options["countries"]
        config_repository = PythonScrapingConfigRepository()
        configs = self._get_configs(config_repository, countries)
        attach_runtime_config_extensions(configs)
        seed_assets = bool(options.get("seed_assets_from_pages"))
        if not options["list_pages"]:
            _ensure_scraping_schema_ready()
        if seed_assets and not visual_asset_tables_exist():
            raise CommandError("La tabla de assets visuales no existe. Ejecuta 'py manage.py migrate'.")
        subdivision_levels = (
            ()
            if options.get("skip_subdivision_assets")
            else _parse_levels(options.get("asset_subdivision_levels") or "")
        )
        country_workers = max(1, int(options.get("country_workers") or 1))
        if country_workers > 1 and seed_assets:
            raise CommandError(
                "--country-workers > 1 no se combina con --seed-assets-from-pages; "
                "los assets/Wikimedia se deben sembrar en ejecucion secuencial."
            )

        if options["list_pages"] or country_workers == 1 or len(configs) <= 1:
            for config in configs:
                self._run_config(
                    config,
                    options,
                    seed_assets=seed_assets,
                    subdivision_levels=subdivision_levels,
                )
            return

        self._write(
            f"[scrape] Ejecutando {len(configs)} configuraciones con country-workers={country_workers} "
            f"y page-workers={max(1, int(options.get('page_workers') or 1))}."
        )
        with ThreadPoolExecutor(max_workers=min(country_workers, len(configs))) as executor:
            futures = {
                executor.submit(
                    self._run_config,
                    config,
                    options,
                    seed_assets=seed_assets,
                    subdivision_levels=subdivision_levels,
                ): config
                for config in configs
            }
            for future in as_completed(futures):
                config = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise CommandError(f"{config.slug}: {exc}") from exc

    def _run_config(
        self,
        config,
        options,
        *,
        seed_assets: bool,
        subdivision_levels: tuple[int, ...] | None,
    ) -> None:
        close_old_connections()
        try:
            if options["list_pages"]:
                for page in config.pages:
                    self._write(
                        f"SCRAPE {config.slug} {page.html_format} L{page.lowest_level}: "
                        f"{build_page_url(config.base_url, page.path)}"
                    )
                return

            ai_service = self._ai_service(options) if options.get("ai_enrich") else None
            resume_store = _resume_store_for_config(config)
            page_log_writer = ScrapedPageLogWriter(slug=config.slug, run_id=_scrape_run_id())
            if not options.get("resume"):
                resume_store.clear()

            use_case = ScrapeAdminAreas(
                repository=DjangoAdminAreaRepository(),
                unit_of_work=DjangoUnitOfWork(self._sqlite_write_lock),
                scrapers=[
                    CityPopulationAdminScraper(debug=options["debug"]),
                    CityPopulationCitiesScraper(debug=options["debug"]),
                    CityPopulationCitiesAdminScraper(debug=options["debug"]),
                ],
                on_page_start=lambda page: self._write(
                    f"SCRAPE {page.html_format} L{page.lowest_level}: {page.url}"
                ),
                on_page_complete=lambda page, current_config=config: self._on_page_complete(
                    page,
                    current_config,
                    resume_store=resume_store,
                    page_log_writer=page_log_writer,
                    seed_assets=seed_assets,
                    download_assets=bool(options.get("download_assets")) and not bool(options.get("no_download_assets")),
                    subdivision_levels=subdivision_levels,
                    max_individual_wikidata_lookups=int(options.get("max_individual_wikidata_lookups") or 0),
                ),
                cached_page_loader=(
                    (lambda page, store=resume_store: _cached_page_from_store(store, page))
                    if options.get("resume")
                    else None
                ),
                on_cached_page=lambda page, writer=page_log_writer: self._on_cached_page(
                    page,
                    page_log_writer=writer,
                ),
                entity_enricher=(
                    ai_service.normalize_scraped_entities
                    if ai_service
                    else lambda current_config, entities: apply_stored_entity_type_inferences(
                        current_config.country_code,
                        entities,
                    )
                ),
                wikimedia_parent_resolver=lambda current_config, entities: repair_entities_with_wikidata_parent_links(
                    current_config.country_code,
                    entities,
                    logger=self._write,
                ),
                page_workers=max(1, int(options.get("page_workers") or 1)),
                html_fetcher=CityPopulationHtmlFetcher(debug=options["debug"]),
                on_unlinked_entities=self._on_unlinked_entities,
                on_persistence_progress=self._on_persistence_progress,
                on_block_validation_error=self._on_block_validation_error,
                on_link_validation_error=self._on_link_validation_error,
            )

            try:
                self._write(f"[paso 1/5] {config.slug}: scraping por bloques CityPopulation...")
                result = self._run_with_sqlite_retry(lambda: use_case.run(config))
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            self._write(
                f"[paso 5/5] OK {config.slug}: found={result.found}, created={result.created}, "
                f"updated={result.updated}, deleted={result.deleted}",
                style=self.style.SUCCESS,
            )
            if seed_assets:
                shared_assets = share_visual_assets_for_country_admin_areas(
                    config.country_code,
                    logger=self._write,
                )
                if shared_assets.found or shared_assets.errors:
                    self._write(shared_assets.as_log_line(f"{config.slug}:shared-assets"))
            if ai_service:
                stats = ai_service.translate_dynamic_texts(
                    country_code=config.country_code,
                    include_country=True,
                    include_admin_area_names=bool(options.get("ai_translate_area_names")),
                    include_entity_types=True,
                    limit=max(1, int(options.get("ai_limit") or 1)),
                )
                if seed_assets:
                    stats += ai_service.describe_missing_visual_assets(
                        country_code=config.country_code,
                        limit=max(1, int(options.get("ai_limit") or 1)),
                )
                self._write(stats.as_log_line(f"[ai] {config.slug}:"))
            resume_store.clear()
        finally:
            close_old_connections()

    def _on_unlinked_entities(self, config, entities) -> None:
        self._write(
            f"[vinculacion] {config.slug}: {len(entities)} entidades sin padre tras data-wd/id HTML/url; "
            "quedan sueltas para revisar configuración o parser.",
            style=self.style.WARNING,
        )
        for entity in entities:
            self._write(
                "[vinculacion] "
                f"{config.slug}: sin padre code={entity.code!r} name={entity.name!r} "
                f"level={entity.level} type={entity.entity_type!r} "
                f"parent={entity.parent_code!r} data_wd={entity.data_wd!r} url={entity.url!r}",
                style=self.style.WARNING,
            )

    def _on_block_validation_error(self, config, error: ScrapeBlockValidationError) -> None:
        log_path = _write_block_validation_log(config.slug, error)
        self._write(
            f"[scraping] {error.code}: bloque {error.block_index + 1} de {config.slug} no valido. "
            f"Log exportado: {log_path}",
            style=self.style.ERROR,
        )

    def _on_link_validation_error(self, config, error: ScrapeLinkValidationError) -> None:
        log_path = _write_link_validation_log(config.slug, error)
        self._write(
            f"[scraping] {error.code}: vinculacion de {config.slug} no valida. "
            f"Log exportado: {log_path}",
            style=self.style.ERROR,
        )

    def _on_persistence_progress(self, config, progress) -> None:
        label = _persistence_phase_label(progress.phase)
        step = _persistence_step_label(progress.phase)
        if progress.status == "start":
            if progress.phase == "save_retry":
                self._write(f"[{step}] {config.slug}: {label} ({progress.count}/10)...")
                return
            self._write(f"[{step}] {config.slug}: {label}...")
            return

        details = []
        if progress.created:
            details.append(_("creadas=%(count)s") % {"count": progress.created})
        if progress.updated:
            details.append(_("actualizadas=%(count)s") % {"count": progress.updated})
        if progress.deleted:
            details.append(_("borradas=%(count)s") % {"count": progress.deleted})
        if not details and progress.count:
            details.append(_("filas=%(count)s") % {"count": progress.count})
        suffix = f" ({', '.join(details)})" if details else ""
        self._write(f"[{step}] {config.slug}: {label} completado{suffix}")
        if progress.phase == "save":
            self._write(f"[{step}] {config.slug}: {_('bloque de persistencia guardado completamente')}")

    def _on_page_complete(
        self,
        page,
        config,
        *,
        resume_store: ScrapeResumeStore,
        page_log_writer: ScrapedPageLogWriter,
        seed_assets: bool,
        download_assets: bool,
        subdivision_levels: tuple[int, ...] | None,
        max_individual_wikidata_lookups: int,
    ) -> None:
        self._write(f"FOUND {page.found} entities: {page.url}")
        self._write_scraped_page_log(page_log_writer, page, cached=False)
        if not seed_assets:
            resume_store.save_page(page)
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
        resume_store.save_page(page)

    def _on_cached_page(self, page, *, page_log_writer: ScrapedPageLogWriter) -> None:
        self._write(f"RESUME {page.found} cached entities: {page.url}")
        self._write_scraped_page_log(page_log_writer, page, cached=True)

    def _write_scraped_page_log(self, writer: ScrapedPageLogWriter, page, *, cached: bool) -> None:
        try:
            writer.write_page(page, cached=cached)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not fail the scrape.
            self._write(
                f"[scraping] No se pudo escribir el log local de pagina para {page.url}: {exc}",
                style=self.style.WARNING,
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

    def _ai_service(self, options) -> AiTextEnrichmentService:
        try:
            provider = OpenAICompatibleJsonProvider.from_env()
        except AiProviderConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        return AiTextEnrichmentService(
            provider,
            languages=_parse_ai_languages(options.get("ai_languages") or ""),
        )


def _ensure_scraping_schema_ready() -> None:
    """Fail before network scraping when the local DB schema is behind models."""
    table_name = AdminArea._meta.db_table
    required_columns = {"raw_entity_type"}
    try:
        pending = _pending_migration_labels()
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
            if table_name not in table_names:
                raise CommandError(
                    "La tabla de AdminArea no existe. Ejecuta 'py manage.py migrate' antes de scrapear."
                )
            column_names = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }
    except CommandError:
        raise
    except (OperationalError, ProgrammingError) as exc:
        raise CommandError(
            "No se pudo comprobar el esquema local antes de scrapear. "
            "Ejecuta 'py manage.py migrate' y vuelve a lanzar el comando."
        ) from exc

    missing_columns = sorted(required_columns - column_names)
    if not pending and not missing_columns:
        return

    details = []
    if missing_columns:
        details.append("faltan columnas en AdminArea: " + ", ".join(missing_columns))
    if pending:
        details.append("migraciones pendientes: " + ", ".join(pending[:5]))
        if len(pending) > 5:
            details.append(f"{len(pending) - 5} migraciones mas pendientes")
    raise CommandError(
        "La base de datos local no esta migrada para el scraper. "
        "Ejecuta 'py manage.py migrate' y vuelve a lanzar el scrapeo. "
        f"Detalle: {'; '.join(details)}."
    )


def _pending_migration_labels() -> list[str]:
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return [
        f"{migration.app_label}.{migration.name}"
        for migration, backwards in executor.migration_plan(targets)
        if not backwards
    ]


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


def _default_country_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_COUNTRY_WORKERS", "1")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _parse_ai_languages(value: str) -> tuple[str, ...]:
    languages = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return languages or DEFAULT_AI_LANGUAGES


def _scrape_run_id() -> str:
    return current_task_id() or time.strftime("cli_%Y%m%d_%H%M%S")


def _write_block_validation_log(slug: str, error: ScrapeBlockValidationError) -> Path:
    return _write_scrape_validation_log(
        slug,
        suffix=f"block_{error.block_index + 1}",
        text=error.to_log_text(),
    )


def _write_link_validation_log(slug: str, error: ScrapeLinkValidationError) -> Path:
    return _write_scrape_validation_log(
        slug,
        suffix="link",
        text=error.to_log_text(),
    )


def _write_scrape_validation_log(slug: str, *, suffix: str, text: str) -> Path:
    cleanup_old_logs()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_slug = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(slug or "config"))
    safe_suffix = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(suffix or "error"))
    log_dir = Path(settings.BASE_DIR) / ".web_scrape_block_errors" / safe_slug
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{safe_slug}_{safe_suffix}_{timestamp}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _persistence_phase_label(phase: str) -> str:
    labels = {
        "normalize": _("normalizando vinculos"),
        "entity_merges": _("aplicando fusiones"),
        "configured_cities": _("aplicando ciudades configuradas"),
        "runtime_extensions": _("aplicando extensiones de configuracion"),
        "wikimedia_parent_links": _("resolviendo padres con Wikimedia"),
        "link_validation": _("validando vinculacion entre bloques"),
        "most_populated_assign": _("asignando ciudad mas poblada"),
        "entity_enrichment": _("enriqueciendo entidades"),
        "reset": _("reiniciando datos existentes"),
        "save": _("guardando filas"),
        "save_retry": _("reintentando guardado"),
        "delete_missing": _("borrando filas ausentes"),
        "most_populated_load": _("leyendo resumenes para ciudad mas poblada"),
        "most_populated_calculate": _("calculando ciudad mas poblada"),
        "most_populated_save": _("guardando ciudad mas poblada"),
        "representatives": _("asignando representantes"),
    }
    return labels.get(str(phase or ""), str(phase or _("fase desconocida")))


def _persistence_step_label(phase: str) -> str:
    link_phases = {
        "normalize",
        "entity_merges",
        "configured_cities",
        "runtime_extensions",
        "wikimedia_parent_links",
        "link_validation",
        "most_populated_assign",
        "entity_enrichment",
    }
    if str(phase or "") in link_phases:
        return "paso 2/5"
    return "paso 5/5"


def _resume_store_for_config(config) -> ScrapeResumeStore:
    record = ScrapingConfig.objects.filter(slug=config.slug).only("content_hash", "country_code").first()
    return ScrapeResumeStore(
        slug=config.slug,
        country_code=getattr(record, "country_code", "") or config.country_code,
        content_hash=getattr(record, "content_hash", "") or "",
    )


def _cached_page_from_store(store: ScrapeResumeStore, page) -> CachedScrapePage | None:
    snapshot = store.load_page(page)
    if not snapshot:
        return None
    return CachedScrapePage(
        found=snapshot.found,
        html=snapshot.html,
        entities=snapshot.entities,
    )
