from __future__ import annotations

import json
import os
import time
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

from django.core.management import BaseCommand, CommandError, call_command
from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.services.visual_assets import (
    visual_asset_tables_exist,
)
from ciudades_del_mundo.web.task_progress import write_config_progress


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
        write_config_progress(slug, "populating", detail="Scrapeando datos")
        try:
            config = PythonScrapingConfigRepository().get(slug)
            country_code = str(config.country_code or slug)
            page_workers = max(1, int(options.get("page_workers") or 1))
            ai_limit = max(1, int(options.get("ai_limit") or 1))
            download_assets = bool(options.get("download_assets")) and not bool(options.get("no_download_assets"))
            config_data = _config_data_for_slug(slug)
            asset_settings = _visual_asset_settings(config_data)

            if options.get("clear_first"):
                write_config_progress(slug, "clearing", detail="Limpiando datos anteriores")
                self._write(f"[limpiar] {slug}: limpiando datos existentes antes de re-popular...")
                self._run_with_sqlite_retry(lambda: call_command("clear_config_data", country_code))
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
                    ai_enrich=bool(options.get("ai_enrich")),
                    ai_languages=options.get("ai_languages") or "",
                    ai_translate_area_names=bool(options.get("ai_translate_area_names")),
                    ai_limit=ai_limit,
                )
            )

            if options.get("skip_assets"):
                self._write("[assets] omitido por --skip-assets")
                write_config_progress(slug, "populated")
                return
            if not visual_asset_tables_exist():
                raise CommandError("La tabla de assets visuales no existe. Ejecuta migraciones.")

            # Fase 2: buscar y registrar banderas/escudos. Se usa una sola
            # estrategia para no duplicar trabajo: consulta masiva Wikidata del país.
            write_config_progress(slug, "populating", detail="Buscando banderas y escudos")
            self._write(f"[assets] {slug}: fase 2/2, búsqueda masiva Wikidata...")
            if options.get("skip_subdivision_assets"):
                self._write("[assets] subdivisiones omitidas por --skip-subdivision-assets")
            elif asset_settings["bulk_country_wikidata"] and not options.get("skip_country_bulk_assets"):
                self._run_with_sqlite_retry(
                    lambda: _assign_bulk_country_wikidata_assets(
                        slug,
                        country_code,
                        config_data=config_data,
                        required_kinds=asset_settings["required_kinds"],
                        levels=_levels_from_cli_or_config(
                            options.get("subdivision_asset_levels") or "",
                            asset_settings["required_levels"],
                        ),
                        logger=self._write,
                    )
                )
                self._run_with_sqlite_retry(
                    lambda: _mirror_country_assets_to_root_admin_areas(
                        country_code,
                        logger=self._write,
                    )
                )
            else:
                self._write("[assets] búsqueda masiva Wikidata omitida")

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
                    raise CommandError(_missing_asset_error_message(missing_required_assets))

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
            write_config_progress(slug, "failed", detail=str(exc))
            raise
        write_config_progress(slug, "populated")


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
        replace_existing = bool(override.get("replace_existing") or override.get("force"))
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
        return [raw]
    return [item for item in raw if isinstance(item, dict)]


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
        "bulk_country_wikidata": _config_bool(raw.get("bulk_country_wikidata"), default=True),
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


def _assign_bulk_country_wikidata_assets(
    slug: str,
    country_code: str,
    *,
    config_data: dict | None = None,
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
    country_qid = _country_wikidata_id_for_bulk(country_code, data)
    if not country_qid:
        _log_asset_mirror(logger, f"[assets] {country_code}: sin wikidata_id de país; búsqueda masiva omitida")
        return 0

    queryset = AdminArea.objects.filter(country_code=country_code).exclude(city_merge_status=3)
    level_filter = [int(level) for level in (levels or []) if str(level).strip() != ""]
    if level_filter:
        queryset = queryset.filter(level__in=level_filter)
    areas = list(queryset.order_by("level", "name", "id"))
    if not areas:
        return 0

    area_index: dict[str, list] = {}
    for area in areas:
        for key in _area_asset_match_keys(area):
            area_index.setdefault(key, []).append(area)

    try:
        candidates = _wikidata_country_asset_candidates(country_qid)
    except Exception as exc:
        _log_asset_mirror(logger, f"[assets] {country_code}: búsqueda masiva Wikidata fallida ({exc})")
        return 0

    applied = 0
    stored_resources = 0
    matched_candidates = 0
    kind_filter = {kind for kind in (required_kinds or []) if kind in WIKIDATA_ASSET_PROPERTIES}
    if not kind_filter:
        kind_filter = {"flag", "coat"}

    for candidate in candidates:
        filenames = candidate.get("filenames") or {}
        candidate_qid = str(candidate.get("wikidata_id") or "")
        label = str(candidate.get("label") or candidate_qid)
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
                    replace_existing=False,
                    source="wikidata_country_bulk",
                )
            matched_candidates += 1
            continue

        matches = _candidate_matching_admin_areas(candidate, area_index)
        if not matches:
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
                    replace_existing=False,
                    source="wikidata_country_bulk",
                )
    _log_asset_mirror(
        logger,
        f"[assets] {country_code}: búsqueda masiva Wikidata, candidatos={len(candidates)}, "
        f"recursos guardados={stored_resources}, candidatos asignados={matched_candidates}, "
        f"assets nuevos/actualizados={applied}",
    )
    return applied


def _country_wikidata_id_for_bulk(country_code: str, config_data: dict) -> str:
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
    return _wikidata_country_id_from_search(country_code)


def _wikidata_country_id_from_search(country_code: str) -> str:
    query = str(country_code or "").replace("_", " ").replace("-", " ").strip()
    if not query:
        return ""
    url = "https://www.wikidata.org/w/api.php?" + urlencode(
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": "en",
            "limit": "3",
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
    for row in payload.get("search", []) or []:
        qid = _normalize_wikidata_id(row.get("id"))
        if qid:
            return qid
    return ""


def _wikidata_country_asset_candidates(country_qid: str) -> list[dict]:
    query = f"""
SELECT ?item ?itemLabel ?flag ?coat ?seal ?locator WHERE {{
  {{
    VALUES ?item {{ wd:{country_qid} }}
  }}
  UNION
  {{
    ?item wdt:P17 wd:{country_qid} .
    ?item wdt:P31/wdt:P279* wd:Q56061 .
  }}
  OPTIONAL {{ ?item wdt:P41 ?flag . }}
  OPTIONAL {{ ?item wdt:P94 ?coat . }}
  OPTIONAL {{ ?item wdt:P158 ?seal . }}
  OPTIONAL {{ ?item wdt:P242 ?locator . }}
  FILTER(BOUND(?flag) || BOUND(?coat) || BOUND(?seal) || BOUND(?locator))
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en,fr,ar,mul" . }}
}}
LIMIT 10000
""".strip()
    url = "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})
    request = Request(
        url,
        headers={
            "Accept": "application/sparql-results+json, application/json",
            "User-Agent": "BEOGRAD-CITIES-POPULATION/1.0 country-visual-assets",
        },
    )
    with urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    candidates = []
    for binding in payload.get("results", {}).get("bindings", []) or []:
        item_url = binding.get("item", {}).get("value", "")
        wikidata_id = _normalize_wikidata_id(item_url.rsplit("/", 1)[-1])
        if not wikidata_id:
            continue
        label = str(binding.get("itemLabel", {}).get("value") or "").strip()
        filenames = {}
        for kind in WIKIDATA_ASSET_PROPERTIES:
            raw = binding.get(kind, {}).get("value")
            filename = _commons_filename_from_sparql_value(raw)
            if filename:
                filenames[kind] = filename
        if filenames:
            candidates.append({"wikidata_id": wikidata_id, "label": label, "filenames": filenames})
    return candidates


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
    matches = []
    seen = set()
    for key in _name_match_variants(candidate.get("label")):
        for area in area_index.get(key, []) or []:
            area_id = str(getattr(area, "id", ""))
            if area_id and area_id not in seen:
                matches.append(area)
                seen.add(area_id)
    return matches


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
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return []
    kinds = [kind for kind in kinds if kind in WIKIDATA_ASSET_PROPERTIES]
    if not kinds:
        return []
    queryset = AdminArea.objects.filter(country_code=country_code).exclude(city_merge_status=3)
    if levels:
        queryset = queryset.filter(level__in=levels)
    areas = list(queryset.order_by("level", "name", "id"))
    if not areas:
        return []
    area_ids = [str(area.id) for area in areas]
    placeholders = ", ".join(["%s"] * len(area_ids))
    kind_placeholders = ", ".join(["%s"] * len(kinds))
    existing = set()
    sql = f"""
        SELECT entity_key, kind, commons_filename, remote_url, local_path, local_exists
        FROM {VISUAL_ASSET_TABLE}
        WHERE entity_type = %s
          AND entity_key IN ({placeholders})
          AND kind IN ({kind_placeholders})
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, ["admin_area", *area_ids, *kinds])
        for row in _dictfetchall(cursor):
            if _visual_asset_row_has_image(row):
                existing.add((str(row.get("entity_key")), str(row.get("kind"))))
    missing = []
    for area in areas:
        area_id = str(area.id)
        for kind in kinds:
            if (area_id, kind) not in existing:
                missing.append({"id": area_id, "name": str(area.name or area_id), "level": area.level, "kind": kind})
    return missing


def _missing_asset_error_message(missing: list[dict]) -> str:
    sample = "; ".join(
        f"{item['name']} (nivel {item['level']}, {item['kind']})"
        for item in missing[:25]
    )
    extra = "" if len(missing) <= 25 else f"; +{len(missing) - 25} más"
    return (
        f"Faltan {len(missing)} assets visuales requeridos: {sample}{extra}. "
        "Corrige la asignación en la subsección Escudos y banderas con [[wikidata_asset_overrides]] "
        "o desactiva strict_required en [visual_assets] si el país no tiene todos esos recursos."
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
