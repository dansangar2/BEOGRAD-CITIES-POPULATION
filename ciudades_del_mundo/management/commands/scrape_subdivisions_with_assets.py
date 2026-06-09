from __future__ import annotations

import json
import os
import time
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from django.core.management import BaseCommand, CommandError, call_command
from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.services.config_asset_overrides import load_config_asset_overrides
from ciudades_del_mundo.services.visual_assets import (
    visual_asset_tables_exist,
)
from ciudades_del_mundo.web.task_progress import write_config_progress


SCRAPE_ERROR_CODES = {
    "validation": "SCR-VAL-001",
    "data": "SCR-DATA-001",
    "assets_bulk": "SCR-ASSET-001",
    "assets_unassigned": "SCR-ASSET-002",
    "assets_unassigned_warning": "SCR-ASSET-W001",
    "success": "SCR-SUCCESS-001",
    "assets_table": "SCR-ASSET-003",
    "database": "SCR-DB-001",
    "network": "SCR-NET-001",
    "clear": "SCR-CLEAR-001",
    "unknown": "SCR-UNKNOWN",
}


def _coded_message(code: str, message: str) -> str:
    text = str(message or "").strip()
    if text.startswith(str(code)):
        return text
    return f"{code}: {text}" if text else str(code)


def _error_code_for_exception(exc: Exception) -> str:
    text_raw = str(exc or "")
    for code in SCRAPE_ERROR_CODES.values():
        if code in text_raw:
            return code
    text = text_raw.casefold()
    if "quedan" in text and "recursos visuales" in text and "sin asignar" in text:
        return SCRAPE_ERROR_CODES["assets_unassigned"]
    if "tabla de assets visuales" in text:
        return SCRAPE_ERROR_CODES["assets_table"]
    if "búsqueda masiva wikidata" in text or "busqueda masiva wikidata" in text or "consultas bulk" in text:
        return SCRAPE_ERROR_CODES["assets_bulk"]
    if "too many sql variables" in text or "database is locked" in text or "sqlite" in text:
        return SCRAPE_ERROR_CODES["database"]
    if "timed out" in text or "timeout" in text or "http error" in text or "bad gateway" in text:
        return SCRAPE_ERROR_CODES["network"]
    return SCRAPE_ERROR_CODES["unknown"]


class Command(BaseCommand):
    help = "Ejecuta scrape_subdivisions y reutiliza sus paginas para registrar bandera/escudo/sello."

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
            "--clear-first",
            action="store_true",
            help="Limpiar los datos existentes del país antes de volver a popular.",
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
            "--max-individual-wikidata-lookups",
            type=int,
            default=0,
            help="Fallbacks individuales de Wikidata por página. Por defecto 0 para evitar 429.",
        )
        parser.add_argument(
            "--skip-country-bulk-assets",
            action="store_true",
            help="No buscar en bloque recursos administrativos del país en Wikidata.",
        )
        parser.add_argument(
            "--allow-missing-assets",
            action="store_true",
            help="No fallar la tarea si faltan assets requeridos por la configuración.",
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
        try:
            config = PythonScrapingConfigRepository().get(slug)
            country_code = str(config.country_code or slug)
            page_workers = max(1, int(options.get("page_workers") or 1))
            ai_limit = max(1, int(options.get("ai_limit") or 1))
            config_data = _config_data_for_slug(slug)
            asset_settings = _visual_asset_settings(config_data)
            warning_detail = ""

            if options.get("clear_first"):
                write_config_progress(slug, "clearing", detail="Limpiando datos anteriores")
                self._write(f"[limpiar] {slug}: limpiando datos existentes antes de re-popular...")
                self._run_with_sqlite_retry(lambda: call_command("clear_config_data_with_assets", country_code))
            write_config_progress(slug, "populating", detail="Scrapeando datos")

            # Fase 1: popular datos. Los assets se buscan despues para mantener
            # separado el scrapeo de datos de la busqueda de banderas/escudos.
            self._write(f"[popular] {slug}: fase 1/2, scrapeando datos...")
            self._run_with_sqlite_retry(
                lambda: call_command(
                    "scrape_subdivisions",
                    slug,
                    page_workers=page_workers,
                    resume=bool(options.get("resume")),
                    # Importante: en este comando los assets NO se siembran durante
                    # el scraping de páginas. Primero se persisten las entidades de
                    # CityPopulation con su data_wd y después una segunda fase resuelve
                    # banderas/escudos en Wikidata usando esos QIDs en lotes pequeños.
                    seed_assets_from_pages=False,
                    no_download_assets=bool(options.get("no_download_assets")),
                    download_assets=False,
                    skip_subdivision_assets=True,
                    asset_subdivision_levels="",
                    max_individual_wikidata_lookups=0,
                    ai_enrich=bool(options.get("ai_enrich")),
                    ai_languages=options.get("ai_languages") or "",
                    ai_translate_area_names=bool(options.get("ai_translate_area_names")),
                    ai_limit=ai_limit,
                )
            )

            # Tras importar datos, intenta rescatar de CityPopulation el Wikidata ID
            # de la entidad raíz del país/territorio. La fase de assets sigue
            # separada y se ejecuta después, pero usa este QID como raíz cuando
            # CityPopulation lo expone.
            citypopulation_country_qid = _citypopulation_country_wikidata_id(
                config,
                slug=slug,
                country_code=country_code,
                config_data=config_data,
                logger=self._write,
            )

            if options.get("skip_assets"):
                self._write("[assets] omitido por --skip-assets")
                write_config_progress(
                    slug,
                    "populated",
                    detail=_coded_message(SCRAPE_ERROR_CODES["success"], "Scraping completado correctamente."),
                )
                return
            if not visual_asset_tables_exist():
                raise CommandError(_coded_message(SCRAPE_ERROR_CODES["assets_table"], "La tabla de assets visuales no existe. Ejecuta migraciones."))

            # Fase 2: buscar y registrar banderas/escudos por data_wd.
            # No se usa una consulta SPARQL gigante por país: se toman los QIDs ya
            # scrapeados desde CityPopulation y se resuelven por lotes con wbgetentities.
            write_config_progress(slug, "populating", detail="Buscando banderas y escudos por data_wd")
            self._write(f"[assets] {slug}: fase 2/2, resolviendo Wikidata por data_wd...")
            self._run_with_sqlite_retry(
                lambda: _assign_wikidata_assets_from_scraped_data_wd(
                    slug,
                    country_code,
                    config_data=config_data,
                    country_wikidata_id=citypopulation_country_qid,
                    required_kinds=asset_settings["required_kinds"],
                    levels=[] if options.get("skip_subdivision_assets") else _levels_from_cli_or_config(
                        options.get("subdivision_asset_levels") or "",
                        asset_settings["required_levels"],
                    ),
                    include_subdivisions=not bool(options.get("skip_subdivision_assets")),
                    logger=self._write,
                )
            )
            self._run_with_sqlite_retry(
                lambda: _mirror_country_assets_to_root_admin_areas(
                    country_code,
                    logger=self._write,
                )
            )

            if asset_settings.get("legacy_country_bulk_wikidata") and not options.get("skip_country_bulk_assets"):
                self._write(f"[assets] {slug}: fallback legado de búsqueda masiva Wikidata activado por configuración...")
                self._run_with_sqlite_retry(
                    lambda: _assign_bulk_country_wikidata_assets(
                        slug,
                        country_code,
                        config_data=config_data,
                        country_wikidata_id=citypopulation_country_qid,
                        required_kinds=asset_settings["required_kinds"],
                        levels=_levels_from_cli_or_config(
                            options.get("subdivision_asset_levels") or "",
                            asset_settings["required_levels"],
                        ),
                        logger=self._write,
                    )
                )

            self._run_with_sqlite_retry(
                lambda: _apply_configured_wikidata_asset_overrides(
                    slug,
                    country_code,
                    config_data=config_data,
                    logger=self._write,
                )
            )

            missing_required_assets = []
            if asset_settings["strict_required"] and not options.get("allow_missing_assets"):
                missing_required_assets = self._run_with_sqlite_retry(
                    lambda: _missing_required_visual_assets(
                        country_code,
                        kinds=asset_settings["required_kinds"],
                        levels=asset_settings["required_levels"],
                    )
                )
                if missing_required_assets:
                    warning_detail = _coded_message(
                        SCRAPE_ERROR_CODES["assets_unassigned_warning"],
                        _missing_asset_warning_message(missing_required_assets),
                    )
                    self._write(f"[assets][warning] {warning_detail}")
                    write_config_progress(slug, "populated", detail=warning_detail)

            if options.get("ai_enrich"):
                self._write(f"[ai] {country_code}: describiendo assets visuales...")
                self._run_with_sqlite_retry(
                    lambda: call_command(
                        "enrich_ai_texts",
                        country_code,
                        languages=options.get("ai_languages") or "",
                        limit=ai_limit,
                        describe_assets=True,
                    )
                )
        except Exception as exc:
            code = _error_code_for_exception(exc)
            coded_detail = _coded_message(code, str(exc))
            write_config_progress(slug, "failed", detail=coded_detail)
            if isinstance(exc, CommandError) and not str(exc).startswith(code):
                raise CommandError(coded_detail) from exc
            raise
        if warning_detail:
            write_config_progress(slug, "populated", detail=warning_detail)
        else:
            write_config_progress(slug, "populated", detail=_coded_message(SCRAPE_ERROR_CODES["success"], "Scraping completado correctamente."))


def _apply_configured_wikidata_asset_overrides(slug: str, country_code: str, *, config_data: dict | None = None, logger=None) -> int:
    """Apply TOML-defined Wikidata fallbacks for visual assets.

    This is meant for cases where CityPopulation does not expose the right
    Wikidata entity for a subdivision, for example a scraped row named
    "El Djazir" whose flag/coat live in Wikidata under the Algiers item.
    """
    overrides = _wikidata_asset_overrides_from_config(slug, config_data=config_data)
    if not overrides:
        return 0
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return 0

    applied = 0
    for index, override in enumerate(overrides, start=1):
        qid = _normalize_wikidata_id(
            override.get("wikidata_id")
            or override.get("qid")
            or override.get("wikidata")
        )
        if not qid:
            _log_asset_mirror(logger, f"[assets] override #{index} sin wikidata_id; omitido")
            continue

        areas = _admin_areas_for_asset_override(AdminArea, country_code, override)
        if not areas:
            target = override.get("code") or override.get("codes") or override.get("name") or override.get("names") or qid
            _log_asset_mirror(logger, f"[assets] override {qid}: no se encontró AdminArea para {target!r}")
            continue

        try:
            filenames = _wikidata_visual_asset_filenames(qid)
        except Exception as exc:
            _log_asset_mirror(logger, f"[assets] override {qid}: no se pudo leer Wikidata ({exc})")
            continue

        kinds = _override_kinds(override)
        if "replace_existing" in override:
            replace_existing = _config_bool(override.get("replace_existing"), default=True)
        elif "force" in override:
            replace_existing = _config_bool(override.get("force"), default=True)
        else:
            replace_existing = True
        for area in areas:
            area_count = 0
            for kind in kinds:
                filename = filenames.get(kind)
                if not filename:
                    continue
                area_count += _upsert_wikidata_override_asset(
                    area,
                    kind=kind,
                    wikidata_id=qid,
                    commons_filename=filename,
                    replace_existing=replace_existing,
                )
            applied += area_count
            _log_asset_mirror(
                logger,
                f"[assets] override {qid}: {area.name} ({area.id}) -> {area_count} assets",
            )
    if applied:
        _log_asset_mirror(logger, f"[assets] overrides Wikidata aplicados: {applied}")
    return applied


def _wikidata_asset_overrides_from_config(slug: str, *, config_data: dict | None = None) -> list[dict]:
    data = config_data if isinstance(config_data, dict) else _config_data_for_slug(slug)
    raw = (
        data.get("wikidata_asset_overrides")
        or data.get("visual_asset_overrides")
        or data.get("asset_overrides")
        or []
    )
    if isinstance(raw, dict):
        overrides = [raw]
    else:
        overrides = [item for item in raw if isinstance(item, dict)]

    # Corrections saved through /configs/{country}/ are persisted in SQL so they
    # survive refreshes and can be applied even if the TOML file is deleted.
    for row in load_config_asset_overrides(slug):
        entity_id = str(row.get("entity_id") or "").strip()
        level = row.get("level")
        for kind, qid_key in (("flag", "flag_qid"), ("coat", "coat_qid")):
            qid = str(row.get(qid_key) or "").strip()
            if not entity_id or not qid:
                continue
            override = {"ids": [entity_id], "wikidata_id": qid, "kinds": [kind]}
            if str(level or "").strip() != "":
                override["level"] = level
            overrides.append(override)
    return overrides


def _config_data_for_slug(slug: str) -> dict:
    slug = str(slug or "").strip()
    if not slug:
        return {}
    try:
        from ciudades_del_mundo.models import ScrapingConfig

        record = ScrapingConfig.objects.filter(slug=slug).first()
        if record and str(record.content or "").strip():
            return tomllib.loads(record.content)
    except Exception:
        pass

    config_path = Path(__file__).resolve().parents[2] / "subdivisions" / f"{slug}.toml"
    if not config_path.exists():
        return {}
    try:
        with config_path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def _visual_asset_settings(config_data: dict) -> dict:
    raw = config_data.get("visual_assets") if isinstance(config_data.get("visual_assets"), dict) else {}
    required_kinds = [kind for kind in _config_list(raw.get("required_kinds") or ["flag", "coat"]) if kind in WIKIDATA_ASSET_PROPERTIES]
    required_levels = []
    for value in _config_list(raw.get("required_levels")):
        try:
            required_levels.append(int(value))
        except (TypeError, ValueError):
            continue
    return {
        # Por defecto ya no se usa el SPARQL masivo por país: los assets se
        # asignan por data_wd con consultas wbgetentities por lotes. El fallback
        # legado queda disponible solo si se activa explícitamente en TOML.
        "bulk_country_wikidata": _config_bool(raw.get("bulk_country_wikidata"), default=True),
        "legacy_country_bulk_wikidata": _config_bool(raw.get("legacy_country_bulk_wikidata"), default=False),
        "strict_required": _config_bool(raw.get("strict_required"), default=True),
        "required_kinds": required_kinds or ["flag", "coat"],
        "required_levels": required_levels,
    }


def _config_list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("\n", ",").split(",")
    return [str(item or "").strip().lower() for item in raw_values if str(item or "").strip()]


def _config_bool(value, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _levels_from_cli_or_config(raw_levels: str, config_levels: list[int]) -> list[int]:
    levels = []
    for value in _config_list(raw_levels):
        try:
            levels.append(int(value))
        except (TypeError, ValueError):
            continue
    return levels or list(config_levels or [])


def _asset_subdivision_levels_for_scrape(raw_levels: str, config_levels: list[int]) -> str:
    """Return the level filter expected by scrape_subdivisions page-asset seeding.

    Empty string intentionally means all scraped subdivision levels. When the
    config declares required_levels, reuse them so page-derived assets and the
    final strict check operate on the same level scope.
    """
    levels = _levels_from_cli_or_config(raw_levels, config_levels)
    return ",".join(str(level) for level in levels) if levels else ""




def _citypopulation_country_wikidata_id(config, *, slug: str, country_code: str, config_data: dict, logger=None) -> str:
    """Try to read the country/territory Wikidata QID from CityPopulation HTML.

    Data scraping remains phase 1. This helper runs after that phase and before
    Wikidata bulk assets so the asset search can use the exact country root that
    CityPopulation associates with the page, instead of relying only on TOML or
    a later Wikidata name search.
    """
    stored_qid = _country_root_data_wd(country_code)
    if stored_qid:
        _log_asset_mirror(logger, f"[assets] {country_code}: Wikidata ID leído del campo data_wd={stored_qid}")
        return stored_qid

    expected_names = _citypopulation_country_expected_names(slug, country_code, config_data)
    for url in _citypopulation_country_qid_candidate_urls(config, config_data, slug=slug):
        try:
            html = _fetch_citypopulation_html(url)
        except Exception as exc:
            _log_asset_mirror(logger, f"[assets] {country_code}: no se pudo leer CityPopulation para Wikidata ID ({url}: {exc})")
            continue
        qid = _country_wikidata_id_from_citypopulation_html(html, expected_names)
        if qid:
            _log_asset_mirror(logger, f"[assets] {country_code}: Wikidata ID leído de CityPopulation={qid} ({url})")
            return qid
    _log_asset_mirror(logger, f"[assets] {country_code}: CityPopulation no expuso Wikidata ID de país; se usará fallback")
    return ""


def _citypopulation_country_expected_names(slug: str, country_code: str, config_data: dict) -> set[str]:
    values = {
        str(slug or ""),
        str(country_code or ""),
        str(country_code or "").replace("_", " ").replace("-", " "),
        str(config_data.get("name") or ""),
        str(config_data.get("country_name") or ""),
        str(config_data.get("label") or ""),
    }
    return {_normalized_text(value) for value in values if _normalized_text(value)}


def _citypopulation_country_qid_candidate_urls(config, config_data: dict, *, slug: str) -> list[str]:
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if not base_url:
        return []

    candidates: list[tuple[int, str]] = []

    def add(priority: int, path: str = "") -> None:
        normalized_path = _citypopulation_path_for_slug(slug, path, base_url=base_url)
        url = _citypopulation_join_url(base_url, normalized_path)
        if url:
            candidates.append((priority, url))

    # The country root page is the safest source for the country/territory QID.
    # Repository configs use a generic base URL, so the root path must include
    # the slug (for example /en/cuba/), not just /en/.
    add(10, "")

    for index, page in enumerate(config_data.get("pages") or []):
        if not isinstance(page, dict):
            continue
        source = str(page.get("source") or "").strip().casefold()
        lowest_level = page.get("lowest_level")
        try:
            lowest_level_int = int(lowest_level)
        except (TypeError, ValueError):
            lowest_level_int = 999
        priority = 50 + index
        if source == "infosection":
            priority = 20 + index
        elif lowest_level_int == 0:
            priority = 30 + index
        for path in _citypopulation_page_paths(page.get("path")):
            add(priority, path)

    # Some repository implementations have the expanded page objects. These
    # paths are already normalized with the slug by parse_pages(), so the helper
    # below leaves them unchanged.
    for index, page in enumerate(getattr(config, "pages", []) or []):
        path = str(getattr(page, "path", "") or "").strip()
        source = str(getattr(page, "html_format", "") or "").strip().casefold()
        try:
            lowest_level_int = int(getattr(page, "lowest_level", 999))
        except (TypeError, ValueError):
            lowest_level_int = 999
        priority = 80 + index
        if source == "infosection":
            priority = 25 + index
        elif lowest_level_int == 0:
            priority = 35 + index
        add(priority, path)

    urls: list[str] = []
    seen = set()
    for _, url in sorted(candidates, key=lambda item: item[0]):
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls[:12]


def _citypopulation_path_for_slug(slug: str, path: str = "", *, base_url: str = "") -> str:
    slug = str(slug or "").strip("/")
    path = str(path or "").strip("/")
    if path.startswith(("http://", "https://")):
        return path
    if not slug:
        return path

    base_tail = str(base_url or "").rstrip("/").rsplit("/", 1)[-1].casefold()
    base_already_points_to_slug = bool(base_tail and base_tail == slug.casefold())
    if base_already_points_to_slug:
        if not path or path == slug:
            return ""
        if path.startswith(f"{slug}/"):
            return path.split("/", 1)[1]
        return path

    if not path:
        return slug
    if path == slug or path.startswith(f"{slug}/"):
        return path
    return f"{slug}/{path}"

def _citypopulation_page_paths(raw_path) -> list[str]:
    if raw_path is None or raw_path == "":
        return [""]
    if isinstance(raw_path, (list, tuple, set)):
        return [str(item or "").strip() for item in raw_path if str(item or "").strip()]
    return [str(raw_path or "").strip()]


def _citypopulation_join_url(base_url: str, path: str = "") -> str:
    base_url = str(base_url or "").strip()
    path = str(path or "").strip()
    if not base_url:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    url = base_url.rstrip("/")
    if path:
        url = f"{url}/{path.strip('/')}"
    return f"{url}/"


def _fetch_citypopulation_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 citypopulation-country-qid",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _country_wikidata_id_from_citypopulation_html(html: str, expected_names: set[str]) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[tuple[int, str, str]] = []
    for element in soup.select("[data-wd]"):
        qid = _normalize_wikidata_id(element.get("data-wd"))
        if not qid:
            continue
        text = element.get_text(" ", strip=True)
        data_wiki = str(element.get("data-wiki") or "")
        name_score = max(
            _country_name_match_score(text, expected_names),
            _country_name_match_score(data_wiki, expected_names),
        )
        # Never accept a QID as the country root only because it appears in a
        # table footer/infosection/header. On pages like Malta, a child row can
        # be structurally prominent and still be Gozo, not the country.
        if not name_score:
            continue

        score = name_score
        if element.find_parent("tfoot"):
            score += 40
        if element.find_parent(class_="infosection"):
            score += 40
        if element.find_parent("header") or element.find_parent("h1"):
            score += 20
        itemtype = " ".join(
            str(node.get("itemtype") or "")
            for node in [element, *element.find_parents(attrs={"itemtype": True})]
        ).casefold()
        if "country" in itemtype:
            score += 30
        candidates.append((score, qid, text or data_wiki))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return ""


def _country_name_match_score(value, expected_names: set[str]) -> int:
    normalized = _normalized_text(value)
    if not normalized or not expected_names:
        return 0
    if normalized in expected_names:
        return 140

    compact = normalized.replace("-", " ")
    if compact in expected_names:
        return 130

    for expected in expected_names:
        if not expected:
            continue
        if compact == expected:
            return 130
        if compact in {f"republic of {expected}", f"state of {expected}", f"kingdom of {expected}"}:
            return 125
        suffixes = (
            " republic",
            " country",
            " state",
            " kingdom",
            " nation",
            " territory",
            " federation",
        )
        if any(compact == f"{expected}{suffix}" for suffix in suffixes):
            return 120
        # Headings often read like "Cuba : Administrative Division". Accept
        # only when the country name is the leading token, never when it appears
        # in an arbitrary child label.
        if compact.startswith(f"{expected} ") or compact.startswith(f"{expected}:"):
            return 95
    return 0

def _assign_bulk_country_wikidata_assets(
    slug: str,
    country_code: str,
    *,
    config_data: dict | None = None,
    country_wikidata_id: str = "",
    required_kinds: list[str] | None = None,
    levels: list[int] | None = None,
    logger=None,
) -> int:
    """Fetch administrative visual assets for the whole country and match them locally.

    This reduces one-by-one Wikidata searches. The pass downloads a single SPARQL
    result set with country administrative entities that already expose flag/coat/
    seal files and then assigns candidates whose labels match scraped AdminArea
    names/codes. Manual [[wikidata_asset_overrides]] still runs afterwards for
    cases where names do not match, such as El Djazir -> Algiers.
    """
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return 0

    data = config_data if isinstance(config_data, dict) else _config_data_for_slug(slug)
    country_qid = _country_wikidata_id_for_bulk(
        country_code,
        data,
        citypopulation_qid=country_wikidata_id,
    )
    if not country_qid:
        _log_asset_mirror(logger, f"[assets] {country_code}: sin wikidata_id de país; búsqueda masiva omitida")
        return 0
    _log_asset_mirror(logger, f"[assets] {country_code}: raíz Wikidata para búsqueda masiva={country_qid}")

    queryset = AdminArea.objects.filter(country_code=country_code).exclude(city_merge_status=3)
    level_filter = [int(level) for level in (levels or []) if str(level).strip() != ""]
    if level_filter:
        queryset = queryset.filter(level__in=level_filter)
    areas = list(queryset.order_by("level", "name", "id"))
    if not areas:
        return 0

    area_index: dict[str, list] = {}
    area_wikidata_index: dict[str, list] = {}
    for area in areas:
        for key in _area_asset_match_keys(area):
            area_index.setdefault(key, []).append(area)
        qid = _normalize_wikidata_id(getattr(area, "data_wd", ""))
        if qid and int(getattr(area, "level", -1) or -1) != 0:
            area_wikidata_index.setdefault(qid, []).append(area)

    kind_filter = {kind for kind in (required_kinds or []) if kind in WIKIDATA_ASSET_PROPERTIES}
    if not kind_filter:
        kind_filter = {"flag", "coat"}

    applied = _upsert_country_root_assets_from_wikidata(
        country_code=country_code,
        country_qid=country_qid,
        kinds=sorted(kind_filter),
        logger=logger,
    )

    try:
        candidates = _wikidata_country_asset_candidates(country_qid, kinds=sorted(kind_filter), logger=logger)
    except Exception as exc:
        # Wikidata Query Service can return intermittent 504/timeout responses for
        # country-wide scans. The data scrape has already succeeded, and the
        # national assets above are read through the lighter EntityData endpoint,
        # so a bulk outage must not fail the whole population command.
        _log_asset_mirror(logger, f"[assets] {country_code}: búsqueda masiva Wikidata omitida por error temporal ({exc})")
        return applied

    stored_resources = 0
    matched_candidates = 0

    for candidate in candidates:
        filenames = candidate.get("filenames") or {}
        candidate_qid = str(candidate.get("wikidata_id") or "")
        label = str(candidate.get("label") or candidate_qid)
        if not candidate_qid or not filenames:
            continue

        matches = []
        if candidate_qid != country_qid:
            matches = _candidate_matching_admin_areas_by_wikidata_id(candidate_qid, area_wikidata_index)
            if not matches:
                matches = _candidate_matching_admin_areas(candidate, area_index)
            if not matches:
                continue

        for kind, filename in filenames.items():
            if kind not in kind_filter:
                continue
            stored_resources += _upsert_visual_asset_row(
                entity_type="wikidata_country_resource",
                entity_key=f"{country_code}:{candidate_qid}:{kind}",
                entity_name=label,
                country_code=country_code,
                kind=kind,
                wikidata_id=candidate_qid,
                commons_filename=filename,
                replace_existing=True,
                source="wikidata_country_bulk_resource",
            )

        if candidate_qid == country_qid:
            for kind, filename in filenames.items():
                if kind not in kind_filter:
                    continue
                applied += _upsert_visual_asset_row(
                    entity_type="country",
                    entity_key=country_code,
                    entity_name=label,
                    country_code=country_code,
                    kind=kind,
                    wikidata_id=candidate_qid,
                    commons_filename=filename,
                    replace_existing=True,
                    source="wikidata_country_bulk",
                )
            matched_candidates += 1
            continue

        matched_candidates += 1
        for area in matches:
            for kind, filename in filenames.items():
                if kind not in kind_filter:
                    continue
                applied += _upsert_wikidata_override_asset(
                    area,
                    kind=kind,
                    wikidata_id=candidate_qid,
                    commons_filename=filename,
                    replace_existing=True,
                    source="wikidata_country_bulk",
                )
    _log_asset_mirror(
        logger,
        f"[assets] {country_code}: búsqueda masiva Wikidata, candidatos={len(candidates)}, "
        f"recursos relevantes guardados={stored_resources}, candidatos asignados={matched_candidates}, "
        f"assets nuevos/actualizados={applied}",
    )
    return applied


def _country_wikidata_id_for_bulk(country_code: str, config_data: dict, *, citypopulation_qid: str = "") -> str:
    qid = _normalize_wikidata_id(citypopulation_qid)
    if qid:
        return qid

    qid = _country_root_data_wd(country_code)
    if qid:
        return qid

    qid = _normalize_wikidata_id(config_data.get("wikidata_id") or config_data.get("wikidata"))
    if qid:
        return qid
    row = _select_one(
        f"""
            SELECT wikidata_id
            FROM {VISUAL_ASSET_TABLE}
            WHERE entity_type = %s AND entity_key = %s AND wikidata_id <> ''
            LIMIT 1
        """,
        ["country", country_code],
    )
    qid = _normalize_wikidata_id(row.get("wikidata_id") if row else "")
    if qid:
        return qid

    for query in _country_wikidata_search_queries(country_code, config_data):
        qid = _wikidata_country_id_from_search(query)
        if qid:
            return qid
    return ""


def _country_root_data_wd(country_code: str) -> str:
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return ""
    try:
        row = (
            AdminArea.objects.filter(country_code=country_code, level=0)
            .exclude(data_wd="")
            .order_by("parent_id", "id")
            .values("data_wd")
            .first()
        )
    except Exception:  # noqa: BLE001 - migrations may not have run yet.
        return ""
    return _normalize_wikidata_id(row.get("data_wd") if row else "")


def _candidate_matching_admin_areas_by_wikidata_id(candidate_qid: str, area_wikidata_index: dict[str, list]) -> list:
    qid = _normalize_wikidata_id(candidate_qid)
    if not qid:
        return []
    matches = list(area_wikidata_index.get(qid, []) or [])
    # Exact data-wd matches are safe. If duplicates exist, assign the same media
    # to each duplicate local row because they intentionally point to one WD item.
    return matches


WIKIDATA_ENTITYDATA_BATCH_LIMIT = 50
WIKIDATA_VALUES_SPARQL_BATCH_LIMIT = 1000



def _assign_wikidata_assets_from_scraped_data_wd(
    slug: str,
    country_code: str,
    *,
    config_data: dict | None = None,
    country_wikidata_id: str = "",
    required_kinds: list[str] | None = None,
    levels: list[int] | None = None,
    include_subdivisions: bool = True,
    logger=None,
) -> int:
    """Assign visual assets using the data_wd stored on AdminArea rows.

    The sequence is intentionally two-phase:
    1. CityPopulation has already populated AdminArea rows and their data_wd.
    2. This function resolves those concrete QIDs with one/few Wikidata SPARQL
       VALUES queries, asking only for the requested image properties.

    That avoids country-wide scans, avoids name matching and also avoids dozens
    of wbgetentities calls that can trigger HTTP 429. A row only receives a
    flag/coat/seal when its own data_wd exposes the corresponding Wikidata
    image property.
    """
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return 0

    data = config_data if isinstance(config_data, dict) else _config_data_for_slug(slug)
    kind_filter = [kind for kind in (required_kinds or []) if kind in WIKIDATA_ASSET_PROPERTIES]
    if not kind_filter:
        kind_filter = ["flag", "coat"]

    country_qid = _country_wikidata_id_for_bulk(
        country_code,
        data,
        citypopulation_qid=country_wikidata_id,
    )

    applied = 0
    if country_qid:
        applied += _upsert_country_root_assets_from_wikidata(
            country_code=country_code,
            country_qid=country_qid,
            kinds=kind_filter,
            logger=logger,
        )
    else:
        _log_asset_mirror(logger, f"[assets] {country_code}: sin data_wd de país; assets nacionales omitidos")

    if not include_subdivisions:
        _log_asset_mirror(logger, f"[assets] {country_code}: subdivisiones omitidas por --skip-subdivision-assets")
        return applied

    queryset = (
        AdminArea.objects.filter(country_code=country_code)
        .exclude(city_merge_status=3)
        .exclude(data_wd="")
        .exclude(level=0)
    )
    level_filter = [int(level) for level in (levels or []) if str(level).strip() != ""]
    if level_filter:
        queryset = queryset.filter(level__in=level_filter)

    areas = list(queryset.order_by("level", "name", "id"))
    qid_to_areas: dict[str, list] = {}
    for area in areas:
        qid = _normalize_wikidata_id(getattr(area, "data_wd", ""))
        if qid:
            qid_to_areas.setdefault(qid, []).append(area)

    if not qid_to_areas:
        _log_asset_mirror(logger, f"[assets] {country_code}: no hay subdivisiones con data_wd para asignar assets")
        return applied

    filenames_by_qid = _wikidata_visual_asset_filenames_for_ids(
        list(qid_to_areas.keys()),
        kinds=kind_filter,
        logger=logger,
    )

    qids_with_assets = 0
    area_assignments = 0
    for qid, target_areas in qid_to_areas.items():
        filenames = filenames_by_qid.get(qid, {})
        if not filenames:
            continue
        qids_with_assets += 1
        for area in target_areas:
            for kind, filename in filenames.items():
                if kind not in kind_filter or not filename:
                    continue
                applied += _upsert_wikidata_override_asset(
                    area,
                    kind=kind,
                    wikidata_id=qid,
                    commons_filename=filename,
                    replace_existing=True,
                    source="wikidata_data_wd",
                )
                area_assignments += 1

    _log_asset_mirror(
        logger,
        f"[assets] {country_code}: data_wd procesados={len(qid_to_areas)}, "
        f"qids con recursos={qids_with_assets}, asignaciones={area_assignments}, "
        f"assets nuevos/actualizados={applied}",
    )
    return applied


def _wikidata_visual_asset_filenames_for_ids(
    wikidata_ids: list[str],
    *,
    kinds: list[str],
    logger=None,
) -> dict[str, dict[str, str]]:
    """Read image claims for concrete QIDs with a small number of SPARQL calls.

    The previous implementation used wbgetentities in batches of 50. That is
    correct but too chatty for countries with many rows: Morocco generated 33
    HTTP calls and Wikidata started returning 429. Here we already know the
    exact QIDs from CityPopulation's data-wd, so the cheapest safe strategy is
    a VALUES query over those QIDs asking only for P41/P94/P158/P242.
    """
    requested_kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not requested_kinds:
        requested_kinds = ["flag", "coat"]

    qids: list[str] = []
    seen = set()
    for value in wikidata_ids:
        qid = _normalize_wikidata_id(value)
        if qid and qid not in seen:
            qids.append(qid)
            seen.add(qid)

    if not qids:
        return {}

    try:
        results = _wikidata_visual_asset_filenames_for_ids_via_values_sparql(
            qids,
            kinds=requested_kinds,
            logger=logger,
        )
        _log_asset_mirror(
            logger,
            f"[assets] Wikidata VALUES: data_wd={len(qids)}, "
            f"qids con recursos={len(results)}",
        )
        return results
    except Exception as exc:
        if len(qids) > 250:
            _log_asset_mirror(
                logger,
                f"[assets] Wikidata VALUES fallido ({exc}); fallback EntityData omitido "
                f"para evitar 429 con {len(qids)} data_wd",
            )
            return {}
        _log_asset_mirror(
            logger,
            f"[assets] Wikidata VALUES fallido ({exc}); fallback EntityData por lotes",
        )

    return _wikidata_visual_asset_filenames_for_ids_via_entitydata(
        qids,
        kinds=requested_kinds,
        logger=logger,
    )


def _wikidata_visual_asset_filenames_for_ids_via_values_sparql(
    wikidata_ids: list[str],
    *,
    kinds: list[str],
    logger=None,
) -> dict[str, dict[str, str]]:
    """Resolve flag/coat/seal for known QIDs through SPARQL VALUES chunks."""
    requested_kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not requested_kinds:
        requested_kinds = ["flag", "coat"]

    qids: list[str] = []
    seen = set()
    for value in wikidata_ids:
        qid = _normalize_wikidata_id(value)
        if qid and qid not in seen:
            qids.append(qid)
            seen.add(qid)

    results: dict[str, dict[str, str]] = {}
    if not qids:
        return results

    for start in range(0, len(qids), WIKIDATA_VALUES_SPARQL_BATCH_LIMIT):
        batch = qids[start : start + WIKIDATA_VALUES_SPARQL_BATCH_LIMIT]
        query = _wikidata_values_visual_assets_query(batch, requested_kinds)
        payload = _wikidata_sparql_json(query, timeout=60, method="POST")
        rows = payload.get("results", {}).get("bindings", []) or []

        for binding in rows:
            item_url = binding.get("item", {}).get("value", "")
            qid = _normalize_wikidata_id(item_url.rsplit("/", 1)[-1])
            kind = str(binding.get("kind", {}).get("value") or "").strip()
            filename = _commons_filename_from_sparql_value(binding.get("asset", {}).get("value"))
            if not qid or kind not in requested_kinds or not filename:
                continue
            results.setdefault(qid, {}).setdefault(kind, filename)

        _log_asset_mirror(
            logger,
            f"[assets] Wikidata VALUES lote {start // WIKIDATA_VALUES_SPARQL_BATCH_LIMIT + 1}: "
            f"qids={len(batch)}, filas={len(rows)}, con recursos={sum(1 for qid in batch if qid in results)}",
        )

    return results


def _wikidata_values_visual_assets_query(wikidata_ids: list[str], kinds: list[str]) -> str:
    qids = [qid for qid in (_normalize_wikidata_id(value) for value in wikidata_ids) if qid]
    requested_kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not qids or not requested_kinds:
        return "SELECT ?item ?kind ?asset WHERE {} LIMIT 0"

    values = " ".join(f"wd:{qid}" for qid in qids)
    branches = []
    for kind in requested_kinds:
        property_id = WIKIDATA_ASSET_PROPERTIES[kind]
        branches.append(
            "{ "
            f"?item wdt:{property_id} ?asset . "
            f'BIND("{kind}" AS ?kind) '
            "}"
        )
    union = "\n  UNION\n  ".join(branches)
    return f"""
SELECT ?item ?kind ?asset WHERE {{
  VALUES ?item {{ {values} }}
  {union}
}}
""".strip()


def _wikidata_visual_asset_filenames_for_ids_via_entitydata(
    wikidata_ids: list[str],
    *,
    kinds: list[str],
    logger=None,
) -> dict[str, dict[str, str]]:
    """Fallback: read image claims for concrete QIDs through wbgetentities batches."""
    requested_kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not requested_kinds:
        requested_kinds = ["flag", "coat"]
    property_by_kind = {kind: WIKIDATA_ASSET_PROPERTIES[kind] for kind in requested_kinds}

    qids: list[str] = []
    seen = set()
    for value in wikidata_ids:
        qid = _normalize_wikidata_id(value)
        if qid and qid not in seen:
            qids.append(qid)
            seen.add(qid)

    results: dict[str, dict[str, str]] = {}
    if not qids:
        return results

    for start in range(0, len(qids), WIKIDATA_ENTITYDATA_BATCH_LIMIT):
        batch = qids[start : start + WIKIDATA_ENTITYDATA_BATCH_LIMIT]
        try:
            payload = _wikidata_entitydata_batch_json(batch)
        except Exception as exc:
            _log_asset_mirror(
                logger,
                f"[assets] Wikidata EntityData lote {start // WIKIDATA_ENTITYDATA_BATCH_LIMIT + 1}: fallido ({exc})",
            )
            time.sleep(1.25)
            continue

        entities = payload.get("entities", {}) or {}
        for qid in batch:
            claims = entities.get(qid, {}).get("claims", {}) or {}
            filenames: dict[str, str] = {}
            for kind, property_id in property_by_kind.items():
                for claim in claims.get(property_id, []) or []:
                    value = (
                        claim.get("mainsnak", {})
                        .get("datavalue", {})
                        .get("value")
                    )
                    if value:
                        filenames[kind] = str(value)
                        break
            if filenames:
                results[qid] = filenames
        _log_asset_mirror(
            logger,
            f"[assets] Wikidata EntityData lote {start // WIKIDATA_ENTITYDATA_BATCH_LIMIT + 1}: "
            f"qids={len(batch)}, con recursos={sum(1 for qid in batch if qid in results)}",
        )
        time.sleep(0.35)

    return results


def _wikidata_entitydata_batch_json(wikidata_ids: list[str]) -> dict:
    ids = [qid for qid in (_normalize_wikidata_id(value) for value in wikidata_ids) if qid]
    if not ids:
        return {"entities": {}}
    url = "https://www.wikidata.org/w/api.php?" + urlencode(
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(ids),
            "props": "claims",
        }
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 data-wd-visual-assets",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _country_wikidata_search_queries(country_code: str, config_data: dict) -> list[str]:
    values = [
        config_data.get("name"),
        config_data.get("country_name"),
        config_data.get("label"),
        str(country_code or "").replace("_", " ").replace("-", " "),
        country_code,
    ]
    queries: list[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _normalized_text(text)
        if key and key not in seen:
            queries.append(text)
            seen.add(key)
    return queries


def _wikidata_country_id_from_search(query: str) -> str:
    query = str(query or "").strip()
    if not query:
        return ""
    url = "https://www.wikidata.org/w/api.php?" + urlencode(
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": "en",
            "limit": "8",
            "search": query,
        }
    )
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 country-wikidata-search",
            },
        )
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ""

    rows = payload.get("search", []) or []
    expected = _normalized_text(query)
    first_qid = ""
    for row in rows:
        qid = _normalize_wikidata_id(row.get("id"))
        if not qid:
            continue
        if not first_qid:
            first_qid = qid
        labels = [
            row.get("label"),
            row.get("description"),
            *(row.get("aliases") or []),
        ]
        if any(_normalized_text(label) == expected for label in labels if label):
            return qid
    return first_qid


def _upsert_country_root_assets_from_wikidata(
    *,
    country_code: str,
    country_qid: str,
    kinds: list[str],
    logger=None,
) -> int:
    """Persist the national flag/coat/seal through Wikidata EntityData.

    This endpoint is much lighter than the country-wide SPARQL scan. It gives
    the country card and the level-0 AdminArea mirror useful assets even when
    Wikidata Query Service is temporarily returning 504 for the bulk query.
    """
    country_qid = _normalize_wikidata_id(country_qid)
    if not country_qid:
        return 0
    kind_filter = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not kind_filter:
        kind_filter = ["flag", "coat"]
    try:
        filenames = _wikidata_visual_asset_filenames(country_qid)
    except Exception as exc:
        _log_asset_mirror(logger, f"[assets] {country_code}: assets nacionales Wikidata no disponibles ({exc})")
        return 0

    applied = 0
    for kind in kind_filter:
        filename = filenames.get(kind)
        if not filename:
            continue
        applied += _upsert_visual_asset_row(
            entity_type="country",
            entity_key=country_code,
            entity_name=country_code,
            country_code=country_code,
            kind=kind,
            wikidata_id=country_qid,
            commons_filename=filename,
            replace_existing=True,
            source="wikidata_country_direct",
        )
    if applied:
        _log_asset_mirror(logger, f"[assets] {country_code}: assets nacionales Wikidata directos={applied}")
    return applied


def _wikidata_country_asset_candidates(country_qid: str, *, kinds: list[str] | None = None, logger=None) -> list[dict]:
    """Return Wikidata candidates using small bulk queries grouped by asset kind.

    The old query combined several OPTIONALs with class/property paths such as
    P31/P279* and P131+/P361+. For Cuba this can timeout with HTTP 504 before
    returning any data. This version starts from the concrete image property
    (P41/P94/P158/P242) and only restricts by country through P17, plus the
    country root itself. The local matcher still decides what is actually
    assigned, so the query can stay broad without becoming fragile.
    """
    country_qid = _normalize_wikidata_id(country_qid)
    if not country_qid:
        return []

    requested_kinds = [kind for kind in (kinds or []) if kind in WIKIDATA_ASSET_PROPERTIES]
    if not requested_kinds:
        requested_kinds = ["flag", "coat"]

    candidates_by_qid: dict[str, dict] = {}

    # Always seed the country itself without SPARQL. This is a cheap fallback
    # and avoids losing the national flag/coat when the bulk endpoint is down.
    try:
        direct_filenames = _wikidata_visual_asset_filenames(country_qid)
    except Exception as exc:
        direct_filenames = {}
        _log_asset_mirror(logger, f"[assets] Wikidata país {country_qid}: lectura directa fallida ({exc})")
    direct_filenames = {
        kind: filename
        for kind, filename in (direct_filenames or {}).items()
        if kind in requested_kinds and filename
    }
    if direct_filenames:
        candidates_by_qid[country_qid] = {
            "wikidata_id": country_qid,
            "label": country_qid,
            "filenames": dict(direct_filenames),
        }

    failed_kinds: list[str] = []
    for kind in requested_kinds:
        property_id = WIKIDATA_ASSET_PROPERTIES[kind]
        query = f"""
SELECT ?item ?itemLabel ?asset WHERE {{
  {{ VALUES ?item {{ wd:{country_qid} }} }}
  UNION
  {{ ?item wdt:P17 wd:{country_qid} . }}
  ?item wdt:{property_id} ?asset .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en,fr,ar,mul" . }}
}}
LIMIT 20000
""".strip()
        try:
            payload = _wikidata_sparql_json(query, timeout=45)
        except Exception as exc:
            failed_kinds.append(kind)
            _log_asset_mirror(logger, f"[assets] Wikidata {kind}: consulta masiva fallida ({exc})")
            continue

        rows = payload.get("results", {}).get("bindings", []) or []
        for binding in rows:
            item_url = binding.get("item", {}).get("value", "")
            wikidata_id = _normalize_wikidata_id(item_url.rsplit("/", 1)[-1])
            if not wikidata_id:
                continue
            filename = _commons_filename_from_sparql_value(binding.get("asset", {}).get("value"))
            if not filename:
                continue
            label = str(binding.get("itemLabel", {}).get("value") or wikidata_id).strip()
            candidate = candidates_by_qid.setdefault(
                wikidata_id,
                {"wikidata_id": wikidata_id, "label": label, "filenames": {}},
            )
            if label and candidate.get("label") == wikidata_id:
                candidate["label"] = label
            candidate.setdefault("filenames", {})[kind] = filename
        _log_asset_mirror(logger, f"[assets] Wikidata {kind}: recursos candidatos={len(rows)}")

    if failed_kinds:
        # Do not raise here. A total bulk failure should degrade to the direct
        # country assets and manual overrides, not invalidate the data scrape.
        _log_asset_mirror(
            logger,
            "[assets] Wikidata: consultas bulk fallidas=" + ", ".join(failed_kinds),
        )

    return list(candidates_by_qid.values())

def _wikidata_sparql_json(query: str, *, timeout: int = 90, method: str = "GET") -> dict:
    headers = {
        "Accept": "application/sparql-results+json, application/json",
        "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 country-visual-assets",
    }
    method_normalized = str(method or "GET").upper()
    if method_normalized == "POST":
        data = urlencode({"query": query, "format": "json"}).encode("utf-8")
        request = Request(
            "https://query.wikidata.org/sparql",
            data=data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    else:
        url = "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})
        request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def _commons_filename_from_sparql_value(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "/Special:FilePath/" in text:
        text = text.rsplit("/Special:FilePath/", 1)[-1]
    elif "/wiki/File:" in text:
        text = text.rsplit("/wiki/File:", 1)[-1]
    return unquote(text).replace("_", " ")


def _candidate_matching_admin_areas(candidate: dict, area_index: dict[str, list]) -> list:
    """Return safe local matches for a Wikidata visual-asset candidate.

    Bulk Wikidata is intentionally conservative here. Names such as
    "Malta", "Murcia" or "Zaragoza" can exist at several levels in the
    same country tree. Assigning a visual asset to every name match causes
    wrong flags/coats. If a candidate label resolves to more than one local
    AdminArea, or only to the country/root area, the candidate is left
    unassigned so it can be corrected explicitly from the asset-corrections
    section.
    """
    exact_matches = []
    seen = set()
    ambiguous = False

    for key in _name_match_variants(candidate.get("label")):
        areas = list(area_index.get(key, []) or [])
        # Country/root rows receive the national assets through the explicit
        # country branch and the root mirror. Do not let subdivision candidates
        # overwrite them just because their label is equal to the country name.
        areas = [area for area in areas if int(getattr(area, "level", -1) or -1) != 0]
        if not areas:
            continue
        if len(areas) > 1:
            ambiguous = True
            continue
        area = areas[0]
        area_id = str(getattr(area, "id", ""))
        if area_id and area_id not in seen:
            exact_matches.append(area)
            seen.add(area_id)

    if ambiguous:
        return []
    return exact_matches


def _area_asset_match_keys(area) -> set[str]:
    keys = set()
    for value in (getattr(area, "name", ""), getattr(area, "official_name", ""), getattr(area, "code", ""), getattr(area, "id", "")):
        keys.update(_name_match_variants(value))
    return {key for key in keys if key}


def _name_match_variants(value) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    variants = {text}
    variants.add(text.replace(" Province", "").replace(" province", ""))
    variants.add(text.replace(" Wilaya", "").replace(" wilaya", ""))
    variants.add(text.replace(" Governorate", "").replace(" governorate", ""))
    variants.add(text.split(",", 1)[0])
    variants.add(text.split("(", 1)[0])
    normalized = {_normalized_text(variant) for variant in variants if str(variant or "").strip()}
    return {variant for variant in normalized if variant}


def _missing_required_visual_assets(country_code: str, *, kinds: list[str], levels: list[int]) -> list[dict]:
    """Return bulk Wikidata resources that were found but not assigned.

    The strict check is about the resource pool generated by the country-level
    bulk search: every flag/coat resource found for the country should be used
    at least once by a country or AdminArea row. It must not require every
    scraped locality to have its own flag/coat, because countries such as Spain
    can have tens of thousands of locality rows and SQLite cannot handle one
    enormous IN query.
    """
    kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not kinds:
        return []

    kind_placeholders = ", ".join(["%s"] * len(kinds))
    sql = f"""
        SELECT
            resources.entity_key,
            resources.entity_name,
            resources.kind,
            resources.wikidata_id,
            resources.commons_filename
        FROM {VISUAL_ASSET_TABLE} resources
        WHERE resources.entity_type = %s
          AND resources.country_code = %s
          AND resources.kind IN ({kind_placeholders})
          AND (
              resources.commons_filename <> ''
              OR resources.remote_url <> ''
              OR resources.local_path <> ''
              OR resources.local_exists
          )
          AND NOT EXISTS (
              SELECT 1
              FROM {VISUAL_ASSET_TABLE} assigned
              WHERE assigned.country_code = resources.country_code
                AND assigned.kind = resources.kind
                AND assigned.entity_type IN ('country', 'admin_area')
                AND assigned.wikidata_id = resources.wikidata_id
                AND assigned.commons_filename = resources.commons_filename
                AND (
                    assigned.commons_filename <> ''
                    OR assigned.remote_url <> ''
                    OR assigned.local_path <> ''
                    OR assigned.local_exists
                )
              LIMIT 1
          )
        ORDER BY resources.entity_name, resources.kind, resources.wikidata_id
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, ["wikidata_country_resource", country_code, *kinds])
        rows = _dictfetchall(cursor)

    missing = []
    for row in rows:
        missing.append(
            {
                "id": str(row.get("entity_key") or row.get("wikidata_id") or ""),
                "name": str(row.get("entity_name") or row.get("wikidata_id") or row.get("entity_key") or ""),
                "level": "-",
                "kind": str(row.get("kind") or ""),
                "wikidata_id": str(row.get("wikidata_id") or ""),
                "commons_filename": str(row.get("commons_filename") or ""),
            }
        )
    return missing

def _missing_asset_error_message(missing: list[dict]) -> str:
    return _missing_asset_warning_message(missing)


def _missing_asset_warning_message(missing: list[dict]) -> str:
    sample = "; ".join(
        f"{item['name']} ({item['kind']}, {item.get('wikidata_id') or item['id']})"
        for item in missing[:25]
    )
    extra = "" if len(missing) <= 25 else f"; +{len(missing) - 25} más"
    return (
        f"Quedan {len(missing)} recursos visuales de Wikidata sin asignar: {sample}{extra}. "
        "La población de datos se ha completado; revisa la subsección Escudos y banderas "
        "si quieres asignarlos manualmente."
    )

def _admin_areas_for_asset_override(AdminArea, country_code: str, override: dict) -> list:
    queryset = AdminArea.objects.filter(country_code=country_code).exclude(city_merge_status=3)
    level = override.get("level")
    if level is not None and str(level).strip() != "":
        try:
            queryset = queryset.filter(level=int(level))
        except (TypeError, ValueError):
            pass

    ids = _override_values(override, "id", "ids", "entity_key", "entity_keys")
    if ids:
        found = list(queryset.filter(id__in=[str(value) for value in ids]).order_by("level", "name", "id"))
        if found:
            return found

    codes = _override_values(override, "code", "codes")
    if codes:
        found = list(queryset.filter(code__in=[str(value) for value in codes]).order_by("level", "name", "id"))
        if found:
            return found

    names = _override_values(override, "name", "names")
    if names:
        expected = {_normalized_text(value) for value in names if str(value or "").strip()}
        if expected:
            matches = []
            for area in queryset.order_by("level", "name", "id"):
                area_names = _normalized_area_names(area)
                if area_names & expected:
                    matches.append(area)
            if matches:
                return matches

    contains_names = _override_values(override, "name_contains", "names_contains")
    if contains_names:
        needles = [_normalized_text(value) for value in contains_names if str(value or "").strip()]
        if needles:
            matches = []
            for area in queryset.order_by("level", "name", "id"):
                haystack = " ".join(_normalized_area_names(area))
                if any(needle in haystack for needle in needles):
                    matches.append(area)
            return matches

    return []


def _normalized_area_names(area) -> set[str]:
    names = {_normalized_text(getattr(area, "name", ""))}
    official_name = getattr(area, "official_name", "")
    if official_name:
        names.add(_normalized_text(official_name))
    return {name for name in names if name}


def _override_values(override: dict, *keys: str) -> list:
    values = []
    for key in keys:
        raw = override.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, (list, tuple, set)):
            values.extend(raw)
        else:
            values.append(raw)
    return values


def _override_kinds(override: dict) -> list[str]:
    raw = _override_values(override, "kind", "kinds")
    kinds = [str(value or "").strip().lower() for value in raw]
    kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    return kinds or ["flag", "coat", "seal"]


def _normalize_wikidata_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http") and "/" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    text = text.upper()
    if text.isdigit():
        text = f"Q{text}"
    if not text.startswith("Q") or not text[1:].isdigit():
        return ""
    return text


def _normalized_text(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("’", "'").split())


WIKIDATA_ASSET_PROPERTIES = {
    "flag": "P41",
    "coat": "P94",
    "seal": "P158",
    "locator": "P242",
}


def _wikidata_visual_asset_filenames(wikidata_id: str) -> dict[str, str]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{quote(wikidata_id)}.json"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 visual-asset-overrides",
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    claims = payload.get("entities", {}).get(wikidata_id, {}).get("claims", {})
    filenames = {}
    for kind, property_id in WIKIDATA_ASSET_PROPERTIES.items():
        for claim in claims.get(property_id, []) or []:
            value = (
                claim.get("mainsnak", {})
                .get("datavalue", {})
                .get("value")
            )
            if value:
                filenames[kind] = str(value)
                break
    return filenames


def _upsert_visual_asset_row(
    *,
    entity_type: str,
    entity_key: str,
    entity_name: str,
    country_code: str,
    kind: str,
    wikidata_id: str,
    commons_filename: str,
    replace_existing: bool = False,
    source: str = "wikidata_country_bulk",
) -> int:
    existing = _select_one(
        f"""
            SELECT id, commons_filename, remote_url, local_path, local_exists, status
            FROM {VISUAL_ASSET_TABLE}
            WHERE entity_type = %s AND entity_key = %s AND kind = %s
            LIMIT 1
        """,
        [entity_type, entity_key, kind],
    )
    if existing and not replace_existing and _visual_asset_row_has_image(existing):
        return 0

    now = timezone.now()
    remote_url = _commons_redirect_file_url(commons_filename)
    source_url = f"https://www.wikidata.org/wiki/{wikidata_id}" if wikidata_id else ""
    values = [
        str(entity_name or entity_key),
        str(country_code or ""),
        str(wikidata_id or ""),
        str(commons_filename or ""),
        remote_url,
        "",
        False,
        source,
        "remote" if commons_filename else "missing",
        "",
        "",
        "",
        "",
        source_url,
        now,
    ]
    if existing:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {VISUAL_ASSET_TABLE}
                    SET
                        entity_name = %s,
                        country_code = %s,
                        wikidata_id = %s,
                        commons_filename = %s,
                        remote_url = %s,
                        local_path = %s,
                        local_exists = %s,
                        source = %s,
                        status = %s,
                        error = %s,
                        license_name = %s,
                        author = %s,
                        attribution = %s,
                        source_url = %s,
                        updated_at = %s
                    WHERE id = %s
                """,
                [*values, int(existing["id"])],
            )
        return 1

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                INSERT INTO {VISUAL_ASSET_TABLE} (
                    entity_type, entity_key, entity_name, country_code, kind,
                    wikidata_id, commons_filename, remote_url, local_path,
                    local_exists, source, status, error, license_name, author,
                    attribution, source_url, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """,
            [entity_type, entity_key, values[0], values[1], kind, *values[2:-1], now, now],
        )
    return 1


def _upsert_wikidata_override_asset(
    area,
    *,
    kind: str,
    wikidata_id: str,
    commons_filename: str,
    replace_existing: bool = False,
    source: str = "wikidata_override",
) -> int:
    entity_key = str(area.id)
    existing = _select_one(
        f"""
            SELECT id, commons_filename, remote_url, local_path, local_exists, status
            FROM {VISUAL_ASSET_TABLE}
            WHERE entity_type = %s AND entity_key = %s AND kind = %s
            LIMIT 1
        """,
        ["admin_area", entity_key, kind],
    )
    if existing and not replace_existing and _visual_asset_row_has_image(existing):
        return 0

    now = timezone.now()
    source_url = f"https://www.wikidata.org/wiki/{wikidata_id}"
    remote_url = _commons_redirect_file_url(commons_filename)
    values = [
        str(getattr(area, "name", "") or entity_key),
        str(getattr(area, "country_code", "") or ""),
        wikidata_id,
        commons_filename,
        remote_url,
        "",
        False,
        source,
        "remote",
        "",
        "",
        "",
        "",
        source_url,
        now,
    ]
    if existing:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {VISUAL_ASSET_TABLE}
                    SET
                        entity_name = %s,
                        country_code = %s,
                        wikidata_id = %s,
                        commons_filename = %s,
                        remote_url = %s,
                        local_path = %s,
                        local_exists = %s,
                        source = %s,
                        status = %s,
                        error = %s,
                        license_name = %s,
                        author = %s,
                        attribution = %s,
                        source_url = %s,
                        updated_at = %s
                    WHERE id = %s
                """,
                [*values, int(existing["id"])],
            )
        return 1

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                INSERT INTO {VISUAL_ASSET_TABLE} (
                    entity_type, entity_key, entity_name, country_code, kind,
                    wikidata_id, commons_filename, remote_url, local_path,
                    local_exists, source, status, error, license_name, author,
                    attribution, source_url, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """,
            ["admin_area", entity_key, values[0], values[1], kind, *values[2:-1], now, now],
        )
    return 1


def _visual_asset_row_has_image(row: dict) -> bool:
    return bool(
        row.get("commons_filename")
        or row.get("remote_url")
        or row.get("local_path")
        or row.get("local_exists")
    )


def _commons_redirect_file_url(filename: str) -> str:
    encoded = quote(str(filename or "").replace(" ", "_"), safe="/():,._-")
    return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{encoded}" if encoded else ""


def _default_page_workers() -> int:
    raw = os.environ.get("CIUDADES_SCRAPE_PAGE_WORKERS", "4")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4


VISUAL_ASSET_TABLE = "ciudades_del_mundo_visual_asset"
VISUAL_TRANSLATION_TABLE = "ciudades_del_mundo_visual_asset_translation"


def _mirror_country_assets_to_root_admin_areas(country_code: str, *, logger=None) -> int:
    """Make level-0 AdminArea rows show the same national assets as the country.

    The UI can render the country either as entity_type="country" or as the
    scraped root AdminArea. The normal subdivision pass skips level 0, so we
    explicitly mirror the country flag/coat/seal onto the root AdminArea rows.
    """
    country_code = str(country_code or "").strip()
    if not country_code:
        return 0
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return 0

    roots = list(
        AdminArea.objects.filter(country_code=country_code, level=0)
        .exclude(city_merge_status=3)
        .order_by("name", "id")
    )
    if not roots:
        _log_asset_mirror(logger, f"[assets] country:{country_code} sin AdminArea raíz para reflejar assets")
        return 0

    country_assets = _country_visual_asset_rows(country_code)
    if not country_assets:
        _log_asset_mirror(logger, f"[assets] country:{country_code} sin assets nacionales que reflejar")
        return 0

    mirrored = 0
    with transaction.atomic():
        for root in roots:
            for asset in country_assets:
                target_asset_id = _upsert_root_admin_area_asset_from_country_asset(
                    asset,
                    root_id=str(root.id),
                    root_name=str(root.name or root.id),
                    country_code=country_code,
                )
                _copy_asset_translations(int(asset["id"]), target_asset_id)
                mirrored += 1
            _log_asset_mirror(
                logger,
                f"[assets] admin_area raíz:{root.id} assets nacionales reflejados: {len(country_assets)}",
            )
    return mirrored


def _country_visual_asset_rows(country_code: str) -> list[dict]:
    sql = f"""
        SELECT
            id, kind, wikidata_id, commons_filename, remote_url, local_path,
            local_exists, source, status, error, license_name, author,
            attribution, source_url
        FROM {VISUAL_ASSET_TABLE}
        WHERE entity_type = %s AND entity_key = %s
        ORDER BY kind
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, ["country", country_code])
        return _dictfetchall(cursor)


def _upsert_root_admin_area_asset_from_country_asset(
    asset: dict,
    *,
    root_id: str,
    root_name: str,
    country_code: str,
) -> int:
    kind = str(asset.get("kind") or "")
    now = timezone.now()
    existing = _select_one(
        f"""
            SELECT id
            FROM {VISUAL_ASSET_TABLE}
            WHERE entity_type = %s AND entity_key = %s AND kind = %s
            LIMIT 1
        """,
        ["admin_area", root_id, kind],
    )
    values = [
        root_name,
        country_code,
        str(asset.get("wikidata_id") or ""),
        str(asset.get("commons_filename") or ""),
        str(asset.get("remote_url") or ""),
        str(asset.get("local_path") or ""),
        bool(asset.get("local_exists")),
        str(asset.get("source") or "country"),
        str(asset.get("status") or "missing"),
        str(asset.get("error") or ""),
        str(asset.get("license_name") or ""),
        str(asset.get("author") or ""),
        str(asset.get("attribution") or ""),
        str(asset.get("source_url") or ""),
        now,
    ]
    if existing:
        target_id = int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    UPDATE {VISUAL_ASSET_TABLE}
                    SET
                        entity_name = %s,
                        country_code = %s,
                        wikidata_id = %s,
                        commons_filename = %s,
                        remote_url = %s,
                        local_path = %s,
                        local_exists = %s,
                        source = %s,
                        status = %s,
                        error = %s,
                        license_name = %s,
                        author = %s,
                        attribution = %s,
                        source_url = %s,
                        updated_at = %s
                    WHERE id = %s
                """,
                [*values, target_id],
            )
        return target_id

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                INSERT INTO {VISUAL_ASSET_TABLE} (
                    entity_type, entity_key, entity_name, country_code, kind,
                    wikidata_id, commons_filename, remote_url, local_path,
                    local_exists, source, status, error, license_name, author,
                    attribution, source_url, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """,
            ["admin_area", root_id, root_name, country_code, kind, *values[2:], now],
        )
    created = _select_one(
        f"""
            SELECT id
            FROM {VISUAL_ASSET_TABLE}
            WHERE entity_type = %s AND entity_key = %s AND kind = %s
            LIMIT 1
        """,
        ["admin_area", root_id, kind],
    )
    return int(created["id"]) if created else 0


def _copy_asset_translations(source_asset_id: int, target_asset_id: int) -> None:
    if not source_asset_id or not target_asset_id:
        return
    translations = _asset_translation_rows(source_asset_id)
    now = timezone.now()
    for row in translations:
        language = str(row.get("language") or "")
        if not language:
            continue
        existing = _select_one(
            f"""
                SELECT id
                FROM {VISUAL_TRANSLATION_TABLE}
                WHERE asset_id = %s AND language = %s
                LIMIT 1
            """,
            [target_asset_id, language],
        )
        values = [
            str(row.get("title") or ""),
            str(row.get("description") or ""),
            str(row.get("blazon") or ""),
            str(row.get("source") or ""),
            bool(row.get("needs_review")),
            now,
        ]
        if existing:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                        UPDATE {VISUAL_TRANSLATION_TABLE}
                        SET
                            title = %s,
                            description = %s,
                            blazon = %s,
                            source = %s,
                            needs_review = %s,
                            updated_at = %s
                        WHERE id = %s
                    """,
                    [*values, int(existing["id"])],
                )
            continue
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    INSERT INTO {VISUAL_TRANSLATION_TABLE} (
                        asset_id, language, title, description, blazon, source,
                        needs_review, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [target_asset_id, language, *values[:-1], now, now],
            )


def _asset_translation_rows(asset_id: int) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT language, title, description, blazon, source, needs_review
                FROM {VISUAL_TRANSLATION_TABLE}
                WHERE asset_id = %s
                ORDER BY language
            """,
            [asset_id],
        )
        return _dictfetchall(cursor)


def _select_one(sql: str, params: list) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(columns, row))


def _dictfetchall(cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _log_asset_mirror(logger, message: str) -> None:
    if logger:
        logger(message)
