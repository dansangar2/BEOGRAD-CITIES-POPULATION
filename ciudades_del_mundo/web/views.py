"""Operational web views for managing local geography data."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import zip_longest
import json
from pathlib import Path
from pprint import pformat
import re
import tomllib
import unicodedata
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from ciudades_del_mundo.domain import (
    RepresentationConfig,
    parse_cities,
    parse_entity_merges,
    parse_pages,
)
from ciudades_del_mundo.domain.nuevo_admin_export import CellMerge, Sheet, Table, Workbook
from ciudades_del_mundo.application.export_nuevo_admin_areas import build_nuevo_admin_workbook
from ciudades_del_mundo.infrastructure.django.nuevo_admin_area_export_repository import (
    DjangoNuevoAdminAreaExportRepository,
)
from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    DerivedSubdivision,
    NuevoAdminArea,
    ScrapingConfig,
    SubdivisionGroup,
    VisualAsset,
)

from ciudades_del_mundo.infrastructure.scraping.page_types import (
    CityPopulationPageProfile,
    CityPopulationPageType,
    detect_citypopulation_page_profile,
)
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.infrastructure.excel import SimpleXlsxWriter

from ciudades_del_mundo.services.scraping_configs import (
    bundled_city_merge_toml_paths,
    bundled_toml_config_paths,
    ensure_initial_scraping_configs,
    export_city_merges_to_toml,
    export_scraping_configs_to_toml,
    scraping_config_bootstrap_status,
    scraping_config_table_exists,
    sync_city_merges_from_toml,
    sync_scraping_configs_from_toml,
    upsert_scraping_config,
)
from ciudades_del_mundo.services.dynamic_translations import (
    dynamic_area_name,
    dynamic_country_name,
    dynamic_entity_type_label,
)
from ciudades_del_mundo.services.visual_assets import (
    get_visual_asset_by_local_or_commons,
    get_visual_asset_for_entity_kind,
    get_visual_assets_for_entity,
    commons_file_url,
    visual_asset_tables_exist,
)
from ciudades_del_mundo.services.config_asset_overrides import (
    load_config_asset_overrides,
    save_config_asset_overrides,
)
from ciudades_del_mundo.services.configured_city_materializer import (
    materialize_configured_cities_for_record,
)
from ciudades_del_mundo.services.derived_config_seeds import (
    bundled_derived_subdivision_paths,
    bundled_new_country_config_paths,
    bundled_subdivision_group_paths,
    export_derived_subdivisions_to_toml,
    import_derived_subdivision_path_records,
    export_subdivision_groups_to_toml,
    import_subdivision_group_path_records,
    render_derived_country_selection_toml,
)
from ciudades_del_mundo.services.derived_codes import derived_code_piece, derived_country_root_code
from ciudades_del_mundo.services.nuevo_admin_builder import (
    ORIGINAL_MUNICIPAL_LEVEL,
    effective_source_municipal_level,
    refresh_nuevo_admin_most_populated,
)
from ciudades_del_mundo.services.status_codes import (
    get_program_message_catalog,
    program_message_payload,
)

from .spain_translations import normalize_language_code, spain_entity_type, spain_name
from .task_progress import read_task_config_progress
from .tasks import task_manager


COUNTRY_BROWSER_DEFAULT_PAGE_SIZE = 20
COUNTRY_BROWSER_MAX_PAGE_SIZE = 100
NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE = 20
NEW_COUNTRY_OPTION_MAX_PAGE_SIZE = 100




def _program_code_catalog() -> dict:
    return get_program_message_catalog()


def _code_description(code: str) -> str:
    return program_message_payload(code).get("description", "")


def _code_severity(code: str) -> str:
    return program_message_payload(code).get("severity", "")


def _extract_status_code(text: str) -> str:
    text = str(text or "")
    catalog = _program_code_catalog()
    for code in catalog:
        if code and code in text:
            return code
    lowered = text.casefold()
    if "quedan" in lowered and "recursos visuales" in lowered and "sin asignar" in lowered:
        return "SCR-ASSET-W001" if "warning" in lowered or "aviso" in lowered else "SCR-ASSET-002"
    if "tabla de assets visuales" in lowered:
        return "SCR-ASSET-003"
    if "búsqueda masiva wikidata" in lowered or "busqueda masiva wikidata" in lowered or "consultas bulk" in lowered:
        return "SCR-ASSET-001"
    if "too many sql variables" in lowered or "database is locked" in lowered or "sqlite" in lowered:
        return "SCR-DB-001"
    if "timed out" in lowered or "timeout" in lowered or "http error" in lowered or "bad gateway" in lowered:
        return "SCR-NET-001"
    return ""


def _task_code_info(task, *, validation_error: str = "") -> dict:
    status = str(getattr(task, "status", "") or "").strip().casefold() if task else ""
    key = str(getattr(task, "key", "") or "").strip().casefold() if task else ""
    code = _extract_status_code(validation_error)
    output = ""
    if task and (not code or status in {"succeeded", "failed", "cancelled"}):
        try:
            output = task_manager.output_tail_text(task)
        except Exception:
            output = ""
        code = code or _extract_status_code(output)

    if not code and status == "cancelled":
        code = "SCR-CANCEL-001"
    if not code and validation_error:
        code = "SCR-VAL-001"
    if not code and status == "failed":
        if key.startswith("validate-config:"):
            code = "SCR-VAL-001"
        elif key.startswith("clear-config:"):
            code = "SCR-CLEAR-001"
        elif key.startswith("asset-corrections:"):
            code = "SCR-ASSET-W001"
        elif key.startswith("scrape:"):
            code = "SCR-DATA-001"
        else:
            code = "SCR-UNKNOWN"
    if not code and status == "succeeded" and key.startswith("scrape:"):
        code = "SCR-SUCCESS-001"

    payload = program_message_payload(code) if code else {"code": "", "severity": "", "message": "", "description": ""}
    return {
        "code": payload.get("code", ""),
        "severity": payload.get("severity", ""),
        "message": payload.get("message", ""),
        "description": payload.get("description", ""),
    }


def _task_error_info(task, *, validation_error: str = "") -> dict:
    # Backwards-compatible name: callers still use error_code/error_description
    # fields, but the catalog can now return success and warning codes too.
    return _task_code_info(task, validation_error=validation_error)


CONFIG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RECIPE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HIDDEN_CITY_MERGE_STATUS = 3
GROUP_SOURCE_CITY_MERGE_STATUSES = (
    int(AdminArea.CityMergeStatus.NONE),
    int(AdminArea.CityMergeStatus.UNIFIED),
)
CITYPOPULATION_DISCOVERY_TIMEOUT = int(getattr(settings, "CITYPOPULATION_DISCOVERY_TIMEOUT", 8))
CITYPOPULATION_DISCOVERY_MAX_PAGES = int(getattr(settings, "CITYPOPULATION_DISCOVERY_MAX_PAGES", 110))
CITYPOPULATION_GENERATOR_USER_AGENT = str(
    getattr(
        settings,
        "CITYPOPULATION_GENERATOR_USER_AGENT",
        "Mozilla/5.0 (compatible; BEOGRAD-CITIES-POPULATION config discovery)",
    )
)
AI_CONFIG_ENABLED = bool(getattr(settings, "AI_CONFIG_ENABLED", True))
AI_PERSONAL_LOGIN_ENABLED = bool(getattr(settings, "AI_PERSONAL_LOGIN_ENABLED", True))
AI_PROVIDER_LOGIN_URLS = {
    "chatgpt": "https://chat.openai.com/",
    "openai": "https://platform.openai.com/login",
    "claude": "https://claude.ai/login",
    "gemini": "https://gemini.google.com/",
    **getattr(settings, "AI_PROVIDER_LOGIN_URLS", {}),
}
PACKAGE_ROOT = Path(settings.BASE_DIR) / "ciudades_del_mundo"
NEW_RECIPES_ROOT = PACKAGE_ROOT / "new_subdivisions"
HISTORICAL_RECIPES_ROOT = PACKAGE_ROOT / "historical_divisions"


@dataclass(frozen=True)
class CityPopulationRouteCandidate:
    """One route discovered in a CityPopulation country index."""

    path: str
    title: str = ""
    description: str = ""
    section: str = ""
    category: str = "other"


@dataclass(frozen=True)
class CityPopulationPageProbe:
    """Structural facts detected after reading a candidate CityPopulation page."""

    path: str
    url: str
    title: str
    h1: str
    headings: tuple[str, ...]
    profile: CityPopulationPageProfile
    table_ids: tuple[str, ...]
    row_count: int
    tl_rows: int
    ts_rows: int
    has_population: bool
    category: str = "other"


@dataclass(frozen=True)
class CityPopulationGeneratedPage:
    """TOML-ready page specification generated from a probed route."""

    path: str
    source: str
    lowest_level: int
    category: str = "other"
    include_infosection: bool = True
    include_major_subdivision: bool = True
    include_minor_subdivision: bool = True
    include_cities: bool = True

COUNTRY_NAME_ES_BY_CODE = {
    "afghanistan": "Afganistán",
    "algeria": "Argelia",
    "americansamoa": "Samoa Americana",
    "antarctica": "Antártida",
    "antigua": "Antigua y Barbuda",
    "azerbaijan": "Azerbaiyán",
    "bahrain": "Baréin",
    "belarus": "Bielorrusia",
    "belgium": "Bélgica",
    "belize": "Belice",
    "benin": "Benín",
    "bhutan": "Bután",
    "bosnia": "Bosnia y Herzegovina",
    "brazil": "Brasil",
    "britishvirginislands": "Islas Vírgenes Británicas",
    "brunei": "Brunéi",
    "cambodia": "Camboya",
    "cameroon": "Camerún",
    "canada": "Canadá",
    "capeverde": "Cabo Verde",
    "caribbeannetherlands": "Caribe Neerlandés",
    "caymans": "Islas Caimán",
    "centralafrica": "República Centroafricana",
    "comoros": "Comoras",
    "congo": "República del Congo",
    "cook": "Islas Cook",
    "croatia": "Croacia",
    "curacao": "Curazao",
    "cyprus": "Chipre",
    "czechrep": "Chequia",
    "denmark": "Dinamarca",
    "djibouti": "Yibuti",
    "domrep": "República Dominicana",
    "drcongo": "República Democrática del Congo",
    "egypt": "Egipto",
    "elsalvador": "El Salvador",
    "equatorialguinea": "Guinea Ecuatorial",
    "eswatini": "Esuatini",
    "ethiopia": "Etiopía",
    "falklands": "Islas Malvinas",
    "faroe": "Islas Feroe",
    "fiji": "Fiyi",
    "finland": "Finlandia",
    "france": "Francia",
    "frenchguiana": "Guayana Francesa",
    "frenchpolynesia": "Polinesia Francesa",
    "gabon": "Gabón",
    "germany": "Alemania",
    "greece": "Grecia",
    "greenland": "Groenlandia",
    "guineabissau": "Guinea-Bisáu",
    "haiti": "Haití",
    "hungary": "Hungría",
    "iceland": "Islandia",
    "iran": "Irán",
    "iraq": "Irak",
    "ireland": "Irlanda",
    "isleofman": "Isla de Man",
    "italy": "Italia",
    "ivorycoast": "Costa de Marfil",
    "japan": "Japón",
    "jordan": "Jordania",
    "kazakhstan": "Kazajistán",
    "kenya": "Kenia",
    "kyrgyzstan": "Kirguistán",
    "latvia": "Letonia",
    "lebanon": "Líbano",
    "lesotho": "Lesoto",
    "lithuania": "Lituania",
    "luxembourg": "Luxemburgo",
    "malawi": "Malaui",
    "malaysia": "Malasia",
    "maldives": "Maldivas",
    "mali": "Malí",
    "marshall": "Islas Marshall",
    "martinique": "Martinica",
    "mauritius": "Mauricio",
    "mexico": "México",
    "moldova": "Moldavia",
    "monaco": "Mónaco",
    "morocco": "Marruecos",
    "netherlands": "Países Bajos",
    "newcaledonia": "Nueva Caledonia",
    "newzealand": "Nueva Zelanda",
    "niger": "Níger",
    "northkorea": "Corea del Norte",
    "northmacedonia": "Macedonia del Norte",
    "northmarianas": "Islas Marianas del Norte",
    "norway": "Noruega",
    "oman": "Omán",
    "pakistan": "Pakistán",
    "palau": "Palaos",
    "palestine": "Palestina",
    "panama": "Panamá",
    "papuanewguinea": "Papúa Nueva Guinea",
    "peru": "Perú",
    "philippines": "Filipinas",
    "poland": "Polonia",
    "puertorico": "Puerto Rico",
    "qatar": "Catar",
    "reunion": "Reunión",
    "romania": "Rumanía",
    "russia": "Rusia",
    "rwanda": "Ruanda",
    "saintbarthelemy": "San Bartolomé",
    "saintmartin": "San Martín",
    "sanmarino": "San Marino",
    "saotome": "Santo Tomé y Príncipe",
    "saudiarabia": "Arabia Saudí",
    "sierraleone": "Sierra Leona",
    "singapore": "Singapur",
    "slovakia": "Eslovaquia",
    "slovenia": "Eslovenia",
    "solomon": "Islas Salomón",
    "southafrica": "Sudáfrica",
    "southkorea": "Corea del Sur",
    "southsudan": "Sudán del Sur",
    "spain": "España",
    "sthelena": "Santa Elena",
    "stkittsnevis": "San Cristóbal y Nieves",
    "stlucia": "Santa Lucía",
    "stpierremiquelon": "San Pedro y Miquelón",
    "stvincent": "San Vicente y las Granadinas",
    "sudan": "Sudán",
    "sweden": "Suecia",
    "switzerland": "Suiza",
    "syria": "Siria",
    "taiwan": "Taiwán",
    "tajikistan": "Tayikistán",
    "thailand": "Tailandia",
    "timor": "Timor Oriental",
    "trinidad": "Trinidad y Tobago",
    "tunisia": "Túnez",
    "turkey": "Turquía",
    "turkmenistan": "Turkmenistán",
    "turkscaicos": "Islas Turcas y Caicos",
    "uae": "Emiratos Árabes Unidos",
    "uk": "Reino Unido",
    "ukraine": "Ucrania",
    "usa": "Estados Unidos",
    "usvirginislands": "Islas Vírgenes de EE. UU.",
    "uzbekistan": "Uzbekistán",
    "vaticancity": "Ciudad del Vaticano",
    "wallisfutuna": "Wallis y Futuna",
    "westernsahara": "Sahara Occidental",
    "zimbabwe": "Zimbabue",
}
COUNTRY_NAME_ES_BY_CODE.update(
    {
        "albania": "Albania",
        "andorra": "Andorra",
        "angola": "Angola",
        "anguilla": "Anguila",
        "argentina": "Argentina",
        "armenia": "Armenia",
        "aruba": "Aruba",
        "australia": "Australia",
        "austria": "Austria",
        "bahamas": "Bahamas",
        "bangladesh": "Bangladés",
        "barbados": "Barbados",
        "bermuda": "Bermudas",
        "bolivia": "Bolivia",
        "botswana": "Botsuana",
        "bulgaria": "Bulgaria",
        "burkinafaso": "Burkina Faso",
        "burundi": "Burundi",
        "chad": "Chad",
        "chile": "Chile",
        "china": "China",
        "colombia": "Colombia",
        "costarica": "Costa Rica",
        "cuba": "Cuba",
        "dominica": "Dominica",
        "ecuador": "Ecuador",
        "eritrea": "Eritrea",
        "estonia": "Estonia",
        "gambia": "Gambia",
        "georgia": "Georgia",
        "ghana": "Ghana",
        "gibraltar": "Gibraltar",
        "grenada": "Granada",
        "guadeloupe": "Guadalupe",
        "guam": "Guam",
        "guatemala": "Guatemala",
        "guernsey": "Guernsey",
        "guinea": "Guinea",
        "guyana": "Guyana",
        "honduras": "Honduras",
        "india": "India",
        "indonesia": "Indonesia",
        "israel": "Israel",
        "jamaica": "Jamaica",
        "jersey": "Jersey",
        "kiribati": "Kiribati",
        "kosovo": "Kosovo",
        "kuwait": "Kuwait",
        "laos": "Laos",
        "liberia": "Liberia",
        "libya": "Libia",
        "liechtenstein": "Liechtenstein",
        "madagascar": "Madagascar",
        "malta": "Malta",
        "mauritania": "Mauritania",
        "mayotte": "Mayotte",
        "micronesia": "Micronesia",
        "mongolia": "Mongolia",
        "montenegro": "Montenegro",
        "montserrat": "Montserrat",
        "mozambique": "Mozambique",
        "myanmar": "Myanmar",
        "namibia": "Namibia",
        "nauru": "Nauru",
        "nepal": "Nepal",
        "nicaragua": "Nicaragua",
        "nigeria": "Nigeria",
        "niue": "Niue",
        "paraguay": "Paraguay",
        "pitcairn": "Pitcairn",
        "portugal": "Portugal",
        "samoa": "Samoa",
        "senegal": "Senegal",
        "serbia": "Serbia",
        "seychelles": "Seychelles",
        "sintmaarten": "Sint Maarten",
        "somalia": "Somalia",
        "srilanka": "Sri Lanka",
        "suriname": "Surinam",
        "tanzania": "Tanzania",
        "togo": "Togo",
        "tokelau": "Tokelau",
        "tonga": "Tonga",
        "tuvalu": "Tuvalu",
        "uganda": "Uganda",
        "uruguay": "Uruguay",
        "vanuatu": "Vanuatu",
        "venezuela": "Venezuela",
        "vietnam": "Vietnam",
        "yemen": "Yemen",
        "zambia": "Zambia",
    }
)


def dashboard(request):
    """Render project summary metrics and current task state."""
    countries = list(
        _visible_admin_areas().values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    admin_country_labels = _admin_country_label_map()
    _apply_country_labels(countries, admin_country_labels)
    derived = list(
        NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    nuevo_country_labels = _nuevo_country_label_map()
    _apply_country_labels(derived, nuevo_country_labels)
    context = {
        "admin_area_count": _visible_admin_areas().count(),
        "admin_country_count": len(countries),
        "nuevo_area_count": NuevoAdminArea.objects.count(),
        "nuevo_country_count": len(derived),
        "countries": countries,
        "configs": _config_summaries(limit=8),
        "recent_tasks": task_manager.list(limit=8),
        "dashboard_population_url": reverse("ciudades_del_mundo:api_country_summary"),
        "dashboard_derived_url": reverse("ciudades_del_mundo:api_derived_summary"),
        "dashboard_country_detail_base_url": reverse(
            "ciudades_del_mundo:api_country_detail",
            kwargs={"country_code": "__country__"},
        ),
        "derived_bars": _bar_rows(derived, "display_label", "population", limit=10),
    }
    return render(request, "ciudades_del_mundo/dashboard.html", context)


def api_country_summary(request):
    """Return imported country summary data for API-driven views."""
    return JsonResponse(
        _country_summary_payload(
            detail_route="ciudades_del_mundo:api_country_detail",
            include_visual_assets=True,
        )
    )


def api_new_country_summary(request):
    """Return derived-country summary cards for the shared country browser."""
    return JsonResponse(_new_country_summary_payload())


def api_country_detail(request, country_code):
    """Return one imported country's dashboard/detail payload."""
    return _country_detail_response(request, country_code)


def api_admin_area_detail(request, area_id):
    """Return one imported area and its direct visible child subdivisions."""
    area = (
        _visible_admin_areas()
        .select_related("parent")
        .prefetch_related("capitals")
        .filter(id=area_id)
        .first()
    )
    if not area:
        raise Http404(_("No existe AdminArea '%(area_id)s'.") % {"area_id": area_id})
    return JsonResponse(_admin_area_detail_payload(area))


def api_new_country_detail(request, country_id):
    """Return one built new country using the country-browser JSON contract."""
    root = _nuevo_country_root_or_404(country_id)
    return JsonResponse(
        _nuevo_country_detail_payload(
            root,
            request.GET.get("level"),
            page=request.GET.get("page"),
            page_size=_country_browser_page_size(request),
        )
    )


def api_new_country_container_detail(request, country_slug):
    """Return one DerivedCountry using the shared country-browser JSON contract."""
    country = _derived_country_or_404(country_slug)
    requested_level = request.GET.get("level")
    page_size = _country_browser_page_size(request)
    configs = list(country.configs.order_by("name", "slug"))
    available_levels = _new_country_config_available_levels_for_configs(configs)
    selected_level, _selected_entity_types, _selected_filter = _resolve_country_level_selection(
        requested_level,
        available_levels,
    )
    rows = _new_country_config_rows(
        configs,
        include_assigned_subdivisions=selected_level is not None,
        target_levels={selected_level} if selected_level is not None else None,
    )
    root = _new_country_detail_export_root(country, rows)
    if root is not None:
        return JsonResponse(
            _nuevo_country_detail_payload(
                root,
                requested_level,
                page=request.GET.get("page"),
                page_size=page_size,
            )
        )
    return JsonResponse(
        _new_country_config_detail_payload(
            country,
            rows,
            requested_level,
            available_levels=available_levels,
            page=request.GET.get("page"),
            page_size=page_size,
        )
    )


def api_new_country_config_tree(request, country_slug):
    """Return a bounded page of editable DerivedCountryConfig tree rows."""
    country = _derived_country_or_404(country_slug)
    configs = list(country.configs.order_by("name", "slug"))
    rows = _new_country_config_tree_request_rows(
        country,
        configs,
        parent_tree_id=request.GET.get("parent", ""),
        selected_level=request.GET.get("level", ""),
    )
    page_obj = _country_browser_page(rows, request.GET.get("page"), _country_browser_page_size(request))
    return JsonResponse(
        {
            "ok": True,
            "country": country.slug,
            "rows": [_new_country_config_tree_row_payload(row) for row in page_obj.object_list],
            "pagination": _pagination_payload(page_obj),
        }
    )


def api_new_country_config_node_detail(request, country_slug, tree_id):
    """Return one unbuilt DerivedCountryConfig tree node as an area detail."""
    country = _derived_country_or_404(country_slug)
    configs = list(country.configs.order_by("name", "slug"))
    target_slug = _new_country_config_node_target_slug(country.slug, tree_id)
    assigned_slugs = None
    source_descendant_slugs = None
    if target_slug:
        direct_child_slugs = {
            config.slug
            for config in configs
            if _new_country_config_parent_slug(config) == target_slug
        }
        assigned_slugs = {target_slug, *direct_child_slugs}
        source_descendant_slugs = {target_slug}
    rows = _new_country_config_rows(
        configs,
        include_assigned_subdivisions=True,
        assigned_subdivision_config_slugs=assigned_slugs,
        source_descendant_config_slugs=source_descendant_slugs,
    )
    return JsonResponse(_new_country_config_node_detail_payload(country, rows, tree_id))


def api_new_area_detail(request, area_id):
    """Return one built new-country area and its direct derived children."""
    area = (
        NuevoAdminArea.objects.select_related("parent")
        .prefetch_related("capitals")
        .filter(id=area_id)
        .first()
    )
    if not area:
        raise Http404(_("No existe NuevoAdminArea '%(area_id)s'.") % {"area_id": area_id})
    return JsonResponse(_nuevo_area_detail_payload(area))


def api_derived_summary(request):
    """Return derived-country population rows for API-driven views."""
    return JsonResponse(_derived_summary_payload())




def api_task_list(request):
    """Return persisted task history for external/API consumers."""
    limit = min(_page_size(request, default=50), 200)
    return JsonResponse({"tasks": [_task_payload(task) for task in task_manager.list(limit=limit)]})


def api_task_detail(request, task_id):
    """Return one persisted task status and its latest log tail."""
    task = task_manager.get(task_id)
    if not task:
        raise Http404(_("Tarea no encontrada."))
    return JsonResponse(_task_payload(task, include_output=True))


def api_visual_assets(request, entity_type, entity_key):
    """Return persisted local/remote flag and coat assets for one entity."""
    return JsonResponse({
        "ok": True,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "assets": _visual_assets_payload(entity_type, entity_key),
    })


def _task_payload(task, *, include_output: bool = False) -> dict:
    error_info = _task_error_info(task)
    payload = {
        "id": task.id,
        "key": task.key,
        "label": task.label,
        "status": task.status,
        "is_active": task.is_active,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "returncode": task.returncode,
        "command": task.command_display,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
        "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
        "error_code": error_info["code"],
        "error_severity": error_info.get("severity", ""),
        "error_message": error_info.get("message", ""),
        "error_description": error_info["description"],
    }
    if include_output:
        payload["output"] = task_manager.output_tail_text(task)
    return payload


def _task_started_payload(task, *, label: str = "", summary_url: str = "") -> dict:
    """Return the compact JSON contract consumed by the shared task popup."""
    task_id = getattr(task, "id", "")
    return {
        "ok": True,
        "task_id": task_id,
        "label": label or getattr(task, "label", ""),
        "status": getattr(task, "status", "queued"),
        "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task_id}),
        "summary_url": summary_url,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task_id}),
    }


def dashboard_population_data(request):
    """Return root-country population rows for the dashboard pie chart."""
    return JsonResponse(_country_summary_payload(detail_route="ciudades_del_mundo:dashboard_country_detail"))


def _country_summary_payload(*, detail_route: str, include_visual_assets: bool = False) -> dict:
    countries = _admin_root_population_rows(
        detail_route=detail_route,
        include_visual_assets=include_visual_assets,
    )
    population_total = sum(row["population"] for row in countries)
    area_total = sum(row["area_km2"] or 0 for row in countries)
    return {
        "type": "donut",
        "total": population_total,
        "countries": countries,
        "items": [
            {
                "key": row["code"],
                "label": row["label"],
                "value": row["population"],
                "detail_url": row["detail_url"],
            }
            for row in countries
        ],
        "charts": {
            "population": {
                "type": "donut",
                "total": population_total,
                "items": [
                    {
                        "key": row["code"],
                        "label": row["label"],
                        "value": row["population"],
                        "detail_url": row["detail_url"],
                    }
                    for row in countries
                ],
            },
            "area": {
                "type": "donut",
                "total": area_total,
                "items": [
                    {
                        "key": row["code"],
                        "label": row["label"],
                        "value": row["area_km2"] or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in sorted(countries, key=lambda item: item["area_km2"] or 0, reverse=True)
                    if row["area_km2"]
                ],
            },
        },
    }


def _new_country_summary_payload() -> dict:
    countries = _new_country_container_rows(
        list(DerivedCountry.objects.prefetch_related("configs").order_by("name", "slug"))
    )
    population_total = sum(row.get("population") or 0 for row in countries)
    area_total = sum(row.get("area_km2") or 0 for row in countries)
    return {
        "type": "donut",
        "total": population_total,
        "countries": countries,
        "items": [
            {
                "key": row["key"],
                "label": row["label"],
                "value": row.get("population") or 0,
                "detail_url": row["detail_url"],
            }
            for row in countries
        ],
        "charts": {
            "population": {
                "type": "donut",
                "total": population_total,
                "items": [
                    {
                        "key": row["key"],
                        "label": row["label"],
                        "value": row.get("population") or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in countries
                ],
            },
            "area": {
                "type": "donut",
                "total": area_total,
                "items": [
                    {
                        "key": row["key"],
                        "label": row["label"],
                        "value": row.get("area_km2") or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in sorted(countries, key=lambda item: item.get("area_km2") or 0, reverse=True)
                    if row.get("area_km2")
                ],
            },
        },
    }


def dashboard_derived_data(request):
    """Return derived-country population rows for the dashboard bar chart."""
    return JsonResponse(_derived_summary_payload())


def _derived_summary_payload() -> dict:
    derived = list(
        NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    _apply_country_labels(derived, _nuevo_country_label_map())
    return {
        "type": "bar",
        "items": _bar_rows(derived, "display_label", "population", limit=10),
    }


def dashboard_country_detail(request, country_code):
    """Return dashboard detail data for one imported country."""
    return _country_detail_response(request, country_code)


def _country_detail_response(request, country_code):
    country_code = country_code.lower()
    if not _visible_admin_areas().filter(country_code=country_code).exists():
        raise Http404(_("No existe el pais '%(country_code)s'.") % {"country_code": country_code})
    return JsonResponse(
        _country_detail_payload(
            country_code,
            request.GET.get("level"),
            page=request.GET.get("page"),
            page_size=_country_browser_page_size(request),
        )
    )


def admin_area_list(request):
    """Render filters for persisted `AdminArea` rows; table loads asynchronously."""
    context = {
        "table_url": reverse("ciudades_del_mundo:admin_area_table"),
        "countries": _admin_country_options(),
        "entity_types": (
            _visible_admin_areas().exclude(entity_type__isnull=True)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        ),
        "merge_statuses": AdminArea.CityMergeStatus.choices,
        "sort_choices": _admin_sort_choices(),
    }
    return render(request, "ciudades_del_mundo/admin_area_list.html", context)


def admin_area_table(request):
    """Render the asynchronous AdminArea table partial."""
    areas = _filtered_admin_areas(request)
    paginator = Paginator(areas, _page_size(request, default=50))
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "ciudades_del_mundo/partials/admin_area_table.html",
        {
            "page_obj": page_obj,
            "querystring": _querystring_without_page(request),
        },
    )


def config_list(request):
    """List SQL-backed scraping configs with operational actions."""
    try:
        bootstrap_status = ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError):
        bootstrap_status = scraping_config_bootstrap_status()
    context = {
        "config_table_url": reverse("ciudades_del_mundo:config_table"),
        "config_tasks_table_url": reverse("ciudades_del_mundo:config_tasks_table"),
        "config_validate_all_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "validate"}),
        "config_scrape_unpopulated_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "scrape-unpopulated"}),
        "config_scrape_all_url": reverse("ciudades_del_mundo:start_all_config_task", kwargs={"action": "scrape"}),
        "config_import_toml_url": reverse("ciudades_del_mundo:config_import_toml"),
        "config_bootstrap_url": reverse("ciudades_del_mundo:config_bootstrap"),
        "config_bootstrap_status": bootstrap_status.as_dict(),
    }
    return render(request, "ciudades_del_mundo/config_list.html", context)


def config_bootstrap(request):
    """Synchronously populate the SQL config table from bundled TOML files."""
    if request.method not in {"POST", "GET"}:
        return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
    try:
        status = ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError) as exc:
        return JsonResponse(
            {
                "ok": False,
                "ready": False,
                "error": _("La tabla de configuraciones todavía no está disponible: %(error)s") % {"error": exc},
            },
            status=503,
        )
    return JsonResponse({"ok": status.ready, **status.as_dict()})


def config_table(request):
    """Render the asynchronous SQL-backed config table partial."""
    try:
        ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError):
        pass
    rows = _filtered_config_summaries(request)
    return render(
        request,
        "ciudades_del_mundo/partials/config_table.html",
        {
            "rows": rows,
            "page_size": _page_size(request, default=25),
        },
    )


def _task_table_context(tasks, *, page_size: int, compact: bool = True) -> dict:
    """Return shared context for task tables, including a cheap polling signature."""
    snapshot = "|".join(
        f"{task.id}:{task.status}:{task.started_at or task.created_at}:{task.finished_at or ''}"
        for task in tasks
    )
    return {
        "tasks": tasks,
        "page_size": page_size,
        "compact": compact,
        "has_active_tasks": any(task.is_active for task in tasks),
        "task_table_snapshot": snapshot,
    }


def config_tasks_table(request):
    """Render the asynchronous recent config/task history table partial."""
    tasks = _filtered_tasks(request, limit=100)
    return render(
        request,
        "ciudades_del_mundo/partials/config_tasks_table.html",
        _task_table_context(tasks, page_size=_page_size(request, default=10), compact=True),
    )


def _try_materialize_configured_city_rows(config_record: ScrapingConfig) -> None:
    """Best-effort DB refresh for [[cities]] edited after a country was populated."""
    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            materialize_configured_cities_for_record(config_record)
        else:
            with write_lock:
                materialize_configured_cities_for_record(config_record)
    except (OperationalError, ProgrammingError, ValueError):
        return


def config_new(request):
    """Create a new SQL-backed scraping config."""
    wants_json = _wants_json(request)
    default_content = (
        "scrape_schema_version = 2\n\n"
        "[[pages]]\n"
        'source = "cities"\n'
        'path = [""]\n'
    )
    slug = ""
    content = default_content
    if request.method == "POST":
        try:
            slug = _normalize_config_slug(request.POST.get("slug", ""))
            if _config_exists(slug):
                raise ValueError(_("Ya existe una configuración para '%(slug)s'.") % {"slug": slug})
            content = _config_content_from_request(request, slug, default_content, existing=False)
            _validate_config_text(slug, content)
            saved_config = upsert_scraping_config(slug, content)
            _persist_manual_asset_overrides(slug, content)
            _try_materialize_configured_city_rows(saved_config)
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            message = _("Configuración '%(slug)s' creada.") % {"slug": slug}
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "slug": slug,
                        "message": message,
                        "redirect_url": reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}),
                        "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                    }
                )
            messages.success(request, message)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)
    return render(request, "ciudades_del_mundo/config_form.html", _config_form_context("new", slug, content))


def config_edit(request, slug):
    """Edit a SQL-backed scraping config without starting scrape work automatically."""
    slug = _normalize_config_slug(slug)
    config_record = _config_record(slug)
    if config_record is None:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    wants_json = _wants_json(request)
    active_scrape = task_manager.latest_for_key(f"scrape:{slug}")
    content = config_record.content

    if request.method == "POST":
        try:
            content = _config_content_from_request(request, slug, content, existing=True)
            _validate_config_text(slug, content)
            saved_config = upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _persist_manual_asset_overrides(slug, content)
            _try_materialize_configured_city_rows(saved_config)
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            message = _("Configuración '%(slug)s' guardada.") % {"slug": slug}
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "slug": slug,
                        "message": message,
                        "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                    }
                )
            messages.success(request, message)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)

    return render(request, "ciudades_del_mundo/config_form.html", _config_form_context("edit", slug, content, active_scrape))



def config_export_data_csv(request, slug):
    """Download all persisted AdminArea rows for this config/country as CSV."""
    slug = _normalize_config_slug(slug)
    config_record = _config_record(slug)
    if config_record is None:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})

    country_code = _config_country_code_for_slug(slug)
    filename = f"{country_code or slug}_admin_areas.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")

    writer = csv.writer(response)
    headers = [
        "id",
        "country_code",
        "code",
        "name",
        "level",
        "parent_id",
        "parent_code",
        "parent_name",
        "parent_level",
        "children_count",
        "entity_type",
        "raw_entity_type",
        "city_merge_status",
        "area_km2",
        "density",
        "pop_latest",
        "pop_latest_date",
        "last_census_year",
        "url",
        "data_wd",
        "annotations",
        "most_populate_city_id",
        "flag_status",
        "flag_wikidata_id",
        "flag_commons_filename",
        "flag_local_path",
        "coat_status",
        "coat_wikidata_id",
        "coat_commons_filename",
        "coat_local_path",
        "created_at",
        "updated_at",
    ]
    writer.writerow(headers)

    areas = list(
        AdminArea.objects.filter(country_code=country_code)
        .select_related("parent", "most_populate_city")
        .annotate(children_count=Count("children"))
        .order_by("level", "parent_id", "name", "code")
    )
    assets_by_key = _config_export_asset_map(country_code)

    for area in areas:
        flag = assets_by_key.get(("admin_area", str(area.id), "flag"), {})
        coat = assets_by_key.get(("admin_area", str(area.id), "coat"), {})
        writer.writerow(
            [
                area.id,
                area.country_code,
                area.code,
                area.name,
                area.level,
                area.parent_id or "",
                area.parent.code if area.parent else "",
                area.parent.name if area.parent else "",
                area.parent.level if area.parent else "",
                area.children_count,
                area.entity_type or "",
                area.raw_entity_type or "",
                area.city_merge_status,
                area.area_km2 if area.area_km2 is not None else "",
                area.density if area.density is not None else "",
                area.pop_latest if area.pop_latest is not None else "",
                area.pop_latest_date.isoformat() if area.pop_latest_date else "",
                area.last_census_year if area.last_census_year is not None else "",
                area.url or "",
                area.data_wd or "",
                area.annotations or "",
                area.most_populate_city_id or "",
                flag.get("status", ""),
                flag.get("wikidata_id", ""),
                flag.get("commons_filename", ""),
                flag.get("local_path", ""),
                coat.get("status", ""),
                coat.get("wikidata_id", ""),
                coat.get("commons_filename", ""),
                coat.get("local_path", ""),
                area.created_at.isoformat(sep=" ", timespec="seconds") if area.created_at else "",
                area.updated_at.isoformat(sep=" ", timespec="seconds") if area.updated_at else "",
            ]
        )
    return response


def _config_export_asset_map(country_code: str) -> dict[tuple[str, str, str], dict[str, str]]:
    """Return visual assets keyed by (entity_type, entity_key, kind)."""
    if not country_code or not visual_asset_tables_exist():
        return {}
    assets: dict[tuple[str, str, str], dict[str, str]] = {}
    try:
        queryset = VisualAsset.objects.filter(
            country_code=country_code,
            entity_type="admin_area",
            kind__in=("flag", "coat"),
        ).only(
            "entity_type",
            "entity_key",
            "kind",
            "status",
            "wikidata_id",
            "commons_filename",
            "local_path",
        )
        for asset in queryset:
            assets[(asset.entity_type, str(asset.entity_key), asset.kind)] = {
                "status": asset.status or "",
                "wikidata_id": asset.wikidata_id or "",
                "commons_filename": asset.commons_filename or "",
                "local_path": asset.local_path or "",
            }
    except (OperationalError, ProgrammingError):
        return {}
    return assets

def config_export_toml(request, slug):
    """Export one SQL-backed config to ciudades_del_mundo/subdivisions/<slug>.toml."""
    wants_json = _wants_json(request)
    slug = _normalize_config_slug(slug)
    config_record = _config_record(slug)
    if config_record is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    try:
        exported = export_scraping_configs_to_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported:
            message = _("Configuración exportada a subdivisions/%(slug)s.toml.") % {"slug": slug}
            if wants_json:
                return JsonResponse({"ok": True, "message": message, "exported": True})
            messages.success(request, message)
        else:
            message = _("El fichero subdivisions/%(slug)s.toml ya estaba actualizado.") % {"slug": slug}
            if wants_json:
                return JsonResponse({"ok": True, "message": message, "exported": False})
            messages.info(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


def config_import_toml_slug(request, slug):
    """Import one ciudades_del_mundo/subdivisions/<slug>.toml seed into SQL."""
    wants_json = _wants_json(request)
    slug = _normalize_config_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not bundled_toml_config_paths([slug]):
        error = _("No existe el fichero subdivisions/%(slug)s.toml.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            imported = sync_scraping_configs_from_toml(force=True, only_if_empty=False, slugs=[slug])
        else:
            with write_lock:
                imported = sync_scraping_configs_from_toml(force=True, only_if_empty=False, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo importar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    if imported:
        message = _("Configuración importada desde subdivisions/%(slug)s.toml.") % {"slug": slug}
    else:
        message = _("El fichero subdivisions/%(slug)s.toml no modificó la configuración.") % {"slug": slug}
    if wants_json:
        return JsonResponse({"ok": True, "message": message, "imported": bool(imported)})
    messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


def config_city_merge_export_toml(request, slug):
    """Export only [[cities]] unification blocks to ciudades_del_mundo/cities_merge/<slug>.toml."""
    wants_json = _wants_json(request)
    slug = _normalize_config_slug(slug)
    config_record = _config_record(slug)
    if config_record is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    try:
        export_city_merges_to_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        message = _("TOML exportado correctamente.")
        if wants_json:
            return JsonResponse({"ok": True, "message": message, "exported": True})
        messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


def config_city_merge_import_toml(request, slug):
    """Import ciudades_del_mundo/cities_merge/<slug>.toml into existing SQL config [[cities]] blocks."""
    wants_json = _wants_json(request)
    slug = _normalize_config_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if _config_record(slug) is None:
        error = _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)
    if not bundled_city_merge_toml_paths([slug]):
        error = _("No se pudo importar el TOML.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            sync_city_merges_from_toml(force=True, slugs=[slug])
        else:
            with write_lock:
                sync_city_merges_from_toml(force=True, slugs=[slug])
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo importar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:config_edit", slug=slug)

    message = _("TOML importado correctamente.")
    if wants_json:
        return JsonResponse({"ok": True, "message": message, "imported": True})
    messages.success(request, message)
    return redirect("ciudades_del_mundo:config_edit", slug=slug)


def config_import_toml(request):
    """Import bundled subdivision TOML files into SQL in a background task."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if not _web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    if not scraping_config_table_exists():
        error = _("La tabla de configuraciones todavía no está disponible. Ejecuta 'py manage.py migrate'.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    label = _("Importar TOML de subdivisión")
    try:
        task = task_manager.start(
            key="config-import:toml",
            label=label,
            args=["sync_scraping_configs", "--force"],
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": "",
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


def start_all_config_task(request, action):
    """Start a bulk validation or validate-then-scrape task for SQL configs."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if not _web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if action in {"scrape", "scrape-unpopulated"}:
        bootstrap_ready, bootstrap_error = _ensure_bulk_configs_available_for_task()
        if not bootstrap_ready and bootstrap_error:
            if wants_json:
                return JsonResponse({"ok": False, "error": bootstrap_error}, status=503)
            messages.error(request, bootstrap_error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    if action == "validate":
        eligible_slugs = _eligible_config_slugs_for_bulk("validate")
        if not eligible_slugs:
            error = _("No hay configuraciones pendientes de validar.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "validate-config:all"
        label = _("Validar configuraciones pendientes (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["validate_subdivision_configs", *eligible_slugs]
    elif action == "scrape-unpopulated":
        eligible_slugs = _eligible_config_slugs_for_bulk("scrape-unpopulated")
        if not eligible_slugs:
            error = _("No hay configuraciones disponibles para popular.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "scrape:unpopulated"
        label = _("Popular configuraciones no populadas (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["validate_and_scrape_configs", *eligible_slugs, "--no-download-assets", "--page-workers=4", "--country-workers=1"]
    elif action == "scrape":
        eligible_slugs = _eligible_config_slugs_for_bulk("scrape")
        if not eligible_slugs:
            error = _("No hay configuraciones disponibles para popular.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = "scrape:all"
        label = _("Popular configuraciones disponibles (%(count)s)") % {"count": len(eligible_slugs)}
        args = ["repopulate_configs", *eligible_slugs, "--no-download-assets", "--page-workers=4", "--country-workers=1"]
    else:
        error = _("Acción de configuración no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": "",
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


def start_config_task(request, slug, action):
    """Start validation or scraping for one SQL config."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no soportado.")}, status=405)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    try:
        slug = _normalize_config_slug(slug)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    config_ready, config_error = _ensure_config_available_for_task(slug, import_from_toml=action == "scrape")
    if not config_ready:
        error = config_error or _("No existe la configuracion '%(slug)s'.") % {"slug": slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    if not _web_task_table_exists():
        error = _(
            "La tabla de tareas no existe todavía. Ejecuta 'py manage.py migrate' y recarga la página."
        )
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    row = _decorate_config_workflow_flags(_config_summary_for_slug(slug))
    current_status = row.get("status_filter") or _config_row_status(row)

    if action == "stop":
        task = row.get("active_task")
        if not task or not getattr(task, "is_active", False):
            error = _("No hay una tarea activa para parar en esta configuración.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        task = task_manager.cancel(task.id) or task
        label = _("Parar tarea: %(slug)s") % {"slug": slug}
        if wants_json:
            next_row = _decorate_config_workflow_flags(_config_summary_for_slug(slug))
            return JsonResponse(
                {
                    "ok": True,
                    "task_id": task.id,
                    "label": label,
                    "status": next_row.get("status_filter") or next_row.get("task_display_status") or "pending",
                    "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                    "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                    "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
                }
            )
        messages.info(request, _("Parada solicitada para '%(label)s'.") % {"label": task.label})
        return redirect("ciudades_del_mundo:task_detail", task_id=task.id)

    if action == "validate":
        if not row.get("can_validate"):
            error = _("Esta configuración ya está validada o populada. Modifica la configuración SQL antes de volver a validar.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"validate-config:{slug}"
        label = _("Validar configuración: %(slug)s") % {"slug": slug}
        args = ["validate_subdivision_configs", slug]
    elif action == "scrape":
        if not row.get("can_scrape"):
            error = _("Solo puedes popular una configuración Por validar, Validada, Populada o en Fallo.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"scrape:{slug}"
        if current_status == "populated":
            label = _("Re-popular datos: %(slug)s") % {"slug": slug}
            args = ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"]
        else:
            label = _("Popular datos: %(slug)s") % {"slug": slug}
            if current_status == "validated":
                args = ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"]
            else:
                args = ["validate_and_scrape_configs", slug, "--no-download-assets", "--page-workers=4"]
    elif action == "asset-corrections":
        if int(row.get("rows") or 0) <= 0:
            error = _("Primero debes popular el país antes de guardar correcciones de escudos y banderas.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        try:
            config_record = _config_record(slug)
            if config_record is None:
                raise ValueError(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
            content = _config_content_from_request(request, slug, config_record.content, existing=True)
            _validate_config_text(slug, content)
            saved_config = upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _persist_manual_asset_overrides(slug, content)
            _try_materialize_configured_city_rows(saved_config)
            export_scraping_configs_to_toml(force=True, slugs=[slug])
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        except Exception as exc:  # noqa: BLE001 - keep AJAX responses JSON.
            error = _("No se pudieron guardar/exportar las correcciones: %(error)s") % {"error": exc}
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=500)
            messages.error(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
        key = f"asset-corrections:{slug}"
        label = _("Guardar correcciones de assets: %(slug)s") % {"slug": slug}
        args = ["apply_config_asset_overrides", slug]
    elif action == "clear":
        if not row.get("can_clear"):
            error = _("No hay datos")
            if current_status in ACTIVE_CONFIG_OPERATION_STATUSES:
                error = _("No puedes limpiar mientras hay una operación activa en esta configuración.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.info(request, error)
            return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
        key = f"clear-config:{slug}"
        label = f"{_('Limpiar')}: {slug}"
        args = ["clear_config_data_with_assets", row.get("country_code") or slug]
    else:
        error = _("Acción de configuración no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "task_id": task.id,
                "label": label,
                "status": task.status,
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "summary_url": reverse("ciudades_del_mundo:config_summary", kwargs={"slug": slug}),
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
            }
        )
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)



def _terminal_config_progress_status(task) -> str:
    """Return per-config terminal status inferred from the owning task."""
    if not task or getattr(task, "is_active", False):
        return ""
    key = str(getattr(task, "key", "") or "")
    status = str(getattr(task, "status", "") or "")
    if status == "succeeded":
        if key.startswith("scrape:"):
            return "populated"
        if key.startswith("validate-config:"):
            return "validated"
        if key.startswith("clear-config:"):
            return "pending"
        if key.startswith("asset-corrections:"):
            return "populated"
    if status == "cancelled":
        return "cancelled"
    if status == "failed":
        return "failed"
    return ""


def _progress_status_after_terminal_task(current_status: str, terminal_status: str) -> str:
    """Map stale progress-file statuses once the parent WebTask is terminal."""
    current_status = str(current_status or "").strip().casefold()
    terminal_status = str(terminal_status or "").strip().casefold()
    active_statuses = {"validating", "populating", "clearing", "running", "queued"}
    if terminal_status == "cancelled":
        if current_status == "populating":
            return "validated"
        if current_status in {"validating", "clearing", "running", "queued", ""}:
            return "pending"
        return current_status
    if terminal_status == "failed":
        return "failed" if current_status in active_statuses else current_status
    if terminal_status in {"validated", "populated"}:
        finishable = active_statuses | {"succeeded", "validated", "populated"}
        return terminal_status if current_status in finishable else current_status
    if terminal_status == "pending":
        return "pending" if current_status in active_statuses | {"pending"} else current_status
    return current_status


def _single_config_slug_from_task_key(task) -> str:
    key = str(getattr(task, "key", "") or "")
    for prefix in ("scrape:", "validate-config:", "clear-config:"):
        if key.startswith(prefix):
            slug = key.split(":", 1)[1]
            return "" if slug in {"all", "unpopulated"} else slug
    return ""

def task_status(request, task_id):
    """Return current state for a web-launched task."""
    task = task_manager.get(task_id)
    if not task:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": _("No existe la tarea solicitada.")}, status=404)
        raise Http404(_("No existe la tarea solicitada."))
    config_progress = read_task_config_progress(task.id)
    terminal_status = _terminal_config_progress_status(task)
    if terminal_status:
        slug = _single_config_slug_from_task_key(task)
        summary_status = ""
        if terminal_status == "cancelled" and slug:
            summary_status = str(_config_summary_for_slug(slug).get("task_display_status") or "")
        for item in config_progress.values():
            if isinstance(item, dict):
                next_status = summary_status or _progress_status_after_terminal_task(
                    str(item.get("status") or ""), terminal_status
                )
                if next_status:
                    item["status"] = next_status
        if slug and not config_progress:
            config_progress = {slug: {"status": summary_status or terminal_status, "detail": ""}}
    error_info = _task_error_info(task)
    if error_info["code"] and isinstance(config_progress, dict):
        for item in config_progress.values():
            if isinstance(item, dict):
                item.setdefault("error_code", error_info["code"])
                item.setdefault("error_severity", error_info.get("severity", ""))
                item.setdefault("error_message", error_info.get("message", ""))
                item.setdefault("error_description", error_info["description"])
    payload = {
        "id": task.id,
        "label": task.label,
        "status": task.status,
        "is_active": task.is_active,
        "returncode": task.returncode,
        "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
        "config_progress": config_progress,
        "error_code": error_info["code"],
        "error_severity": error_info.get("severity", ""),
        "error_message": error_info.get("message", ""),
        "error_description": error_info["description"],
    }
    if "since" in request.GET:
        try:
            offset = int(request.GET.get("since") or 0)
        except (TypeError, ValueError):
            offset = 0
        delta, next_offset, reset = task_manager.output_since_text(task, offset)
        payload.update({
            "output_delta": delta,
            "output_offset": next_offset,
            "output_reset": reset,
        })
    else:
        payload["output"] = task_manager.output_tail_text(task)
    return JsonResponse(payload)


def config_summary(request, slug):
    """Return an updated row summary for one config after an async task."""
    slug = _normalize_config_slug(slug)
    row = _config_summary_for_slug(slug)
    task = row.get("active_task")
    validate_task = row.get("validate_task")
    scrape_task = row.get("scrape_task")
    clear_task = row.get("clear_task")
    task_display_status = row.get("task_display_status") or _config_task_display_status(task)
    status_filter = _config_row_status({**row, "task_display_status": task_display_status})
    workflow_row = _decorate_config_workflow_flags({**row, "task_display_status": task_display_status})
    error_info = _task_error_info(task, validation_error=row.get("error") or "")
    return JsonResponse(
        {
            "slug": row["slug"],
            "name": row["name"],
            "country_code": row["country_code"],
            "country_label": row["country_label"],
            "pages": row["pages"],
            "cities": row["cities"],
            "rows": row["rows"],
            "task_status": task_display_status,
            "status_filter": status_filter,
            "task_is_active": task.is_active if task else False,
            "task_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}) if task else "",
            "validate_task_status": _config_task_display_status(validate_task),
            "validate_task_is_active": validate_task.is_active if validate_task else False,
            "scrape_task_status": _config_task_display_status(scrape_task),
            "scrape_task_is_active": scrape_task.is_active if scrape_task else False,
            "clear_task_status": _config_task_display_status(clear_task),
            "clear_task_is_active": clear_task.is_active if clear_task else False,
            "can_validate": bool(workflow_row.get("can_validate")),
            "can_scrape": bool(workflow_row.get("can_scrape")),
            "can_clear": bool(workflow_row.get("can_clear")),
            "can_resume": bool(workflow_row.get("can_resume")),
            "can_stop": bool(workflow_row.get("can_stop")),
            "error": row.get("error") or "",
            "error_code": error_info["code"],
            "error_severity": error_info.get("severity", ""),
            "error_message": error_info.get("message", ""),
            "error_description": error_info["description"],
        }
    )


def config_editor_data(request, slug):
    """Return config content/manual data after the page shell has loaded."""
    slug = _normalize_config_slug(slug)
    record = _config_record(slug)
    if not record:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": _("No existe la configuracion '%(slug)s'.") % {"slug": slug}}, status=404)
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    workflow = _decorate_config_workflow_flags(_config_summary_for_slug(slug))
    asset_editor_enabled = False
    content = record.content or ""
    return JsonResponse(
        {
            "ok": True,
            "content": content,
            "manual": _parse_config_editor_data(slug, content, asset_editor_enabled=asset_editor_enabled),
            # Keep the editor-data endpoint fully JSON-serializable: workflow rows
            # contain WebTask model instances used by server-rendered templates.
            "workflow": _config_workflow_json_payload(workflow),
        }
    )



def config_source_entities(request, slug):
    """Return populated source entities for the manual city-unification UI."""
    slug = _normalize_config_slug(slug)
    if not _config_exists(slug):
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    country_code = _config_country_code_for_slug(slug)
    if request.GET.get("asset_options") == "1":
        return JsonResponse(
            _asset_assignment_options_payload(
                country_code,
                level=request.GET.get("level"),
                entity_id=request.GET.get("entity_id"),
            )
        )
    entities = _source_entities_for_config(country_code)
    level_rows = _source_level_filter_options(entities)
    entity_type_rows = _source_entity_type_filter_options(entities)
    parent_rows = _source_parent_filter_options(entities)
    parent_rows_by_level = {
        row["value"]: _source_parent_filter_options(entities, level=row["value"])
        for row in level_rows
    }
    return JsonResponse(
        {
            "entities": entities,
            "levels": level_rows,
            "entity_types": entity_type_rows,
            "parents": parent_rows,
            "parents_by_level": parent_rows_by_level,
        }
    )


def config_generate_base(request, slug):
    """Generate a draft TOML config by discovering links on CityPopulation."""
    if request.method != "POST":
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_edit", kwargs={"slug": slug}))
    slug = _normalize_config_slug(slug)
    try:
        content, log_lines = _generate_citypopulation_config(slug)
        _validate_config_text(slug, content)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "content": content, "manual": _parse_config_editor_data(slug, content), "log": "\n".join(log_lines)})


def config_ai_login(request, slug):
    """Redirect to the selected AI provider login page without storing credentials."""
    slug = _normalize_config_slug(slug)
    if not _config_exists(slug):
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    if not AI_CONFIG_ENABLED or not AI_PERSONAL_LOGIN_ENABLED:
        messages.error(request, _("La IA está deshabilitada por configuración."))
        return redirect(f"{reverse('ciudades_del_mundo:config_edit', kwargs={'slug': slug})}?tab=ai")

    provider = str(request.GET.get("provider") or "chatgpt").strip().lower()
    login_url = AI_PROVIDER_LOGIN_URLS.get(provider)
    if not login_url:
        messages.error(request, _("Proveedor IA no soportado: %(provider)s") % {"provider": provider})
        return redirect(f"{reverse('ciudades_del_mundo:config_edit', kwargs={'slug': slug})}?tab=ai")
    return redirect(login_url)


def recipe_list(request):
    """List available derived hierarchy recipes."""
    context = {
        "recipes": _recipe_summaries(),
        "recent_tasks": task_manager.list(limit=12),
    }
    return render(request, "ciudades_del_mundo/recipe_list.html", context)


def recipe_new(request):
    """Create a new Python recipe through a structured form."""
    default_divisions = json.dumps(
        [
            {
                "name": "Provincia ejemplo",
                "code": "PEJ",
                "entity_type": "Provincia",
                "capitals": ["Capital ejemplo"],
                "dat": {"2": ["Provincia fuente"]},
            }
        ],
        indent=2,
        ensure_ascii=False,
    )
    form = {
        "slug": "",
        "root_name": "",
        "source_country": "",
        "municipal_level": "",
        "representation_level": "",
        "representation_total": "",
        "representation_min": "",
        "divisions_json": default_divisions,
    }

    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            slug = _normalize_recipe_slug(form["slug"])
            path = _recipe_path(slug, group="new", must_exist=False)
            if path.exists():
                raise ValueError(_("Ya existe una receta nueva para '%(slug)s'.") % {"slug": slug})
            content = _render_recipe_from_form(form)
            _validate_recipe_text(content, filename=str(path))
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Receta '%(slug)s' creada.") % {"slug": slug})
            return redirect("ciudades_del_mundo:recipe_edit", slug=slug)

    return render(request, "ciudades_del_mundo/recipe_new.html", {"form": form})


def recipe_edit(request, slug):
    """Edit a Python recipe in `new_subdivisions`."""
    slug = _normalize_recipe_slug(slug)
    path = _recipe_path(slug, group="new")
    active_build = task_manager.latest_for_key(f"build:{slug}")

    if request.method == "POST":
        content = request.POST.get("content", "")
        try:
            _validate_recipe_text(content, filename=str(path))
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Receta '%(slug)s' guardada.") % {"slug": slug})
            if active_build and active_build.is_active:
                replacement = task_manager.start(
                    key=f"build:{slug}",
                    label=_("Crear subdivisiones: %(slug)s") % {"slug": slug},
                    args=active_build.args,
                )
                messages.info(
                    request,
                    _("Habia una construccion activa para esta receta; se cancelo y se relanzo."),
                )
                return redirect("ciudades_del_mundo:task_detail", task_id=replacement.id)
            return redirect("ciudades_del_mundo:recipe_edit", slug=slug)

    return render(
        request,
        "ciudades_del_mundo/recipe_edit.html",
        {
            "slug": slug,
            "content": path.read_text(encoding="utf-8"),
            "active_task": active_build,
        },
    )


def start_recipe_task(request, slug, action):
    """Start build/export tasks for a derived recipe."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:recipe_list")

    slug = _normalize_recipe_slug(slug)
    if action == "build":
        _any_recipe_path(slug)
        args = ["build_new_subdivisions", "--country-id", slug]
        population_year = (request.POST.get("population_year") or "").strip()
        if population_year:
            if not population_year.isdigit():
                error = _("El ano de poblacion debe ser numerico.")
                if wants_json:
                    return JsonResponse({"ok": False, "error": error}, status=400)
                messages.error(request, error)
                return redirect("ciudades_del_mundo:recipe_list")
            args.extend(["--population-year", population_year])
        key = f"build:{slug}"
        label = _("Crear subdivisiones: %(slug)s") % {"slug": slug}
    elif action == "export-csv":
        args = ["export_nuevoadmin_csv", "--country-id", slug]
        key = f"export-csv:{slug}"
        label = _("Exportar CSV: %(slug)s") % {"slug": slug}
    elif action == "export-excel":
        args = ["export_nuevoadmin_excel", "--country-id", slug]
        key = f"export-excel:{slug}"
        label = _("Exportar Excel: %(slug)s") % {"slug": slug}
    else:
        error = _("Accion de receta no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:recipe_list")
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(_task_started_payload(task, label=label))
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


def new_country_import_toml(request):
    """Queue one SQL import task per new-country TOML seed."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:new_country_list")
    paths = bundled_new_country_config_paths()
    label_template = _("Importar pais nuevo TOML: %(slug)s")
    try:
        queued_tasks = _queue_derived_seed_import_tasks(
            paths,
            section="new-countries",
            key_prefix="import-new-country",
            label_template=label_template,
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:new_country_list")
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    task_count = len(queued_tasks)
    if wants_json:
        if queued_tasks:
            task, label = queued_tasks[0]
            return JsonResponse(_task_started_payload(task, label=label))
        return JsonResponse({"ok": False, "error": _("No hay semillas TOML de nuevos paises para importar.")}, status=404)
    if task_count:
        messages.info(
            request,
            _("Encoladas %(count)s tarea(s) de importacion TOML para nuevos paises.")
            % {"count": task_count},
        )
    else:
        messages.warning(request, _("No hay semillas TOML de nuevos paises para importar."))
    return redirect("ciudades_del_mundo:task_list")


def group_import_toml(request):
    """Queue one SQL import task per subdivision-group TOML seed."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:group_list")
    country_code = _normalize_group_country_key(request.POST.get("country_code"))
    paths = bundled_subdivision_group_paths()
    if country_code:
        paths = _subdivision_group_seed_paths_for_country(paths, country_code)
    label_template = _("Importar grupo TOML: %(slug)s")
    try:
        queued_tasks = _queue_derived_seed_import_tasks(
            paths,
            section="groups",
            key_prefix="import-group",
            label_template=label_template,
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:group_list")
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    task_count = len(queued_tasks)
    if wants_json:
        if queued_tasks:
            task, label = queued_tasks[0]
            return JsonResponse(_task_started_payload(task, label=label))
        return JsonResponse({"ok": False, "error": _("No hay semillas TOML de grupos para importar.")}, status=404)
    if task_count:
        messages.info(
            request,
            _("Encoladas %(count)s tarea(s) de importacion TOML para grupos.")
            % {"count": task_count},
        )
    else:
        messages.warning(request, _("No hay semillas TOML de grupos para importar."))
    return redirect("ciudades_del_mundo:task_list")


def new_country_list(request):
    """Render source-country cards with editable derived-country containers."""
    countries = list(DerivedCountry.objects.prefetch_related("configs").order_by("name", "slug"))
    return render(
        request,
        "ciudades_del_mundo/new_country_list.html",
        {
            "country_cards": _new_country_cards(countries),
            "seed_count": len(bundled_new_country_config_paths()),
        },
    )


def new_country_new(request, source_country_code=""):
    """Create a derived-country container."""
    context_source_country_code = _normalize_group_country_key(
        source_country_code or request.GET.get("source_country_code")
    )
    form = {
        "slug": "",
        "name": "",
        "source_country_code": context_source_country_code,
        "description": "",
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        if context_source_country_code:
            form["source_country_code"] = context_source_country_code
        try:
            slug = _normalize_new_country_code(form["slug"])
            if DerivedCountry.objects.filter(slug=slug).exists():
                raise ValueError(_("Ya existe un pais nuevo con codigo '%(code)s'.") % {"code": slug})
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            country = DerivedCountry.objects.create(
                slug=slug,
                name=name,
                source_country_code=str(form["source_country_code"] or "").strip().lower(),
                description=str(form["description"] or "").strip(),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Pais nuevo '%(name)s' creado.") % {"name": country.name})
            return redirect(_new_country_detail_url(country))
    return render(
        request,
        "ciudades_del_mundo/new_country_form.html",
        {
            "form": form,
            "mode": "country",
            "source_country_label": _source_country_display_label(form["source_country_code"]),
        },
    )


def new_country_detail(request, source_country_code="", country_slug=""):
    """List configurations for one derived-country container."""
    country = _derived_country_or_404(country_slug)
    source_country_code = _normalize_group_country_key(source_country_code)
    expected_source = _normalize_group_country_key(country.source_country_code)
    if expected_source and source_country_code and source_country_code != expected_source:
        raise Http404(_("El pais nuevo no pertenece a '%(country)s'.") % {"country": source_country_code})
    if request.method == "POST":
        name = str(request.POST.get("name") or "").strip()
        description = str(request.POST.get("description") or "").strip()
        if not name:
            messages.error(request, _("El nombre es obligatorio."))
        else:
            country.name = name
            country.description = description
            country.save(update_fields=["name", "description"])
            messages.success(request, _("Datos guardados."))
        return redirect(_new_country_detail_url(country))
    configs = list(country.configs.order_by("name", "slug"))
    return render(
        request,
        "ciudades_del_mundo/new_country_detail.html",
        {
            "country": country,
            "form": {
                "slug": country.slug,
                "name": country.name,
                "description": country.description,
            },
            "tree_data_url": reverse(
                "ciudades_del_mundo:api_new_country_config_tree",
                kwargs={"country_slug": country.slug},
            ),
            "tree_levels": _new_country_config_level_options(configs),
            "country_detail_url": _new_country_detail_url(country),
            "export_excel_url": _new_country_detail_export_excel_url(country),
            "new_country_config_new_url": _new_country_config_new_url(country),
            "source_country_label": _source_country_display_label(country.source_country_code),
        },
    )


def new_country_detail_export_excel(request, source_country_code="", country_slug=""):
    """Download the visible new-country config hierarchy as an Excel workbook."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    rows = _new_country_config_rows(
        list(country.configs.order_by("name", "slug")),
        include_assigned_subdivisions=True,
    )
    workbook, export_rows, summary = _new_country_detail_export_workbook(country, rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = _safe_excel_filename(f"{country.slug}_{stamp}.xlsx")
    source_code = _normalize_group_country_key(country.source_country_code) or "_"
    output_path = Path(settings.BASE_DIR) / "excels" / "new-countries" / source_code / filename
    SimpleXlsxWriter().write(workbook, output_path)
    log_path = _write_new_country_detail_export_log(
        country,
        request=request,
        output_path=output_path,
        rows=export_rows,
        summary=summary,
    )
    response = HttpResponse(
        output_path.read_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Export-Log-Path"] = str(log_path)
    return response


def new_country_detail_export_excel_legacy(request, country_slug):
    """Redirect old new-country export URLs to the source-qualified route."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country = _derived_country_or_404(country_slug)
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return redirect(_new_country_detail_export_excel_url(country))
    return new_country_detail_export_excel(request, country_slug=country_slug)


def new_country_detail_legacy(request, country_slug):
    """Redirect old /new-countries/<region>/ URLs to /new-countries/<country>/<region>/."""
    country = _derived_country_or_404(country_slug)
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return redirect(_new_country_detail_url(country))
    return new_country_detail(request, country_slug=country_slug)


def new_country_config_delete(request, source_country_code="", country_slug="", config_slug=""):
    """Delete one SQL new-country entity configuration."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _derived_country_config_or_404(country, config_slug)
    record_slug = config.slug
    parent_slug = _new_country_config_parent_slug(config)
    with transaction.atomic():
        reparented_slugs = _reparent_new_country_config_children_before_delete(
            country,
            deleted_slug=record_slug,
            new_parent_slug=parent_slug,
        )
        materialized_deleted = _delete_materialized_rows_for_new_country_config(config)
        config.delete()
        _refresh_new_country_config_metrics_for_slugs(country, [parent_slug, *reparented_slugs])
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "deleted": True,
                "country_slug": country.slug,
                "config_slug": record_slug,
                "reparented_slugs": reparented_slugs,
                "materialized_deleted": materialized_deleted,
                "redirect_url": _new_country_detail_url(country),
            }
        )
    messages.success(request, _("Entidad eliminada."))
    return redirect(_new_country_detail_url(country))


def new_country_config_delete_legacy(request, country_slug, config_slug):
    """Redirect old delete URLs to the source-qualified new-country delete route."""
    country = _derived_country_or_404(country_slug)
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return new_country_config_delete(
            request,
            source_country_code=source_country_code,
            country_slug=country_slug,
            config_slug=config_slug,
        )
    return new_country_config_delete(request, country_slug=country_slug, config_slug=config_slug)


def new_country_config_clone(request, source_country_code="", country_slug="", config_slug=""):
    """Clone one SQL new-country entity configuration."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _derived_country_config_or_404(country, config_slug)
    try:
        with transaction.atomic():
            clone = _clone_new_country_config(config)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect(_new_country_detail_url(country))
    redirect_url = _new_country_config_edit_url(clone)
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "cloned": True,
                "country_slug": country.slug,
                "config_slug": clone.slug,
                "redirect_url": redirect_url,
            }
        )
    messages.success(request, _("Entidad clonada."))
    return redirect(redirect_url)


def new_country_config_clone_legacy(request, country_slug, config_slug):
    """Redirect old clone URLs to the source-qualified new-country clone route."""
    country = _derived_country_or_404(country_slug)
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return new_country_config_clone(
            request,
            source_country_code=source_country_code,
            country_slug=country_slug,
            config_slug=config_slug,
        )
    return new_country_config_clone(request, country_slug=country_slug, config_slug=config_slug)


def new_country_config_new(request, source_country_code="", country_slug=""):
    """Create a TOML configuration for one derived country."""
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    fixed_source_country_code = _normalize_group_country_key(country.source_country_code)
    source_country_code = str(
        fixed_source_country_code
        or request.POST.get("source_country_code")
        or request.GET.get("source_country_code")
        or ""
    ).strip().lower()
    form = {
        "slug": "",
        "name": "",
        "source_country_code": source_country_code,
        "parent_config_slug": "",
        "parent_level": "0",
        "entity_level": "1",
        "use_subdivisions": "",
        "entity_type": "Subdivision",
        "capitals": [],
        "content": "",
        "selection_json": "",
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        form["capitals"] = _derived_subdivision_capital_values_from_post(request.POST)
        if fixed_source_country_code:
            form["source_country_code"] = fixed_source_country_code
        try:
            requested_code = _new_country_entity_code_from_form(form["slug"])
            slug = _new_country_slug_from_entity_code(requested_code)
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = _normalize_new_country_code(country.slug)
            use_subdivisions = _new_country_selection_uses_subdivisions(form["selection_json"])
            sync_child_configs = _new_country_selection_has_child_config_slugs(form["selection_json"])
            selected_child_config_slugs = _new_country_selected_child_config_slugs_from_selection_json(
                form["selection_json"]
            )
            if use_subdivisions:
                selected_child_config_slugs = []
            parent_config_slug = _validate_new_country_config_parent(country, form["parent_config_slug"])
            parent_level = _new_country_parent_level(country, parent_config_slug)
            content = _derived_country_content_from_create_post(
                country=country,
                slug=slug,
                name=name,
                source_country_code=form["source_country_code"],
                derived_country_code=derived_code,
                raw_content=form["content"],
                selection_json=form["selection_json"],
                parent_config_slug=parent_config_slug,
                entity_level=parent_level + 1,
                entity_code=requested_code,
                entity_type=form["entity_type"],
                capitals=form["capitals"],
            )
            _validate_plain_toml(content, expected_kind="derived_country_config")
            with transaction.atomic():
                selected_child_config_slugs = _rename_new_country_selected_child_slug_collision(
                    country,
                    slug=slug,
                    use_subdivisions=use_subdivisions,
                    selected_child_config_slugs=selected_child_config_slugs,
                )
                config = DerivedCountryConfig.objects.create(
                    country=country,
                    slug=slug,
                    name=name,
                    source_country_code=str(form["source_country_code"] or "").strip().lower(),
                    derived_country_code=derived_code,
                    content=content,
                    is_active=True,
                )
                if sync_child_configs:
                    metric_refresh_slugs = _new_country_metric_refresh_slugs_for_child_sync(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                    _sync_new_country_config_children(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                else:
                    metric_refresh_slugs = []
                _refresh_new_country_config_metrics_for_slugs(
                    country,
                    [config.slug, *metric_refresh_slugs],
                )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' creada.") % {"slug": config.slug})
            return redirect(_new_country_config_edit_url(config))
    elif not form["content"]:
        form["content"] = _default_derived_country_toml(
            country=country,
            slug="nueva_configuracion",
            name="Nueva configuracion",
            source_country_code=country.source_country_code,
            derived_country_code=country.slug,
        )
    return render(
        request,
        "ciudades_del_mundo/new_country_config_create.html",
        {
            "country": country,
            "form": form,
            "config": None,
            "country_detail_url": _new_country_detail_url(country),
            "new_country_config_new_url": _new_country_config_new_url(country),
            "parent_options": _new_country_config_parent_options(country),
            "created_entity_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_entity_options",
                kwargs=_new_country_source_kwargs(country),
            ),
            "derived_subdivision_options": [],
            "selected_derived_subdivision_keys": _new_country_selected_subdivision_keys_from_selection_json(
                form.get("selection_json", ""),
                fallback_country_code=form["source_country_code"],
            ),
            "capital_options": _new_country_config_capital_options_payload(
                form["source_country_code"],
                country=country,
                selected_ids=form.get("capitals", []),
                selected_derived_subdivisions=_new_country_selected_derived_subdivisions_from_selection_json(
                    form.get("selection_json", ""),
                    fallback_country_code=form["source_country_code"],
                ),
                selected_child_config_slugs=(
                    []
                    if _form_bool(form.get("use_subdivisions"), default=False)
                    else _new_country_selected_child_config_slugs_from_selection_json(form.get("selection_json", ""))
                ),
                use_subdivisions=bool(form.get("use_subdivisions")),
            ),
            "capital_options_url": reverse(
                "ciudades_del_mundo:new_country_config_capital_options",
                kwargs=_new_country_source_kwargs(country),
            ),
            "derived_subdivision_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_subdivision_options",
                kwargs=_new_country_source_kwargs(country),
            ),
            "source_area_options_url": reverse(
                "ciudades_del_mundo:new_country_config_source_area_options",
                kwargs=_new_country_source_kwargs(country),
            ),
            "source_area_countries": _derived_source_country_options(),
            "source_countries": _new_country_created_subdivision_country_options(),
            "source_country_label": _source_country_display_label(form["source_country_code"]),
            "children_url": reverse("ciudades_del_mundo:new_country_source_children"),
        },
    )


def new_country_config_created_subdivision_options(request, source_country_code="", country_slug=""):
    """Return paginated created-subdivision options for a new-country config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    search_term = str(request.GET.get("q") or request.GET.get("term") or "").strip()
    country_filter = _normalize_group_country_key(request.GET.get("country") or request.GET.get("country_code"))
    try:
        queryset = _new_country_derived_subdivision_queryset(search_term, country_code=country_filter)
        paginator = Paginator(queryset, _new_country_option_page_size(request))
        page_obj = paginator.get_page(request.GET.get("page"))
        options = _new_country_derived_subdivision_options_from_records(list(page_obj.object_list))
    except (OperationalError, ProgrammingError):
        return JsonResponse(
            {
                "ok": True,
                "results": [],
                "page": 1,
                "page_size": NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
                "total": 0,
                "num_pages": 1,
                "has_next": False,
                "has_previous": False,
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "results": options,
            "page": page_obj.number,
            "page_size": page_obj.paginator.per_page,
            "total": page_obj.paginator.count,
            "num_pages": page_obj.paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        }
    )


def new_country_config_source_area_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return paginated SQL AdminArea options for a new-country config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    if config_slug and config_slug != "new":
        _derived_country_config_or_404(country, config_slug)
    country_filter = _normalize_group_country_key(
        request.GET.get("country")
        or request.GET.get("country_code")
        or country.source_country_code
        or source_country_code
    )
    page_size = _new_country_option_page_size(request)
    try:
        level_options = _source_level_filter_options_for_country(country_filter) if country_filter else []
    except (OperationalError, ProgrammingError, ValueError):
        level_options = []
    empty_payload = {
        "ok": True,
        "results": [],
        "levels": level_options,
        "page": 1,
        "page_size": page_size,
        "total": 0,
        "num_pages": 1,
        "has_next": False,
        "has_previous": False,
    }
    if not country_filter:
        return JsonResponse(empty_payload)
    raw_level = str(request.GET.get("level") or "").strip()
    if not raw_level:
        return JsonResponse(empty_payload)
    try:
        level = int(raw_level)
    except (TypeError, ValueError):
        return JsonResponse(empty_payload)
    if level <= 0:
        return JsonResponse(empty_payload)
    search_term = str(request.GET.get("q") or request.GET.get("term") or "").strip()
    try:
        queryset = _group_source_admin_areas().filter(country_code__iexact=country_filter, level=level)
        if search_term:
            queryset = queryset.filter(
                Q(id__icontains=search_term)
                | Q(code__icontains=search_term)
                | Q(name__icontains=search_term)
                | Q(entity_type__icontains=search_term)
                | Q(parent__name__icontains=search_term)
            )
            queryset = queryset.annotate(
                search_rank=Case(
                    When(name__iexact=search_term, then=Value(0)),
                    When(code__iexact=search_term, then=Value(1)),
                    When(id__iexact=search_term, then=Value(2)),
                    When(name__icontains=search_term, then=Value(3)),
                    When(code__icontains=search_term, then=Value(4)),
                    When(id__icontains=search_term, then=Value(5)),
                    When(entity_type__icontains=search_term, then=Value(6)),
                    When(parent__name__icontains=search_term, then=Value(7)),
                    default=Value(8),
                    output_field=IntegerField(),
                )
            )
        queryset = queryset.select_related("parent").only(
            "id",
            "country_code",
            "code",
            "name",
            "level",
            "entity_type",
            "parent_id",
            "parent__id",
            "parent__country_code",
            "parent__name",
        )
        if search_term:
            queryset = queryset.order_by("search_rank", "name", "code", "id")
        else:
            queryset = queryset.order_by("name", "code", "id")
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(request.GET.get("page"))
        options = _new_country_source_area_options_from_records(list(page_obj.object_list))
    except (OperationalError, ProgrammingError, ValueError):
        return JsonResponse(empty_payload)
    return JsonResponse(
        {
            "ok": True,
            "results": options,
            "levels": empty_payload["levels"],
            "page": page_obj.number,
            "page_size": page_obj.paginator.per_page,
            "total": page_obj.paginator.count,
            "num_pages": page_obj.paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        }
    )


def new_country_config_created_entity_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return paginated created new-country entity options for a config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    current_config = None
    if config_slug and config_slug != "new":
        current_config = _derived_country_config_or_404(country, config_slug)
    search_term = str(request.GET.get("q") or request.GET.get("term") or "").strip()
    try:
        queryset = _new_country_created_entity_queryset(
            country,
            search_term,
            current_config=current_config,
        )
        paginator = Paginator(queryset, _new_country_option_page_size(request))
        page_obj = paginator.get_page(request.GET.get("page"))
        options = _new_country_created_entity_options_from_records(list(page_obj.object_list))
    except (OperationalError, ProgrammingError):
        return JsonResponse(
            {
                "ok": True,
                "results": [],
                "page": 1,
                "page_size": NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
                "total": 0,
                "num_pages": 1,
                "has_next": False,
                "has_previous": False,
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "results": options,
            "page": page_obj.number,
            "page_size": page_obj.paginator.per_page,
            "total": page_obj.paginator.count,
            "num_pages": page_obj.paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        }
    )


def new_country_config_capital_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return capital options for a new-country config creation form."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    current_config = None
    if config_slug and config_slug != "new":
        current_config = _derived_country_config_or_404(country, config_slug)
    country_code = _normalize_group_country_key(country.source_country_code or source_country_code)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    selected_values = _derived_subdivision_capital_values_from_payload(body)
    current_data = None
    current_entity = None

    def current_config_entity() -> dict:
        nonlocal current_data, current_entity
        if current_config is None:
            return {}
        if current_data is None:
            current_data = _new_country_config_toml(current_config)
        if current_entity is None:
            current_entity = _new_country_config_first_entity(current_data)
        return current_entity

    selection_touched = _form_bool(body.get("selection_touched"), default=False)
    source_selection_touched = selection_touched or _form_bool(
        body.get("source_selection_touched"),
        default=False,
    )
    derived_selection_touched = selection_touched or _form_bool(
        body.get("derived_subdivision_selection_touched"),
        default=False,
    )
    selected_derived_subdivisions = _new_country_normalized_derived_subdivision_selections(
        body.get("selected_derived_subdivisions")
        or body.get("selected_subdivision_keys")
        or body.get("derived_subdivisions")
        or [],
        fallback_country_code=country_code,
    )
    has_use_subdivisions_payload = "use_subdivisions" in body
    use_subdivisions = body.get("use_subdivisions") in (True, "1", 1, "true", "True", "on")
    if current_config is not None and not has_use_subdivisions_payload:
        current_entity = current_config_entity()
        use_subdivisions = bool(current_entity.get("use_selected_entities_as_children")) or bool(
            _new_country_config_selected_derived_subdivisions(
                current_entity,
                fallback_country_code=country_code,
            )
        )
    if current_config is not None and use_subdivisions and not selected_derived_subdivisions and not derived_selection_touched:
        selected_derived_subdivisions = _new_country_config_selected_derived_subdivisions(
            current_config_entity(),
            fallback_country_code=country_code,
        )
    selected_source_ids = body.get("selected_ids") if isinstance(body.get("selected_ids"), list) else []
    if current_config is not None and use_subdivisions and not selected_source_ids and not source_selection_touched:
        if current_data is None:
            current_data = _new_country_config_toml(current_config)
        selected_source_ids = _new_country_config_selected_source_ids(
            current_data,
            current_config_entity(),
        )
    selected_child_config_slugs = [] if use_subdivisions else _new_country_child_config_slugs_for_capital_payload(
        body,
        country=country,
        current_config=current_config,
    )
    search_term = str(body.get("query") or body.get("term") or "").strip()
    options = _new_country_config_capital_options_payload(
        country_code,
        country=country,
        selected_ids=selected_values,
        selected_source_ids=selected_source_ids,
        selected_derived_subdivisions=selected_derived_subdivisions,
        selected_child_config_slugs=selected_child_config_slugs,
        search_term=search_term,
        use_subdivisions=use_subdivisions,
    )
    option_values = {option["value"] for option in options}
    selected_values = [value for value in selected_values if value in option_values]
    return JsonResponse(
        {
            "ok": True,
            "capital": selected_values[0] if selected_values else "",
            "capitals": selected_values,
            "capital_options": options,
        }
    )


def new_country_config_new_legacy(request, country_slug):
    """Redirect old config-create URLs to the source-qualified route."""
    country = _derived_country_or_404(country_slug)
    return redirect(_new_country_config_new_url(country))


def new_country_config_edit(request, source_country_code="", country_slug="", config_slug=""):
    """Edit one TOML-backed derived-country configuration."""
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _derived_country_config_or_404(country, config_slug)
    form = _new_country_config_visual_form(country, config)
    fixed_source_country_code = _normalize_group_country_key(country.source_country_code)
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        form["capitals"] = _derived_subdivision_capital_values_from_post(request.POST)
        if fixed_source_country_code:
            form["source_country_code"] = fixed_source_country_code
        try:
            requested_code = _new_country_entity_code_from_form(form["slug"])
            current_code = _new_country_config_local_code(config)
            requested_slug = _new_country_slug_from_entity_code(requested_code)
            slug = config.slug if requested_code == current_code else requested_slug
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = _normalize_new_country_code(country.slug)
            use_subdivisions = _new_country_selection_uses_subdivisions(form["selection_json"])
            sync_child_configs = _new_country_selection_has_child_config_slugs(form["selection_json"])
            selected_child_config_slugs = _new_country_selected_child_config_slugs_from_selection_json(
                form["selection_json"]
            )
            if use_subdivisions:
                selected_child_config_slugs = []
            parent_config_slug = _validate_new_country_config_parent(
                country,
                form["parent_config_slug"],
                current_config=config,
            )
            parent_level = _new_country_parent_level(country, parent_config_slug)
            content = _derived_country_content_from_create_post(
                country=country,
                slug=slug,
                name=name,
                source_country_code=form["source_country_code"],
                derived_country_code=derived_code,
                raw_content=form["content"],
                selection_json=form["selection_json"],
                parent_config_slug=parent_config_slug,
                entity_level=parent_level + 1,
                entity_code=requested_code,
                entity_type=form["entity_type"],
                capitals=form["capitals"],
            )
            _validate_plain_toml(content, expected_kind="derived_country_config")
            old_slug = config.slug
            with transaction.atomic():
                if old_slug != slug:
                    selected_child_config_slugs = _rename_new_country_selected_child_slug_collision(
                        country,
                        slug=slug,
                        use_subdivisions=use_subdivisions,
                        selected_child_config_slugs=selected_child_config_slugs,
                        current_config=config,
                    )
                config.slug = slug
                config.name = name
                config.source_country_code = str(form["source_country_code"] or "").strip().lower()
                config.derived_country_code = derived_code
                config.content = content
                config.is_active = True
                config.save()
                if old_slug != slug:
                    _rename_new_country_config_parent_references(
                        country,
                        old_slug=old_slug,
                        new_slug=slug,
                        exclude_config=config,
                    )
                if sync_child_configs:
                    metric_refresh_slugs = _new_country_metric_refresh_slugs_for_child_sync(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                    _sync_new_country_config_children(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                else:
                    metric_refresh_slugs = []
                _refresh_new_country_config_metrics_for_slugs(
                    country,
                    [config.slug, *metric_refresh_slugs],
                )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' guardada.") % {"slug": config.slug})
            return redirect(_new_country_config_edit_url(config))
    return render(
        request,
        "ciudades_del_mundo/new_country_config_create.html",
        {
            "country": country,
            "config": config,
            "form": form,
            "country_detail_url": _new_country_detail_url(country),
            "new_country_config_new_url": _new_country_config_new_url(country),
            "parent_options": _new_country_config_parent_options(country, current_config=config),
            "created_entity_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_created_entity_options",
                kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "derived_subdivision_options": [],
            "selected_derived_subdivision_keys": _new_country_selected_subdivision_keys_from_selection_json(
                form.get("selection_json", ""),
                fallback_country_code=form["source_country_code"],
            ),
            "capital_options": _new_country_config_capital_options_payload(
                form["source_country_code"],
                country=country,
                selected_ids=form.get("capitals", []),
                selected_derived_subdivisions=_new_country_selected_derived_subdivisions_from_selection_json(
                    form.get("selection_json", ""),
                    fallback_country_code=form["source_country_code"],
                ),
                selected_child_config_slugs=(
                    []
                    if _form_bool(form.get("use_subdivisions"), default=False)
                    else _new_country_selected_child_config_slugs_from_selection_json(form.get("selection_json", ""))
                ),
                use_subdivisions=bool(form.get("use_subdivisions")),
            ),
            "capital_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_capital_options",
                kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "derived_subdivision_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_subdivision_options",
                kwargs=_new_country_source_kwargs(country),
            ),
            "source_area_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_source_area_options",
                kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "source_area_countries": _derived_source_country_options(),
            "source_countries": _new_country_created_subdivision_country_options(),
            "source_country_label": _source_country_display_label(form["source_country_code"]),
            "children_url": reverse("ciudades_del_mundo:new_country_source_children"),
        },
    )


def new_country_config_edit_legacy(request, country_slug, config_slug):
    """Redirect old config-edit URLs to the source-qualified route."""
    country = _derived_country_or_404(country_slug)
    config = _derived_country_config_or_404(country, config_slug)
    return redirect(_new_country_config_edit_url(config))


def start_new_country_config_task(request, source_country_code="", country_slug="", config_slug="", action=""):
    """Start build/export tasks for one SQL new-country configuration."""
    wants_json = _wants_json(request)
    country = _derived_country_or_404(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _derived_country_config_or_404(country, config_slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect(_new_country_detail_url(country))

    built_code = _new_country_config_built_code(config)
    if action == "build":
        key = f"build-new-country:{config.full_slug}"
        label = _("Crear pais nuevo: %(name)s") % {"name": config.name}
        args = ["build_new_subdivisions", "--country-id", built_code]
    elif action == "export-excel":
        if not NuevoAdminArea.objects.filter(id=built_code).exists():
            error = _("Primero debes crear el pais nuevo antes de exportarlo.")
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.error(request, error)
            return redirect(_new_country_detail_url(country))
        key = f"export-new-country-excel:{config.full_slug}"
        label = _("Exportar Excel: %(name)s") % {"name": config.name}
        args = ["export_nuevoadmin_excel", "--country-id", built_code]
    else:
        error = _("Accion de pais nuevo no soportada.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)

    try:
        task = task_manager.start(key=key, label=label, args=args)
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect(_new_country_detail_url(country))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(_task_started_payload(task, label=label))
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


def start_new_country_config_task_legacy(request, country_slug, config_slug, action):
    """Redirect old config-task URLs to the source-qualified route."""
    country = _derived_country_or_404(country_slug)
    config = _derived_country_config_or_404(country, config_slug)
    return redirect(_new_country_config_task_url(config, action))


def new_country_source_children(request):
    """Return source `AdminArea` children for the derived-country creation tree."""
    country_code = str(request.GET.get("country_code") or "").strip().lower()
    parent_id = str(request.GET.get("parent_id") or "").strip()
    if not country_code:
        return JsonResponse({"children": []})
    try:
        legal_level = effective_source_municipal_level(country_code)
        if parent_id:
            parent = _group_source_admin_areas().get(id=parent_id, country_code__iexact=country_code)
            if legal_level is not None and int(parent.level or 0) >= legal_level:
                children = AdminArea.objects.none()
            else:
                children = _group_source_admin_areas().filter(parent=parent)
        else:
            root = _source_country_root_for_code(country_code)
            if root and legal_level == 0:
                children = _group_source_admin_areas().filter(id=root.id)
            elif root:
                children = _group_source_admin_areas().filter(parent=root)
            else:
                children = _group_source_admin_areas().filter(country_code__iexact=country_code, level=1)
            if legal_level == 0 and not root:
                children = AdminArea.objects.none()
        rows = list(
            children.order_by("level", "name", "id").only(
                "id",
                "country_code",
                "code",
                "name",
                "level",
                "entity_type",
                "parent_id",
            )
        )
    except (AdminArea.DoesNotExist, OperationalError, ProgrammingError, ValueError):
        rows = []
        legal_level = None
    child_counts = _admin_area_child_counts([str(row.id) for row in rows], group_source_only=True)
    return JsonResponse(
        {
            "children": [
                {
                    "id": str(row.id),
                    "code": str(row.code or ""),
                    "name": str(row.name or ""),
                    "label": _area_display_name(row),
                    "level": int(row.level or 0),
                    "entity_type": str(row.entity_type or ""),
                    "parent_id": str(row.parent_id or ""),
                    "has_children": (
                        (legal_level is None or int(row.level or 0) < legal_level)
                        and child_counts.get(str(row.id), 0) > 0
                    ),
                }
                for row in rows
            ]
        }
    )


def group_source_data(request):
    """Return source hierarchy options for the country-scoped group editor."""
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        country_code = _normalize_group_country_key(body.get("country_code"))
        if not country_code:
            return JsonResponse({"country_code": "", "names": [], "sections": []})
        raw_names = body.get("names") or []
        if not isinstance(raw_names, list):
            raw_names = []
        names = [str(name).strip() for name in raw_names if str(name or "").strip()]
        sections = _group_sections_from_payload(body.get("sections") or [])
        if names:
            resolved = _group_sections_for_flat_names(country_code, names)
            existing_section_ids = {section.get("area_id") for section in sections}
            sections.extend(
                section
                for section in resolved["sections"]
                if section.get("area_id") and section.get("area_id") not in existing_section_ids
            )
            names = resolved["unresolved_names"]
        return JsonResponse({"country_code": country_code, "names": names, "sections": sections})

    country_code = str(request.GET.get("country_code") or "").strip().lower()
    if not country_code:
        return JsonResponse({"levels": [], "sections": [], "children": []})
    level = str(request.GET.get("level") or "").strip()
    parent_id = str(request.GET.get("parent_id") or "").strip()
    section_parent_id = str(request.GET.get("section_parent_id") or "").strip()
    source_mode = str(request.GET.get("source_mode") or "").strip().lower()
    include_groups = str(request.GET.get("include_groups") or "").strip().lower() in {"1", "true", "yes"}
    include_derived_subdivisions = str(request.GET.get("include_derived_subdivisions") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    current_level = str(request.GET.get("current_level") or "").strip()
    current_subdivision = str(request.GET.get("current_subdivision") or "").strip()
    current_subdivision_country = _normalize_group_country_key(request.GET.get("current_subdivision_country"))
    descendant_parent_ids = [
        parent_id.strip()
        for parent_id in str(request.GET.get("descendant_parent_ids") or "").split(",")
        if parent_id.strip()
    ]
    group_entries_cache: dict[str, list[dict]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    if source_mode == "descendants":
        children = _group_source_descendant_options(country_code, descendant_parent_ids)
        if include_groups:
            descendant_levels = _source_item_levels(children)
            children = _source_items_with_group_options(
                children,
                _derived_subdivision_group_source_options(
                    country_code,
                    levels=descendant_levels,
                    parent_ids=descendant_parent_ids,
                    entries_cache=group_entries_cache,
                    include_member_names=False,
                )
                if descendant_levels
                else [],
            )
        if include_derived_subdivisions:
            descendant_levels = _source_item_levels(children)
            children = _source_items_with_group_options(
                children,
                _derived_subdivision_source_options(
                    country_code,
                    levels=descendant_levels,
                    parent_ids=descendant_parent_ids,
                    current_level=current_level,
                    current_subdivision=current_subdivision,
                    current_subdivision_country=current_subdivision_country,
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                    include_member_names=False,
                ),
            )
        return JsonResponse(
            {
                "root": _group_source_root_payload(country_code),
                "levels": _group_source_item_level_options(country_code),
                "sections": [],
                "children": children,
            }
        )
    if source_mode == "items":
        payload = {
            "root": _group_source_root_payload(country_code),
            "levels": _group_source_item_level_options(country_code),
            "sections": [],
            "children": [],
        }
        if level:
            sections = _group_source_item_options(country_code, level, parent_id=section_parent_id)
            if section_parent_id and not sections:
                payload["sections"] = []
                return JsonResponse(payload)
            if include_groups:
                parent_ids = [section_parent_id] if section_parent_id else []
                sections = _source_items_with_group_options(
                    sections,
                    _derived_subdivision_group_source_options(
                        country_code,
                        level=level,
                        parent_ids=parent_ids,
                        direct_parent_only=True,
                        entries_cache=group_entries_cache,
                        include_member_names=False,
                    ),
                )
            if include_derived_subdivisions:
                parent_ids = [section_parent_id] if section_parent_id else []
                sections = _source_items_with_group_options(
                    sections,
                    _derived_subdivision_source_options(
                        country_code,
                        level=level,
                        parent_ids=parent_ids,
                        current_level=current_level,
                        current_subdivision=current_subdivision,
                        current_subdivision_country=current_subdivision_country,
                        source_ids_cache=source_ids_cache,
                        group_entry_cache=group_entry_cache,
                        group_entries_cache=group_entries_cache,
                        include_member_names=False,
                    ),
                )
            payload["sections"] = sections
        return JsonResponse(payload)

    payload = {"root": _group_source_root_payload(country_code), "levels": _group_source_level_options(country_code), "sections": [], "children": []}
    if level:
        payload["sections"] = _group_source_section_options(country_code, level, parent_id=section_parent_id)
    if parent_id:
        children = _group_source_child_options(country_code, parent_id)
        if include_groups:
            direct_child_levels = _source_item_levels(children)
            direct_group_level = direct_child_levels[0] if direct_child_levels else None
            children = _source_items_with_group_options(
                children,
                _derived_subdivision_group_source_options(
                    country_code,
                    level=direct_group_level,
                    parent_ids=[parent_id],
                    direct_parent_only=True,
                    entries_cache=group_entries_cache,
                    include_member_names=False,
                )
                if direct_group_level is not None
                else [],
            )
        if include_derived_subdivisions:
            direct_child_levels = _source_item_levels(children)
            direct_child_level = direct_child_levels[0] if direct_child_levels else None
            if direct_child_level is not None:
                children = _source_items_with_group_options(
                    children,
                    _derived_subdivision_source_options(
                        country_code,
                        level=direct_child_level,
                        parent_ids=[parent_id],
                        current_level=current_level,
                        current_subdivision=current_subdivision,
                        current_subdivision_country=current_subdivision_country,
                        source_ids_cache=source_ids_cache,
                        group_entry_cache=group_entry_cache,
                        group_entries_cache=group_entries_cache,
                        include_member_names=False,
                    ),
                )
        payload["children"] = children
    return JsonResponse(payload)


def group_list(request):
    """List reusable TOML groups for historical/custom subdivisions."""
    groups = list(SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"))
    seed_records = _subdivision_group_seed_records()
    subdivision_seed_paths = bundled_derived_subdivision_paths()
    country_cards = _group_country_cards(
        groups,
        seed_records=seed_records,
        subdivision_seed_paths=subdivision_seed_paths,
    )
    return render(
        request,
        "ciudades_del_mundo/group_list.html",
        {
            "country_cards": country_cards,
            "seed_count": len(seed_records),
        },
    )


def group_country_detail_data(request, country_code):
    """Return the lazily loaded /groups/ detail tables for one source country."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return JsonResponse({"ok": False, "error": _("No se pudo identificar el pais.")}, status=404)
    return JsonResponse(_group_country_detail_payload(country_code))


def derived_subdivision_import_toml(request):
    """Import or queue SQL refreshes from derived-subdivision TOML seeds."""
    wants_json = _wants_json(request)
    queue_task = request.POST.get("queue_task") == "1"
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = _normalize_group_country_key(request.POST.get("country_code"))
    paths = bundled_derived_subdivision_paths()
    if country_code:
        paths = _derived_subdivision_seed_paths_for_country(paths, country_code)
    if wants_json and not queue_task:
        if not paths:
            return JsonResponse(
                {"ok": False, "error": _("No hay semillas TOML de subdivisiones para importar.")},
                status=404,
            )
        try:
            imported = []
            write_lock = sqlite_write_lock_if_needed()
            if write_lock is None:
                for path in paths:
                    imported.extend(import_derived_subdivision_path_records(path, force=True))
            else:
                with write_lock:
                    for path in paths:
                        imported.extend(import_derived_subdivision_path_records(path, force=True))
        except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a JSON error.
            return JsonResponse(
                {"ok": False, "error": _("No se pudo importar el TOML: %(error)s") % {"error": exc}},
                status=500,
            )
        message = _("Importadas %(count)s subdivision(es) desde TOML.") % {"count": len(imported)}
        return JsonResponse({"ok": True, "message": message, "imported": bool(imported), "refresh": True})
    label_template = _("Importar subdivision TOML: %(slug)s")
    try:
        queued_tasks = _queue_derived_seed_import_tasks(
            paths,
            section="subdivisions",
            key_prefix="import-subdivision",
            label_template=label_template,
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    task_count = len(queued_tasks)
    if wants_json:
        if queued_tasks:
            task, label = queued_tasks[0]
            return JsonResponse(_task_started_payload(task, label=label))
        return JsonResponse({"ok": False, "error": _("No hay semillas TOML de subdivisiones para importar.")}, status=404)
    if task_count:
        messages.info(
            request,
            _("Encoladas %(count)s tarea(s) de importacion TOML para subdivisiones.")
            % {"count": task_count},
        )
    else:
        messages.warning(request, _("No hay semillas TOML de subdivisiones para importar."))
    return redirect("ciudades_del_mundo:task_list")


def derived_subdivision_export_toml(request, slug: str):
    """Export derived subdivision SQL definitions to one TOML file for the country."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = _normalize_group_country_key(slug)
    if not country_code:
        error = _("No se pudo identificar el pais de la exportacion.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    try:
        exported_count = export_derived_subdivisions_to_toml(
            force=True,
            country_codes=[country_code],
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a toast/form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported_count:
            message = _("Subdivisiones exportadas a subdivision_groups/subdivisions/%(country)s.toml.") % {
                "country": country_code
            }
        else:
            message = _("No hay subdivisiones SQL para exportar en %(country)s.") % {"country": country_code}
        if wants_json:
            status = 200 if exported_count else 404
            return JsonResponse({"ok": bool(exported_count), "message": message, "error": message}, status=status)
        if exported_count:
            messages.success(request, message)
        else:
            messages.warning(request, message)
    return redirect("ciudades_del_mundo:derived_subdivision_list")


def derived_subdivision_build(request, country_code: str):
    """Queue a build task that materializes derived subdivisions for one country."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        error = _("No se pudo identificar el pais.")
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    if not DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).exists():
        error = _("No hay subdivisiones SQL para %(country)s.") % {"country": country_code}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.warning(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    label = _("Popular nuevas divisiones: %(country)s") % {"country": country_code}
    try:
        task = task_manager.start(
            key=f"build-derived-subdivisions:{country_code}",
            label=label,
            args=["build_derived_subdivisions", country_code, "--force", "--continue-on-error"],
        )
    except (OperationalError, ProgrammingError) as exc:
        error = _(
            "No se pudo guardar la tarea en la base de datos. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=503)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise
    if wants_json:
        return JsonResponse(_task_started_payload(task, label=label))
    messages.info(request, _("Encolada la poblacion de nuevas divisiones para %(country)s.") % {"country": country_code})
    return redirect("ciudades_del_mundo:task_list")


def derived_subdivision_list(request):
    """List fictional or historical subdivision definitions."""
    records = list(DerivedSubdivision.objects.order_by("source_country_code", "name", "slug"))
    seed_paths = bundled_derived_subdivision_paths()
    return render(
        request,
        "ciudades_del_mundo/derived_subdivision_list.html",
        {
            "country_cards": _derived_subdivision_country_cards(records, seed_paths=seed_paths),
            "seed_count": len(seed_paths),
        },
    )


def derived_subdivision_country_detail_data(request, country_code):
    """Return the lazily loaded /subdivisions/ detail tables for one source country."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return JsonResponse({"ok": False, "error": _("No se pudo identificar el pais.")}, status=404)
    return JsonResponse(_derived_subdivision_country_detail_payload(country_code))


def derived_subdivision_new(request, country_code):
    """Create one fictional or historical subdivision definition."""
    country_code = _normalize_group_country_key(country_code)
    return _derived_subdivision_form(request, country_code=country_code, record=None, requested_slug="new")


def derived_subdivision_edit(request, country_code, subdivision_slug):
    """Edit one fictional or historical subdivision definition."""
    country_code = _normalize_group_country_key(country_code)
    subdivision_slug = _group_entry_slug(subdivision_slug)
    if request.method == "GET":
        return _derived_subdivision_form(
            request,
            country_code=country_code,
            record=None,
            requested_slug=subdivision_slug,
        )
    record = _derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        raise Http404(_("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug})
    return _derived_subdivision_form(
        request,
        country_code=country_code,
        record=record,
        requested_slug=subdivision_slug,
    )


def derived_subdivision_delete(request, country_code, subdivision_slug):
    """Delete one SQL derived-subdivision definition from the groups table."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country_code = _normalize_group_country_key(country_code)
    subdivision_slug = _group_entry_slug(subdivision_slug)
    record = _derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        error = _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    record_slug = record.slug
    with transaction.atomic():
        materialized_deleted = _delete_materialized_rows_for_derived_subdivision(record)
        record.delete()
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "deleted": True,
                "country_code": country_code,
                "subdivision_slug": subdivision_slug,
                "record_slug": record_slug,
                "materialized_deleted": materialized_deleted,
                "redirect_url": reverse("ciudades_del_mundo:group_list"),
            }
        )
    return redirect("ciudades_del_mundo:group_list")


def derived_subdivision_clone(request, country_code, subdivision_slug):
    """Clone one SQL derived-subdivision definition."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country_code = _normalize_group_country_key(country_code)
    subdivision_slug = _group_entry_slug(subdivision_slug)
    record = _derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        error = _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    try:
        with transaction.atomic():
            clone = _clone_derived_subdivision(record)
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    clone_country_code = _normalize_group_country_key(clone.source_country_code)
    clone_entry_slug = _derived_subdivision_entry_slug(clone)
    redirect_url = reverse(
        "ciudades_del_mundo:derived_subdivision_edit",
        kwargs={"country_code": clone_country_code, "subdivision_slug": clone_entry_slug},
    )
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "cloned": True,
                "country_code": clone_country_code,
                "subdivision_slug": clone_entry_slug,
                "record_slug": clone.slug,
                "redirect_url": redirect_url,
            }
        )
    return redirect(redirect_url)


def derived_subdivision_form_data(request, country_code, subdivision_slug):
    """Return dynamic form data for one derived-subdivision editor."""
    country_code = _normalize_group_country_key(country_code)
    subdivision_slug = _group_entry_slug(subdivision_slug)
    if subdivision_slug == "new":
        record = None
        mode = "new"
    else:
        record = _derived_subdivision_for_country(country_code, subdivision_slug)
        mode = "edit"
        if not record:
            return JsonResponse(
                {
                    "ok": False,
                    "error": _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug},
                },
                status=404,
            )
    return JsonResponse(_derived_subdivision_form_payload(country_code=country_code, record=record, mode=mode))


def derived_subdivision_capital_options(request, country_code, subdivision_slug):
    """Return capital options constrained to the current derived-subdivision source selection."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country_code = _normalize_group_country_key(country_code)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    content = str(body.get("content") or "").strip()
    if not content:
        record = None if subdivision_slug == "new" else _derived_subdivision_for_country(country_code, subdivision_slug)
        content = str(record.content or "") if record else ""
    if str(body.get("source_dirty") or body.get("derived_source_dirty") or "") == "1":
        try:
            if body.get("include_ids_json") is not None or body.get("include_ids") is not None:
                content = _derived_subdivision_content_with_visual_source_ids(
                    content,
                    include_ids_json=body.get("include_ids") or body.get("include_ids_json") or "[]",
                    subtract_ids_json=body.get("subtract_ids") or body.get("subtract_ids_json") or "[]",
                    country_code=country_code,
                )
            else:
                content = _derived_subdivision_content_with_visual_source_blocks(
                    content,
                    include_blocks_json=body.get("include_blocks") or body.get("include_blocks_json") or "[]",
                    subtract_blocks_json=body.get("subtract_blocks") or body.get("subtract_blocks_json") or "[]",
                    country_code=country_code,
                )
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    selected_values = _derived_subdivision_capital_values_from_payload(body)
    search_term = str(body.get("query") or body.get("term") or "").strip()
    search_key = _derived_subdivision_capital_search_key(search_term)
    if len(search_key) >= 2:
        try:
            options = _derived_subdivision_capital_search_options(
                country_code,
                content=content,
                selected_ids=selected_values,
                search_term=search_term,
            )
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    else:
        options = _derived_subdivision_capital_options(country_code, selected_ids=selected_values)
    option_values = {option["value"] for option in options}
    selected_values = [value for value in selected_values if value in option_values]
    return JsonResponse(
        {
            "ok": True,
            "capital": selected_values[0] if selected_values else "",
            "capitals": selected_values,
            "capital_options": options,
        }
    )


def _derived_subdivision_form(request, *, country_code: str, record: DerivedSubdivision | None, requested_slug: str = ""):
    country_code = _normalize_group_country_key(country_code)
    wants_json = _wants_json(request)
    if record:
        mode = "edit"
        form = _derived_subdivision_form_initial(country_code=country_code, record=record)
    elif requested_slug and requested_slug != "new":
        mode = "edit"
        root_code = _derived_subdivision_root_code(country_code)
        try:
            internal_name = _group_internal_name(requested_slug)
        except ValueError:
            internal_name = ""
        form = {
            "internal_name": internal_name,
            "name": "",
            "source_country_code": country_code,
            "code": "",
            "code_suffix": "",
            "parent_code": root_code,
            "entity_type": "",
            "level": "1",
            "capital": "",
            "capitals": [],
            "flag_url": "",
            "coat_url": "",
            "use_selected_entities_as_children": False,
            "description": "",
            "content": "",
        }
    else:
        mode = "new"
        root_code = _derived_subdivision_root_code(country_code)
        form = {
            "internal_name": "",
            "name": "",
            "source_country_code": country_code,
            "code": "",
            "code_suffix": "",
            "parent_code": root_code,
            "entity_type": "Provincia",
            "level": "1",
            "capital": "",
            "capitals": [],
            "flag_url": "",
            "coat_url": "",
            "use_selected_entities_as_children": False,
            "description": "",
            "content": _default_derived_subdivision_toml(
                country_code=country_code,
                internal_name="NUEVA_SUBDIVISION",
                parent_code=root_code,
            ),
        }

    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        form["capitals"] = _derived_subdivision_capital_values_from_post(request.POST)
        form["capital"] = form["capitals"][0] if form["capitals"] else ""
        form["source_country_code"] = country_code
        form["parent_code"] = _derived_subdivision_root_code(country_code)
        form["code_suffix"] = str(request.POST.get("code_suffix") or "").strip()
        form["level"] = str(request.POST.get("level") or "").strip()
        form["use_selected_entities_as_children"] = _form_bool(
            request.POST.get("use_selected_entities_as_children"),
            default=False,
        )
        try:
            internal_name = _group_internal_name(form["internal_name"])
            entry_slug = _group_entry_slug(internal_name)
            source_country_code = country_code
            if not source_country_code:
                raise ValueError(_("El pais es obligatorio."))
            level = _derived_subdivision_level(form["level"])
            parent_code = _derived_subdivision_root_code(source_country_code)
            code = _derived_subdivision_compose_code(
                country_code=source_country_code,
                parent_code=parent_code,
                code_value=form.get("code_suffix") or internal_name,
            )
            name = str(form["name"] or "").strip() or _group_entry_display_name(internal_name)
            raw_content = str(form["content"] or "").strip() or _default_derived_subdivision_toml(
                country_code=source_country_code,
                internal_name=internal_name,
                name=name,
                code=code,
                entity_type=form.get("entity_type", ""),
            )
            if str(request.POST.get("derived_source_dirty") or "") == "1":
                if request.POST.get("include_ids_json") is not None:
                    raw_content = _derived_subdivision_content_with_visual_source_ids(
                        raw_content,
                        include_ids_json=request.POST.get("include_ids_json", "[]"),
                        subtract_ids_json=request.POST.get("subtract_ids_json", "[]"),
                        country_code=source_country_code,
                    )
                else:
                    raw_content = _derived_subdivision_content_with_visual_source_blocks(
                        raw_content,
                        include_blocks_json=request.POST.get("include_blocks_json", "[]"),
                        subtract_blocks_json=request.POST.get("subtract_blocks_json", "[]"),
                        country_code=source_country_code,
                    )
            content = _render_derived_subdivision_form_toml(
                raw_content,
                country_code=source_country_code,
                internal_name=internal_name,
                name=name,
                code=code,
                parent_code=parent_code,
                entity_type=form.get("entity_type", ""),
                level=level,
                capitals=form.get("capitals", []),
                flag_url=form.get("flag_url", ""),
                coat_url=form.get("coat_url", ""),
                use_selected_entities_as_children=bool(form.get("use_selected_entities_as_children")),
            )
            _validate_derived_subdivision_toml(content)
            slug = _derived_subdivision_record_slug(source_country_code, entry_slug)
            if record is None and DerivedSubdivision.objects.filter(slug=slug).exists():
                raise ValueError(_("Ya existe una subdivision '%(slug)s'.") % {"slug": internal_name})
            duplicate_internal_name = DerivedSubdivision.objects.filter(
                source_country_code__iexact=source_country_code,
                internal_name__iexact=internal_name,
            )
            if record is not None:
                duplicate_internal_name = duplicate_internal_name.exclude(slug=record.slug)
            if duplicate_internal_name.exists():
                raise ValueError(_("Ya existe una subdivision '%(slug)s'.") % {"slug": internal_name})
            target = record or DerivedSubdivision(slug=slug)
            target.internal_name = internal_name
            target.name = name
            target.source_country_code = source_country_code
            target.code = code
            target.entity_type = str(form["entity_type"] or "").strip()
            target.description = str(form["description"] or "").strip()
            target.content = content
            target.save()
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            message = _("Subdivision '%(name)s' guardada.") % {"name": target.name}
            redirect_url = reverse(
                "ciudades_del_mundo:derived_subdivision_edit",
                kwargs={
                    "country_code": target.source_country_code,
                    "subdivision_slug": _derived_subdivision_entry_slug(target),
                },
            )
            if wants_json:
                payload = _derived_subdivision_form_save_payload(
                    country_code=target.source_country_code,
                    record=target,
                )
                payload.update(
                    {
                        "message": message,
                        "redirect_url": redirect_url,
                        "country_code": target.source_country_code,
                        "subdivision_slug": _derived_subdivision_entry_slug(target),
                        "data_url": reverse(
                            "ciudades_del_mundo:derived_subdivision_form_data",
                            kwargs={
                                "country_code": target.source_country_code,
                                "subdivision_slug": _derived_subdivision_entry_slug(target),
                            },
                        ),
                        "capital_options_url": reverse(
                            "ciudades_del_mundo:derived_subdivision_capital_options",
                            kwargs={
                                "country_code": target.source_country_code,
                                "subdivision_slug": _derived_subdivision_entry_slug(target),
                            },
                        ),
                    }
                )
                return JsonResponse(payload)
            messages.success(request, message)
            return redirect(f"{redirect_url}?saved=1")

    load_dynamic = request.method == "GET"
    if load_dynamic:
        parent_options = _derived_subdivision_root_parent_options(country_code)
        capital_options = []
        include_items = _derived_subdivision_default_source_items(country_code)
        subtract_items = _derived_subdivision_default_source_items(country_code)
    else:
        parent_options = _derived_subdivision_root_parent_options(country_code)
        capital_options = _derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        )
        include_items = _derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "include",
            fallback_country_code=country_code,
        )
        subtract_items = _derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "subtract",
            fallback_country_code=country_code,
        )
    data_slug = requested_slug or ("new" if mode == "new" else _derived_subdivision_entry_slug(record))
    return render(
        request,
        "ciudades_del_mundo/derived_subdivision_form.html",
        {
            "mode": mode,
            "record": record,
            "form": form,
            "country_code": country_code,
            "country_label": _display_name("", country_code, country_code=country_code),
            "root_code": _derived_subdivision_root_code(country_code),
            "parent_options": parent_options,
            "parent_options_json": parent_options,
            "capital_options": capital_options,
            "include_items": include_items,
            "subtract_items": subtract_items,
            "source_countries": _source_country_options_with_current(country_code),
            "source_data_url": reverse("ciudades_del_mundo:group_source_data"),
            "default_group_level": ORIGINAL_MUNICIPAL_LEVEL.get(country_code, 0),
            "current_subdivision_key": form.get("internal_name") or "",
            "load_dynamic": load_dynamic,
            "return_row_href": (
                reverse(
                    "ciudades_del_mundo:derived_subdivision_edit",
                    kwargs={"country_code": country_code, "subdivision_slug": data_slug},
                )
                if data_slug != "new"
                else ""
            ),
            "return_needs_refresh": request.GET.get("saved") == "1",
            "data_url": reverse(
                "ciudades_del_mundo:derived_subdivision_form_data",
                kwargs={"country_code": country_code, "subdivision_slug": data_slug},
            ),
            "capital_options_url": reverse(
                "ciudades_del_mundo:derived_subdivision_capital_options",
                kwargs={"country_code": country_code, "subdivision_slug": data_slug},
            ),
        },
    )


def _derived_subdivision_form_payload(*, country_code: str, record: DerivedSubdivision | None, mode: str) -> dict:
    country_code = _normalize_group_country_key(country_code)
    if record:
        form = _derived_subdivision_form_initial(country_code=country_code, record=record)
    else:
        root_code = _derived_subdivision_root_code(country_code)
        form = {
            "internal_name": "",
            "name": "",
            "source_country_code": country_code,
            "code": "",
            "code_suffix": "",
            "parent_code": root_code,
            "entity_type": "Provincia",
            "level": "1",
            "capital": "",
            "capitals": [],
            "flag_url": "",
            "coat_url": "",
            "use_selected_entities_as_children": False,
            "description": "",
            "content": _default_derived_subdivision_toml(
                country_code=country_code,
                internal_name="NUEVA_SUBDIVISION",
                parent_code=root_code,
            ),
        }
    parent_options = _derived_subdivision_root_parent_options(country_code)
    group_entries_cache: dict[str, list[dict]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    return {
        "ok": True,
        "mode": mode,
        "title": str(_("NUEVA SUBDIVISION")) if mode == "new" else form.get("internal_name", ""),
        "form": form,
        "parent_options": parent_options,
        "capital_options": _derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        ),
        "include_items": _derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "include",
            fallback_country_code=country_code,
            group_entries_cache=group_entries_cache,
            group_entry_cache=group_entry_cache,
            source_ids_cache=source_ids_cache,
        ),
        "subtract_items": _derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "subtract",
            fallback_country_code=country_code,
            group_entries_cache=group_entries_cache,
            group_entry_cache=group_entry_cache,
            source_ids_cache=source_ids_cache,
        ),
    }


def _derived_subdivision_form_save_payload(*, country_code: str, record: DerivedSubdivision) -> dict:
    country_code = _normalize_group_country_key(country_code)
    form = _derived_subdivision_form_initial(country_code=country_code, record=record)
    return {
        "ok": True,
        "mode": "edit",
        "title": form.get("internal_name", ""),
        "form": form,
        "parent_options": _derived_subdivision_root_parent_options(country_code),
        "capital_options": _derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        ),
    }


def _derived_subdivision_form_initial(*, country_code: str, record: DerivedSubdivision) -> dict:
    data = _derived_subdivision_toml_data(record.content)
    internal_name = str(data.get("internal_name") or record.internal_name or _derived_subdivision_entry_slug(record)).strip()
    parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    level = _derived_subdivision_level(data.get("level") or 1)
    code = _derived_subdivision_compose_code(
        country_code=country_code,
        parent_code=parent_code,
        code_value=data.get("code") or record.code or internal_name,
    )
    capitals = _derived_subdivision_capital_values(country_code, data)
    return {
        "internal_name": internal_name,
        "name": str(data.get("name") or record.name or _group_entry_display_name(internal_name)).strip(),
        "source_country_code": country_code,
        "code": code,
        "code_suffix": _derived_subdivision_code_suffix(code, parent_code),
        "parent_code": parent_code,
        "entity_type": str(data.get("entity_type") or record.entity_type or "").strip(),
        "level": str(level),
        "capital": capitals[0] if capitals else "",
        "capitals": capitals,
        "flag_url": str(data.get("flag_url") or "").strip(),
        "coat_url": str(data.get("coat_url") or "").strip(),
        "use_selected_entities_as_children": _form_bool(
            data.get("use_selected_entities_as_children"),
            default=False,
        ),
        "description": record.description,
        "content": record.content,
    }


def _derived_subdivision_toml_data(content: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derived_subdivision_level(value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise ValueError(_("El nivel debe ser numerico.")) from None
    if level < 1 or level > 5:
        raise ValueError(_("El nivel debe estar entre 1 y 5."))
    return level


def _derived_subdivision_root_code(country_code: str) -> str:
    country_code = _normalize_group_country_key(country_code)
    try:
        root = AdminArea.objects.filter(country_code__iexact=country_code, level=0).order_by("id").only("code").first()
    except (OperationalError, ProgrammingError):
        root = None
    code = str(getattr(root, "code", "") or "").strip()
    return derived_country_root_code(country_code, code)


def _derived_subdivision_code_piece(value: str) -> str:
    return derived_code_piece(value)


def _derived_subdivision_clean_parent_code(value, *, country_code: str) -> str:
    text = _derived_subdivision_code_piece(str(value or ""))
    return text or _derived_subdivision_root_code(country_code)


def _derived_subdivision_compose_code(*, country_code: str, parent_code: str, code_value: str) -> str:
    parent_code = _derived_subdivision_clean_parent_code(parent_code, country_code=country_code)
    code = _derived_subdivision_code_piece(code_value)
    if not code:
        raise ValueError(_("El codigo es obligatorio."))
    if parent_code and code.startswith(f"{parent_code}-"):
        return code
    return f"{parent_code}-{code}" if parent_code else code


def _derived_subdivision_code_suffix(code: str, parent_code: str) -> str:
    code = _derived_subdivision_code_piece(code)
    parent_code = _derived_subdivision_code_piece(parent_code)
    prefix = f"{parent_code}-" if parent_code else ""
    return code[len(prefix) :] if prefix and code.startswith(prefix) else code


def _derived_subdivision_root_parent_options(country_code: str) -> list[dict]:
    root_code = _derived_subdivision_root_code(country_code)
    return [
        {
            "value": root_code,
            "code": root_code,
            "level": 0,
            "label": _display_name("", country_code, country_code=country_code),
        }
    ]


def _derived_subdivision_parent_options(country_code: str, *, exclude_record: DerivedSubdivision | None = None) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    root_code = _derived_subdivision_root_code(country_code)
    excluded_codes = set()
    if exclude_record:
        excluded_data = _derived_subdivision_toml_data(exclude_record.content)
        excluded_parent_code = _derived_subdivision_clean_parent_code(
            excluded_data.get("parent_code"),
            country_code=country_code,
        )
        try:
            excluded_codes.add(
                _derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=excluded_parent_code,
                    code_value=excluded_data.get("code") or exclude_record.code or exclude_record.internal_name,
                )
            )
        except ValueError:
            pass
    options = [
        {
            "value": root_code,
            "code": root_code,
            "level": 0,
            "label": _display_name("", country_code, country_code=country_code),
        }
    ]
    seen_codes = {root_code}
    exclude_slug = exclude_record.slug if exclude_record else ""
    records = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("name", "slug")
    for candidate in records:
        if exclude_slug and candidate.slug == exclude_slug:
            continue
        data = _derived_subdivision_toml_data(candidate.content)
        try:
            level = _derived_subdivision_level(data.get("level") or 1)
        except ValueError:
            level = 1
        parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
        raw_code = data.get("code") or candidate.code or candidate.internal_name or candidate.slug
        try:
            code = _derived_subdivision_compose_code(
                country_code=country_code,
                parent_code=parent_code,
                code_value=raw_code,
            )
        except ValueError:
            continue
        if code in seen_codes or code in excluded_codes:
            continue
        seen_codes.add(code)
        label = str(data.get("name") or candidate.name or candidate.internal_name or candidate.slug).strip()
        entity_type = str(data.get("entity_type") or candidate.entity_type or "").strip()
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append({"value": code, "code": code, "level": level, "label": f"{label} - {code}"})
    try:
        areas = (
            NuevoAdminArea.objects.filter(country_code__iexact=country_code, level__gt=0, level__lt=5)
            .only("code", "name", "level", "entity_type")
            .order_by("level", "name", "code")
        )
    except (OperationalError, ProgrammingError):
        areas = []
    for area in areas:
        code = _derived_subdivision_code_piece(area.code)
        if not code or code in seen_codes or code in excluded_codes:
            continue
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            continue
        label = str(area.name or code).strip()
        entity_type = str(area.entity_type or "").strip()
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append({"value": code, "code": code, "level": level, "label": f"{label} - {code}"})
        seen_codes.add(code)
    return options


def _derived_subdivision_group_country_sections_payload(sections: list[dict]) -> list[dict]:
    payload = []
    for country in sections:
        payload.append(
            {
                "country_code": str(country.get("country_code") or ""),
                "country_label": str(country.get("country_label") or ""),
                "default_group_level": int(country.get("default_group_level") or 0),
                "groups": [
                    {
                        "internal_name": str(group.get("internal_name") or ""),
                        "municipality_count_text": str(group.get("municipality_count_text") or ""),
                    }
                    for group in country.get("groups") or []
                    if str(group.get("internal_name") or "").strip()
                ],
            }
        )
    return payload


def _render_derived_subdivision_form_toml(
    content: str,
    *,
    country_code: str,
    internal_name: str,
    name: str,
    code: str,
    parent_code: str,
    entity_type: str,
    level: int,
    capital: str = "",
    capitals: list[str] | tuple[str, ...] | None = None,
    flag_url: str = "",
    coat_url: str = "",
    use_selected_entities_as_children: bool = False,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data.update(
        {
            "schema_version": int(data.get("schema_version") or 1),
            "kind": "derived_subdivision",
            "internal_name": internal_name,
            "source_country_code": country_code,
            "name": name,
            "code": code,
            "parent_code": parent_code,
            "entity_type": str(entity_type or "").strip(),
            "level": level,
            "flag_url": str(flag_url or "").strip(),
            "coat_url": str(coat_url or "").strip(),
            "use_selected_entities_as_children": bool(use_selected_entities_as_children),
        }
    )
    if capitals is None:
        capitals = [capital] if str(capital or "").strip() else []
    data["capitals"] = _normalized_derived_subdivision_capital_values(capitals)
    return _render_derived_subdivision_toml_data(data)


def _render_derived_subdivision_toml_data(data: dict) -> str:
    lines = [
        f"schema_version = {int(data.get('schema_version') or 1)}",
        'kind = "derived_subdivision"',
        f"internal_name = {_toml_string(data.get('internal_name') or '')}",
        f"source_country_code = {_toml_string(data.get('source_country_code') or '')}",
        f"name = {_toml_string(data.get('name') or '')}",
        f"code = {_toml_string(data.get('code') or '')}",
        f"parent_code = {_toml_string(data.get('parent_code') or '')}",
        f"entity_type = {_toml_string(data.get('entity_type') or '')}",
        f"level = {int(data.get('level') or 1)}",
        f"use_selected_entities_as_children = {_toml_bool(_form_bool(data.get('use_selected_entities_as_children'), default=False))}",
    ]
    if data.get("generic_name") not in (None, ""):
        lines.append(f"generic_name = {_toml_string(data.get('generic_name') or '')}")
    if data.get("capitals") is not None:
        lines.append(f"capitals = {_toml_array([str(item) for item in data.get('capitals') or []])}")
    if data.get("flag_url"):
        lines.append(f"flag_url = {_toml_string(data.get('flag_url') or '')}")
    if data.get("coat_url"):
        lines.append(f"coat_url = {_toml_string(data.get('coat_url') or '')}")
    lines.append("")
    _append_derived_capital_group_toml(lines, data.get("capital_groups") if isinstance(data.get("capital_groups"), list) else [])
    _append_derived_block_toml(lines, "include", data.get("include") if isinstance(data.get("include"), list) else [])
    _append_derived_block_toml(lines, "subtract", data.get("subtract") if isinstance(data.get("subtract"), list) else [])
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if not isinstance(child, dict):
            continue
        lines.extend(
            [
                "[[children]]",
                f"name = {_toml_string(child.get('name') or '')}",
                f"code = {_toml_string(child.get('code') or '')}",
                f"entity_type = {_toml_string(child.get('entity_type') or '')}",
                f"level = {int(child.get('level') or 1)}",
                "",
            ]
        )
        _append_derived_capital_group_toml(lines, child.get("capital_groups") if isinstance(child.get("capital_groups"), list) else [], table_name="children.capital_groups")
        _append_derived_block_toml(lines, "children.include", child.get("include") if isinstance(child.get("include"), list) else [])
        _append_derived_block_toml(lines, "children.subtract", child.get("subtract") if isinstance(child.get("subtract"), list) else [])
    return "\n".join(lines).rstrip() + "\n"


def _append_derived_capital_group_toml(lines: list[str], blocks: list[dict], *, table_name: str = "capital_groups") -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        level = int(block.get("level") or 0)
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_toml_string(block.get('country_code') or '')}",
                f"level = {level}",
                f"group = {_toml_string(block.get('group') or block.get('group_key') or '')}",
                f"names = {_toml_array([str(item) for item in block.get('names') or []])}",
                f"capital_name = {_toml_string(block.get('capital_name') or block.get('display_name') or '')}",
                "",
            ]
        )


def _append_derived_block_toml(lines: list[str], table_name: str, blocks: list[dict]) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        level = int(block.get("level") or 0)
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_toml_string(block.get('country_code') or '')}",
                f"level = {level}",
                f"names = {_toml_array([str(item) for item in block.get('names') or []])}",
                f"ids = {_toml_array([str(item) for item in block.get('ids') or []])}",
                f"codes = {_toml_array([str(item) for item in block.get('codes') or []])}",
                f"groups = {_toml_array([_group_internal_name(str(item)) for item in block.get('groups') or []])}",
                f"derived_subdivisions = {_toml_array([_group_internal_name(str(item)) for item in block.get('derived_subdivisions') or []])}",
                f"expressions = {_toml_array([str(item) for item in block.get('expressions') or []])}",
                "",
            ]
        )


def _derived_subdivision_default_source_blocks(country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    return [{"country_code": country_code, "names": [], "sections": []}] if country_code else []


def _derived_subdivision_default_source_items(country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    return [{"country_code": country_code, "items": []}] if country_code else []


def _derived_subdivision_visual_source_items_from_content(
    content: str,
    table_name: str,
    *,
    fallback_country_code: str,
    group_entries_cache: dict[str, list[dict]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> list[dict]:
    data = _derived_subdivision_toml_data(content)
    source_blocks = data.get(table_name) if isinstance(data.get(table_name), list) else []
    scoped_municipal_ids_by_country = (
        _derived_subdivision_include_municipal_scope_by_country(
            data,
            fallback_country_code=fallback_country_code,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        if table_name == "subtract"
        else {}
    )
    visual_by_country: dict[str, dict] = {}
    seen_ids: dict[str, set[str]] = {}
    for block in source_blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        raw_level = block.get("level")
        if raw_level in (None, ""):
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level < 0:
            continue
        refs = _derived_subdivision_block_source_refs(
            block,
            country_code=block_country,
            expand_groups=False,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        areas = _derived_subdivision_source_ref_areas(block_country, level, refs)
        country_block = visual_by_country.setdefault(block_country, {"country_code": block_country, "items": []})
        country_seen = seen_ids.setdefault(block_country, set())
        parent_by_id, _ = _derived_subdivision_group_parent_maps(areas)
        for area in sorted(areas, key=lambda item: (int(item.level or 0), _area_display_name(item), str(item.id))):
            area_id = str(area.id)
            if area_id in country_seen:
                continue
            country_seen.add(area_id)
            country_block["items"].append(
                _derived_subdivision_source_item_payload(
                    area,
                    ancestor_ids=_area_ancestor_ids_from_parent_map(area_id, parent_by_id),
                )
            )
        for group_key in _derived_subdivision_list_values(block.get("groups")):
            group_item = _derived_subdivision_group_source_item_for_key(
                block_country,
                group_key,
                level=level,
                scoped_municipal_ids=scoped_municipal_ids_by_country.get(block_country),
                entries_cache=group_entries_cache,
            )
            if not group_item:
                continue
            group_item_id = str(group_item.get("id") or group_item.get("value") or "").strip()
            if not group_item_id or group_item_id in country_seen:
                continue
            country_seen.add(group_item_id)
            country_block["items"].append(group_item)
        for subdivision_key in _derived_subdivision_list_values(block.get("derived_subdivisions")):
            subdivision_item = _derived_subdivision_record_source_item_for_key(
                block_country,
                subdivision_key,
                level=level,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
            if not subdivision_item:
                continue
            subdivision_item_id = str(subdivision_item.get("id") or subdivision_item.get("value") or "").strip()
            if not subdivision_item_id or subdivision_item_id in country_seen:
                continue
            country_seen.add(subdivision_item_id)
            country_block["items"].append(subdivision_item)
    return _derived_subdivision_source_items_with_default(
        list(visual_by_country.values()),
        fallback_country_code=fallback_country_code,
    )


def _derived_subdivision_include_municipal_scope_by_country(
    data: dict,
    *,
    fallback_country_code: str,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, set[str]]:
    include_blocks = data.get("include") if isinstance(data.get("include"), list) else []
    result: dict[str, set[str]] = {}
    for block in include_blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        raw_level = block.get("level")
        if raw_level in (None, ""):
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level < 0:
            continue
        refs = _derived_subdivision_block_source_refs(
            block,
            country_code=block_country,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        areas = _derived_subdivision_source_ref_areas(block_country, level, refs)
        result.setdefault(block_country, set()).update(
            _derived_subdivision_areas_to_capital_source_ids(areas, country_code=block_country)
        )
    return result


def _derived_subdivision_source_item_payload(area: AdminArea, *, ancestor_ids: list[str] | None = None) -> dict:
    name = _area_display_name(area)
    entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    metadata = {} if ancestor_ids is not None else _group_admin_area_metadata(str(area.id))
    return {
        "id": str(area.id),
        "value": str(area.id),
        "country_code": _normalize_group_country_key(area.country_code),
        "code": str(area.code or ""),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
        "entity_type": entity_type,
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": ancestor_ids if ancestor_ids is not None else metadata.get("ancestor_ids") or [],
    }


def _area_ancestor_ids_from_parent_map(area_id: str, parent_by_id: dict[str, str]) -> list[str]:
    ancestor_ids = []
    parent_id = parent_by_id.get(str(area_id), "")
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        ancestor_ids.append(parent_id)
        parent_id = parent_by_id.get(parent_id, "")
    return ancestor_ids


def _derived_subdivision_group_source_item_id(country_code: str, group_key: str) -> str:
    return f"group::{_normalize_group_country_key(country_code)}::{_group_internal_name(group_key)}"


def _derived_subdivision_record_source_item_id(country_code: str, subdivision_key: str) -> str:
    return f"derived-subdivision::{_normalize_group_country_key(country_code)}::{_group_internal_name(subdivision_key)}"


def _derived_subdivision_group_entries_cached(
    country_code: str,
    entries_cache: dict[str, list[dict]] | None = None,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    if entries_cache is None:
        return _subdivision_group_entries_for_country(_subdivision_groups_for_country(country_code), country_code)
    if country_code not in entries_cache:
        entries_cache[country_code] = _subdivision_group_entries_for_country(
            _subdivision_groups_for_country(country_code),
            country_code,
        )
    return entries_cache[country_code]


def _derived_subdivision_group_entry_cached(
    country_code: str,
    group_key: str,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return None
    try:
        entry_slug = _group_entry_slug(group_key)
    except ValueError:
        return None
    if group_entry_cache is None:
        if entries_cache is not None:
            for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache):
                if _derived_subdivision_group_entry_matches(entry, entry_slug):
                    return entry
        return _group_entry_for_country(country_code, entry_slug, hydrate=False)
    cache_key = (country_code, entry_slug)
    if cache_key not in group_entry_cache:
        group_entry = None
        if entries_cache is not None:
            for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache):
                if _derived_subdivision_group_entry_matches(entry, entry_slug):
                    group_entry = entry
                    break
        if group_entry is None:
            group_entry = _group_entry_for_country(country_code, entry_slug, hydrate=False)
        group_entry_cache[cache_key] = group_entry
    return group_entry_cache[cache_key]


def _source_item_levels(items) -> list[int]:
    levels: set[int] = set()
    for item in items or []:
        raw_level = item.get("level") if isinstance(item, dict) else item
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level > 0:
            levels.add(level)
    return sorted(levels)


def _derived_subdivision_group_source_item_for_key(
    country_code: str,
    group_key: str,
    *,
    level: int | None = None,
    scoped_municipal_ids: set[str] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    try:
        normalized_key = _group_internal_name(group_key)
    except ValueError:
        return None
    options = _derived_subdivision_group_source_options(
        country_code,
        level=level,
        group_key=normalized_key,
        scoped_municipal_ids=scoped_municipal_ids,
        entries_cache=entries_cache,
    )
    if options:
        return options[0]
    level = int(level or _derived_subdivision_group_level(country_code) or 1)
    label = f"{normalized_key} ({_('Grupo')})"
    return {
        "id": _derived_subdivision_group_source_item_id(country_code, normalized_key),
        "value": _derived_subdivision_group_source_item_id(country_code, normalized_key),
        "source_kind": "group",
        "item_type": "group",
        "country_code": country_code,
        "code": normalized_key,
        "name": normalized_key,
        "label": label,
        "level": level,
        "entity_type": str(_("Grupo")),
        "parent_id": "",
        "ancestor_ids": [],
        "group_key": normalized_key,
        "group_slug": _group_entry_slug(normalized_key),
        "member_names": [],
        "member_ids": [],
        "member_count": 0,
        "member_text": "",
        "description": "",
    }


def _derived_subdivision_group_source_options(
    country_code: str,
    *,
    level: str | int | None = None,
    levels: list[str | int] | tuple[str | int, ...] | set[str | int] | None = None,
    parent_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    group_key: str = "",
    direct_parent_only: bool = False,
    scoped_municipal_ids: set[str] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    try:
        level_int = int(level) if level not in (None, "") else None
    except (TypeError, ValueError):
        return []
    requested_levels = {level_int} if level_int is not None else _source_item_levels(
        {"level": raw_level} for raw_level in (levels or [])
    )
    parent_id_set = {str(parent_id).strip() for parent_id in (parent_ids or []) if str(parent_id or "").strip()}
    scoped_municipal_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    lookup_key = str(group_key or "").strip()
    entries = [
        entry
        for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache)
        if not lookup_key or _derived_subdivision_group_entry_matches(entry, lookup_key)
    ]
    if not entries:
        return []
    entry_refs = [
        _derived_subdivision_group_member_refs(entry, country_code)
        for entry in entries
    ]
    target_levels = requested_levels or {int(_derived_subdivision_group_level(country_code) or 1)}
    if parent_id_set and not scoped_municipal_ids:
        for target_level in target_levels:
            scoped_municipal_ids.update(
                _derived_subdivision_descendant_ids_at_level(parent_id_set, int(target_level or 0))
            )
    area_by_id, area_by_key = _derived_subdivision_group_member_area_indexes(
        country_code,
        member_ids=[
            item_id
            for refs in entry_refs
            for item_id in refs["member_ids"]
        ],
        member_names=[
            name
            for refs in entry_refs
            for name in refs["member_names"]
            if _group_name_match_key(name) not in refs["selected_name_keys"]
        ],
        target_levels=target_levels,
    )
    parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(area_by_id.values())

    options = []
    seen: set[str] = set()
    for entry, refs in zip(entries, entry_refs):
        member_areas = _derived_subdivision_group_resolved_member_areas(
            refs,
            area_by_id=area_by_id,
            area_by_key=area_by_key,
            scoped_municipal_ids=scoped_municipal_ids,
        )
        resolved_level = _derived_subdivision_group_item_level(country_code, member_areas)
        parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
            member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
        )
        if requested_levels and resolved_level not in requested_levels:
            continue
        if parent_id_set and not _derived_subdivision_group_item_matches_parent_ids(
            {"parent_id": parent_id, "ancestor_ids": ancestor_ids},
            parent_id_set,
            direct_parent_only=direct_parent_only,
        ):
            continue
        item = _derived_subdivision_group_source_item_payload(
            entry,
            country_code,
            member_names=refs["member_names"],
            member_areas=member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
            resolved_level=resolved_level,
            resolved_parent_id=parent_id,
            resolved_ancestor_ids=ancestor_ids,
            include_member_names=include_member_names,
        )
        if not item:
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        options.append(item)
    return sorted(
        options,
        key=lambda item: (
            int(item.get("level") or 0),
            str(item.get("label") or item.get("name") or item.get("group_key") or ""),
            str(item.get("id") or ""),
        ),
    )


def _derived_subdivision_group_member_refs(entry: dict, country_code: str) -> dict:
    member_ids: list[str] = []
    member_names: list[str] = []
    selected_name_keys: set[str] = set()
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    country_code = _normalize_group_country_key(country_code)
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for name in _group_block_selected_names(block):
            name = str(name or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                member_names.append(name)
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                selected_name = str(selected.get("name") or selected.get("label") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
                selected_name_key = _group_name_match_key(selected_name)
                if selected_name_key:
                    selected_name_keys.add(selected_name_key)
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    member_ids.append(selected_id)
    return {
        "member_ids": member_ids,
        "member_names": member_names,
        "selected_name_keys": selected_name_keys,
    }


def _derived_subdivision_group_member_area_indexes(
    country_code: str,
    *,
    member_ids: list[str],
    member_names: list[str],
    target_levels: set[int] | None,
) -> tuple[dict[str, AdminArea], dict[str, list[AdminArea]]]:
    country_code = _normalize_group_country_key(country_code)
    clean_ids = sorted({str(item_id).strip() for item_id in member_ids if str(item_id or "").strip()})
    clean_names = [str(name).strip() for name in member_names if str(name or "").strip()]
    name_keys = {_group_name_match_key(name) for name in clean_names if _group_name_match_key(name)}
    area_by_id: dict[str, AdminArea] = {}
    candidates_by_key: dict[str, list[AdminArea]] = {}
    try:
        if clean_ids:
            for area in (
                _group_source_admin_areas()
                .filter(country_code__iexact=country_code, id__in=clean_ids)
                .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            ):
                area_by_id[str(area.id)] = area
        if name_keys:
            queryset = _group_source_admin_areas().filter(country_code__iexact=country_code).exclude(level=0)
            clean_target_levels = {int(value) for value in (target_levels or set()) if int(value or 0) > 0}
            if clean_target_levels:
                queryset = queryset.filter(level__in=clean_target_levels)
            if len(name_keys) <= 250:
                condition = Q()
                for name in clean_names:
                    condition |= Q(name__iexact=name) | Q(code__iexact=name) | Q(id=name)
                queryset = queryset.filter(condition) if condition else AdminArea.objects.none()
            rows = list(
                queryset.only(
                    "id",
                    "country_code",
                    "code",
                    "name",
                    "level",
                    "entity_type",
                    "parent_id",
                    "city_merge_status",
                )
            )
        else:
            rows = []
    except (OperationalError, ProgrammingError, ValueError):
        return area_by_id, candidates_by_key
    child_counts = _admin_area_child_counts([str(area.id) for area in rows], group_source_only=True)
    for area in sorted(rows, key=lambda item: _group_area_name_candidate_rank(item, child_counts)):
        for lookup_value in (area.name, area.code, area.id):
            key = _group_name_match_key(lookup_value)
            if key and key in name_keys:
                candidates_by_key.setdefault(key, []).append(area)
                area_by_id.setdefault(str(area.id), area)
    return area_by_id, candidates_by_key


def _derived_subdivision_group_resolved_member_areas(
    refs: dict,
    *,
    area_by_id: dict[str, AdminArea],
    area_by_key: dict[str, list[AdminArea]],
    scoped_municipal_ids: set[str] | None = None,
) -> list[AdminArea]:
    result: list[AdminArea] = []
    seen_ids: set[str] = set()
    selected_name_keys = refs.get("selected_name_keys") or set()
    scoped_municipal_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    for item_id in refs.get("member_ids") or []:
        area = area_by_id.get(str(item_id))
        if area and str(area.id) not in seen_ids:
            result.append(area)
            seen_ids.add(str(area.id))
    for name in refs.get("member_names") or []:
        key = _group_name_match_key(name)
        if key in selected_name_keys:
            continue
        candidates = area_by_key.get(key) or []
        area = _derived_subdivision_group_candidate_in_scope(candidates, scoped_municipal_ids)
        if area and str(area.id) not in seen_ids:
            result.append(area)
            seen_ids.add(str(area.id))
    return result


def _derived_subdivision_group_candidate_in_scope(
    candidates: list[AdminArea],
    scoped_municipal_ids: set[str],
) -> AdminArea | None:
    if not candidates:
        return None
    if not scoped_municipal_ids:
        return candidates[0]
    for candidate in candidates:
        if _derived_subdivision_area_matches_municipal_scope(candidate, scoped_municipal_ids):
            return candidate
    return candidates[0]


def _derived_subdivision_area_matches_municipal_scope(area: AdminArea, scoped_municipal_ids: set[str]) -> bool:
    if not scoped_municipal_ids:
        return False
    if str(area.id) in scoped_municipal_ids:
        return True
    return bool(
        _derived_subdivision_areas_to_capital_source_ids([area], country_code=area.country_code)
        & scoped_municipal_ids
    )


def _derived_subdivision_group_parent_maps(member_areas) -> tuple[dict[str, str], dict[str, int]]:
    parent_by_id: dict[str, str] = {}
    level_by_id: dict[str, int] = {}
    frontier = set()
    for area in member_areas:
        area_id = str(area.id)
        parent_by_id[area_id] = str(area.parent_id or "")
        level_by_id[area_id] = int(area.level or 0)
        if area.parent_id:
            frontier.add(str(area.parent_id))
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        try:
            rows = list(_visible_admin_areas().filter(id__in=current).values("id", "parent_id", "level"))
        except (OperationalError, ProgrammingError, ValueError):
            break
        frontier = set()
        for row in rows:
            area_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_by_id[area_id] = parent_id
            level_by_id[area_id] = int(row.get("level") or 0)
            if parent_id and parent_id not in seen:
                frontier.add(parent_id)
    return parent_by_id, level_by_id


def _admin_area_parent_maps_for_ids(area_ids: set[str]) -> tuple[dict[str, str], dict[str, int]]:
    parent_by_id: dict[str, str] = {}
    level_by_id: dict[str, int] = {}
    frontier = {str(area_id).strip() for area_id in area_ids if str(area_id or "").strip()}
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        try:
            rows = list(_visible_admin_areas().filter(id__in=current).values("id", "parent_id", "level"))
        except (OperationalError, ProgrammingError, ValueError):
            break
        frontier = set()
        for row in rows:
            area_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_by_id[area_id] = parent_id
            level_by_id[area_id] = int(row.get("level") or 0)
            if parent_id and parent_id not in seen:
                frontier.add(parent_id)
    return parent_by_id, level_by_id


def _admin_area_common_scope_for_ids(
    area_ids: set[str],
    *,
    parent_by_id: dict[str, str],
    level_by_id: dict[str, int],
) -> tuple[str, list[str]]:
    clean_ids = {str(area_id).strip() for area_id in area_ids if str(area_id or "").strip()}
    if not clean_ids:
        return "", []
    common_ids: set[str] | None = None
    for area_id in clean_ids:
        area_ancestor_ids = set()
        parent_id = parent_by_id.get(area_id, "")
        guard = 0
        while parent_id and guard < 20:
            area_ancestor_ids.add(parent_id)
            parent_id = parent_by_id.get(parent_id, "")
            guard += 1
        common_ids = area_ancestor_ids if common_ids is None else common_ids & area_ancestor_ids
    if not common_ids:
        return "", []
    parent_id = sorted(common_ids, key=lambda candidate: (level_by_id.get(candidate, -1), candidate), reverse=True)[0]
    ancestor_ids = sorted(
        (candidate for candidate in common_ids if candidate != parent_id),
        key=lambda candidate: (level_by_id.get(candidate, -1), candidate),
        reverse=True,
    )
    return parent_id, ancestor_ids


def _derived_subdivision_group_entry_matches(entry: dict, group_key: str) -> bool:
    candidates = [
        entry.get("internal_name"),
        entry.get("slug"),
        entry.get("record_slug"),
    ]
    try:
        lookup_internal = _group_internal_name(group_key)
    except ValueError:
        lookup_internal = ""
    try:
        lookup_slug = _group_entry_slug(group_key)
    except ValueError:
        lookup_slug = ""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if lookup_internal:
            try:
                if _group_internal_name(text) == lookup_internal:
                    return True
            except ValueError:
                pass
        if lookup_slug:
            try:
                if _group_entry_slug(text) == lookup_slug:
                    return True
            except ValueError:
                pass
    return False


def _derived_subdivision_group_source_item_payload(
    entry: dict,
    country_code: str,
    *,
    member_names: list[str] | None = None,
    member_areas: list[AdminArea] | None = None,
    parent_by_id: dict[str, str] | None = None,
    level_by_id: dict[str, int] | None = None,
    resolved_level: int | None = None,
    resolved_parent_id: str | None = None,
    resolved_ancestor_ids: list[str] | None = None,
    include_member_names: bool = True,
) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    try:
        group_key = _group_internal_name(entry.get("internal_name") or entry.get("slug") or "")
    except ValueError:
        return None
    group_slug = _group_entry_slug(entry.get("slug") or group_key)
    if member_names is None:
        member_names = _derived_subdivision_group_member_names(entry, country_code)
    if member_areas is None:
        member_areas = _derived_subdivision_group_member_areas(entry, country_code, member_names)
    if parent_by_id is None or level_by_id is None:
        parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    level = int(resolved_level or _derived_subdivision_group_item_level(country_code, member_areas))
    if resolved_parent_id is None or resolved_ancestor_ids is None:
        parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
            member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
        )
    else:
        parent_id = str(resolved_parent_id or "")
        ancestor_ids = resolved_ancestor_ids
    member_ids = [str(area.id) for area in member_areas]
    member_labels = [_derived_subdivision_group_member_label(area) for area in member_areas[:30]]
    resolved_name_keys = {_group_name_match_key(area.name) for area in member_areas}
    unresolved_names = [name for name in member_names if _group_name_match_key(name) not in resolved_name_keys]
    display_members = member_labels + [name for name in unresolved_names if name not in member_labels]
    member_text = ", ".join(display_members)
    if len(member_areas) > len(member_labels):
        member_text = f"{member_text}, +{len(member_areas) - len(member_labels)}" if member_text else f"+{len(member_areas) - len(member_labels)}"
    record = entry.get("record")
    description = str(getattr(record, "description", "") or "").strip()
    name = str(entry.get("name") or group_key).strip()
    label = f"{name} ({_('Grupo')})"
    return {
        "id": _derived_subdivision_group_source_item_id(country_code, group_key),
        "value": _derived_subdivision_group_source_item_id(country_code, group_key),
        "source_kind": "group",
        "item_type": "group",
        "country_code": country_code,
        "code": group_key,
        "name": name,
        "label": label,
        "level": level,
        "entity_type": str(_("Grupo")),
        "parent_id": parent_id,
        "ancestor_ids": ancestor_ids,
        "group_key": group_key,
        "group_slug": group_slug,
        "member_names": member_names if include_member_names else [],
        "member_ids": member_ids,
        "member_count": len(member_names),
        "member_text": member_text,
        "description": description,
    }


def _derived_subdivision_group_member_label(area: AdminArea) -> str:
    name = str(area.name or area.code or area.id)
    entity_type = str(area.entity_type or "")
    return f"{name} ({entity_type})" if entity_type else name


def _derived_subdivision_group_member_names(entry: dict, country_code: str) -> list[str]:
    names = []
    seen: set[str] = set()
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for name in _group_block_selected_names(block):
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _derived_subdivision_group_member_areas(entry: dict, country_code: str, member_names: list[str]) -> list[AdminArea]:
    country_code = _normalize_group_country_key(country_code)
    member_ids: list[str] = []
    seen_ids: set[str] = set()
    selected_name_keys: set[str] = set()
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                selected_name = str(selected.get("name") or selected.get("label") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
                selected_name_key = _group_name_match_key(selected_name)
                if selected_name_key:
                    selected_name_keys.add(selected_name_key)
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    member_ids.append(selected_id)
    resolved_ids = list(member_ids)
    unresolved_member_names = [name for name in member_names if _group_name_match_key(name) not in selected_name_keys]
    if unresolved_member_names:
        resolved = _group_sections_for_flat_names(country_code, unresolved_member_names)
        for section in resolved.get("sections") or []:
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    resolved_ids.append(selected_id)
    if not resolved_ids:
        return []
    try:
        rows = list(
            _group_source_admin_areas()
            .filter(country_code__iexact=country_code, id__in=resolved_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    area_by_id = {str(area.id): area for area in rows}
    return [area_by_id[item_id] for item_id in resolved_ids if item_id in area_by_id]


def _derived_subdivision_group_item_level(country_code: str, member_areas: list[AdminArea]) -> int:
    levels = {int(area.level or 0) for area in member_areas if int(area.level or 0) > 0}
    if len(levels) == 1:
        return next(iter(levels))
    return int(_derived_subdivision_group_level(country_code) or 1)


def _derived_subdivision_group_common_scope(
    member_areas: list[AdminArea],
    *,
    parent_by_id: dict[str, str] | None = None,
    level_by_id: dict[str, int] | None = None,
) -> tuple[str, list[str]]:
    if not member_areas:
        return "", []
    if parent_by_id is None or level_by_id is None:
        parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    common_ids: set[str] | None = None
    for area in member_areas:
        area_ancestor_ids = set()
        parent_id = str(area.parent_id or "")
        guard = 0
        while parent_id and guard < 20:
            area_ancestor_ids.add(parent_id)
            parent_id = parent_by_id.get(parent_id, "")
            guard += 1
        common_ids = area_ancestor_ids if common_ids is None else common_ids & area_ancestor_ids
    if not common_ids:
        return "", []
    parent_id = sorted(common_ids, key=lambda area_id: (level_by_id.get(area_id, -1), area_id), reverse=True)[0]
    ancestor_ids = sorted(
        (area_id for area_id in common_ids if area_id != parent_id),
        key=lambda area_id: (level_by_id.get(area_id, -1), area_id),
        reverse=True,
    )
    return parent_id, ancestor_ids


def _derived_subdivision_group_item_matches_parent_ids(item: dict, parent_ids: set[str], *, direct_parent_only: bool = False) -> bool:
    if direct_parent_only:
        return str(item.get("parent_id") or "").strip() in parent_ids
    scope_ids = {str(item.get("parent_id") or "").strip()}
    scope_ids.update(str(value).strip() for value in item.get("ancestor_ids") or [] if str(value or "").strip())
    return bool(scope_ids & parent_ids)


def _source_items_with_group_options(items: list[dict], group_options: list[dict]) -> list[dict]:
    result = list(items or [])
    seen = {str(item.get("id") or item.get("value") or "") for item in result if isinstance(item, dict)}
    for option in group_options or []:
        option_id = str(option.get("id") or option.get("value") or "").strip()
        if option_id and option_id not in seen:
            result.append(option)
            seen.add(option_id)
    return result


def _derived_subdivision_source_record_for_key(country_code: str, subdivision_key: str) -> DerivedSubdivision | None:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return None
    try:
        internal_name = _group_internal_name(subdivision_key)
    except ValueError:
        return None
    try:
        entry_slug = _group_entry_slug(internal_name)
    except ValueError:
        entry_slug = ""
    queryset = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    if entry_slug:
        record = queryset.filter(slug=_derived_subdivision_record_slug(country_code, entry_slug)).first()
        if record:
            return record
        record = queryset.filter(slug=entry_slug).first()
        if record:
            return record
    return queryset.filter(internal_name__iexact=internal_name).first()


def _derived_subdivision_source_key(record: DerivedSubdivision, data: dict | None = None) -> str:
    data = data if isinstance(data, dict) else _derived_subdivision_toml_data(record.content)
    for candidate in (
        data.get("internal_name"),
        record.internal_name,
        _derived_subdivision_entry_slug(record),
        record.slug,
    ):
        try:
            return _group_internal_name(str(candidate or ""))
        except ValueError:
            continue
    return _group_internal_name(record.slug)


def _derived_subdivision_source_level(record: DerivedSubdivision, data: dict | None = None) -> int:
    data = data if isinstance(data, dict) else _derived_subdivision_toml_data(record.content)
    try:
        return _derived_subdivision_level(data.get("level") or 1)
    except ValueError:
        return 1


def _derived_subdivision_source_ids_for_key(
    country_code: str,
    subdivision_key: str,
    *,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    record = _derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        return set()
    return _derived_subdivision_source_ids_for_record(
        record,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )


def _derived_subdivision_source_ids_for_record(
    record: DerivedSubdivision,
    *,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    data = _derived_subdivision_toml_data(record.content)
    country_code = _normalize_group_country_key(record.source_country_code or data.get("source_country_code") or "")
    if not country_code:
        return set()
    try:
        record_key = _derived_subdivision_source_key(record, data)
    except ValueError:
        return set()
    stack = set(seen or set())
    cache_key = (country_code, record_key)
    if source_ids_cache is not None and cache_key in source_ids_cache:
        return set(source_ids_cache[cache_key])
    if cache_key in stack:
        return set()
    stack.add(cache_key)
    source_ids = _derived_subdivision_node_source_ids(
        data,
        default_country_code=country_code,
        seen=stack,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    if source_ids_cache is not None:
        source_ids_cache[cache_key] = set(source_ids)
    return source_ids


def _derived_subdivision_node_source_ids(
    data: dict,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    if not isinstance(data, dict):
        return set()
    include_ids = _derived_subdivision_blocks_source_ids(
        data.get("include"),
        default_country_code=default_country_code,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    subtract_ids = _derived_subdivision_blocks_source_ids(
        data.get("subtract"),
        default_country_code=default_country_code,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    result = include_ids - subtract_ids
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if isinstance(child, dict):
            result.update(
                _derived_subdivision_node_source_ids(
                    child,
                    default_country_code=default_country_code,
                    seen=seen,
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                )
            )
    return result


def _derived_subdivision_record_source_areas(
    record: DerivedSubdivision,
    *,
    source_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> list[AdminArea]:
    if source_ids is None:
        source_ids = _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    if not source_ids:
        return []
    try:
        return list(
            _group_source_admin_areas()
            .filter(id__in=source_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            .order_by("level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_record_source_item_for_key(
    country_code: str,
    subdivision_key: str,
    *,
    level: int | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    record = _derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        try:
            normalized_key = _group_internal_name(subdivision_key)
        except ValueError:
            return None
        level = int(level or 1)
        label = f"{normalized_key} ({_('Subdivisiones creadas')}, {_('Nivel')} {level})"
        item_id = _derived_subdivision_record_source_item_id(country_code, normalized_key)
        return {
            "id": item_id,
            "value": item_id,
            "source_kind": "derived_subdivision",
            "item_type": "derived_subdivision",
            "country_code": country_code,
            "code": normalized_key,
            "name": normalized_key,
            "label": label,
            "level": level,
            "entity_type": str(_("Subdivisiones creadas")),
            "parent_id": "",
            "ancestor_ids": [],
            "derived_subdivision_key": normalized_key,
            "derived_subdivision_slug": _group_entry_slug(normalized_key),
            "member_names": [],
            "member_ids": [],
            "member_count": 0,
            "member_text": "",
            "description": "",
        }
    return _derived_subdivision_record_source_item_payload(
        record,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )


def _derived_subdivision_record_source_item_payload(
    record: DerivedSubdivision,
    *,
    source_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
    compact_parent_id: str = "",
    compact_ancestor_ids: list[str] | None = None,
) -> dict | None:
    country_code = _normalize_group_country_key(record.source_country_code)
    data = _derived_subdivision_toml_data(record.content)
    if not country_code:
        country_code = _normalize_group_country_key(data.get("source_country_code") or "")
    if not country_code:
        return None
    try:
        subdivision_key = _derived_subdivision_source_key(record, data)
    except ValueError:
        return None
    level = _derived_subdivision_source_level(record, data)
    if not include_member_names and source_ids is not None:
        member_ids = sorted(str(item_id).strip() for item_id in source_ids if str(item_id or "").strip())
        if not member_ids:
            return None
        parent_id = str(compact_parent_id or "").strip()
        ancestor_ids = [str(value) for value in (compact_ancestor_ids or []) if str(value or "").strip()]
        if not parent_id:
            parent_by_id, level_by_id = _admin_area_parent_maps_for_ids(set(member_ids))
            parent_id, ancestor_ids = _admin_area_common_scope_for_ids(
                set(member_ids),
                parent_by_id=parent_by_id,
                level_by_id=level_by_id,
            )
        try:
            preview_areas = list(
                _group_source_admin_areas()
                .filter(id__in=member_ids)
                .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
                .order_by("level", "name", "id")[:30]
            )
        except (OperationalError, ProgrammingError, ValueError):
            preview_areas = []
        member_labels = [_derived_subdivision_group_member_label(area) for area in preview_areas]
        member_text = ", ".join(member_labels)
        if len(member_ids) > len(member_labels):
            member_text = f"{member_text}, +{len(member_ids) - len(member_labels)}" if member_text else f"+{len(member_ids) - len(member_labels)}"
        name = str(data.get("name") or record.name or subdivision_key).strip()
        entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
        if entity_type:
            label = f"{name} ({entity_type}, {_('Nivel')} {level})"
        else:
            label = f"{name} ({_('Nivel')} {level})"
        item_id = _derived_subdivision_record_source_item_id(country_code, subdivision_key)
        return {
            "id": item_id,
            "value": item_id,
            "source_kind": "derived_subdivision",
            "item_type": "derived_subdivision",
            "country_code": country_code,
            "code": subdivision_key,
            "name": name,
            "label": label,
            "level": level,
            "entity_type": str(_("Subdivisiones creadas")),
            "parent_id": parent_id,
            "ancestor_ids": ancestor_ids,
            "derived_subdivision_key": subdivision_key,
            "derived_subdivision_slug": _derived_subdivision_entry_slug(record),
            "member_names": [],
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "member_text": member_text,
            "description": str(record.description or "").strip(),
        }
    member_areas = _derived_subdivision_record_source_areas(
        record,
        source_ids=source_ids,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    if not member_areas:
        return None
    parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
        member_areas,
        parent_by_id=parent_by_id,
        level_by_id=level_by_id,
    )
    member_ids = [str(area.id) for area in member_areas]
    member_labels = [_derived_subdivision_group_member_label(area) for area in member_areas[:30]]
    display_members = member_labels
    member_text = ", ".join(display_members)
    if len(member_areas) > len(display_members):
        member_text = f"{member_text}, +{len(member_areas) - len(display_members)}" if member_text else f"+{len(member_areas) - len(display_members)}"
    name = str(data.get("name") or record.name or subdivision_key).strip()
    entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
    if entity_type:
        label = f"{name} ({entity_type}, {_('Nivel')} {level})"
    else:
        label = f"{name} ({_('Nivel')} {level})"
    item_id = _derived_subdivision_record_source_item_id(country_code, subdivision_key)
    return {
        "id": item_id,
        "value": item_id,
        "source_kind": "derived_subdivision",
        "item_type": "derived_subdivision",
        "country_code": country_code,
        "code": subdivision_key,
        "name": name,
        "label": label,
        "level": level,
        "entity_type": str(_("Subdivisiones creadas")),
        "parent_id": parent_id,
        "ancestor_ids": ancestor_ids,
        "derived_subdivision_key": subdivision_key,
        "derived_subdivision_slug": _derived_subdivision_entry_slug(record),
        "member_names": member_labels if include_member_names else [],
        "member_ids": member_ids,
        "member_count": len(member_ids),
        "member_text": member_text,
        "description": str(record.description or "").strip(),
    }


def _derived_subdivision_source_options(
    country_code: str,
    *,
    level: str | int | None = None,
    levels: list[str | int] | tuple[str | int, ...] | set[str | int] | None = None,
    parent_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    current_level: str | int | None = None,
    current_subdivision: str = "",
    current_subdivision_country: str = "",
    scoped_municipal_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    try:
        minimum_level = int(level) if level not in (None, "") else None
    except (TypeError, ValueError):
        minimum_level = None
    if minimum_level is None:
        requested_levels = _source_item_levels({"level": raw_level} for raw_level in (levels or []))
        minimum_level = min(requested_levels) if requested_levels else None
    try:
        current_level_int = int(current_level) if current_level not in (None, "") else None
    except (TypeError, ValueError):
        current_level_int = None
    try:
        current_key = _group_internal_name(current_subdivision) if str(current_subdivision or "").strip() else ""
    except ValueError:
        current_key = ""
    current_subdivision_country = _normalize_group_country_key(current_subdivision_country)
    parent_id_set = {str(parent_id).strip() for parent_id in (parent_ids or []) if str(parent_id or "").strip()}
    compact_parent_id = next(iter(parent_id_set)) if len(parent_id_set) == 1 else ""
    scope_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    if parent_id_set and not scope_ids:
        scope_ids = _derived_subdivision_municipal_scope_ids_for_parents(country_code, parent_id_set)
    try:
        records = list(
            DerivedSubdivision.objects
            .filter(source_country_code__iexact=country_code)
            .only("slug", "internal_name", "name", "source_country_code", "entity_type", "code", "description", "content")
            .order_by("name", "slug")
        )
    except (OperationalError, ProgrammingError):
        return []
    options = []
    seen_ids: set[str] = set()
    for record in records:
        data = _derived_subdivision_toml_data(record.content)
        try:
            record_key = _derived_subdivision_source_key(record, data)
        except ValueError:
            continue
        if current_key and (not current_subdivision_country or current_subdivision_country == country_code) and record_key == current_key:
            continue
        record_level = _derived_subdivision_source_level(record, data)
        if current_level_int is not None and record_level <= current_level_int:
            continue
        if minimum_level is not None and record_level < minimum_level:
            continue
        source_ids = _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
        if parent_id_set:
            if not source_ids or not (source_ids & scope_ids):
                continue
        item = _derived_subdivision_record_source_item_payload(
            record,
            source_ids=source_ids,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            include_member_names=include_member_names,
            compact_parent_id=compact_parent_id if not include_member_names else "",
        )
        if not item:
            continue
        item_id = str(item.get("id") or item.get("value") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        options.append(item)
        seen_ids.add(item_id)
    return sorted(
        options,
        key=lambda item: (
            int(item.get("level") or 0),
            str(item.get("label") or item.get("name") or ""),
            str(item.get("id") or ""),
        ),
    )


def _derived_subdivision_municipal_scope_ids_for_parents(country_code: str, parent_ids: set[str]) -> set[str]:
    target_level = _derived_subdivision_capital_target_level(country_code)
    if target_level is None:
        return set()
    parent_ids = {str(parent_id).strip() for parent_id in parent_ids if str(parent_id or "").strip()}
    result: set[str] = set()
    if not parent_ids:
        return result
    try:
        parents = list(_group_source_admin_areas().filter(id__in=parent_ids).only("id", "level", "country_code", "city_merge_status"))
    except (OperationalError, ProgrammingError, ValueError):
        parents = []
    lower_parent_ids: set[str] = set()
    for parent in parents:
        try:
            level = int(parent.level or 0)
        except (TypeError, ValueError):
            continue
        if level == target_level:
            result.add(str(parent.id))
        elif level < target_level:
            lower_parent_ids.add(str(parent.id))
        else:
            ancestor = _derived_subdivision_ancestor_at_level(parent, target_level)
            if ancestor:
                result.add(str(ancestor.id))
    result |= _derived_subdivision_descendant_ids_at_level(lower_parent_ids, target_level)
    return result


def _derived_subdivision_source_items_with_default(items: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = _normalize_group_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    seen_ids: dict[str, set[str]] = {}
    for block in items:
        if not isinstance(block, dict):
            continue
        country_code = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "items": []})
        country_seen = seen_ids.setdefault(country_code, set())
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("value") or "").strip()
            if not item_id or item_id in country_seen:
                continue
            normalized = dict(item)
            normalized["id"] = item_id
            normalized["value"] = item_id
            normalized["country_code"] = _normalize_group_country_key(normalized.get("country_code") or country_code)
            grouped_block["items"].append(normalized)
            country_seen.add(item_id)
    if fallback_country_code and fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "items": []}
    ordered_codes = ([fallback_country_code] if fallback_country_code in grouped else []) + sorted(
        code for code in grouped if code != fallback_country_code
    )
    return [grouped[code] for code in ordered_codes]


def _derived_subdivision_visual_source_blocks_from_content(
    content: str,
    table_name: str,
    *,
    fallback_country_code: str,
) -> list[dict]:
    data = _derived_subdivision_toml_data(content)
    source_blocks = data.get(table_name) if isinstance(data.get(table_name), list) else []
    visual_by_country: dict[str, dict] = {}
    for block in source_blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        try:
            level = int(block.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        refs = _derived_subdivision_block_source_refs(block, country_code=block_country) if level else {"ids": [], "codes": [], "names": []}
        areas = _derived_subdivision_source_ref_areas(block_country, level, refs) if level else []
        if not areas:
            continue
        country_block = visual_by_country.setdefault(block_country, {"country_code": block_country, "names": [], "sections": []})
        for section in _derived_subdivision_visual_sections_from_areas(areas):
            _derived_subdivision_merge_visual_section(country_block, section)
    return _derived_subdivision_source_blocks_with_default(
        list(visual_by_country.values()),
        fallback_country_code=fallback_country_code,
    )


def _derived_subdivision_visual_sections_from_areas(areas: list[AdminArea]) -> list[dict]:
    area_ids = [str(area.id) for area in areas if str(getattr(area, "id", "") or "").strip()]
    if not area_ids:
        return []
    try:
        hydrated = list(
            _group_source_admin_areas()
            .filter(id__in=area_ids)
            .select_related("parent")
            .only(
                "id",
                "country_code",
                "code",
                "name",
                "level",
                "entity_type",
                "parent_id",
                "parent__id",
                "parent__country_code",
                "parent__code",
                "parent__name",
                "parent__level",
                "parent__entity_type",
                "parent__parent_id",
            )
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    sections_by_parent: dict[str, dict] = {}
    selected_seen: dict[str, set[str]] = {}
    for area in sorted(hydrated, key=lambda item: (str(item.parent_id or ""), int(item.level or 0), _area_display_name(item), str(item.id))):
        parent = area.parent
        if parent is None:
            continue
        parent_id = str(parent.id)
        section = sections_by_parent.setdefault(parent_id, _group_section_payload_from_area(parent))
        section_seen = selected_seen.setdefault(parent_id, set())
        area_id = str(area.id)
        if area_id in section_seen:
            continue
        section_seen.add(area_id)
        section["selected"].append(_derived_subdivision_visual_selected_area_payload(area))
    return list(sections_by_parent.values())


def _derived_subdivision_visual_selected_area_payload(area: AdminArea) -> dict:
    name = _area_display_name(area)
    label = name
    entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
    if entity_type:
        label = f"{label} ({entity_type})"
    return {
        "id": str(area.id),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
    }


def _derived_subdivision_merge_visual_section(country_block: dict, section: dict) -> None:
    if not isinstance(section, dict):
        return
    section_id = str(section.get("area_id") or "").strip()
    if not section_id:
        return
    sections = country_block.setdefault("sections", [])
    existing = next((item for item in sections if str(item.get("area_id") or "") == section_id), None)
    if existing is None:
        sections.append(section)
        return
    seen = {str(item.get("id") or item.get("name") or "") for item in existing.get("selected") or [] if isinstance(item, dict)}
    for selected in section.get("selected") or []:
        if not isinstance(selected, dict):
            continue
        selected_key = str(selected.get("id") or selected.get("name") or "").strip()
        if selected_key and selected_key not in seen:
            existing.setdefault("selected", []).append(selected)
            seen.add(selected_key)


def _derived_subdivision_source_blocks_with_default(blocks: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = _normalize_group_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        country_code = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "names": [], "sections": []})
        grouped_block["names"].extend(str(name).strip() for name in (block.get("names") or []) if str(name or "").strip())
        for section in block.get("sections") or []:
            _derived_subdivision_merge_visual_section(grouped_block, section)
    if fallback_country_code and fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "names": [], "sections": []}
    ordered_codes = ([fallback_country_code] if fallback_country_code in grouped else []) + sorted(
        code for code in grouped if code != fallback_country_code
    )
    return [grouped[code] for code in ordered_codes]


def _derived_subdivision_content_with_visual_source_blocks(
    content: str,
    *,
    include_blocks_json,
    subtract_blocks_json,
    country_code: str,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data["include"] = _derived_subdivision_toml_blocks_from_visual_source_json(
        include_blocks_json,
        fallback_country_code=country_code,
    )
    data["subtract"] = _derived_subdivision_toml_blocks_from_visual_source_json(
        subtract_blocks_json,
        fallback_country_code=country_code,
    )
    return _render_derived_subdivision_toml_data(data)


def _derived_subdivision_content_with_visual_source_ids(
    content: str,
    *,
    include_ids_json,
    subtract_ids_json,
    country_code: str,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data["include"] = _derived_subdivision_toml_blocks_from_visual_source_ids(
        include_ids_json,
        fallback_country_code=country_code,
    )
    data["subtract"] = _derived_subdivision_toml_blocks_from_visual_source_ids(
        subtract_ids_json,
        fallback_country_code=country_code,
    )
    return _render_derived_subdivision_toml_data(data)


def _derived_subdivision_toml_blocks_from_visual_source_ids(value, *, fallback_country_code: str) -> list[dict]:
    raw_items = _derived_subdivision_raw_source_items_from_json(value)
    requested_ids = []
    seen_requested_ids = set()
    for item in raw_items:
        if _derived_subdivision_raw_source_item_group_key(item):
            continue
        if _derived_subdivision_raw_source_item_subdivision_key(item):
            continue
        item_id = str(item.get("id") or item.get("value") or "").strip()
        if item_id and item_id not in seen_requested_ids:
            requested_ids.append(item_id)
            seen_requested_ids.add(item_id)
    if requested_ids:
        try:
            rows = list(
                _group_source_admin_areas()
                .filter(id__in=requested_ids)
                .values("id", "country_code", "level")
            )
        except (OperationalError, ProgrammingError, ValueError):
            rows = []
    else:
        rows = []
    area_by_id = {str(row["id"]): row for row in rows}
    result: dict[tuple[str, int], dict] = {}
    seen_values: dict[tuple[str, int, str], set[str]] = {}

    def toml_block_for(block_country: str, level: int) -> dict:
        key = (block_country, level)
        return result.setdefault(
            key,
            {
                "country_code": block_country,
                "level": level,
                "names": [],
                "ids": [],
                "codes": [],
                "groups": [],
                "derived_subdivisions": [],
                "expressions": [],
            },
        )

    for item in raw_items:
        group_key = _derived_subdivision_raw_source_item_group_key(item)
        if group_key:
            block_country = _derived_subdivision_raw_source_item_country(item, fallback_country_code=fallback_country_code)
            try:
                level = int(item.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if not level:
                level = _derived_subdivision_group_level(block_country)
            if not block_country or not level:
                continue
            toml_block = toml_block_for(block_country, int(level))
            seen = seen_values.setdefault((block_country, int(level), "groups"), set())
            if group_key not in seen:
                toml_block["groups"].append(group_key)
                seen.add(group_key)
            continue

        subdivision_key = _derived_subdivision_raw_source_item_subdivision_key(item)
        if subdivision_key:
            block_country = _derived_subdivision_raw_source_item_country(item, fallback_country_code=fallback_country_code)
            try:
                level = int(item.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if not block_country or not level:
                continue
            toml_block = toml_block_for(block_country, int(level))
            seen = seen_values.setdefault((block_country, int(level), "derived_subdivisions"), set())
            if subdivision_key not in seen:
                toml_block["derived_subdivisions"].append(subdivision_key)
                seen.add(subdivision_key)
            continue

        item_id = str(item.get("id") or item.get("value") or "").strip()
        row = area_by_id.get(item_id)
        if not row:
            continue
        block_country = _normalize_group_country_key(row.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level < 0:
            continue
        toml_block = toml_block_for(block_country, level)
        seen = seen_values.setdefault((block_country, level, "ids"), set())
        if item_id not in seen:
            toml_block["ids"].append(item_id)
            seen.add(item_id)
    fallback_country_code = _normalize_group_country_key(fallback_country_code)
    return sorted(
        result.values(),
        key=lambda block: (
            0 if block.get("country_code") == fallback_country_code else 1,
            str(block.get("country_code") or ""),
            int(block.get("level") or 0),
        ),
    )


def _derived_subdivision_raw_source_item_country(item: dict, *, fallback_country_code: str) -> str:
    country_code = _normalize_group_country_key(item.get("country_code") or fallback_country_code)
    item_id = str(item.get("id") or item.get("value") or "").strip()
    if item_id.startswith("group::") or item_id.startswith("derived-subdivision::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            country_code = _normalize_group_country_key(parts[1] or country_code)
    return country_code


def _derived_subdivision_raw_source_item_group_key(item: dict) -> str:
    item_id = str(item.get("id") or item.get("value") or "").strip()
    source_kind = str(item.get("source_kind") or item.get("item_type") or "").strip().lower()
    is_group = source_kind in {"group", "subdivision_group"} or item_id.startswith("group::")
    if not is_group:
        return ""
    group_key = str(item.get("group_key") or item.get("internal_name") or item.get("code") or "").strip()
    if not group_key and item_id.startswith("group::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            group_key = parts[2]
    try:
        return _group_internal_name(group_key)
    except ValueError:
        return ""


def _derived_subdivision_raw_source_item_subdivision_key(item: dict) -> str:
    item_id = str(item.get("id") or item.get("value") or "").strip()
    source_kind = str(item.get("source_kind") or item.get("item_type") or "").strip().lower()
    is_subdivision = source_kind in {"derived_subdivision", "derived-subdivision"} or item_id.startswith("derived-subdivision::")
    if not is_subdivision:
        return ""
    subdivision_key = str(
        item.get("derived_subdivision_key")
        or item.get("internal_name")
        or item.get("code")
        or ""
    ).strip()
    if not subdivision_key and item_id.startswith("derived-subdivision::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            subdivision_key = parts[2]
    try:
        return _group_internal_name(subdivision_key)
    except ValueError:
        return ""


def _derived_subdivision_toml_blocks_from_visual_source_json(value, *, fallback_country_code: str) -> list[dict]:
    raw_blocks = _derived_subdivision_raw_source_blocks_from_json(value)
    selected_ids = {
        str(selected.get("id") or "").strip()
        for block in raw_blocks
        if isinstance(block, dict)
        for section in block.get("sections") or []
        if isinstance(section, dict)
        for selected in section.get("selected") or []
        if (
            isinstance(selected, dict)
            and str(selected.get("id") or "").strip()
            and not _derived_subdivision_raw_source_item_group_key(selected)
            and not _derived_subdivision_raw_source_item_subdivision_key(selected)
        )
    }
    area_level_map = _derived_subdivision_selected_area_levels(selected_ids)
    result: dict[tuple[str, int], dict] = {}
    seen_values: dict[tuple[str, int, str], set[str]] = {}
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            try:
                section_level = int(section.get("level") or 0)
            except (TypeError, ValueError):
                section_level = 0
            selected_values = section.get("selected") if isinstance(section.get("selected"), list) else []
            for selected in selected_values:
                if not isinstance(selected, dict):
                    continue
                selected_id = str(selected.get("id") or "").strip()
                selected_name = str(selected.get("name") or selected.get("label") or "").strip()
                try:
                    selected_level = int(selected.get("level") or 0)
                except (TypeError, ValueError):
                    selected_level = 0
                subdivision_key = _derived_subdivision_raw_source_item_subdivision_key(selected)
                if subdivision_key:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "derived_subdivisions")
                    seen = seen_values.setdefault(seen_key, set())
                    if subdivision_key not in seen:
                        toml_block["derived_subdivisions"].append(subdivision_key)
                        seen.add(subdivision_key)
                    continue
                group_key = _derived_subdivision_raw_source_item_group_key(selected)
                if group_key:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "groups")
                    seen = seen_values.setdefault(seen_key, set())
                    if group_key not in seen:
                        toml_block["groups"].append(group_key)
                        seen.add(group_key)
                    continue
                if selected_id:
                    selected_level = (
                        area_level_map[selected_id]
                        if selected_id in area_level_map
                        else selected_level or (section_level + 1 if section_level else 0)
                    )
                    if selected_level < 0:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "ids")
                    seen = seen_values.setdefault(seen_key, set())
                    if selected_id not in seen:
                        toml_block["ids"].append(selected_id)
                        seen.add(selected_id)
                elif selected_name:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "names")
                    seen = seen_values.setdefault(seen_key, set())
                    if selected_name not in seen:
                        toml_block["names"].append(selected_name)
                        seen.add(selected_name)
    return [result[key] for key in sorted(result)]


def _derived_subdivision_raw_source_blocks_from_json(value) -> list[dict]:
    if isinstance(value, str):
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    else:
        payload = value
    if payload in (None, ""):
        return []
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    return [item for item in payload if isinstance(item, dict)]


def _derived_subdivision_raw_source_items_from_json(value) -> list[dict]:
    if isinstance(value, str):
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    else:
        payload = value
    if payload in (None, ""):
        return []
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    items = []
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            country_code = _normalize_group_country_key(entry.get("country_code") or "")
            for item in entry.get("items") or []:
                if isinstance(item, dict):
                    normalized = dict(item)
                    if country_code and not normalized.get("country_code"):
                        normalized["country_code"] = country_code
                    items.append(normalized)
                else:
                    item_id = str(item or "").strip()
                    if item_id:
                        items.append({"id": item_id, "country_code": country_code})
        elif isinstance(entry, dict):
            items.append(entry)
        else:
            item_id = str(entry or "").strip()
            if item_id:
                items.append({"id": item_id})
    return items


def _derived_subdivision_selected_area_levels(area_ids: set[str]) -> dict[str, int]:
    if not area_ids:
        return {}
    try:
        rows = list(_group_source_admin_areas().filter(id__in=area_ids).values("id", "level"))
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    return {str(row["id"]): int(row.get("level") or 0) for row in rows}


def _derived_subdivision_group_country_sections(current_country_code: str) -> list[dict]:
    current_country_code = _normalize_group_country_key(current_country_code)
    country_codes = {current_country_code}
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        country_codes.update(_subdivision_group_record_country_codes(group))
    sections = []
    for country_code in sorted(code for code in country_codes if code):
        entries = _subdivision_group_entries_for_country(_subdivision_groups_for_country(country_code), country_code)
        if not entries:
            continue
        sections.append(
            {
                "country_code": country_code,
                "country_label": _display_name("", country_code, country_code=country_code),
                "default_group_level": _derived_subdivision_group_level(country_code),
                "groups": entries,
            }
        )
    return sections


def _derived_subdivision_group_level(country_code: str) -> int:
    country_code = _normalize_group_country_key(country_code)
    configured_level = effective_source_municipal_level(country_code)
    if configured_level is not None:
        return int(configured_level)
    try:
        level = (
            _visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .order_by("-level")
            .values_list("level", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        level = None
    return int(level or 1)


def _derived_subdivision_capital_queryset(country_code: str):
    country_code = _normalize_group_country_key(country_code)
    queryset = _group_source_admin_areas().filter(country_code__iexact=country_code)
    level = _derived_subdivision_group_level(country_code)
    if level is not None:
        queryset = queryset.filter(level=int(level))
    return queryset


def _derived_subdivision_capital_options(
    country_code: str,
    *,
    source_ids: set[str] | None = None,
    selected_ids=None,
    search_term: str = "",
    limit: int = 50,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code or source_ids == set():
        return []
    selected_ids = set(_normalized_derived_subdivision_capital_values(selected_ids))
    search_key = _derived_subdivision_capital_search_key(search_term)
    has_search = len(search_key) >= 2
    if not selected_ids and not has_search:
        return []
    try:
        queryset = _derived_subdivision_capital_queryset(country_code)
        if source_ids is not None:
            queryset = _group_source_admin_areas().filter(id__in=source_ids)
        condition = Q()
        if selected_ids:
            condition |= Q(id__in=selected_ids)
        if has_search:
            if source_ids is None:
                first_letters = _derived_subdivision_capital_first_letter_filters(search_term)
                search_condition = Q()
                for first_letter in first_letters:
                    search_condition |= Q(name__istartswith=first_letter)
                condition |= search_condition
            else:
                condition |= Q(id__in=source_ids)
        rows = list(
            queryset.filter(condition)
            .only("id", "country_code", "code", "name", "level", "entity_type")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    options = []
    matched_count = 0
    seen_values = set()
    for area in rows:
        value = str(area.id)
        is_selected = value in selected_ids
        name = _area_display_name(area)
        if not is_selected:
            if not has_search or not _derived_subdivision_capital_name_matches(area, search_key, display_name=name):
                continue
            if matched_count >= limit:
                continue
            matched_count += 1
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append(_derived_subdivision_capital_option(area, display_name=name))
    return options


def _derived_subdivision_declared_capital_ids_for_key(
    country_code: str,
    subdivision_key: str,
) -> set[str]:
    record = _derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        return set()
    data = _derived_subdivision_record_toml(record)
    record_country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    if not record_country_code:
        return set()
    return _declared_capital_ids_from_data(record_country_code, data)


def _declared_capital_ids_from_data(country_code: str, data: dict) -> set[str]:
    country_code = _normalize_group_country_key(country_code)
    capital_values = _derived_subdivision_capital_values(country_code, data)
    if not country_code or not capital_values:
        return set()
    capital_ids = {str(value).strip() for value in capital_values if str(value or "").strip()}
    if not capital_ids:
        return set()
    try:
        return {
            str(area_id)
            for chunk in _new_country_chunks(sorted(capital_ids), 900)
            for area_id in _group_source_admin_areas()
            .filter(id__in=chunk)
            .values_list("id", flat=True)
        }
    except (OperationalError, ProgrammingError, ValueError):
        return set()


def _derived_subdivision_capital_search_options(
    country_code: str,
    *,
    content: str,
    selected_ids=None,
    search_term: str = "",
    limit: int = 50,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    search_key = _derived_subdivision_capital_search_key(search_term)
    selected_ids = set(_normalized_derived_subdivision_capital_values(selected_ids))
    if len(search_key) < 2:
        return _derived_subdivision_capital_options(country_code, selected_ids=selected_ids)
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        return _derived_subdivision_capital_options(country_code, selected_ids=selected_ids)
    target_level = _derived_subdivision_capital_target_level(country_code)
    include_scope = _derived_subdivision_capital_scope(
        data.get("include"),
        country_code=country_code,
        target_level=target_level,
    )
    if not _derived_subdivision_capital_scope_has_rules(include_scope):
        return []
    subtract_scope = _derived_subdivision_capital_scope(
        data.get("subtract"),
        country_code=country_code,
        target_level=target_level,
    )
    candidates = _derived_subdivision_capital_prefix_candidates(
        country_code,
        search_term=search_term,
        target_level=target_level,
    )
    seen_values = set()
    options = []
    matched_count = 0

    def collect(candidate_rows):
        nonlocal matched_count
        ancestor_map = _derived_subdivision_candidate_ancestor_ids(candidate_rows)
        for area in candidate_rows:
            if matched_count >= limit:
                break
            value = str(area.id)
            if value in seen_values:
                continue
            ancestor_ids = ancestor_map.get(value, {value})
            if not _derived_subdivision_capital_area_in_scope(area, include_scope, ancestor_ids):
                continue
            if _derived_subdivision_capital_area_in_scope(area, subtract_scope, ancestor_ids):
                continue
            name = _area_display_name(area)
            if not _derived_subdivision_capital_name_matches(area, search_key, display_name=name):
                continue
            options.append(_derived_subdivision_capital_option(area, display_name=name))
            seen_values.add(value)
            matched_count += 1

    collect(candidates)
    if matched_count < limit:
        collect(
            _derived_subdivision_capital_scoped_candidates(
                country_code,
                include_scope=include_scope,
                target_level=target_level,
                exclude_ids=seen_values,
            )
        )
    return options


def _derived_subdivision_capital_target_level(country_code: str) -> int:
    target_level = _derived_subdivision_group_level(country_code)
    return int(target_level or 0)


def _derived_subdivision_capital_prefix_candidates(
    country_code: str,
    *,
    search_term: str,
    target_level: int,
):
    queryset = _derived_subdivision_capital_queryset(country_code)
    if target_level is not None:
        queryset = queryset.filter(level=target_level)
    first_letters = _derived_subdivision_capital_first_letter_filters(search_term)
    search_condition = Q()
    for first_letter in first_letters:
        search_condition |= Q(name__istartswith=first_letter)
    if not search_condition:
        return []
    try:
        return list(
            queryset.filter(search_condition)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_capital_scoped_candidates(
    country_code: str,
    *,
    include_scope: dict[str, set[str]],
    target_level: int,
    exclude_ids: set[str],
) -> list[AdminArea]:
    source_ids = set(include_scope.get("area_ids", set()))
    source_ids |= _derived_subdivision_descendant_ids_at_level(set(include_scope.get("parent_ids", set())), target_level)
    source_ids = {str(value) for value in source_ids if str(value or "").strip() and str(value) not in exclude_ids}
    if not source_ids:
        return []
    queryset = _group_source_admin_areas().filter(country_code__iexact=country_code, id__in=source_ids)
    if target_level is not None:
        queryset = queryset.filter(level=target_level)
    try:
        return list(
            queryset.only("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_capital_scope(blocks, *, country_code: str, target_level: int) -> dict[str, set[str]]:
    scope = {"area_ids": set(), "parent_ids": set()}
    if not isinstance(blocks, list):
        return scope
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        try:
            level = int(block.get("level"))
        except (TypeError, ValueError):
            continue
        refs = _derived_subdivision_block_source_refs(block, country_code=block_country)
        areas = _derived_subdivision_source_ref_areas(block_country, level, refs)
        for area in areas:
            try:
                area_level = int(area.level or 0)
            except (TypeError, ValueError):
                continue
            area_id = str(area.id)
            if area_level == target_level:
                scope["area_ids"].add(area_id)
            elif area_level < target_level:
                scope["parent_ids"].add(area_id)
            else:
                ancestor = _derived_subdivision_ancestor_at_level(area, target_level)
                if ancestor and int(ancestor.city_merge_status or 0) in GROUP_SOURCE_CITY_MERGE_STATUSES:
                    scope["area_ids"].add(str(ancestor.id))
    return scope


def _derived_subdivision_capital_scope_has_rules(scope: dict[str, set[str]]) -> bool:
    return bool(scope.get("area_ids") or scope.get("parent_ids"))


def _derived_subdivision_capital_area_in_scope(area: AdminArea, scope: dict[str, set[str]], ancestor_ids: set[str]) -> bool:
    if not _derived_subdivision_capital_scope_has_rules(scope):
        return False
    area_id = str(area.id)
    return area_id in scope.get("area_ids", set()) or bool(scope.get("parent_ids", set()) & ancestor_ids)


def _derived_subdivision_candidate_ancestor_ids(areas) -> dict[str, set[str]]:
    result = {str(area.id): {str(area.id)} for area in areas}
    parent_ids = {str(area.parent_id) for area in areas if getattr(area, "parent_id", None)}
    seen_parent_ids: set[str] = set()
    parent_map: dict[str, str] = {}
    while parent_ids:
        current = parent_ids - seen_parent_ids
        if not current:
            break
        seen_parent_ids |= current
        parents = list(
            _group_source_admin_areas()
            .filter(id__in=current)
            .only("id", "parent_id")
        )
        parent_ids = set()
        for parent in parents:
            parent_id = str(parent.id)
            parent_map[parent_id] = str(parent.parent_id or "")
            if parent.parent_id:
                parent_ids.add(str(parent.parent_id))
    for area in areas:
        value = str(area.id)
        parent_id = str(area.parent_id or "")
        seen = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            result[value].add(parent_id)
            parent_id = parent_map.get(parent_id, "")
    return result


def _derived_subdivision_capital_search_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_value).strip().casefold()


def _derived_subdivision_capital_name_matches(area: AdminArea, search_key: str, *, display_name: str = "") -> bool:
    for value in (getattr(area, "name", ""), display_name or _area_display_name(area)):
        for alias_key in _derived_subdivision_capital_search_alias_keys(value):
            if alias_key.startswith(search_key):
                return True
    return False


def _derived_subdivision_capital_search_alias_keys(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [text]
    parts.extend(re.split(r"\s*(?:/|\(|\)|\[|\]|;)\s*", text))
    parts.extend(re.split(r"\s+-\s+", text))
    keys = []
    seen = set()
    for part in parts:
        key = _derived_subdivision_capital_search_key(part)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _derived_subdivision_capital_option(area: AdminArea, *, display_name: str = "") -> dict:
    name = display_name or _area_display_name(area)
    return {
        "value": str(area.id),
        "label": name,
        "name": name,
        "code": str(area.code or "").strip(),
        "level": int(area.level or 0),
        "entity_type": str(area.entity_type or ""),
    }


def _derived_subdivision_capital_first_letter_filters(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    first = text[:1]
    normalized = _derived_subdivision_capital_search_key(first)[:1]
    filters = {first}
    accent_variants = {
        "a": "aáàâäãå",
        "c": "cç",
        "e": "eéèêë",
        "i": "iíìîï",
        "n": "nñ",
        "o": "oóòôöõ",
        "u": "uúùûü",
    }
    filters.update(accent_variants.get(normalized, normalized))
    return {item for item in filters if item}


def _derived_subdivision_capital_source_ids(country_code: str, content: str) -> set[str]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code or not str(content or "").strip():
        return set()
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        return set()
    include_ids = _derived_subdivision_blocks_source_ids(data.get("include"), default_country_code=country_code)
    subtract_ids = _derived_subdivision_blocks_source_ids(data.get("subtract"), default_country_code=country_code)
    return include_ids - subtract_ids


def _derived_subdivision_blocks_source_ids(
    blocks,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    if not isinstance(blocks, list):
        return set()
    result: set[str] = set()
    for block in blocks:
        if isinstance(block, dict):
            result |= _derived_subdivision_block_source_ids(
                block,
                default_country_code=default_country_code,
                seen=seen,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
                source_ids_cache=source_ids_cache,
            )
    return result


def _derived_subdivision_block_source_ids(
    block: dict,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    country_code = _normalize_group_country_key(block.get("country_code") or default_country_code)
    if not country_code:
        return set()
    try:
        level = int(block.get("level"))
    except (TypeError, ValueError):
        return set()
    refs = _derived_subdivision_block_source_refs(
        block,
        country_code=country_code,
        seen=seen,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
        source_ids_cache=source_ids_cache,
    )
    areas = _derived_subdivision_source_ref_areas(country_code, level, refs)
    return _derived_subdivision_areas_to_capital_source_ids(areas, country_code=country_code)


def _derived_subdivision_block_source_refs(
    block: dict,
    *,
    country_code: str,
    expand_groups: bool = True,
    expand_derived_subdivisions: bool = True,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, list[str]]:
    refs = {
        "ids": _derived_subdivision_list_values(block.get("ids")),
        "codes": _derived_subdivision_list_values(block.get("codes")),
        "names": _derived_subdivision_list_values(block.get("names")),
    }
    if not expand_groups and not expand_derived_subdivisions:
        return refs
    if expand_groups:
        for group_key in _derived_subdivision_list_values(block.get("groups")):
            group_entry = _derived_subdivision_group_entry_cached(
                country_code,
                group_key,
                group_entry_cache=group_entry_cache,
                entries_cache=group_entries_cache,
            )
            if not group_entry:
                continue
            for group_block in group_entry.get("blocks") or []:
                if not isinstance(group_block, dict):
                    continue
                block_country = _normalize_group_country_key(group_block.get("country_code") or country_code)
                if block_country != country_code:
                    continue
                group_refs = _derived_subdivision_group_block_refs(group_block)
                refs["ids"].extend(group_refs["ids"])
                refs["names"].extend(group_refs["names"])
    if expand_derived_subdivisions:
        for subdivision_key in _derived_subdivision_list_values(block.get("derived_subdivisions")):
            refs["ids"].extend(
                sorted(
                    _derived_subdivision_source_ids_for_key(
                        country_code,
                        subdivision_key,
                        seen=seen,
                        source_ids_cache=source_ids_cache,
                        group_entry_cache=group_entry_cache,
                        group_entries_cache=group_entries_cache,
                    )
                )
            )
    return refs


def _derived_subdivision_group_block_refs(block: dict) -> dict[str, list[str]]:
    refs = {"ids": [], "names": []}
    refs["names"].extend(_derived_subdivision_list_values(block.get("names")))
    for section in block.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for selected in section.get("selected") or []:
            if isinstance(selected, dict):
                selected_id = str(selected.get("id") or "").strip()
                selected_name = str(selected.get("name") or selected.get("label") or "").strip()
                if selected_id:
                    refs["ids"].append(selected_id)
                elif selected_name:
                    refs["names"].append(selected_name)
            else:
                selected_name = str(selected or "").strip()
                if selected_name:
                    refs["names"].append(selected_name)
    return refs


def _derived_subdivision_source_ref_areas(country_code: str, level: int, refs: dict[str, list[str]]) -> list[AdminArea]:
    ids = {str(value).strip() for value in refs.get("ids") or [] if str(value or "").strip()}
    codes = [str(value).strip() for value in refs.get("codes") or [] if str(value or "").strip()]
    names = [str(value).strip() for value in refs.get("names") or [] if str(value or "").strip()]
    areas_by_id: dict[str, AdminArea] = {}
    base = _group_source_admin_areas().filter(country_code__iexact=country_code)
    if ids:
        for area in base.filter(id__in=ids).only("id", "country_code", "code", "name", "level", "parent_id", "city_merge_status"):
            areas_by_id[str(area.id)] = area
    if codes or names:
        level_qs = base.filter(level=level)
        if len(codes) + len(names) <= 50:
            condition = Q()
            for code in codes:
                condition |= Q(code__iexact=code) | Q(id=code)
            for name in names:
                condition |= Q(name__iexact=name)
            rows = level_qs.filter(condition) if condition else AdminArea.objects.none()
        else:
            condition = Q()
            if codes:
                condition |= Q(code__in=codes) | Q(id__in=codes)
            if names:
                condition |= Q(name__in=names)
            rows = level_qs.filter(condition) if condition else AdminArea.objects.none()
        for area in rows.only("id", "country_code", "code", "name", "level", "parent_id", "city_merge_status"):
            areas_by_id[str(area.id)] = area
    return list(areas_by_id.values())


def _derived_subdivision_areas_to_capital_source_ids(areas: list[AdminArea], *, country_code: str) -> set[str]:
    target_level = _derived_subdivision_group_level(country_code)
    target_level = int(target_level or 0)
    result: set[str] = set()
    parent_ids: set[str] = set()
    for area in areas:
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            continue
        if level == target_level:
            if int(area.city_merge_status or 0) in GROUP_SOURCE_CITY_MERGE_STATUSES:
                result.add(str(area.id))
        elif level < target_level:
            parent_ids.add(str(area.id))
        else:
            ancestor = _derived_subdivision_ancestor_at_level(area, target_level)
            if ancestor and int(ancestor.city_merge_status or 0) in GROUP_SOURCE_CITY_MERGE_STATUSES:
                result.add(str(ancestor.id))
    result |= _derived_subdivision_descendant_ids_at_level(parent_ids, target_level)
    return result


def _derived_subdivision_descendant_ids_at_level(parent_ids: set[str], target_level: int) -> set[str]:
    result: set[str] = set()
    frontier = {str(parent_id) for parent_id in parent_ids if str(parent_id or "").strip()}
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        children = list(
            _group_source_admin_areas()
            .filter(parent_id__in=current)
            .only("id", "level", "parent_id", "city_merge_status")
        )
        frontier = set()
        for child in children:
            try:
                level = int(child.level or 0)
            except (TypeError, ValueError):
                continue
            if level == target_level:
                result.add(str(child.id))
            elif level < target_level:
                frontier.add(str(child.id))
    return result


def _derived_subdivision_ancestor_at_level(area: AdminArea, target_level: int) -> AdminArea | None:
    parent_id = area.parent_id
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(str(parent_id))
        parent = _group_source_admin_areas().filter(id=parent_id).only("id", "level", "parent_id", "city_merge_status").first()
        if parent is None:
            return None
        try:
            level = int(parent.level or 0)
        except (TypeError, ValueError):
            return None
        if level == target_level:
            return parent
        if level < target_level:
            return None
        parent_id = parent.parent_id
    return None


def _derived_subdivision_list_values(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalized_derived_subdivision_capital_values(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result = []
    seen = set()
    for raw_value in values:
        value = _derived_subdivision_capital_raw_value(raw_value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _derived_subdivision_capital_values_from_post(post_data) -> list[str]:
    values = post_data.getlist("capitals") if hasattr(post_data, "getlist") else []
    if not values:
        values = [post_data.get("capital")] if post_data.get("capital") not in (None, "") else []
    return _normalized_derived_subdivision_capital_values(values)


def _derived_subdivision_capital_values_from_payload(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if "capitals" in payload:
        return _normalized_derived_subdivision_capital_values(payload.get("capitals"))
    return _normalized_derived_subdivision_capital_values(payload.get("capital"))


def _derived_subdivision_capital_values(country_code: str, data: dict) -> list[str]:
    raw_values = data.get("capitals")
    if not isinstance(raw_values, list):
        raw_values = [data.get("capital")] if data.get("capital") not in (None, "") else []
    values = []
    seen = set()
    for value in _normalized_derived_subdivision_capital_values(raw_values):
        resolved = _resolve_derived_subdivision_capital_value(country_code, value) or value
        if resolved and resolved not in seen:
            seen.add(resolved)
            values.append(resolved)
    return values


def _derived_subdivision_capital_value(country_code: str, data: dict) -> str:
    values = _derived_subdivision_capital_values(country_code, data)
    return values[0] if values else ""


def _derived_subdivision_capital_raw_value(raw_value) -> str:
    if isinstance(raw_value, dict):
        for key in ("id", "code", "name", "label"):
            if raw_value.get(key) not in (None, ""):
                raw_value = raw_value.get(key)
                break
        else:
            raw_value = ""
    return str(raw_value or "").strip()


def _resolve_derived_subdivision_capital_value(country_code: str, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        queryset = _derived_subdivision_capital_queryset(country_code)
        candidate = queryset.filter(id=value).first()
        if candidate:
            return str(candidate.id)
        candidate = queryset.filter(code__iexact=value).first() or queryset.filter(name__iexact=value).first()
        if candidate:
            return str(candidate.id)
        value_key = _group_name_match_key(value)
        for area in queryset.only("id", "code", "name"):
            if value_key in {
                _group_name_match_key(area.id),
                _group_name_match_key(area.code),
                _group_name_match_key(area.name),
            }:
                return str(area.id)
    except (OperationalError, ProgrammingError, ValueError):
        return ""
    return ""


def _normalize_group_country_key(value: str | None) -> str:
    return str(value or "").strip().lower()


def _group_new_area_country_codes_by_source(country_codes: set[str] | None = None) -> dict[str, set[str]]:
    country_codes = {
        _normalize_group_country_key(code)
        for code in (country_codes or set())
        if _normalize_group_country_key(code)
    }
    if not country_codes:
        return {}

    # Keep the direct country_code mapping for derived rows whose code matches
    # the source country, then add explicit new-country config mappings.
    result: dict[str, set[str]] = {code: {code} for code in country_codes}
    try:
        for row in DerivedCountry.objects.filter(source_country_code__in=country_codes).values(
            "source_country_code",
            "slug",
        ):
            source_code = _normalize_group_country_key(row.get("source_country_code"))
            derived_code = _normalize_group_country_key(row.get("slug"))
            if source_code and derived_code:
                result.setdefault(source_code, set()).add(derived_code)

        for row in (
            DerivedCountryConfig.objects.filter(
                Q(source_country_code__in=country_codes)
                | Q(country__source_country_code__in=country_codes)
            )
            .values(
                "source_country_code",
                "derived_country_code",
                "country_id",
                "country__source_country_code",
            )
            .order_by("source_country_code", "derived_country_code", "country_id")
        ):
            source_code = _normalize_group_country_key(row.get("source_country_code")) or _normalize_group_country_key(
                row.get("country__source_country_code")
            )
            derived_code = _normalize_group_country_key(row.get("derived_country_code")) or _normalize_group_country_key(
                row.get("country_id")
            )
            if source_code and derived_code:
                result.setdefault(source_code, set()).add(derived_code)
    except (OperationalError, ProgrammingError):
        pass
    return result


def _group_country_cards(
    groups: list[SubdivisionGroup],
    *,
    seed_records: list[dict],
    subdivision_seed_paths: list[Path],
) -> list[dict]:
    groups_by_country: dict[str, list[SubdivisionGroup]] = {}
    fallback_groups: list[SubdivisionGroup] = []
    for group in groups:
        codes = _subdivision_group_record_country_codes(group)
        if not codes:
            fallback_groups.append(group)
            continue
        for code in codes:
            groups_by_country.setdefault(code, []).append(group)

    cards = []
    visible_group_slugs: set[str] = set()
    country_rows = _admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes = {
        str(country.get("code") or "").strip().lower()
        for country in country_rows
        if str(country.get("code") or "").strip()
    }
    derived_subdivision_counts_by_country = _derived_subdivision_counts_by_country(visible_country_codes)
    seed_counts_by_country: dict[str, int] = {}
    fallback_seed_count = 0
    for seed in seed_records:
        codes = set(seed.get("country_codes") or set())
        visible_codes = codes & visible_country_codes
        if not visible_codes:
            fallback_seed_count += 1
            continue
        for code in visible_codes:
            seed_counts_by_country[code] = seed_counts_by_country.get(code, 0) + 1

    subdivision_seed_counts_by_country: dict[str, int] = {}
    fallback_subdivision_seed_count = 0
    for path in subdivision_seed_paths:
        country_code = _derived_subdivision_seed_country_from_path(path)
        if country_code and country_code in visible_country_codes:
            subdivision_seed_counts_by_country[country_code] = subdivision_seed_counts_by_country.get(country_code, 0) + 1
        else:
            fallback_subdivision_seed_count += 1

    for country in country_rows:
        code = str(country.get("code") or "").strip().lower()
        if not code:
            continue
        country_groups = groups_by_country.pop(code, [])
        visible_group_slugs.update(group.slug for group in country_groups)
        group_entries = _subdivision_group_entries_for_country(country_groups, code)
        seed_count = seed_counts_by_country.pop(code, 0)
        subdivision_seed_count = subdivision_seed_counts_by_country.pop(code, 0)
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population": int(country.get("population") or 0),
                "area_km2": country.get("area_km2"),
                "population_text": _group_metric_text(country.get("population")),
                "area_text": _group_metric_text(country.get("area_km2")),
                "flag_url": _stored_visual_asset_image_url(country.get("flag_asset")),
                "detail_url": reverse(
                    "ciudades_del_mundo:group_country_detail_data",
                    kwargs={"country_code": code},
                ),
                "groups": [],
                "group_count": len(group_entries),
                "level_rows": [],
                "division_rows": [],
                "seed_count": seed_count,
                "subdivision_seed_count": subdivision_seed_count,
                "subdivision_count": derived_subdivision_counts_by_country.get(code, 0),
                "export_url": reverse(
                    "ciudades_del_mundo:group_export_toml",
                    kwargs={"slug": code},
                ),
                "new_group_url": reverse(
                    "ciudades_del_mundo:group_entry_new",
                    kwargs={"country_code": code},
                ),
                "subdivision_export_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_export_toml",
                    kwargs={"slug": code},
                ),
                "new_subdivision_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_new",
                    kwargs={"country_code": code},
                ),
            }
        )

    unmatched_groups = list(fallback_groups)
    for code in sorted(groups_by_country):
        unmatched_groups.extend(
            group for group in groups_by_country[code] if group.slug not in visible_group_slugs
        )
    unmatched_seed_count = fallback_seed_count
    unmatched_subdivision_seed_count = fallback_subdivision_seed_count + sum(subdivision_seed_counts_by_country.values())
    if unmatched_groups or unmatched_seed_count:
        fallback_key = "__other__"
        fallback_entries = []
        for group in unmatched_groups:
            for entry in _subdivision_group_record_entries(group, fallback_key):
                fallback_entries.append(
                    {
                        **entry,
                        "href": reverse(
                            "ciudades_del_mundo:group_edit",
                            kwargs={"slug": group.slug},
                        ),
                    }
                )
        cards.append(
            {
                "key": fallback_key,
                "code": "",
                "label": _("Otros paises"),
                "groups": fallback_entries,
                "group_count": len(fallback_entries),
                "level_rows": [],
                "division_rows": [],
                "population": None,
                "area_km2": None,
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "detail_url": "",
                "new_group_url": reverse("ciudades_del_mundo:group_new"),
                "seed_count": unmatched_seed_count,
                "subdivision_seed_count": unmatched_subdivision_seed_count,
                "subdivision_count": 0,
                "subdivision_export_url": "",
                "new_subdivision_url": reverse("ciudades_del_mundo:derived_subdivision_list"),
            }
        )
    return cards


def _new_country_cards(countries: list[DerivedCountry]) -> list[dict]:
    configs_by_country: dict[str, list[DerivedCountryConfig]] = {}
    countries_by_source: dict[str, list[DerivedCountry]] = {}
    fallback_countries: list[DerivedCountry] = []
    for country in countries:
        country_configs = list(country.configs.all())
        configs_by_country[country.slug] = country_configs
        source_code = _new_country_container_source_code(country, country_configs)
        if source_code:
            countries_by_source.setdefault(source_code, []).append(country)
        else:
            fallback_countries.append(country)

    country_rows = _admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes = {
        _normalize_group_country_key(country.get("code"))
        for country in country_rows
        if _normalize_group_country_key(country.get("code"))
    }
    cards = []
    for country in country_rows:
        code = _normalize_group_country_key(country.get("code"))
        if not code:
            continue
        derived_countries = countries_by_source.pop(code, [])
        container_rows = _new_country_container_rows(
            derived_countries,
            configs_by_country=configs_by_country,
            include_objects=True,
        )
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population_text": _group_metric_text(country.get("population")),
                "area_text": _group_metric_text(country.get("area_km2")),
                "flag_url": _stored_visual_asset_image_url(country.get("flag_asset")),
                "configs": container_rows,
                "config_count": len(container_rows),
                "new_url": reverse(
                    "ciudades_del_mundo:new_country_new_for_source",
                    kwargs={"source_country_code": code},
                ),
            }
        )

    unmatched_countries = list(fallback_countries)
    for source_code in sorted(countries_by_source):
        if source_code not in visible_country_codes:
            unmatched_countries.extend(countries_by_source[source_code])
    if unmatched_countries:
        container_rows = _new_country_container_rows(
            unmatched_countries,
            configs_by_country=configs_by_country,
            include_objects=True,
        )
        cards.append(
            {
                "key": "__other__",
                "code": "",
                "label": _("Otros paises"),
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "configs": container_rows,
                "config_count": len(container_rows),
                "new_url": reverse("ciudades_del_mundo:new_country_new"),
            }
        )
    return cards


def _new_country_container_source_code(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig] | None = None,
) -> str:
    source_code = _normalize_group_country_key(country.source_country_code)
    if source_code:
        return source_code
    for config in (configs if configs is not None else country.configs.all()):
        source_code = _new_country_config_source_code(config)
        if source_code:
            return source_code
    return ""


def _new_country_container_built_code(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig] | None = None,
) -> str:
    for config in (configs if configs is not None else country.configs.all()):
        built_code = _new_country_config_built_code(config)
        if built_code:
            return built_code
    return _normalize_group_country_key(country.slug)


def _new_country_container_rows(
    countries: list[DerivedCountry],
    *,
    configs_by_country: dict[str, list[DerivedCountryConfig]] | None = None,
    include_objects: bool = False,
) -> list[dict]:
    rows = []
    for country in sorted(countries, key=lambda item: (item.name.casefold(), item.slug)):
        configs = (
            list(configs_by_country.get(country.slug, []))
            if configs_by_country is not None
            else list(country.configs.order_by("name", "slug"))
        )
        config_rows = _new_country_config_rows(configs)
        source_code = _new_country_container_source_code(country, configs)
        built_code = _new_country_container_built_code(country, configs)
        root = _new_country_detail_export_root(country, config_rows)
        if root is not None:
            fallback_area, fallback_population = _new_country_detail_export_totals(country, config_rows)
            display_name = _area_display_name(root)
            population_value = root.pop_latest if root.pop_latest is not None else fallback_population
            area_value = root.area_km2 if root.area_km2 is not None else fallback_area
            built_code = _normalize_group_country_key(root.country_code) or built_code
            built_count = NuevoAdminArea.objects.filter(country_code=root.country_code).count()
            visual_assets = _new_country_visual_assets_for_root(root, source_code)
        else:
            area_value, population_value = _new_country_detail_export_totals(country, config_rows)
            display_name = country.name
            built_count = 0
            visual_assets = {}
        row = {
            "key": country.slug,
            "code": built_code,
            "label": display_name,
            "display_name": display_name,
            "built_code": built_code,
            "source_code": source_code,
            "population": _new_country_json_number(population_value) or 0,
            "area_km2": _number_or_none(area_value) or 0,
            "population_text": _group_metric_text(population_value),
            "area_text": _group_metric_text(area_value),
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
            "flag_url": _stored_visual_asset_image_url(visual_assets.get("flag")),
            "built_count": built_count,
            "config_count": len(config_rows),
            "detail_url": (
                reverse("ciudades_del_mundo:api_new_country_container_detail", kwargs={"country_slug": country.slug})
                if config_rows or root is not None
                else ""
            ),
            "detail_api_url": (
                reverse("ciudades_del_mundo:api_new_country_container_detail", kwargs={"country_slug": country.slug})
                if config_rows or root is not None
                else ""
            ),
            "edit_url": _new_country_detail_url(country),
            "export_excel_url": _new_country_detail_export_excel_url(country),
            "view_url": (
                reverse("ciudades_del_mundo:nuevo_area_detail", kwargs={"country_id": root.id})
                if root is not None
                else ""
            ),
            "search_text": " ".join(
                [
                    country.name,
                    country.slug,
                    built_code,
                    source_code,
                    *(config.name for config in configs),
                    *(config.slug for config in configs),
                ]
            ),
        }
        if include_objects:
            row["country"] = country
            row["root"] = root
        rows.append(row)
    return rows


def _new_country_config_level_options(configs: list[DerivedCountryConfig]) -> list[dict]:
    levels = _new_country_config_available_levels_for_configs(configs)
    return [
        {
            "value": row["value"],
            "label": _("NV %(level)s") % {"level": row["value"]},
            "count": row["count"],
        }
        for row in levels
    ]


def _new_country_config_tree_request_rows(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig],
    *,
    parent_tree_id: str = "",
    selected_level: int | str | None = None,
) -> list[dict]:
    parent_tree_id = str(parent_tree_id or "").strip()
    try:
        level = int(selected_level or 0)
    except (TypeError, ValueError):
        level = 0

    if level > 0:
        available_levels = _new_country_config_available_levels_for_configs(configs)
        level, _selected_entity_types, _selected_filter = _resolve_country_level_selection(
            level,
            available_levels,
        )
    if level is not None and level > 0:
        rows = _new_country_config_rows(
            configs,
            include_assigned_subdivisions=True,
            target_levels={level},
        )
        return [
            dict(row, depth=0, tree_parent="")
            for row in rows
            if int(row.get("level") or 0) == level
        ]

    if parent_tree_id:
        target_slug = _new_country_config_node_target_slug(country.slug, parent_tree_id)
        assigned_slugs = None
        source_descendant_slugs = None
        if target_slug:
            direct_child_slugs = {
                config.slug
                for config in configs
                if _new_country_config_parent_slug(config) == target_slug
            }
            assigned_slugs = {target_slug, *direct_child_slugs}
            source_descendant_slugs = {target_slug}
        rows = _new_country_config_rows(
            configs,
            include_assigned_subdivisions=True,
            assigned_subdivision_config_slugs=assigned_slugs,
            source_descendant_config_slugs=source_descendant_slugs,
        )
        parent_depth = next(
            (int(row.get("depth") or 0) for row in rows if str(row.get("tree_id") or "") == parent_tree_id),
            0,
        )
        return [
            dict(row, depth=parent_depth + 1)
            for row in rows
            if str(row.get("tree_parent") or "") == parent_tree_id
        ]

    rows = _new_country_config_rows(configs, include_assigned_subdivisions=False)
    return [row for row in rows if not str(row.get("tree_parent") or "")]


def _new_country_config_tree_row_payload(row: dict) -> dict:
    return {
        "tree_id": str(row.get("tree_id") or ""),
        "tree_parent": str(row.get("tree_parent") or ""),
        "depth": int(row.get("depth") or 0),
        "tree_depth_class": str(row.get("tree_depth_class") or ""),
        "has_children": bool(row.get("has_children")),
        "level": int(row.get("level") or 0),
        "display_code": str(row.get("display_code") or ""),
        "parent_display_code": str(row.get("parent_display_code") or ""),
        "entity_display_name": str(row.get("entity_display_name") or ""),
        "official_name": str(row.get("official_name") or ""),
        "entity_type": str(row.get("entity_type") or ""),
        "source_level": str(row.get("source_level") or ""),
        "entity_area_text": str(row.get("entity_area_text") or "-"),
        "entity_population_text": str(row.get("entity_population_text") or "-"),
        "entity_density_text": str(row.get("entity_density_text") or "-"),
        "capital": _new_country_config_row_capital_names(row),
        "subdivision_count": int(row.get("subdivision_count") or 0),
        "is_config_row": bool(row.get("is_config_row")),
        "is_assigned_subdivision": bool(row.get("is_assigned_subdivision")),
        "edit_url": str(row.get("edit_url") or ""),
        "clone_url": str(row.get("clone_url") or ""),
        "delete_url": str(row.get("delete_url") or ""),
    }


def _new_country_config_rows(
    configs: list[DerivedCountryConfig],
    *,
    include_assigned_subdivisions: bool = False,
    assigned_subdivision_config_slugs: set[str] | None = None,
    source_descendant_config_slugs: set[str] | None = None,
    target_levels: set[int] | None = None,
) -> list[dict]:
    rows = []
    normalized_target_levels = (
        {
            int(level)
            for level in target_levels
            if isinstance(level, int) or str(level or "").strip().isdigit()
        }
        if target_levels is not None
        else None
    )
    assigned_subdivision_config_slugs = (
        {_normalize_group_country_key(slug) for slug in assigned_subdivision_config_slugs}
        if assigned_subdivision_config_slugs is not None
        else None
    )
    source_descendant_config_slugs = (
        {_normalize_group_country_key(slug) for slug in source_descendant_config_slugs}
        if source_descendant_config_slugs is not None
        else None
    )
    for config in sorted(configs, key=lambda item: (item.country.name, item.name, item.slug)):
        data = _new_country_config_toml(config)
        entity = _new_country_config_first_entity(data)
        built_code = _new_country_config_built_code(config)
        source_code = _new_country_config_source_code(config)
        level = _new_country_config_level(config)
        parent_slug = _new_country_config_parent_slug(config)
        tree_id = _new_country_config_tree_id(config)
        parent_tree_id = _new_country_config_tree_parent_id(config, parent_slug)
        entity_type = str(entity.get("entity_type") or "").strip()
        selected_source_ids = _new_country_config_selected_source_ids(data, entity)
        selected_derived_subdivisions = _new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code=source_code,
        )
        assigned_subdivision_child_count = len(selected_source_ids) + len(selected_derived_subdivisions)
        has_assigned_subdivision_children = bool(assigned_subdivision_child_count)
        root = NuevoAdminArea.objects.filter(country_code=built_code, parent__isnull=True).order_by("name").first()
        entity_node = _new_country_config_materialized_entity(config, data=data)
        entity_display_name = _area_display_name(entity_node) if entity_node else config.name
        area_value, population_value, density_value = _new_country_config_entity_metric_values(
            data=data,
            entity_node=entity_node,
        )
        built_count = NuevoAdminArea.objects.filter(country_code=built_code).count()
        visual_assets = _new_country_visual_assets_for_root(root, source_code) if root else {}
        detail_api_url = (
            reverse("ciudades_del_mundo:api_new_country_detail", kwargs={"country_id": root.id})
            if root
            else ""
        )
        rows.append(
            {
                "config": config,
                "country": config.country,
                "built_code": built_code,
                "source_code": source_code,
                "root": root,
                "entity_node": entity_node,
                "built_count": built_count,
                "display_code": _new_country_config_expected_full_code(config, data=data) or derived_code_piece(config.slug),
                "entity_display_name": entity_display_name,
                "official_name": _new_country_official_name(entity_type, entity_display_name),
                "entity_type": entity_type,
                "level": level,
                "parent_slug": parent_slug,
                "entity_area_value": area_value,
                "entity_population_value": population_value,
                "entity_density_value": density_value,
                "entity_area_text": _group_metric_text(area_value),
                "entity_population_text": _group_metric_text(population_value),
                "entity_density_text": _group_metric_text(density_value),
                "parent_display_code": "",
                "source_level": "",
                "tree_id": tree_id,
                "tree_parent": parent_tree_id,
                "row_kind": "config",
                "sort_kind": 0,
                "sort_index": 0,
                "assigned_subdivision_child_count": assigned_subdivision_child_count,
                "has_assigned_subdivision_children": has_assigned_subdivision_children,
                "is_config_row": True,
                "is_assigned_subdivision": False,
                "display_name": _area_display_name(root) if root else config.country.name,
                "population_text": _group_metric_text(root.pop_latest if root else population_value),
                "area_text": _group_metric_text(root.area_km2 if root else area_value),
                "flag_url": _stored_visual_asset_image_url(visual_assets.get("flag")),
                "detail_api_url": detail_api_url,
                "edit_url": _new_country_config_edit_url(config),
                "delete_url": _new_country_config_delete_url(config),
                "clone_url": _new_country_config_clone_url(config),
                "build_url": _new_country_config_task_url(config, "build"),
                "export_excel_url": _new_country_config_task_url(config, "export-excel"),
                "view_url": (
                    reverse("ciudades_del_mundo:nuevo_area_detail", kwargs={"country_id": root.id})
                    if root
                    else ""
                ),
                "search_text": " ".join(
                    [
                        config.country.name,
                        config.country.slug,
                        config.name,
                        config.slug,
                        built_code,
                    ]
                ),
            }
        )
        if include_assigned_subdivisions and (
            assigned_subdivision_config_slugs is None
            or _normalize_group_country_key(config.slug) in assigned_subdivision_config_slugs
        ):
            rows.extend(
                _new_country_config_assigned_subdivision_rows(
                    config,
                    data=data,
                    entity=entity,
                    source_country_code=source_code,
                    parent_tree_id=tree_id,
                    parent_level=level,
                    include_derived_source_rows=(
                        source_descendant_config_slugs is None
                        or _normalize_group_country_key(config.slug) in source_descendant_config_slugs
                    ),
                    target_levels=normalized_target_levels,
                )
            )
    _new_country_config_apply_source_child_counts(rows)
    return _new_country_config_tree_rows(rows)


def _new_country_config_tree_id(config: DerivedCountryConfig) -> str:
    return f"config:{config.country_id}:{config.slug}"


def _new_country_config_tree_parent_id(config: DerivedCountryConfig, parent_slug: str) -> str:
    parent_slug = _normalize_group_country_key(parent_slug)
    return f"config:{config.country_id}:{parent_slug}" if parent_slug else ""


def _new_country_config_node_target_slug(country_slug: str, tree_id: str) -> str:
    prefix = f"config:{country_slug}:"
    value = str(tree_id or "")
    if not value.startswith(prefix):
        return ""
    return _normalize_group_country_key(value[len(prefix):].split(":", 1)[0])


def _new_country_config_assigned_subdivision_rows(
    config: DerivedCountryConfig,
    *,
    data: dict,
    entity: dict,
    source_country_code: str,
    parent_tree_id: str,
    parent_level: int,
    include_derived_source_rows: bool = True,
    target_levels: set[int] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    display_level = max(1, int(parent_level or 1) + 1)
    if target_levels is not None:
        clean_target_levels: set[int] = set()
        for raw_level in target_levels:
            try:
                clean_level = int(raw_level or 0)
            except (TypeError, ValueError):
                continue
            if clean_level > 0:
                clean_target_levels.add(clean_level)
        target_levels = clean_target_levels

    def wants_level(level: int) -> bool:
        return target_levels is None or int(level or 0) in target_levels

    source_ids = _new_country_config_selected_source_ids(data, entity)
    if source_ids and wants_level(display_level):
        areas = {
            area.id: area
            for area in _group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        }
        for index, area_id in enumerate(source_ids):
            area = areas.get(area_id)
            if area is None:
                continue
            rows.append(
                _new_country_config_source_area_row(
                    config,
                    area,
                    parent_tree_id=parent_tree_id,
                    display_level=display_level,
                    sort_index=index,
                    tree_id=f"{parent_tree_id}:source:{area.id}",
                )
            )

    derived_records: list[DerivedSubdivision] = []
    derived_ref_by_key: dict[tuple[str, str], DerivedSubdivision] = {}
    derived_level_by_key: dict[tuple[str, str], int] = {}
    derived_selections = _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    for selection in derived_selections:
        country_code = _normalize_group_country_key(selection.get("country_code") or source_country_code)
        key = str(selection.get("key") or "").strip()
        if not country_code or not key:
            continue
        record = _derived_subdivision_source_record_for_key(country_code, key)
        if record is None:
            continue
        record_data = _derived_subdivision_record_toml(record)
        record_key = _derived_subdivision_source_key(record, record_data)
        lookup_key = (
            _normalize_group_country_key(record_data.get("source_country_code") or record.source_country_code),
            record_key,
        )
        if lookup_key in derived_ref_by_key:
            continue
        derived_ref_by_key[lookup_key] = record
        try:
            derived_level = int(selection.get("level") or display_level)
        except (TypeError, ValueError):
            derived_level = display_level
        assigned_level = max(display_level, max(1, min(9, derived_level)))
        source_level = assigned_level + 1
        if not wants_level(assigned_level) and not (
            include_derived_source_rows and wants_level(source_level)
        ):
            continue
        derived_level_by_key[lookup_key] = assigned_level
        derived_records.append(record)
    if not derived_records:
        return rows

    option_by_key = {
        (_normalize_group_country_key(option.get("country_code")), str(option.get("key") or "")): option
        for option in _new_country_derived_subdivision_options_from_records(derived_records)
    }
    record_data_by_slug = {record.slug: _derived_subdivision_record_toml(record) for record in derived_records}
    code_counts = _new_country_derived_subdivision_code_counts(derived_records, data_by_slug=record_data_by_slug)
    materialized_rows = _derived_subdivision_materialized_rows_for_records(
        derived_records,
        data_by_slug=record_data_by_slug,
        code_counts=code_counts,
    )
    materialized_source_areas = _new_country_config_materialized_source_area_map(materialized_rows.values())
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}
    for index, record in enumerate(derived_records, start=len(rows)):
        record_data = record_data_by_slug.get(record.slug) or {}
        record_country_code = _normalize_group_country_key(record_data.get("source_country_code") or record.source_country_code)
        record_key = _derived_subdivision_source_key(record, record_data)
        option = option_by_key.get((record_country_code, record_key)) or {}
        assigned_level = max(
            display_level,
            derived_level_by_key.get((record_country_code, record_key), display_level),
        )
        materialized_code = _derived_subdivision_materialized_code(record, record_data, code_counts)
        record_area, record_population = _derived_subdivision_metric_values(
            record,
            record_data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        record_density = _density(record_population, record_area)
        derived_tree_id = f"{parent_tree_id}:derived:{record_country_code}:{record_key}"
        display_name = option.get("name") or _new_country_clean_created_subdivision_name(record.name)
        entity_type = option.get("type_text") or record.entity_type
        rows.append(
            {
                "config": config,
                "country": config.country,
                "source_code": record_country_code,
                "display_code": record_key,
                "entity_display_name": display_name,
                "official_name": _new_country_official_name(entity_type, display_name),
                "entity_type": entity_type,
                "level": assigned_level,
                "parent_slug": config.slug,
                "entity_area_value": record_area,
                "entity_population_value": record_population,
                "entity_density_value": record_density,
                "entity_area_text": option.get("area_text") or "-",
                "entity_population_text": option.get("population_text") or "-",
                "entity_density_text": option.get("density_text") or "-",
                "parent_display_code": "",
                "source_level": "",
                "tree_id": derived_tree_id,
                "tree_parent": parent_tree_id,
                "row_kind": "assigned-subdivision",
                "sort_kind": 1,
                "sort_index": index,
                "is_config_row": False,
                "is_assigned_subdivision": True,
                "root": None,
                "edit_url": "",
                "delete_url": "",
                "clone_url": "",
                "search_text": " ".join(
                    [
                        config.name,
                        config.slug,
                        record_country_code,
                        record_key,
                        record.name,
                        record.entity_type,
                        record.code,
                    ]
                ),
            }
        )
        if include_derived_source_rows and wants_level(assigned_level + 1):
            rows.extend(
                _new_country_config_derived_subdivision_source_rows(
                    config,
                    record,
                    parent_tree_id=derived_tree_id,
                    display_level=assigned_level + 1,
                    sort_index=index,
                    materialized_source_areas=materialized_source_areas.get(
                        str(
                            getattr(
                                materialized_rows.get(
                                    (record_country_code, materialized_code)
                                ),
                                "id",
                                "",
                            )
                        ),
                        [],
                    ),
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                )
            )
    return rows


def _new_country_config_source_area_row(
    config: DerivedCountryConfig,
    area: AdminArea,
    *,
    parent_tree_id: str,
    display_level: int,
    sort_index: int,
    tree_id: str,
    sort_kind: int = 1,
) -> dict:
    density_value = area.density if area.density is not None else _density(area.pop_latest, area.area_km2)
    display_name = _new_country_clean_created_subdivision_name(
        _new_country_config_source_area_display_name(area)
    )
    entity_type = str(area.entity_type or "").strip()
    return {
        "config": config,
        "country": config.country,
        "source_code": _normalize_group_country_key(area.country_code),
        "entity_node": area,
        "display_code": str(area.code or area.id),
        "entity_display_name": display_name,
        "official_name": _new_country_official_name(entity_type, display_name),
        "entity_type": entity_type,
        "level": display_level,
        "parent_slug": config.slug,
        "entity_area_value": area.area_km2,
        "entity_population_value": area.pop_latest,
        "entity_density_value": density_value,
        "entity_area_text": _group_metric_text(area.area_km2),
        "entity_population_text": _group_metric_text(area.pop_latest),
        "entity_density_text": _group_metric_text(density_value),
        "parent_display_code": "",
        "source_level": "",
        "tree_id": tree_id,
        "tree_parent": parent_tree_id,
        "row_kind": "assigned-subdivision",
        "sort_kind": sort_kind,
        "sort_index": sort_index,
        "is_config_row": False,
        "is_assigned_subdivision": True,
        "root": None,
        "edit_url": "",
        "delete_url": "",
        "clone_url": "",
        "search_text": " ".join(
            [
                config.name,
                config.slug,
                area.id,
                area.country_code,
                area.name,
                str(area.entity_type or ""),
            ]
        ),
    }


def _new_country_config_derived_subdivision_source_rows(
    config: DerivedCountryConfig,
    record: DerivedSubdivision,
    *,
    parent_tree_id: str,
    display_level: int,
    sort_index: int,
    materialized_source_areas: list[AdminArea],
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> list[dict]:
    areas = list(materialized_source_areas or [])
    if not areas:
        source_ids = _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
        if not source_ids:
            return []
        areas = list(
            _group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        )
    else:
        areas = _new_country_legal_source_areas_for_display(areas)
    return [
        _new_country_config_source_area_row(
            config,
            area,
            parent_tree_id=parent_tree_id,
            display_level=display_level,
            sort_index=sort_index + index,
            tree_id=f"{parent_tree_id}:source:{area.id}",
            sort_kind=2,
        )
        for index, area in enumerate(areas)
    ]


def _new_country_legal_source_areas_for_display(areas: Iterable[AdminArea]) -> list[AdminArea]:
    original_areas = list(areas)
    source_ids = _new_country_legal_source_unit_ids_for_source_areas(original_areas)
    if not source_ids:
        return sorted(
            original_areas,
            key=lambda item: (
                str(getattr(item, "country_code", "")),
                int(getattr(item, "level", 0) or 0),
                str(getattr(item, "name", "")).casefold(),
                str(getattr(item, "id", "")),
            ),
        )
    try:
        return list(
            _group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return original_areas


def _new_country_config_materialized_source_area_map(
    areas: Iterable[NuevoAdminArea],
) -> dict[str, list[AdminArea]]:
    area_ids = [str(area.id) for area in areas if getattr(area, "id", None)]
    if not area_ids:
        return {}
    try:
        through_rows = list(
            NuevoAdminArea.municipios_originales.through.objects
            .filter(nuevoadminarea_id__in=area_ids)
            .values_list("nuevoadminarea_id", "adminarea_id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    source_ids = {str(source_id) for _, source_id in through_rows if str(source_id or "").strip()}
    if not source_ids:
        return {}
    try:
        source_areas = {
            str(area.id): area
            for area in _group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        }
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    by_area_id: dict[str, list[AdminArea]] = {}
    for area_id, source_id in through_rows:
        source_area = source_areas.get(str(source_id))
        if source_area is not None:
            by_area_id.setdefault(str(area_id), []).append(source_area)
    for rows in by_area_id.values():
        rows.sort(key=lambda item: (str(item.country_code), int(item.level or 0), str(item.name).casefold(), str(item.id)))
    return by_area_id


def _new_country_config_source_area_display_name(area: AdminArea) -> str:
    return _display_name(
        getattr(area, "name", ""),
        getattr(area, "name", ""),
        country_code=getattr(area, "country_code", ""),
    )


def _new_country_config_entity_metric_values(*, data: dict, entity_node: NuevoAdminArea | None):
    entity = _new_country_config_first_entity(data)
    stored_area, stored_population, stored_density = _new_country_config_stored_metric_values(entity)
    area_value = entity_node.area_km2 if entity_node and entity_node.area_km2 is not None else stored_area
    population_value = (
        entity_node.pop_latest
        if entity_node and entity_node.pop_latest is not None
        else stored_population
    )
    density_value = entity_node.density if entity_node and entity_node.density is not None else stored_density
    if density_value is None:
        density_value = _density(population_value, area_value)
    return area_value, population_value, density_value


def _new_country_detail_export_workbook(
    country: DerivedCountry,
    rows: list[dict],
) -> tuple[Workbook, list[dict], dict]:
    materialized_root = _new_country_detail_export_root(country, rows)
    if materialized_root is not None:
        return _new_country_detail_materialized_export_workbook(country, materialized_root)

    export_rows, summary = _new_country_detail_export_rows(country, rows)
    hierarchy_rows, merged_cells = _new_country_detail_export_sheet_rows_and_merges(export_rows)
    sheets = [
        _new_country_detail_export_sheet(
            _("Jerarquia"),
            hierarchy_rows,
            table_name="Jerarquia",
            merged_cells=merged_cells,
        ),
    ]
    return (
        Workbook(
            sheets=tuple(sheets),
            properties={"title": f"{country.name} - new country export"},
        ),
        export_rows,
        summary,
    )


def _new_country_detail_materialized_export_workbook(
    country: DerivedCountry,
    root: NuevoAdminArea,
) -> tuple[Workbook, list[dict], dict]:
    refresh_nuevo_admin_most_populated(root.country_code)
    data = DjangoNuevoAdminAreaExportRepository().get_export_data(root.id)
    hierarchy_workbook, _data_rows, levels = build_nuevo_admin_workbook(data)
    hierarchy_sheet = hierarchy_workbook.sheets[0]
    export_rows = [
        {
            "level": area.level,
            "row_kind": "materialized",
            "tree_id": area.id,
            "tree_parent": area.parent_id or "",
        }
        for area in (data.root, *data.areas)
    ]
    summary = {
        "total_area_km2": data.root.area_km2,
        "total_population": int(data.root.pop_latest) if data.root.pop_latest is not None else None,
        "legal_subdivision_count": _new_country_materialized_legal_count(data),
        "levels": tuple(levels),
        "log_pending_label": _("Se guardara al completar la descarga"),
    }
    return (
        Workbook(
            sheets=(hierarchy_sheet,),
            properties={"title": f"{country.name} - new country export"},
        ),
        export_rows,
        summary,
    )


def _new_country_materialized_legal_count(data) -> int | None:
    source_unit_ids = set(data.root.source_unit_ids)
    for area in data.areas:
        source_unit_ids.update(area.source_unit_ids)
    if source_unit_ids:
        return len(source_unit_ids)
    if data.root.source_units_count:
        return data.root.source_units_count
    child_total = sum(area.source_units_count for area in data.areas if area.parent_id == data.root.id)
    return child_total or None


_NEW_COUNTRY_EXPORT_BLOCK_SIZE = 17


def _new_country_detail_export_headers() -> tuple[str, ...]:
    return _new_country_export_path_headers(0)


def _new_country_detail_export_rows(country: DerivedCountry, rows: list[dict]) -> tuple[list[dict], dict]:
    total_area, total_population = _new_country_detail_export_totals(country, rows)
    rows_by_tree_id = {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}
    raw_export_rows: list[dict] = []
    legal_context = _new_country_export_legal_context()
    for row in rows:
        area_value = _new_country_export_decimal(row.get("entity_area_value"))
        if area_value is None:
            area_value = _new_country_export_decimal(row.get("entity_area_text"))
        population_value = _new_country_export_decimal(row.get("entity_population_value"))
        if population_value is None:
            population_value = _new_country_export_decimal(row.get("entity_population_text"))
        density_value = _new_country_export_decimal(row.get("entity_density_value"))
        if density_value is None:
            density_value = _new_country_export_decimal(row.get("entity_density_text"))
        if density_value is None:
            density_value = _new_country_export_decimal(_density(population_value, area_value))
        legal_source_unit_ids = _new_country_export_legal_source_unit_ids(row, legal_context)
        capital_population = _new_country_export_capital_population(row)
        most_populated_city, most_populated_population = _new_country_export_most_populated_city(row)
        raw_export_rows.append(
            {
                "level": int(row.get("level") or 0),
                "depth": int(row.get("depth") or 0),
                "code": str(row.get("display_code") or ""),
                "name": str(row.get("entity_display_name") or ""),
                "type": str(row.get("entity_type") or ""),
                "parent": _new_country_export_parent_label(row, rows_by_tree_id),
                "area_km2": area_value,
                "area_pct_total": _new_country_export_percentage(area_value, total_area),
                "population": int(population_value) if population_value is not None else None,
                "population_pct_total": _new_country_export_percentage(population_value, total_population),
                "density": density_value,
                "legal_subdivision_count": len(legal_source_unit_ids) or None,
                "_legal_source_unit_ids": tuple(sorted(legal_source_unit_ids)),
                "capitals": _new_country_export_capitals(row),
                "capital_population": capital_population,
                "capital_pct_entity": None,
                "most_populated_city": most_populated_city,
                "most_populated_population": most_populated_population,
                "most_populated_pct_entity": None,
                "row_kind": str(row.get("row_kind") or ""),
                "tree_id": str(row.get("tree_id") or ""),
                "tree_parent": str(row.get("tree_parent") or ""),
                "sort_kind": int(row.get("sort_kind") or 0),
                "sort_index": int(row.get("sort_index") or 0),
            }
        )
        raw_export_rows[-1]["capital_pct_entity"] = _new_country_export_percentage(
            raw_export_rows[-1]["capital_population"],
            raw_export_rows[-1]["population"],
        )
        raw_export_rows[-1]["most_populated_pct_entity"] = _new_country_export_percentage(
            raw_export_rows[-1]["most_populated_population"],
            raw_export_rows[-1]["population"],
        )
    export_rows = _new_country_export_ordered_rows(
        country,
        raw_export_rows,
        total_area=total_area,
        total_population=total_population,
    )
    _new_country_finalize_export_rows(export_rows)
    levels = tuple(sorted({row["level"] for row in export_rows if row["level"] > 0}))
    summary = {
        "total_area_km2": total_area,
        "total_population": int(total_population) if total_population is not None else None,
        "legal_subdivision_count": _new_country_export_fallback_legal_count(export_rows),
        "levels": levels,
        "log_pending_label": _("Se guardara al completar la descarga"),
    }
    return export_rows, summary


def _new_country_export_ordered_rows(
    country: DerivedCountry,
    rows: list[dict],
    *,
    total_area: Decimal | None,
    total_population: Decimal | None,
) -> list[dict]:
    country_row = {
        "level": 0,
        "depth": 0,
        "code": country.slug,
        "name": country.name,
        "type": _("Pais nuevo"),
        "parent": "",
        "area_km2": total_area,
        "area_pct_total": 100.0 if total_area not in (None, 0) else None,
        "population": int(total_population) if total_population is not None else None,
        "population_pct_total": 100.0 if total_population not in (None, 0) else None,
        "density": _density(total_population, total_area),
        "legal_subdivision_count": _new_country_export_fallback_legal_count(rows),
        "_legal_source_unit_ids": (),
        "capitals": "",
        "capital_population": None,
        "capital_pct_entity": None,
        "most_populated_city": "",
        "most_populated_population": None,
        "most_populated_pct_entity": None,
        "row_kind": "country",
        "tree_id": f"country:{country.slug}",
        "tree_parent": "",
        "sort_kind": -1,
        "sort_index": 0,
    }
    children_by_parent: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for row in rows:
        parent_key = str(row.get("tree_parent") or "")
        if parent_key:
            children_by_parent.setdefault(parent_key, []).append(row)
        else:
            roots.append(row)

    ordered = [country_row]
    seen = set()
    current_layer = []
    for row in sorted(roots, key=_new_country_export_sort_key):
        item = dict(row)
        item["depth"] = 1
        ordered.append(item)
        seen.add(str(item.get("tree_id") or ""))
        current_layer.append(item)

    while current_layer:
        next_layer = []
        for parent in current_layer:
            parent_key = str(parent.get("tree_id") or "")
            for child in sorted(children_by_parent.get(parent_key, []), key=_new_country_export_sort_key):
                child_key = str(child.get("tree_id") or "")
                if child_key in seen:
                    continue
                item = dict(child)
                item["depth"] = int(parent.get("depth") or 0) + 1
                ordered.append(item)
                seen.add(child_key)
                next_layer.append(item)
        current_layer = next_layer

    for row in sorted(rows, key=_new_country_export_sort_key):
        row_key = str(row.get("tree_id") or "")
        if row_key in seen:
            continue
        item = dict(row)
        item["depth"] = max(1, int(item.get("depth") or 0))
        ordered.append(item)
        seen.add(row_key)
    return ordered


def _new_country_finalize_export_rows(rows: list[dict]) -> None:
    _new_country_apply_legal_source_rollups(rows)
    _new_country_apply_most_populated_from_legal_units(rows)
    for row in rows:
        row["capital_pct_entity"] = _new_country_export_percentage(
            row.get("capital_population"),
            row.get("population"),
        )
        row["most_populated_pct_entity"] = _new_country_export_percentage(
            row.get("most_populated_population"),
            row.get("population"),
        )
    _new_country_apply_export_rankings(rows)


def _new_country_apply_legal_source_rollups(rows: list[dict]) -> None:
    if not rows:
        return
    children_by_parent = _new_country_export_children_by_parent(rows)
    cache: dict[str, set[str]] = {}

    def resolve(row: dict) -> set[str]:
        row_key = str(row.get("tree_id") or "")
        if row_key in cache:
            return set(cache[row_key])
        source_ids = {str(value) for value in row.get("_legal_source_unit_ids") or () if str(value)}
        for child in children_by_parent.get(row_key, []):
            source_ids.update(resolve(child))
        cache[row_key] = source_ids
        row["_legal_source_unit_ids"] = tuple(sorted(source_ids))
        row["legal_subdivision_count"] = len(source_ids) or None
        return set(source_ids)

    for row in rows:
        resolve(row)


def _new_country_export_children_by_parent(rows: list[dict]) -> dict[str, list[dict]]:
    if not rows:
        return {}
    country_row = next((row for row in rows if row.get("row_kind") == "country"), rows[0])
    country_key = str(country_row.get("tree_id") or "")
    known_ids = {str(row.get("tree_id") or "") for row in rows if str(row.get("tree_id") or "")}
    children_by_parent: dict[str, list[dict]] = {}
    for row in rows:
        if row is country_row:
            continue
        row_key = str(row.get("tree_id") or "")
        if not row_key:
            continue
        parent_key = str(row.get("tree_parent") or "")
        if not parent_key or parent_key not in known_ids:
            parent_key = country_key
        children_by_parent.setdefault(parent_key, []).append(row)
    return children_by_parent


def _new_country_apply_most_populated_from_legal_units(rows: list[dict]) -> None:
    source_ids = {
        str(value)
        for row in rows
        for value in row.get("_legal_source_unit_ids") or ()
        if str(value)
    }
    if not source_ids:
        return
    ranked_source_rows: list[tuple[str, str, int]] = []
    for chunk in _new_country_chunks(sorted(source_ids), 900):
        ranked_source_rows.extend(
            (str(area_id), str(name or ""), int(population))
            for area_id, name, population in AdminArea.objects.filter(
                id__in=chunk,
                pop_latest__isnull=False,
            ).values_list("id", "name", "pop_latest")
            if population is not None
        )
    ranked_source_rows.sort(
        key=lambda item: (-item[2], _group_name_match_key(item[1]), item[0])
    )
    for row in rows:
        if row.get("most_populated_population") is not None and row.get("most_populated_city"):
            continue
        row_source_ids = {str(value) for value in row.get("_legal_source_unit_ids") or () if str(value)}
        for source_id, name, population in ranked_source_rows:
            if source_id not in row_source_ids:
                continue
            row["most_populated_city"] = name
            row["most_populated_population"] = population
            break


def _new_country_chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _new_country_apply_export_rankings(rows: list[dict]) -> None:
    ranking_specs = (
        ("population", "population_rank"),
        ("area_km2", "area_rank"),
        ("capital_population", "capital_rank"),
        ("most_populated_population", "most_populated_rank"),
    )
    rows_by_level: dict[int, list[dict]] = {}
    for row in rows:
        rows_by_level.setdefault(int(row.get("level") or 0), []).append(row)
    for value_key, rank_key in ranking_specs:
        for level_rows in rows_by_level.values():
            ranked = sorted(
                (row for row in level_rows if _new_country_export_decimal(row.get(value_key)) is not None),
                key=lambda row: (
                    -float(_new_country_export_decimal(row.get(value_key)) or 0),
                    _group_name_match_key(str(row.get("name") or "")),
                    str(row.get("code") or ""),
                ),
            )
            for index, row in enumerate(ranked, start=1):
                row[rank_key] = index


def _new_country_export_sort_key(row: dict) -> tuple[int, int, str, str]:
    return (
        int(row.get("level") or 0),
        int(row.get("sort_kind") or 0),
        _group_name_match_key(str(row.get("name") or "")),
        str(row.get("code") or ""),
    )


def _new_country_export_fallback_legal_count(rows: list[dict]) -> int | None:
    source_unit_ids: set[str] = set()
    for row in rows:
        source_unit_ids.update(str(value) for value in row.get("_legal_source_unit_ids") or () if str(value))
    if source_unit_ids:
        return len(source_unit_ids)
    total = sum(int(row.get("legal_subdivision_count") or 0) for row in rows)
    return total or None


def _new_country_export_legal_context() -> dict:
    return {
        "area_units": {},
        "derived_units": {},
        "derived_source_ids": {},
        "group_entry": {},
        "group_entries": {},
    }


def _new_country_detail_export_sheet_rows(rows: list[dict]) -> tuple[tuple[object, ...], ...]:
    sheet_rows, _merged_cells = _new_country_detail_export_sheet_rows_and_merges(rows)
    return sheet_rows


def _new_country_detail_export_sheet_rows_and_merges(
    rows: list[dict],
) -> tuple[tuple[tuple[object, ...], ...], tuple[CellMerge, ...]]:
    paths = _new_country_export_path_rows(rows)
    max_depth = max((len(path) - 1 for path in paths), default=0)
    block_count = max_depth + 1
    sheet_rows: list[list[object]] = [list(_new_country_export_path_headers(max_depth))]
    for path in paths:
        row: list[object] = []
        for item in path:
            row.extend(_new_country_export_path_block(item))
        row.extend([None] * (_NEW_COUNTRY_EXPORT_BLOCK_SIZE * (block_count - len(path))))
        sheet_rows.append(row)
    merged_cells = _new_country_merge_repeated_path_blocks(sheet_rows, paths, block_count)
    return tuple(tuple(row) for row in sheet_rows), tuple(merged_cells)


def _new_country_export_path_headers(max_depth: int) -> tuple[str, ...]:
    block_headers = (
        _("Nombre"),
        _("Poblacion"),
        "%",
        _("Ranking poblacion"),
        _("Terreno"),
        "%",
        _("Ranking terreno"),
        _("Densidad"),
        _("Capital"),
        _("Poblacion capital"),
        _("% capital"),
        _("Ranking capital"),
        _("Ciudad mas poblada"),
        _("Poblacion ciudad mas poblada"),
        _("% ciudad mas poblada"),
        _("Ranking ciudad mas poblada"),
        _("Num municipios"),
    )
    headers = list(block_headers)
    for depth in range(1, max_depth + 1):
        headers.extend(f"NV{depth} {header}" for header in block_headers)
    return tuple(headers)


def _new_country_export_path_rows(rows: list[dict]) -> tuple[tuple[dict, ...], ...]:
    if not rows:
        return ()

    country_row = next((row for row in rows if row.get("row_kind") == "country"), rows[0])
    country_key = str(country_row.get("tree_id") or "country")
    indexed_rows: list[dict] = []
    fallback_index = 0
    for row in rows:
        if row is country_row:
            continue
        item = dict(row)
        tree_id = str(item.get("tree_id") or "")
        if not tree_id:
            fallback_index += 1
            tree_id = f"row:{fallback_index}"
            item["tree_id"] = tree_id
        indexed_rows.append(item)

    known_ids = {country_key, *(str(row.get("tree_id") or "") for row in indexed_rows)}
    children_by_parent: dict[str, list[dict]] = {country_key: []}
    for row in indexed_rows:
        parent_key = str(row.get("tree_parent") or "")
        if parent_key and parent_key in known_ids:
            children_by_parent.setdefault(parent_key, []).append(row)
        else:
            children_by_parent.setdefault(country_key, []).append(row)

    paths: list[tuple[dict, ...]] = []
    seen = {country_key}

    def visit(row: dict, path: tuple[dict, ...]) -> None:
        row_key = str(row.get("tree_id") or "")
        if row_key:
            seen.add(row_key)
        children = [
            child
            for child in sorted(children_by_parent.get(row_key, []), key=_new_country_export_sort_key)
            if str(child.get("tree_id") or "") not in seen
        ]
        if not children:
            paths.append(path)
            return
        for child in children:
            visit(child, (*path, child))

    visit(country_row, (country_row,))
    for row in sorted(indexed_rows, key=_new_country_export_sort_key):
        row_key = str(row.get("tree_id") or "")
        if row_key in seen:
            continue
        visit(row, (country_row, row))
    return tuple(paths)


def _new_country_export_path_block(row: dict) -> tuple[object, ...]:
    return (
        row["name"],
        row["population"],
        row["population_pct_total"],
        row.get("population_rank"),
        row["area_km2"],
        row["area_pct_total"],
        row.get("area_rank"),
        row["density"],
        row["capitals"],
        row["capital_population"],
        row["capital_pct_entity"],
        row.get("capital_rank"),
        row["most_populated_city"],
        row["most_populated_population"],
        row["most_populated_pct_entity"],
        row.get("most_populated_rank"),
        row["legal_subdivision_count"],
    )


def _new_country_merge_repeated_path_blocks(
    rows: list[list[object]],
    paths: tuple[tuple[dict, ...], ...],
    block_count: int,
) -> list[CellMerge]:
    merged_cells: list[CellMerge] = []
    if len(paths) <= 1:
        return merged_cells
    for depth in range(block_count):
        start = 0
        while start < len(paths):
            if depth >= len(paths[start]):
                start += 1
                continue
            row_key = str(paths[start][depth].get("tree_id") or "")
            end = start + 1
            while (
                end < len(paths)
                and depth < len(paths[end])
                and str(paths[end][depth].get("tree_id") or "") == row_key
            ):
                end += 1
            if row_key and end - start > 1:
                first_row = start + 2
                last_row = end + 1
                first_column = depth * _NEW_COUNTRY_EXPORT_BLOCK_SIZE + 1
                for offset in range(_NEW_COUNTRY_EXPORT_BLOCK_SIZE):
                    column = first_column + offset
                    merged_cells.append(
                        CellMerge(
                            start_row=first_row,
                            start_column=column,
                            end_row=last_row,
                            end_column=column,
                        )
                    )
                    for row_index in range(start + 1, end):
                        rows[row_index + 1][column - 1] = None
            start = end
    return merged_cells


def _new_country_export_legal_source_unit_ids(row: dict, context: dict) -> set[str]:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, NuevoAdminArea):
        return set(entity_node.municipios_originales.values_list("id", flat=True))

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        return _new_country_legal_source_unit_ids_for_area_id(source_id, context=context)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            return _new_country_derived_subdivision_legal_source_unit_ids(country_code, record_key, context=context)

    config = row.get("config")
    if isinstance(config, DerivedCountryConfig):
        data = _new_country_config_toml(config)
        entity = _new_country_config_first_entity(data)
        source_code = _new_country_config_source_code(config)
        return _new_country_config_legal_source_unit_ids(data, entity, source_code, context=context)
    return set()


def _new_country_config_legal_source_unit_ids(
    data: dict,
    entity: dict,
    source_country_code: str,
    *,
    context: dict,
) -> set[str]:
    include_ids = _new_country_config_direct_legal_source_unit_ids(
        data,
        entity,
        source_country_code,
        "include",
        context=context,
    )
    include_ids.update(
        _new_country_config_derived_legal_source_unit_ids(
            entity,
            source_country_code,
            "include",
            context=context,
        )
    )
    subtract_ids = _new_country_config_direct_legal_source_unit_ids(
        data,
        entity,
        source_country_code,
        "subtract",
        context=context,
    )
    subtract_ids.update(
        _new_country_config_derived_legal_source_unit_ids(
            entity,
            source_country_code,
            "subtract",
            context=context,
        )
    )
    return include_ids - subtract_ids


def _new_country_config_direct_legal_source_unit_ids(
    data: dict,
    entity: dict,
    source_country_code: str,
    operation: str,
    *,
    context: dict,
) -> set[str]:
    source_country_code = _normalize_group_country_key(source_country_code)
    ids: list[str] = []
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    selection_items = selection.get("items") if isinstance(selection, dict) else None
    if isinstance(selection_items, list):
        for item in selection_items:
            if not isinstance(item, dict):
                continue
            item_operation = str(item.get("operation") or "add").strip().lower()
            expected_operation = "add" if operation == "include" else "subtract"
            if item_operation != expected_operation:
                continue
            ids.extend(_new_country_config_text_values([item.get("id") or item.get("value")]))
    elif isinstance(selection, dict):
        key = "include_ids" if operation == "include" else "subtract_ids"
        ids.extend(_new_country_config_text_values(selection.get(key)))

    blocks = entity.get(operation) if isinstance(entity.get(operation), list) else []
    for block in blocks:
        if isinstance(block, dict):
            ids.extend(_new_country_config_text_values(block.get("ids")))

    result: set[str] = set()
    for area_id in ids:
        result.update(
            _new_country_legal_source_unit_ids_for_area_id(
                area_id,
                fallback_country_code=source_country_code,
                context=context,
            )
        )
    return result


def _new_country_config_derived_legal_source_unit_ids(
    entity: dict,
    source_country_code: str,
    operation: str,
    *,
    context: dict,
) -> set[str]:
    blocks = entity.get(operation) if isinstance(entity.get(operation), list) else []
    result: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or source_country_code)
        for key in _new_country_config_text_values(block.get("derived_subdivisions")):
            result.update(
                _new_country_derived_subdivision_legal_source_unit_ids(
                    block_country,
                    key,
                    context=context,
                )
            )
    return result


def _new_country_derived_subdivision_legal_source_unit_ids(
    country_code: str,
    record_key: str,
    *,
    context: dict,
) -> set[str]:
    country_code = _normalize_group_country_key(country_code)
    record_key = str(record_key or "").strip()
    if not country_code or not record_key:
        return set()
    cache_key = (country_code, record_key.casefold())
    derived_cache: dict[tuple[str, str], set[str]] = context["derived_units"]
    if cache_key in derived_cache:
        return set(derived_cache[cache_key])
    record = _derived_subdivision_source_record_for_key(country_code, record_key)
    if record is None:
        return set()

    materialized_country, code_candidates = _derived_subdivision_materialized_code_candidates(record)
    if materialized_country and code_candidates:
        materialized_rows = NuevoAdminArea.objects.filter(
            country_code__iexact=materialized_country,
            code__in=code_candidates,
        ).prefetch_related("municipios_originales")
        materialized_source_areas: list[AdminArea] = []
        for area in materialized_rows:
            materialized_source_areas.extend(area.municipios_originales.all())
        source_ids = _new_country_legal_source_unit_ids_for_source_areas(materialized_source_areas)
        if source_ids:
            derived_cache[cache_key] = set(source_ids)
            return source_ids

    source_ids = set(
        _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=context["derived_source_ids"],
            group_entry_cache=context["group_entry"],
            group_entries_cache=context["group_entries"],
        )
    )
    derived_cache[cache_key] = set(source_ids)
    return source_ids


def _new_country_legal_source_unit_ids_for_area_id(
    area_id: str,
    *,
    fallback_country_code: str = "",
    context: dict,
) -> set[str]:
    cache_key = (str(area_id or ""), _normalize_group_country_key(fallback_country_code))
    area_cache: dict[tuple[str, str], set[str]] = context["area_units"]
    if cache_key in area_cache:
        return set(area_cache[cache_key])
    area = _group_source_admin_areas().filter(id=area_id).only("id", "country_code", "level", "parent_id").first()
    if area is None and fallback_country_code:
        area = (
            _group_source_admin_areas()
            .filter(country_code__iexact=fallback_country_code)
            .filter(Q(id=area_id) | Q(code__iexact=area_id))
            .only("id", "country_code", "level", "parent_id")
            .first()
        )
    if area is None:
        return set()
    source_ids = _new_country_legal_source_unit_ids_for_area(area)
    area_cache[cache_key] = set(source_ids)
    return source_ids


def _new_country_legal_source_unit_ids_for_area(area: AdminArea) -> set[str]:
    country_code = _normalize_group_country_key(area.country_code)
    target_level = _derived_subdivision_capital_target_level(country_code)
    if target_level is None:
        target_level = int(area.level or 0)
    try:
        area_level = int(area.level or 0)
    except (TypeError, ValueError):
        area_level = 0
    if area_level > target_level:
        ancestor = _derived_subdivision_ancestor_at_level(area, target_level)
        return {str(ancestor.id)} if ancestor is not None else set()
    result: set[str] = set()
    seen: set[str] = set()
    stack = [area]
    while stack:
        current = stack.pop()
        current_id = str(current.id)
        if current_id in seen:
            continue
        seen.add(current_id)
        current_level = int(current.level or 0)
        if current_level == target_level:
            result.add(current_id)
            continue
        if current_level > target_level:
            continue
        children = list(
            _group_source_admin_areas()
            .filter(parent_id=current.id)
            .only("id", "country_code", "level")
            .order_by("name", "id")
        )
        if children:
            stack.extend(children)
        elif current_level > 0:
            result.add(current_id)
    return result


def _new_country_detail_export_sheet(
    name: str,
    rows: tuple[tuple[object, ...], ...],
    *,
    table_name: str,
    merged_cells: tuple[CellMerge, ...] = (),
) -> Sheet:
    tables: tuple[Table, ...] = ()
    if len(rows) > 1 and not merged_cells:
        column_count = max((len(row) for row in rows), default=1)
        ref = f"A1:{_new_country_excel_column_name(column_count)}{len(rows)}"
        tables = (Table(name=table_name, ref=ref, columns=tuple(str(value or "") for value in rows[0])),)
    return Sheet(
        name=str(name or "Hoja")[:31],
        rows=rows,
        freeze_panes="A2",
        auto_filter=not bool(merged_cells),
        tables=tables,
        merged_cells=merged_cells,
        center_cells=True,
        auto_column_widths=True,
    )


def _new_country_detail_export_totals(
    country: DerivedCountry,
    rows: list[dict],
) -> tuple[Decimal | None, Decimal | None]:
    root = _new_country_detail_export_root(country, rows)
    if root is not None:
        area = _new_country_export_decimal(root.area_km2)
        population = _new_country_export_decimal(root.pop_latest)
        if area is not None or population is not None:
            return area, population

    top_rows = [
        row
        for row in rows
        if row.get("is_config_row") and not str(row.get("tree_parent") or "")
    ]
    if not top_rows:
        top_rows = [row for row in rows if row.get("is_config_row")]
    area_total = sum(
        (value for value in (_new_country_export_decimal(row.get("entity_area_value")) for row in top_rows) if value is not None),
        Decimal("0"),
    )
    population_total = sum(
        (
            value
            for value in (_new_country_export_decimal(row.get("entity_population_value")) for row in top_rows)
            if value is not None
        ),
        Decimal("0"),
    )
    return (area_total if area_total else None, population_total if population_total else None)


def _new_country_detail_export_root(country: DerivedCountry, rows: list[dict]) -> NuevoAdminArea | None:
    built_codes = []
    seen = set()
    for row in rows:
        config = row.get("config")
        if not isinstance(config, DerivedCountryConfig):
            continue
        code = _new_country_config_built_code(config)
        if code and code not in seen:
            seen.add(code)
            built_codes.append(code)
    for code in [country.slug, *built_codes]:
        root = (
            NuevoAdminArea.objects.filter(country_code=code, parent__isnull=True)
            .order_by("level", "id")
            .first()
        )
        if root is not None:
            return root
    return None


def _new_country_export_parent_label(row: dict, rows_by_tree_id: dict[str, dict]) -> str:
    parent = rows_by_tree_id.get(str(row.get("tree_parent") or ""))
    if not parent:
        return ""
    code = str(parent.get("display_code") or "").strip()
    name = str(parent.get("entity_display_name") or "").strip()
    if code and name:
        return f"{code} - {name}"
    return code or name


def _new_country_export_capitals(row: dict) -> str:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        names = _area_capital_display_names(entity_node, get_language())
        if names:
            return " | ".join(names)

    if row.get("is_config_row"):
        config = row.get("config")
        if isinstance(config, DerivedCountryConfig):
            data = _new_country_config_toml(config)
            entity = _new_country_config_first_entity(data)
            source_code = _new_country_config_source_code(config)
            return _new_country_export_capital_names_from_values(
                source_code,
                _derived_subdivision_capital_values(source_code, entity),
            )

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).first()
        if area is not None:
            names = _area_capital_display_names(area, get_language())
            if names:
                return " | ".join(names)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            record = _derived_subdivision_source_record_for_key(country_code, record_key)
            if record is not None:
                data = _derived_subdivision_record_toml(record)
                source_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
                return _new_country_export_capital_names_from_values(
                    source_code,
                    _derived_subdivision_capital_values(source_code, data),
                )
    return ""


def _new_country_export_capital_population(row: dict) -> int | None:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        return _new_country_export_capital_population_for_area(entity_node)

    if row.get("is_config_row"):
        config = row.get("config")
        if isinstance(config, DerivedCountryConfig):
            data = _new_country_config_toml(config)
            entity = _new_country_config_first_entity(data)
            source_code = _new_country_config_source_code(config)
            return _new_country_export_capital_population_from_values(
                _derived_subdivision_capital_values(source_code, entity)
            )

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).prefetch_related("capitals").first()
        if area is not None:
            return _new_country_export_capital_population_for_area(area)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            record = _derived_subdivision_source_record_for_key(country_code, record_key)
            if record is not None:
                data = _derived_subdivision_record_toml(record)
                source_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
                return _new_country_export_capital_population_from_values(
                    _derived_subdivision_capital_values(source_code, data)
                )
    return None


def _new_country_export_capital_population_for_area(area) -> int | None:
    populations = [
        capital.pop_latest
        for capital in area.capitals.all()
        if capital.pop_latest is not None
    ]
    return sum(populations) if populations else None


def _new_country_export_capital_population_from_values(values: list[str]) -> int | None:
    clean_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not clean_values:
        return None
    populations = [
        area.pop_latest
        for area in AdminArea.objects.filter(id__in=clean_values)
        if area.pop_latest is not None
    ]
    return sum(populations) if populations else None


def _new_country_export_most_populated_city(row: dict) -> tuple[str, int | None]:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        city = getattr(entity_node, "most_populate_city", None)
        if city is not None:
            return _area_display_name(city), city.pop_latest

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).select_related("most_populate_city").first()
        if area is not None and area.most_populate_city is not None:
            return _area_display_name(area.most_populate_city), area.most_populate_city.pop_latest
    return "", None


def _new_country_export_capital_names_from_values(country_code: str, values: list[str]) -> str:
    clean_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not clean_values:
        return ""
    areas = {
        area.id: area
        for area in AdminArea.objects.filter(id__in=clean_values).prefetch_related("capitals")
    }
    names = []
    seen = set()
    for value in clean_values:
        area = areas.get(value)
        name = _area_display_name(area) if area is not None else value
        marker = str(name or "").casefold()
        if marker and marker not in seen:
            seen.add(marker)
            names.append(name)
    return " | ".join(names)


def _new_country_export_decimal(value) -> Decimal | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _new_country_export_percentage(value, total) -> float | None:
    value = _new_country_export_decimal(value)
    total = _new_country_export_decimal(total)
    if value is None or total in (None, 0):
        return None
    return round(float(value / total * Decimal("100")), 2)


def _new_country_excel_column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _safe_excel_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    filename = re.sub(r"_+", "_", filename).strip("._") or "new_country_export.xlsx"
    if not filename.lower().endswith(".xlsx"):
        filename = f"{filename}.xlsx"
    return filename


def _write_new_country_detail_export_log(
    country: DerivedCountry,
    *,
    request,
    output_path: Path,
    rows: list[dict],
    summary: dict,
) -> Path:
    source_code = _normalize_group_country_key(country.source_country_code) or "_"
    log_path = Path(settings.BASE_DIR) / ".web_export_logs" / "new-countries" / source_code / f"{country.slug}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "route": request.get_full_path() if request is not None else "",
        "country_slug": country.slug,
        "country_name": country.name,
        "source_country_code": source_code,
        "xlsx_path": str(output_path),
        "rows": len(rows),
        "levels": list(summary.get("levels") or []),
        "total_area_km2": summary.get("total_area_km2"),
        "total_population": summary.get("total_population"),
        "legal_subdivision_count": summary.get("legal_subdivision_count"),
        "row_kinds": dict(Counter(row.get("row_kind") or "" for row in rows)),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return log_path


def _new_country_config_tree_rows(rows: list[dict]) -> list[dict]:
    row_by_key = {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}
    children_by_key: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for row in rows:
        key = str(row.get("tree_id") or "")
        parent_key = str(row.get("tree_parent") or "")
        if parent_key and parent_key in row_by_key and parent_key != key:
            children_by_key.setdefault(parent_key, []).append(row)
        else:
            row["tree_parent"] = ""
            row["parent_slug"] = "" if row.get("is_config_row") else row.get("parent_slug", "")
            roots.append(row)

    ordered: list[dict] = []
    seen: set[str] = set()

    def sort_key(row: dict) -> tuple[int, int, int, str, str]:
        return (
            int(row.get("sort_kind") or 0),
            int(row.get("level") or 1),
            int(row.get("sort_index") or 0),
            str(row.get("entity_display_name") or getattr(row.get("config"), "name", "") or "").casefold(),
            str(row.get("tree_id") or ""),
        )

    def append_tree(row: dict, depth: int) -> None:
        key = str(row.get("tree_id") or "")
        if key in seen:
            return
        seen.add(key)
        children = sorted(children_by_key.get(key, []), key=sort_key)
        row["depth"] = depth
        row["tree_depth_class"] = f"new-country-tree-depth-{min(depth, 5)}"
        row["has_children"] = bool(children) or bool(row.get("has_assigned_subdivision_children"))
        if "subdivision_count" not in row:
            row["subdivision_count"] = len(children) or int(row.get("assigned_subdivision_child_count") or 0)
        ordered.append(row)
        for child in children:
            append_tree(child, depth + 1)

    for row in sorted(roots, key=sort_key):
        append_tree(row, 0)
    for row in sorted(rows, key=sort_key):
        append_tree(row, 0)
    return ordered


def _new_country_config_apply_source_child_counts(rows: list[dict]) -> None:
    source_ids = [
        str(row["entity_node"].id)
        for row in rows
        if isinstance(row.get("entity_node"), AdminArea)
    ]
    if not source_ids:
        return
    counts = {
        str(item["parent_id"]): int(item["total"] or 0)
        for item in _visible_admin_areas()
        .filter(parent_id__in=source_ids)
        .values("parent_id")
        .annotate(total=Count("id"))
    }
    for row in rows:
        entity_node = row.get("entity_node")
        if isinstance(entity_node, AdminArea):
            row["source_child_count"] = counts.get(str(entity_node.id), 0)


def _new_country_config_source_code(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    return _normalize_group_country_key(
        data.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )


def _new_country_config_built_code(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    return _normalize_group_country_key(data.get("derived_country_code") or config.derived_country_code or config.slug)


def _new_country_config_toml(config: DerivedCountryConfig) -> dict:
    try:
        data = tomllib.loads(config.content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derived_subdivision_country_cards(
    records: list[DerivedSubdivision],
    *,
    seed_paths: list[Path],
) -> list[dict]:
    records_by_country: dict[str, list[DerivedSubdivision]] = {}
    fallback_records: list[DerivedSubdivision] = []
    for record in records:
        country_code = _normalize_group_country_key(record.source_country_code)
        if not country_code:
            fallback_records.append(record)
            continue
        records_by_country.setdefault(country_code, []).append(record)

    seed_counts_by_country: dict[str, int] = {}
    fallback_seed_count = 0
    for path in seed_paths:
        country_code = _derived_subdivision_seed_country_from_path(path)
        if country_code:
            seed_counts_by_country[country_code] = seed_counts_by_country.get(country_code, 0) + 1
        else:
            fallback_seed_count += 1

    country_rows = _admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes: set[str] = set()
    cards = []
    for country in country_rows:
        code = _normalize_group_country_key(country.get("code"))
        if not code or code not in records_by_country:
            continue
        visible_country_codes.add(code)
        country_records = records_by_country.pop(code, [])
        group_rows = _subdivision_group_entries_for_country(_subdivision_groups_for_country(code), code)
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population_text": _group_metric_text(country.get("population")),
                "area_text": _group_metric_text(country.get("area_km2")),
                "flag_url": _stored_visual_asset_image_url(country.get("flag_asset")),
                "detail_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_country_detail_data",
                    kwargs={"country_code": code},
                ),
                "subdivisions": [],
                "subdivision_count": len(country_records),
                "groups": [],
                "group_count": len(group_rows),
                "seed_count": seed_counts_by_country.pop(code, 0),
                "export_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_export_toml",
                    kwargs={"slug": code},
                ),
                "build_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_build",
                    kwargs={"country_code": code},
                ),
                "new_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_new",
                    kwargs={"country_code": code},
                ),
            }
        )

    unmatched_records = list(fallback_records)
    for country_code in sorted(records_by_country):
        if country_code not in visible_country_codes:
            unmatched_records.extend(records_by_country[country_code])
    unmatched_seed_count = fallback_seed_count
    if unmatched_records:
        cards.append(
            {
                "key": "__other__",
                "code": "",
                "label": _("Otros paises"),
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "detail_url": "",
                "subdivisions": [],
                "subdivision_count": len(unmatched_records),
                "groups": [],
                "group_count": 0,
                "seed_count": unmatched_seed_count,
                "export_url": "",
                "build_url": "",
                "new_url": reverse("ciudades_del_mundo:derived_subdivision_list"),
            }
        )
    return cards


def _derived_subdivision_rows_for_country(records: list[DerivedSubdivision], country_code: str) -> list[dict]:
    rows = []
    data_by_slug = {record.slug: _derived_subdivision_record_toml(record) for record in records}
    code_counts = Counter(
        _derived_subdivision_requested_code_count_key(record, data_by_slug.get(record.slug) or {})
        for record in records
    )
    materialized_rows = _derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        area_value, population_value = _derived_subdivision_metric_values(
            record,
            data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        summary = _derived_subdivision_content_summary(record.content)
        entry_slug = _derived_subdivision_entry_slug(record)
        href = (
            reverse(
                "ciudades_del_mundo:derived_subdivision_edit",
                kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
            )
            if country_code
            else "#"
        )
        rows.append(
            {
                "record": record,
                "internal_name": record.internal_name or entry_slug.upper(),
                "display_code": str(record.code or _derived_subdivision_source_key(record, data) or entry_slug.upper()),
                "entry_slug": entry_slug,
                "href": href,
                "delete_url": (
                    reverse(
                        "ciudades_del_mundo:derived_subdivision_delete",
                        kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
                    )
                    if country_code
                    else ""
                ),
                "clone_url": (
                    reverse(
                        "ciudades_del_mundo:derived_subdivision_clone",
                        kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
                    )
                    if country_code
                    else ""
                ),
                "include_count": summary["include_count"],
                "subtract_count": summary["subtract_count"],
                "child_count": summary["child_count"],
                "group_count": summary["group_count"],
                "area_text": area_text,
                "population_text": population_text,
                "search_text": " ".join(
                    [
                        record.slug,
                        record.internal_name,
                        record.name,
                        record.entity_type,
                        record.code,
                        str(summary["include_count"]),
                        str(summary["subtract_count"]),
                        area_text,
                        population_text,
                    ]
                ),
            }
        )
    return sorted(rows, key=lambda row: (str(row["internal_name"]), str(row["record"].name)))


def _group_country_detail_payload(country_code: str) -> dict:
    country_code = _normalize_group_country_key(country_code)
    division_rows = _group_derived_subdivision_definition_rows(country_code)
    group_entries = _subdivision_group_entries_for_country(_subdivision_groups_for_country(country_code), country_code)
    return {
        "ok": True,
        "country_code": country_code,
        "groups": _group_entries_payload(group_entries),
        "level_rows": _group_level_rows_from_division_rows(division_rows),
        "division_rows": _group_division_rows_payload(division_rows),
    }


def _derived_subdivision_country_detail_payload(country_code: str) -> dict:
    country_code = _normalize_group_country_key(country_code)
    try:
        records = list(
            DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by(
                "source_country_code",
                "name",
                "slug",
            )
        )
    except (OperationalError, ProgrammingError):
        records = []
    subdivision_rows = _derived_subdivision_rows_for_country(records, country_code)
    group_entries = _subdivision_group_entries_for_country(_subdivision_groups_for_country(country_code), country_code)
    return {
        "ok": True,
        "country_code": country_code,
        "subdivisions": _derived_subdivision_list_rows_payload(subdivision_rows),
        "groups": _group_entries_payload(group_entries),
    }


def _group_derived_subdivision_definition_rows(country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    rows = list(_pending_derived_subdivision_rows_by_country({country_code}).get(country_code, []))
    rows.sort(key=lambda row: (int(row.get("level") or 0), str(row.get("name") or ""), str(row.get("id") or "")))
    return rows


def _group_entries_payload(entries: list[dict]) -> list[dict]:
    return [
        {
            "internal_name": str(entry.get("internal_name") or ""),
            "municipality_count": int(entry.get("municipality_count") or 0),
            "municipality_count_text": str(entry.get("municipality_count_text") or "0"),
            "href": str(entry.get("href") or ""),
            "search_text": " ".join(
                str(value or "")
                for value in (
                    entry.get("slug"),
                    entry.get("internal_name"),
                    entry.get("name"),
                    entry.get("municipality_count"),
                )
            ),
        }
        for entry in entries
    ]


def _group_level_rows_from_division_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        entry = grouped.setdefault(level, {"level": level, "types": set(), "count": 0})
        type_text = str(row.get("type_text") or "").strip()
        if type_text and type_text != "-":
            entry["types"].add(type_text)
        entry["count"] += 1
    level_rows = []
    for level, entry in sorted(grouped.items()):
        type_names = sorted(entry["types"])
        type_text = ", ".join(type_names[:3])
        if len(type_names) > 3:
            type_text = f"{type_text}, +{len(type_names) - 3}"
        level_rows.append(
            {
                "level": level,
                "label": f"L{level}",
                "type_text": type_text or "-",
                "count": int(entry["count"] or 0),
                "count_text": _group_metric_text(entry["count"]),
            }
        )
    return level_rows


def _group_division_rows_payload(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "level": int(row.get("level") or 0),
            "type_text": str(row.get("type_text") or "-"),
            "type_level_text": str(row.get("type_level_text") or row.get("type_text") or "-"),
            "area_text": str(row.get("area_text") or "-"),
            "population_text": str(row.get("population_text") or "-"),
            "search_text": str(row.get("search_text") or ""),
            "href": str(row.get("href") or ""),
            "clone_url": str(row.get("clone_url") or ""),
            "delete_url": str(row.get("delete_url") or ""),
        }
        for row in rows
    ]


def _derived_subdivision_list_rows_payload(rows: list[dict]) -> list[dict]:
    payload = []
    for row in rows:
        record = row.get("record")
        payload.append(
            {
                "display_code": str(row.get("display_code") or ""),
                "name": str(getattr(record, "name", "") or ""),
                "entity_type": str(getattr(record, "entity_type", "") or "-"),
                "area_text": str(row.get("area_text") or "-"),
                "population_text": str(row.get("population_text") or "-"),
                "include_count": int(row.get("include_count") or 0),
                "subtract_count": int(row.get("subtract_count") or 0),
                "group_count": int(row.get("group_count") or 0),
                "href": str(row.get("href") or ""),
                "clone_url": str(row.get("clone_url") or ""),
                "delete_url": str(row.get("delete_url") or ""),
                "search_text": str(row.get("search_text") or ""),
            }
        )
    return payload


def _derived_subdivision_materialized_rows_for_records(
    records: list[DerivedSubdivision],
    *,
    data_by_slug: dict[str, dict],
    code_counts: Counter,
) -> dict[tuple[str, str], NuevoAdminArea]:
    codes_by_country: dict[str, set[str]] = {}
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        source_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
        code = _derived_subdivision_materialized_code(record, data, code_counts)
        if source_code and code:
            codes_by_country.setdefault(source_code, set()).add(code)

    materialized: dict[tuple[str, str], NuevoAdminArea] = {}
    for source_code, codes in codes_by_country.items():
        try:
            rows = (
                NuevoAdminArea.objects
                .filter(country_code__iexact=source_code, code__in=codes)
                .only("country_code", "code", "area_km2", "pop_latest", "updated_at")
            )
        except (OperationalError, ProgrammingError, ValueError):
            continue
        for row in rows:
            materialized[(source_code, str(row.code or ""))] = row
    return materialized


def _derived_subdivision_metric_values(
    record: DerivedSubdivision,
    data: dict,
    *,
    code_counts: Counter,
    materialized_rows: dict[tuple[str, str], NuevoAdminArea],
) -> tuple[object, object]:
    source_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    code = _derived_subdivision_materialized_code(record, data, code_counts)
    area_value = None
    population_value = None
    materialized = materialized_rows.get((source_code, code))
    if materialized is not None:
        area_value = materialized.area_km2
        population_value = materialized.pop_latest
    if materialized is not None and not _derived_subdivision_materialized_row_is_stale(record, materialized):
        if area_value is not None and population_value is not None:
            return area_value, population_value
    preview_area, preview_population = _derived_subdivision_preview_metric_values(source_code, record.content)
    if materialized is not None and _derived_subdivision_materialized_row_is_stale(record, materialized):
        return (
            preview_area if preview_area is not None else area_value,
            preview_population if preview_population is not None else population_value,
        )
    if area_value is not None and population_value is not None:
        return area_value, population_value
    return (
        area_value if area_value is not None else preview_area,
        population_value if population_value is not None else preview_population,
    )


def _derived_subdivision_materialized_row_is_stale(record: DerivedSubdivision, materialized: NuevoAdminArea) -> bool:
    record_updated = getattr(record, "updated_at", None)
    materialized_updated = getattr(materialized, "updated_at", None)
    return bool(record_updated and materialized_updated and record_updated > materialized_updated)


def _derived_subdivision_preview_metric_values(country_code: str, content: str) -> tuple[object, object]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return None, None
    try:
        source_ids = _derived_subdivision_capital_source_ids(country_code, content)
    except (OperationalError, ProgrammingError, ValueError):
        return None, None
    if not source_ids:
        return None, None
    try:
        aggregate = _group_source_admin_areas().filter(id__in=source_ids).aggregate(
            area=Sum("area_km2"),
            population=Sum("pop_latest"),
        )
    except (OperationalError, ProgrammingError, ValueError):
        return None, None
    return aggregate.get("area"), aggregate.get("population")


def _derived_subdivision_content_summary(content: str) -> dict[str, int]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    def list_count(key: str) -> int:
        value = data.get(key)
        return len(value) if isinstance(value, list) else 0

    group_names: set[str] = set()
    for key in ("include", "subtract"):
        blocks = data.get(key) if isinstance(data.get(key), list) else []
        for block in blocks:
            if isinstance(block, dict):
                for group in block.get("groups") or []:
                    if str(group or "").strip():
                        group_names.add(str(group).strip())
    for block in (data.get("capital_groups") if isinstance(data.get("capital_groups"), list) else []):
        if isinstance(block, dict) and str(block.get("group") or "").strip():
            group_names.add(str(block.get("group")).strip())
    children = data.get("children") if isinstance(data.get("children"), list) else []
    for child in children:
        if not isinstance(child, dict):
            continue
        for key in ("include", "subtract"):
            blocks = child.get(key) if isinstance(child.get(key), list) else []
            for block in blocks:
                if isinstance(block, dict):
                    for group in block.get("groups") or []:
                        if str(group or "").strip():
                            group_names.add(str(group).strip())
        for block in (child.get("capital_groups") if isinstance(child.get("capital_groups"), list) else []):
            if isinstance(block, dict) and str(block.get("group") or "").strip():
                group_names.add(str(block.get("group")).strip())
    return {
        "include_count": list_count("include"),
        "subtract_count": list_count("subtract"),
        "child_count": len(children),
        "group_count": len(group_names),
    }


def _group_level_rows_by_country(country_codes: set[str] | None = None) -> dict[str, list[dict]]:
    new_area_codes_by_country = _group_new_area_country_codes_by_source(country_codes)
    new_area_country_codes = {
        derived_code
        for derived_codes in new_area_codes_by_country.values()
        for derived_code in derived_codes
        if derived_code
    }
    if not new_area_country_codes:
        return {}
    countries_by_new_area_code: dict[str, set[str]] = {}
    for source_code, derived_codes in new_area_codes_by_country.items():
        for derived_code in derived_codes:
            countries_by_new_area_code.setdefault(derived_code, set()).add(source_code)
    try:
        rows = list(
            NuevoAdminArea.objects.filter(country_code__in=new_area_country_codes, level__gt=0)
            .values("country_code", "level", "entity_type")
            .annotate(total=Count("id"), population=Sum("pop_latest"))
            .order_by("country_code", "level", "entity_type")
        )
    except (OperationalError, ProgrammingError):
        return {}

    grouped: dict[tuple[str, int], dict] = {}
    for row in rows:
        new_area_code = _normalize_group_country_key(row.get("country_code"))
        source_codes = countries_by_new_area_code.get(new_area_code, set())
        if not source_codes:
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        for code in source_codes:
            key = (code, level)
            entry = grouped.setdefault(
                key,
                {
                    "country_code": code,
                    "level": level,
                    "types": set(),
                    "count": 0,
                    "population": 0,
                },
            )
            entity_type = str(row.get("entity_type") or "").strip()
            if entity_type:
                entry["types"].add(entity_type)
            entry["count"] += int(row.get("total") or 0)
            entry["population"] += int(row.get("population") or 0)

    by_country: dict[str, list[dict]] = {}
    for (code, level), row in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        type_names = sorted(row["types"])
        type_text = ", ".join(type_names[:3])
        if len(type_names) > 3:
            type_text = f"{type_text}, +{len(type_names) - 3}"
        by_country.setdefault(code, []).append(
            {
                "country_code": code,
                "level": level,
                "label": f"L{level}",
                "type_text": type_text or "-",
                "count": row["count"],
                "count_text": _group_metric_text(row["count"]),
                "population_text": _group_metric_text(row["population"]),
                "new_group_url": (
                    reverse("ciudades_del_mundo:group_entry_new", kwargs={"country_code": code})
                    + f"?include_level={level}"
                ),
            }
        )
    return by_country


def _derived_subdivision_record_toml(record: DerivedSubdivision) -> dict:
    try:
        data = tomllib.loads(record.content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derived_subdivision_requested_code(record: DerivedSubdivision, data: dict | None = None) -> str:
    data = data if data is not None else _derived_subdivision_record_toml(record)
    return str(data.get("code") or record.code or "").strip()


def _derived_subdivision_requested_code_count_key(
    record: DerivedSubdivision,
    data: dict | None = None,
) -> tuple[str, str]:
    data = data if data is not None else _derived_subdivision_record_toml(record)
    country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    return country_code, _derived_subdivision_requested_code(record, data)


def _derived_subdivision_materialized_code(
    record: DerivedSubdivision,
    data: dict,
    code_counts: Counter,
) -> str:
    requested_code = _derived_subdivision_requested_code(record, data)
    country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    count_key = (country_code, requested_code)
    requested_code_count = code_counts[count_key] if count_key in code_counts else code_counts[requested_code]
    if requested_code and requested_code_count == 1:
        code_value = requested_code
    else:
        code_value = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    try:
        return _derived_subdivision_compose_code(
            country_code=country_code,
            parent_code=parent_code,
            code_value=code_value,
        )
    except ValueError:
        return ""


def _derived_subdivision_materialized_code_candidates(record: DerivedSubdivision) -> tuple[str, set[str]]:
    data = _derived_subdivision_record_toml(record)
    country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    if not country_code:
        return "", set()
    country_records = list(DerivedSubdivision.objects.filter(source_country_code__iexact=country_code))
    data_by_slug = {item.slug: _derived_subdivision_record_toml(item) for item in country_records}
    code_counts = Counter(
        _derived_subdivision_requested_code(item, data_by_slug.get(item.slug) or {})
        for item in country_records
    )
    parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    candidates = set()
    materialized_code = _derived_subdivision_materialized_code(record, data, code_counts)
    if materialized_code:
        candidates.add(materialized_code)
    requested_code = _derived_subdivision_requested_code(record, data)
    if requested_code and code_counts[requested_code] == 1:
        try:
            candidates.add(
                _derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=parent_code,
                    code_value=requested_code,
                )
            )
        except ValueError:
            pass
    legacy_code = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    if legacy_code:
        try:
            candidates.add(
                _derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=parent_code,
                    code_value=legacy_code,
                )
            )
        except ValueError:
            pass
    return country_code, {code for code in candidates if code}


def _delete_materialized_rows_for_derived_subdivision(record: DerivedSubdivision) -> int:
    country_code, code_candidates = _derived_subdivision_materialized_code_candidates(record)
    if not country_code or not code_candidates:
        return 0
    qs = NuevoAdminArea.objects.filter(country_code=country_code)
    root_ids = set(qs.filter(code__in=code_candidates).values_list("id", flat=True))
    if not root_ids:
        return 0
    ids_to_delete = set(root_ids)
    frontier = set(root_ids)
    while frontier:
        child_ids = set(qs.filter(parent_id__in=frontier).values_list("id", flat=True))
        child_ids -= ids_to_delete
        if not child_ids:
            break
        ids_to_delete.update(child_ids)
        frontier = child_ids
    deleted, _details = NuevoAdminArea.objects.filter(id__in=ids_to_delete).delete()
    return int(deleted or 0)


def _delete_materialized_rows_for_new_country_config(config: DerivedCountryConfig) -> int:
    country_code = _new_country_config_built_code(config)
    code_candidates = _new_country_config_materialized_code_candidates(config)
    if not country_code or not code_candidates:
        return 0
    qs = NuevoAdminArea.objects.filter(country_code=country_code).exclude(parent__isnull=True)
    root_ids = set(qs.filter(code__in=code_candidates).values_list("id", flat=True))
    materialized = _new_country_config_materialized_entity(config)
    if materialized is not None:
        root_ids.add(materialized.id)
    if not root_ids:
        return 0
    all_rows = NuevoAdminArea.objects.filter(country_code=country_code)
    ids_to_delete = set(root_ids)
    frontier = set(root_ids)
    while frontier:
        child_ids = set(all_rows.filter(parent_id__in=frontier).values_list("id", flat=True))
        child_ids -= ids_to_delete
        if not child_ids:
            break
        ids_to_delete.update(child_ids)
        frontier = child_ids
    deleted, _details = NuevoAdminArea.objects.filter(id__in=ids_to_delete).delete()
    return int(deleted or 0)


def _derived_subdivision_edit_link_indexes(
    country_codes: set[str] | None,
) -> tuple[
    dict[tuple[str, str], list[tuple[str, str]]],
    dict[tuple[str, str], list[tuple[str, str]]],
    dict[str, dict],
]:
    normalized_codes = {
        _normalize_group_country_key(code)
        for code in (country_codes or set())
        if _normalize_group_country_key(code)
    }
    if not normalized_codes:
        return {}, {}, {}
    records = list(
        DerivedSubdivision.objects.filter(source_country_code__in=normalized_codes).order_by(
            "source_country_code",
            "slug",
        )
    )
    records_by_country: dict[str, list[tuple[DerivedSubdivision, dict]]] = {}
    for record in records:
        source_code = _normalize_group_country_key(record.source_country_code)
        if not source_code:
            continue
        records_by_country.setdefault(source_code, []).append((record, _derived_subdivision_record_toml(record)))

    code_links: dict[tuple[str, str], list[tuple[str, str]]] = {}
    name_links: dict[tuple[str, str], list[tuple[str, str]]] = {}
    metadata_by_href: dict[str, dict] = {}
    for source_code, country_records in records_by_country.items():
        code_counts = Counter(
            _derived_subdivision_requested_code(record, data)
            for record, data in country_records
        )
        name_counts = Counter(
            name_key
            for record, data in country_records
            for name_key in _derived_subdivision_edit_name_keys(record, data)
        )
        for record, data in country_records:
            materialized_code = _derived_subdivision_materialized_code(record, data, code_counts)
            if not materialized_code:
                continue
            link_codes = [materialized_code]
            requested_code = _derived_subdivision_requested_code(record, data)
            if requested_code and code_counts[requested_code] == 1 and requested_code not in link_codes:
                link_codes.append(requested_code)
            legacy_code = str(data.get("internal_name") or record.internal_name or record.slug).strip()
            if legacy_code and legacy_code not in link_codes:
                link_codes.append(legacy_code)
            if legacy_code:
                parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=source_code)
                try:
                    legacy_materialized_code = _derived_subdivision_compose_code(
                        country_code=source_code,
                        parent_code=parent_code,
                        code_value=legacy_code,
                    )
                except ValueError:
                    legacy_materialized_code = ""
                if legacy_materialized_code and legacy_materialized_code not in link_codes:
                    link_codes.append(legacy_materialized_code)
            href = reverse(
                "ciudades_del_mundo:derived_subdivision_edit",
                kwargs={
                    "country_code": source_code,
                    "subdivision_slug": _derived_subdivision_entry_slug(record),
                },
            )
            metadata_by_href[href] = _derived_subdivision_edit_metadata(record, data, href=href)
            for code in link_codes:
                code_links.setdefault((source_code, source_code), []).append((code, href))
            for name_key in _derived_subdivision_edit_name_keys(record, data):
                if name_counts[name_key] == 1:
                    name_links.setdefault((source_code, source_code), []).append((name_key, href))
    return code_links, name_links, metadata_by_href


def _derived_subdivision_edit_link_index(country_codes: set[str] | None) -> dict[tuple[str, str], list[tuple[str, str]]]:
    code_links, _name_links, _metadata = _derived_subdivision_edit_link_indexes(country_codes)
    return code_links


def _derived_subdivision_edit_metadata(record: DerivedSubdivision, data: dict, *, href: str) -> dict:
    try:
        level = _derived_subdivision_level(data.get("level") or 1)
    except ValueError:
        level = 1
    entity_type = str(data.get("entity_type") or record.entity_type or data.get("generic_name") or "").strip()
    return {
        "href": href,
        "delete_url": reverse(
            "ciudades_del_mundo:derived_subdivision_delete",
            kwargs={
                "country_code": _normalize_group_country_key(record.source_country_code),
                "subdivision_slug": _derived_subdivision_entry_slug(record),
            },
        ),
        "clone_url": reverse(
            "ciudades_del_mundo:derived_subdivision_clone",
            kwargs={
                "country_code": _normalize_group_country_key(record.source_country_code),
                "subdivision_slug": _derived_subdivision_entry_slug(record),
            },
        ),
        "name": str(data.get("name") or record.name or data.get("internal_name") or record.internal_name or "").strip(),
        "level": level,
        "type_text": entity_type or "-",
        "source_country_code": _normalize_group_country_key(record.source_country_code),
        "content": record.content,
        "updated_at": record.updated_at,
    }


def _derived_subdivision_edit_name_keys(record: DerivedSubdivision, data: dict) -> set[str]:
    keys = set()
    for value in (data.get("name"), record.name, data.get("internal_name"), record.internal_name):
        key = _group_name_match_key(str(value or ""))
        if key:
            keys.add(key)
    return keys


def _division_row_code_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _derived_subdivision_edit_href_for_area(
    link_index: dict[tuple[str, str], list[tuple[str, str]]],
    name_index: dict[tuple[str, str], list[tuple[str, str]]],
    source_codes: set[str],
    *,
    new_area_code: str,
    area_code: str | None,
    area_name: str | None = None,
) -> str:
    return _derived_subdivision_edit_match_for_area(
        link_index,
        name_index,
        source_codes,
        new_area_code=new_area_code,
        area_code=area_code,
        area_name=area_name,
    ).get("href", "")


def _derived_subdivision_edit_match_for_area(
    link_index: dict[tuple[str, str], list[tuple[str, str]]],
    name_index: dict[tuple[str, str], list[tuple[str, str]]],
    source_codes: set[str],
    *,
    new_area_code: str,
    area_code: str | None,
    area_name: str | None = None,
) -> dict:
    area_key = _division_row_code_key(area_code)
    area_name_key = _group_name_match_key(str(area_name or ""))
    for source_code in sorted(_normalize_group_country_key(code) for code in source_codes):
        entries = link_index.get((source_code, new_area_code), [])
        if area_key:
            for code, href in entries:
                if _division_row_code_key(code) == area_key:
                    return {"href": href, "use_definition_metadata": True}
            for code, href in sorted(entries, key=lambda item: len(_division_row_code_key(item[0])), reverse=True):
                code_key = _division_row_code_key(code)
                if code_key and area_key.startswith(f"{code_key}-"):
                    return {"href": href, "use_definition_metadata": False}
        if area_name_key:
            for name_key, href in name_index.get((source_code, new_area_code), []):
                if name_key == area_name_key:
                    return {"href": href, "use_definition_metadata": True}
    return {"href": "", "use_definition_metadata": False}


def _group_division_rows_by_country(country_codes: set[str] | None = None) -> dict[str, list[dict]]:
    new_area_codes_by_country = _group_new_area_country_codes_by_source(country_codes)
    new_area_country_codes = {
        derived_code
        for derived_codes in new_area_codes_by_country.values()
        for derived_code in derived_codes
        if derived_code
    }
    if not new_area_country_codes:
        return {}
    edit_link_index, edit_name_index, edit_metadata = _derived_subdivision_edit_link_indexes(country_codes)
    countries_by_new_area_code: dict[str, set[str]] = {}
    for source_code, derived_codes in new_area_codes_by_country.items():
        for derived_code in derived_codes:
            countries_by_new_area_code.setdefault(derived_code, set()).add(source_code)
    try:
        areas = list(
            NuevoAdminArea.objects.filter(country_code__in=new_area_country_codes, level__gt=0)
            .only("id", "country_code", "code", "name", "level", "entity_type", "area_km2", "pop_latest", "updated_at")
            .order_by("country_code", "level", "name", "id")
        )
    except (OperationalError, ProgrammingError):
        return {}

    by_country: dict[str, list[dict]] = {}
    for area in areas:
        new_area_code = _normalize_group_country_key(area.country_code)
        source_codes = countries_by_new_area_code.get(new_area_code, set())
        if not source_codes:
            continue
        edit_match = _derived_subdivision_edit_match_for_area(
            edit_link_index,
            edit_name_index,
            source_codes,
            new_area_code=new_area_code,
            area_code=area.code,
            area_name=area.name,
        )
        href = edit_match.get("href", "")
        sql_metadata = edit_metadata.get(href, {}) if edit_match.get("use_definition_metadata") and href else {}
        delete_metadata = edit_metadata.get(href, {}) if href else {}
        try:
            level = int(sql_metadata.get("level") if sql_metadata else area.level or 0)
        except (TypeError, ValueError):
            level = 0
        raw_type = str(sql_metadata.get("type_text") if sql_metadata else area.entity_type or "").strip()
        display_name = str(sql_metadata.get("name") if sql_metadata else area.name or "").strip() or "-"
        type_text = raw_type or "-"
        type_level_text = type_text
        area_value = area.area_km2
        population_value = area.pop_latest
        if (
            sql_metadata
            and sql_metadata.get("updated_at")
            and area.updated_at
            and sql_metadata["updated_at"] > area.updated_at
        ):
            preview_area, preview_population = _derived_subdivision_preview_metric_values(
                str(sql_metadata.get("source_country_code") or ""),
                str(sql_metadata.get("content") or ""),
            )
            area_value = preview_area if preview_area is not None else area_value
            population_value = preview_population if preview_population is not None else population_value
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        search_text = " ".join(
            str(value or "")
            for value in (
                area.name,
                display_name,
                raw_type,
                type_text,
                level,
                area_text,
                population_text,
            )
        )
        row = {
            "id": area.id,
            "name": display_name,
            "level": level,
            "type_text": type_text,
            "type_level_text": type_level_text,
            "area_text": area_text,
            "population_text": population_text,
            "search_text": search_text,
            "href": href,
            "delete_url": str(delete_metadata.get("delete_url") or ""),
            "clone_url": str(delete_metadata.get("clone_url") or ""),
        }
        for code in sorted(source_codes):
            by_country.setdefault(code, []).append(row)
    materialized_hrefs = {
        str(row.get("href") or "")
        for rows in by_country.values()
        for row in rows
        if str(row.get("href") or "")
    }
    for code, rows in _pending_derived_subdivision_rows_by_country(
        country_codes,
        exclude_hrefs=materialized_hrefs,
    ).items():
        by_country.setdefault(code, []).extend(rows)
    for rows in by_country.values():
        rows.sort(key=lambda row: (int(row.get("level") or 0), str(row.get("name") or ""), str(row.get("id") or "")))
    return by_country


def _pending_derived_subdivision_rows_by_country(
    country_codes: set[str] | None = None,
    *,
    exclude_hrefs: set[str] | None = None,
) -> dict[str, list[dict]]:
    normalized_codes = {
        _normalize_group_country_key(code)
        for code in (country_codes or set())
        if _normalize_group_country_key(code)
    }
    if not normalized_codes:
        return {}
    exclude_hrefs = {str(href) for href in (exclude_hrefs or set()) if str(href)}
    by_country: dict[str, list[dict]] = {}
    try:
        records = list(
            DerivedSubdivision.objects.filter(source_country_code__in=normalized_codes).order_by(
                "source_country_code",
                "slug",
            )
        )
    except (OperationalError, ProgrammingError):
        return {}
    for record in records:
        source_code = _normalize_group_country_key(record.source_country_code)
        if not source_code:
            continue
        data = _derived_subdivision_record_toml(record)
        entry_slug = _derived_subdivision_entry_slug(record)
        href = reverse(
            "ciudades_del_mundo:derived_subdivision_edit",
            kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
        )
        if href in exclude_hrefs:
            continue
        metadata = _derived_subdivision_edit_metadata(record, data, href=href)
        try:
            level = int(metadata.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        type_text = str(metadata.get("type_text") or "-").strip() or "-"
        name = str(metadata.get("name") or record.name or record.internal_name or record.slug).strip() or "-"
        type_level_text = type_text
        area_value, population_value = _derived_subdivision_preview_metric_values(source_code, record.content)
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        search_text = " ".join(
            str(value or "")
            for value in (
                record.slug,
                record.internal_name,
                record.name,
                record.entity_type,
                record.code,
                name,
                type_text,
                level,
                area_text,
                population_text,
            )
        )
        by_country.setdefault(source_code, []).append(
            {
                "id": record.slug,
                "name": name,
                "level": level,
                "type_text": type_text,
                "type_level_text": type_level_text,
                "area_text": area_text,
                "population_text": population_text,
                "search_text": search_text,
                "href": href,
                "clone_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_clone",
                    kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
                ),
                "delete_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_delete",
                    kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
                ),
            }
        )
    return by_country


def _subdivision_group_seed_records(paths: list[Path] | None = None) -> list[dict]:
    return [
        {
            "path": path,
            "slug": path.stem,
            "country_codes": _subdivision_group_seed_country_codes(path),
        }
        for path in (paths if paths is not None else bundled_subdivision_group_paths())
    ]


def _subdivision_group_seed_paths_for_country(paths: list[Path], country_code: str) -> list[Path]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return paths
    return [
        seed["path"]
        for seed in _subdivision_group_seed_records(paths)
        if country_code in seed.get("country_codes", set())
    ]


def _derived_subdivision_seed_paths_for_country(paths: list[Path], country_code: str) -> list[Path]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return paths
    return [path for path in paths if _derived_subdivision_seed_country_from_path(path) == country_code]


def _derived_subdivision_seed_country_from_path(path: Path) -> str:
    path = Path(path)
    if path.parent.name == "subdivisions":
        return _normalize_group_country_key(path.stem)
    try:
        if path.parent.parent.name == "subdivisions":
            return _normalize_group_country_key(path.parent.name)
    except IndexError:
        return ""
    return ""


def _subdivision_group_seed_country_codes(path: Path) -> set[str]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return set()
    return _subdivision_group_content_country_codes(content)


def _subdivision_group_record_country_codes(group: SubdivisionGroup) -> set[str]:
    code = _normalize_group_country_key(group.source_country_code)
    if code:
        return {code}
    return _subdivision_group_content_country_codes(group.content)


def _subdivision_groups_for_country(country_code: str) -> list[SubdivisionGroup]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    groups = []
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if country_code in _subdivision_group_record_country_codes(group):
            groups.append(group)
    return groups


def _derived_subdivision_counts_by_country(country_codes: set[str] | None = None) -> dict[str, int]:
    country_codes = {
        _normalize_group_country_key(code)
        for code in (country_codes or set())
        if _normalize_group_country_key(code)
    }
    try:
        queryset = DerivedSubdivision.objects.values("source_country_code").annotate(total=Count("slug"))
        if country_codes:
            queryset = queryset.filter(source_country_code__in=country_codes)
        return {
            _normalize_group_country_key(row["source_country_code"]): int(row["total"] or 0)
            for row in queryset
            if _normalize_group_country_key(row["source_country_code"])
        }
    except (OperationalError, ProgrammingError):
        return {}


def _subdivision_group_content_country_codes(content: str) -> set[str]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()

    explicit = _normalize_group_country_key(data.get("source_country_code"))
    selection = data.get("selection")
    if not explicit and isinstance(selection, dict):
        explicit = _normalize_group_country_key(selection.get("source_country_code"))
    if explicit:
        return {explicit}

    legacy = data.get("legacy")
    legacy_source_ref = _subdivision_group_legacy_source_reference_country_code(data.get("source_python"))
    if legacy_source_ref:
        return {legacy_source_ref}

    legacy_source = str(legacy.get("python_source") or "") if isinstance(legacy, dict) else ""
    return _subdivision_group_legacy_spec_country_codes(legacy_source)


def _subdivision_group_legacy_source_reference_country_code(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidates = []
    for pattern in (
        r"historical_divisions[\\/\.]([A-Za-z][A-Za-z0-9_]*)",
        r"new_subdivisions[\\/\.]([A-Za-z][A-Za-z0-9_]*)",
    ):
        candidates.extend(match.group(1) for match in re.finditer(pattern, text))
    if not candidates:
        candidates.append(Path(text).stem or text.rsplit(".", 1)[-1])
    aliases = {
        "spain": "spain",
        "spanish": "spain",
        "france": "france",
        "french": "france",
        "italy": "italy",
        "italian": "italy",
        "portugal": "portugal",
        "morocco": "morocco",
        "colombia": "colombia",
        "cuba": "cuba",
        "guatemala": "guatemala",
        "mexico": "mexico",
        "equatorialguinea": "equatorialguinea",
        "western_sahara": "westernsahara",
        "westernsahara": "westernsahara",
    }
    for candidate in candidates:
        slug = re.sub(r"[^a-z0-9_]+", "_", str(candidate or "").casefold()).strip("_")
        for prefix, country_code in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if slug == prefix or slug.startswith(f"{prefix}_"):
                return country_code
    return ""


def _subdivision_group_legacy_spec_country_codes(source: str) -> set[str]:
    if not str(source or "").strip():
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    codes: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DIVISIONS" for target in node.targets):
            continue
        for country_code in _legacy_division_spec_country_codes_from_node(node.value):
            normalized = _normalize_group_country_key(country_code)
            if normalized:
                codes.add(normalized)
    return codes


def _legacy_division_spec_country_codes_from_node(node) -> set[str]:
    codes: set[str] = set()
    if isinstance(node, ast.List):
        for item in node.elts:
            codes.update(_legacy_division_spec_country_codes_from_node(item))
        return codes
    if not isinstance(node, ast.Dict):
        return codes
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "spec":
            codes.update(_legacy_spec_value_country_codes(value))
        elif isinstance(key, ast.Constant) and key.value == "source":
            maybe_code = _literal_string_node(value)
            if maybe_code:
                codes.add(maybe_code)
    return codes


def _legacy_spec_value_country_codes(node) -> set[str]:
    codes: set[str] = set()
    if not isinstance(node, ast.Dict):
        return codes
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            codes.add(key.value)
            continue
        if isinstance(value, ast.Dict):
            for inner_key in value.keys:
                if isinstance(inner_key, ast.Constant) and isinstance(inner_key.value, str):
                    codes.add(inner_key.value)
    return codes


def _literal_string_node(node) -> str:
    return str(node.value or "").strip() if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _group_metric_text(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _group_source_level_options(country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    try:
        level_rows = list(
                _group_source_admin_areas()
                .filter(country_code__iexact=country_code)
                .exclude(level=0)
                .values("level", "entity_type")
                .annotate(count=Count("id"))
                .order_by("level")
            )
        level_counts: dict[int, int] = {}
        level_types: dict[int, dict[str, int]] = {}
        for row in level_rows:
            if row["level"] is None:
                continue
            level = int(row["level"])
            level_counts[level] = level_counts.get(level, 0) + int(row["count"] or 0)
            entity_type = str(row.get("entity_type") or "").strip()
            if entity_type:
                type_counts = level_types.setdefault(level, {})
                type_counts[entity_type] = type_counts.get(entity_type, 0) + int(row["count"] or 0)
    except (OperationalError, ProgrammingError, ValueError):
        return []
    if not level_counts:
        return []
    max_level = _group_effective_max_source_level(level_counts, level_types=level_types)
    options = []
    for row in _source_level_filter_options_for_country(country_code):
        try:
            row_level = int(row.get("value"))
        except (TypeError, ValueError):
            continue
        if row_level < max_level:
            options.append(row)
    return options


def _group_effective_max_source_level(level_counts: dict[int, int], *, level_types: dict[int, dict[str, int]] | None = None) -> int:
    levels = sorted(level for level, count in level_counts.items() if count > 0)
    if not levels:
        return 0
    level_types = level_types or {}
    while len(levels) >= 2 and _group_level_is_lowest_place_only(level_types.get(levels[-1], set())):
        levels.pop()
    effective_max = levels[-1]
    while len(levels) >= 2:
        deepest = levels[-1]
        previous = levels[-2]
        deepest_count = int(level_counts.get(deepest) or 0)
        previous_count = int(level_counts.get(previous) or 0)
        if previous_count < 100 or deepest_count / previous_count >= 0.01:
            break
        effective_max = previous
        levels.pop()
    return effective_max


def _group_level_is_lowest_place_only(entity_types) -> bool:
    if not entity_types:
        return False
    lowest_tokens = ("locality", "municipality seat", "seat", "place", "settlement")
    if isinstance(entity_types, dict):
        total = sum(int(count or 0) for count in entity_types.values())
        if total <= 0:
            return False
        lowest_total = 0
        for entity_type, count in entity_types.items():
            normalized = re.sub(r"[^a-z0-9 ]+", " ", str(entity_type or "").casefold()).strip()
            if any(token in normalized for token in lowest_tokens):
                lowest_total += int(count or 0)
        return lowest_total / total >= 0.95
    normalized_types = [
        re.sub(r"[^a-z0-9 ]+", " ", str(entity_type or "").casefold()).strip()
        for entity_type in entity_types
    ]
    return all(any(token in entity_type for token in lowest_tokens) for entity_type in normalized_types)


def _group_source_section_options(country_code: str, level: str | int, *, parent_id: str = "") -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return []
    try:
        queryset = _group_source_admin_areas().filter(country_code__iexact=country_code, level=level_int)
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        rows = list(
            queryset
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    child_counts = _admin_area_child_counts([str(row["id"]) for row in rows], group_source_only=True)
    sections = []
    for row in rows:
        row_id = str(row["id"])
        children_count = child_counts.get(row_id, 0)
        if children_count <= 0:
            continue
        name = _display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
        entity_type = _entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        sections.append(
            {
                "id": row_id,
                "code": str(row.get("code") or ""),
                "name": name,
                "label": label,
                "level": int(row.get("level") or 0),
                "entity_type": entity_type,
                "parent_id": str(row.get("parent_id") or ""),
                "children_count": children_count,
            }
        )
    return sections


def _group_source_root_payload(country_code: str) -> dict | None:
    root = _source_country_root_for_code(country_code)
    if root is None:
        return None
    children_count = _admin_area_child_counts([str(root.id)], group_source_only=True).get(str(root.id), 0)
    if children_count <= 0:
        return None
    name = _area_display_name(root)
    entity_type = _entity_type_label(root.entity_type, country_code=root.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(root.id),
        "value": str(root.id),
        "code": str(root.code or ""),
        "name": name,
        "label": label,
        "level": 0,
        "entity_type": entity_type,
        "parent_id": "",
        "ancestor_ids": [],
        "children_count": children_count,
    }


def _group_source_child_options(country_code: str, parent_id: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    if not country_code or not parent_id:
        return []
    try:
        rows = list(
            _group_source_admin_areas()
            .filter(parent_id=parent_id, country_code__iexact=country_code)
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    direct_child_levels = sorted({int(row.get("level") or 0) for row in rows if int(row.get("level") or 0) > 0})
    if len(direct_child_levels) > 1:
        direct_child_level = direct_child_levels[0]
        rows = [
            row
            for row in rows
            if int(row.get("level") or 0) == direct_child_level
            or int(row.get("city_merge_status") or 0) == int(AdminArea.CityMergeStatus.UNIFIED)
        ]
    children = []
    for row in rows:
        name = _display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
        entity_type = _entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        children.append(
            {
                "id": str(row["id"]),
                "code": str(row.get("code") or ""),
                "name": name,
                "raw_name": str(row.get("name") or ""),
                "label": label,
                "level": int(row.get("level") or 0),
                "entity_type": entity_type,
                "parent_id": str(row.get("parent_id") or ""),
                "city_merge_status": int(row.get("city_merge_status") or 0),
            }
        )
    return children


def _group_source_descendant_options(country_code: str, parent_ids: list[str]) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    current_parent_ids = [str(parent_id).strip() for parent_id in parent_ids if str(parent_id or "").strip()]
    if not country_code or not current_parent_ids:
        return []
    seen_ids = set(current_parent_ids)
    ancestor_paths = {parent_id: [] for parent_id in current_parent_ids}
    descendants = []
    depth_guard = 0
    while current_parent_ids and depth_guard < 20:
        depth_guard += 1
        try:
            rows = list(
                _group_source_admin_areas()
                .filter(parent_id__in=current_parent_ids, country_code__iexact=country_code)
                .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
                .order_by("level", "name", "code", "id")
            )
        except (OperationalError, ProgrammingError, ValueError):
            return []
        next_parent_ids = []
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids:
                continue
            parent_id = str(row.get("parent_id") or "")
            ancestor_ids = [parent_id] + ancestor_paths.get(parent_id, [])
            row["ancestor_ids"] = ancestor_ids
            descendants.append(row)
            seen_ids.add(row_id)
            next_parent_ids.append(row_id)
            ancestor_paths[row_id] = ancestor_ids
        current_parent_ids = next_parent_ids
    return [_group_source_item_payload_from_row(row) for row in descendants]


def _group_source_item_level_options(country_code: str) -> list[dict]:
    return _group_source_level_options(country_code)


def _group_source_item_options(country_code: str, level: str | int, *, parent_id: str = "") -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return []
    try:
        queryset = _group_source_admin_areas().filter(country_code__iexact=country_code, level=level_int)
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        rows = list(
            queryset
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    return [_group_source_item_payload_from_row(row) for row in rows]


def _group_source_item_payload_from_row(row: dict) -> dict:
    name = _display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
    entity_type = _entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(row["id"]),
        "value": str(row["id"]),
        "country_code": _normalize_group_country_key(row.get("country_code")),
        "code": str(row.get("code") or ""),
        "name": name,
        "raw_name": str(row.get("name") or ""),
        "label": label,
        "level": int(row.get("level") or 0),
        "entity_type": entity_type,
        "parent_id": str(row.get("parent_id") or ""),
        "ancestor_ids": [str(value) for value in row.get("ancestor_ids") or [] if str(value or "").strip()],
    }


def _group_entry_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError(_("El nombre interno es obligatorio."))
    if not RECIPE_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El nombre interno debe empezar por una letra y usar letras, numeros o guion bajo."))
    return slug


def _group_internal_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
    if not text:
        raise ValueError(_("El nombre interno es obligatorio."))
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", text):
        raise ValueError(_("El nombre interno debe empezar por una letra y usar letras, numeros o guion bajo."))
    return text.upper()


def _group_entry_display_name(internal_name: str) -> str:
    return str(internal_name or "").replace("_", " ").title()


def _country_group_record_slug(country_code: str, group_slug: str) -> str:
    return _normalize_recipe_slug(f"{_normalize_group_country_key(country_code)}_{_group_entry_slug(group_slug)}")


def _derived_subdivision_record_slug(country_code: str, subdivision_slug: str) -> str:
    return _normalize_recipe_slug(
        f"{_normalize_group_country_key(country_code)}_{_group_entry_slug(subdivision_slug)}"
        if _normalize_group_country_key(country_code)
        else _group_entry_slug(subdivision_slug)
    )


def _derived_subdivision_entry_slug(record: DerivedSubdivision) -> str:
    country_code = _normalize_group_country_key(record.source_country_code)
    if country_code and record.slug.startswith(f"{country_code}_"):
        return _group_entry_slug(record.slug[len(country_code) + 1 :])
    if record.internal_name:
        return _group_entry_slug(record.internal_name)
    return _group_entry_slug(record.slug)


def _derived_subdivision_clone_internal_and_code(record: DerivedSubdivision, data: dict) -> tuple[str, str, str]:
    country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    source_key = _derived_subdivision_source_key(record, data)
    base_internal = _group_internal_name(f"{source_key}_COPIA")
    base_entry_slug = _group_entry_slug(base_internal)
    suffix_index = 1
    candidate_internal = base_internal
    candidate_entry_slug = base_entry_slug
    candidate_slug = _derived_subdivision_record_slug(country_code, candidate_entry_slug)
    while DerivedSubdivision.objects.filter(slug=candidate_slug).exists():
        suffix_index += 1
        candidate_internal = _group_internal_name(f"{base_internal}_{suffix_index}")
        candidate_entry_slug = _group_entry_slug(candidate_internal)
        candidate_slug = _derived_subdivision_record_slug(country_code, candidate_entry_slug)
    base_code = derived_code_piece(data.get("code") or record.code or source_key)
    suffix = "COPIA" if suffix_index <= 1 else f"COPIA-{suffix_index}"
    clone_code = f"{base_code}-{suffix}" if base_code else suffix
    return candidate_internal, clone_code, candidate_slug


def _clone_derived_subdivision(record: DerivedSubdivision) -> DerivedSubdivision:
    data = _derived_subdivision_toml_data(record.content)
    source_country_code = _normalize_group_country_key(data.get("source_country_code") or record.source_country_code)
    clone_internal_name, clone_code, clone_slug = _derived_subdivision_clone_internal_and_code(record, data)
    clone_name = _clone_display_name(record.name)
    content = _set_toml_string_field_in_content(record.content, field="internal_name", value=clone_internal_name)
    content = _set_toml_string_field_in_content(content, field="source_country_code", value=source_country_code)
    content = _set_toml_string_field_in_content(content, field="name", value=clone_name)
    content = _set_toml_string_field_in_content(content, field="code", value=clone_code)
    _validate_plain_toml(content, expected_kind="derived_subdivision")
    return DerivedSubdivision.objects.create(
        slug=clone_slug,
        internal_name=clone_internal_name,
        name=clone_name,
        source_country_code=source_country_code,
        entity_type=str(data.get("entity_type") or record.entity_type or "").strip(),
        code=clone_code,
        description=record.description,
        content=content.rstrip() + "\n",
    )


def _derived_subdivision_for_country(country_code: str, subdivision_slug: str) -> DerivedSubdivision | None:
    country_code = _normalize_group_country_key(country_code)
    subdivision_slug = _group_entry_slug(subdivision_slug)
    record_slug = _derived_subdivision_record_slug(country_code, subdivision_slug)
    record = DerivedSubdivision.objects.filter(slug=record_slug).first()
    if record:
        return record
    direct = DerivedSubdivision.objects.filter(slug=subdivision_slug, source_country_code__iexact=country_code).first()
    if direct:
        return direct
    queryset = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    try:
        internal_name = _group_internal_name(subdivision_slug)
    except ValueError:
        internal_name = ""
    if internal_name:
        record = queryset.filter(internal_name__iexact=internal_name).first()
        if record:
            return record
    requested_code = _derived_subdivision_code_piece(subdivision_slug)
    if requested_code:
        record = queryset.filter(code__iexact=requested_code).first()
        if record:
            return record
    for candidate in queryset:
        data = _derived_subdivision_record_toml(candidate)
        names = [
            data.get("entry_slug"),
            data.get("internal_name"),
            data.get("slug"),
            candidate.internal_name,
            candidate.code,
        ]
        for name in names:
            if not str(name or "").strip():
                continue
            try:
                if _group_entry_slug(str(name)) == subdivision_slug:
                    return candidate
            except ValueError:
                continue
    return None


def _validate_derived_subdivision_toml(content: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("TOML invalido."))
    kind = str(data.get("kind") or "").strip()
    if kind and kind != "derived_subdivision":
        raise ValueError(_("El TOML debe usar kind = 'derived_subdivision'."))
    for key in ("include", "subtract"):
        blocks = data.get(key)
        if blocks is not None and not isinstance(blocks, list):
            raise ValueError(_("%(key)s debe ser una lista de bloques TOML.") % {"key": key})
        for block in blocks or []:
            if not isinstance(block, dict):
                raise ValueError(_("%(key)s debe contener bloques TOML validos.") % {"key": key})
            try:
                level = int(block.get("level"))
            except (TypeError, ValueError):
                raise ValueError(_("Cada bloque debe tener un nivel numerico.")) from None
            if level < 0 or level > 9:
                raise ValueError(_("El nivel debe estar entre 0 y 9."))
            for list_key in ("names", "groups", "derived_subdivisions", "ids", "codes"):
                value = block.get(list_key)
                if value is not None and not isinstance(value, list):
                    raise ValueError(_("%(key)s.%(field)s debe ser una lista.") % {"key": key, "field": list_key})
    for key in ("level", "source_population_year", "population_year", "year"):
        if data.get(key) not in (None, ""):
            try:
                int(data.get(key))
            except (TypeError, ValueError):
                raise ValueError(_("%(key)s debe ser numerico.") % {"key": key}) from None
    capitals = data.get("capitals")
    if capitals is not None and not isinstance(capitals, list):
        raise ValueError(_("capitals debe ser una lista."))
    capital_groups = data.get("capital_groups")
    if capital_groups is not None and not isinstance(capital_groups, list):
        raise ValueError(_("capital_groups debe ser una lista de bloques TOML."))
    for block in capital_groups or []:
        if not isinstance(block, dict):
            raise ValueError(_("capital_groups debe contener bloques TOML validos."))
        if block.get("level") not in (None, ""):
            try:
                int(block.get("level"))
            except (TypeError, ValueError):
                raise ValueError(_("capital_groups.level debe ser numerico.")) from None
        for list_key in ("names",):
            value = block.get(list_key)
            if value is not None and not isinstance(value, list):
                raise ValueError(_("capital_groups.%(field)s debe ser una lista.") % {"field": list_key})
    return data


def _default_derived_subdivision_toml(
    *,
    country_code: str,
    internal_name: str,
    name: str = "",
    code: str = "",
    parent_code: str = "",
    entity_type: str = "Provincia",
    level: int = 1,
) -> str:
    internal_name = _group_internal_name(internal_name)
    country_code = _normalize_group_country_key(country_code)
    parent_code = parent_code or _derived_subdivision_root_code(country_code)
    name = str(name or _group_entry_display_name(internal_name)).strip()
    return "\n".join(
        [
            "schema_version = 1",
            'kind = "derived_subdivision"',
            f"internal_name = {_toml_string(internal_name)}",
            f"source_country_code = {_toml_string(country_code)}",
            f"name = {_toml_string(name)}",
            f"code = {_toml_string(code)}",
            f"parent_code = {_toml_string(parent_code)}",
            f"entity_type = {_toml_string(entity_type)}",
            f"level = {int(level or 1)}",
            'generic_name = ""',
            "capitals = []",
            "",
            "# Capital compuesta opcional: puede usar un grupo o nombres directos.",
            "# [[capital_groups]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 3",
            '# group = ""',
            "# names = []",
            '# capital_name = ""',
            "",
            "# Incluye subdivisiones completas o grupos reutilizables.",
            "# [[include]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 2",
            "# names = []",
            "# groups = []",
            "",
            "# Resta solo elementos de niveles inferiores o grupos de esos niveles.",
            "# [[subtract]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 3",
            "# names = []",
            "# groups = []",
            "",
        ]
    )


def _legacy_python_source_from_content(content: str) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return ""
    legacy = data.get("legacy") if isinstance(data, dict) else None
    return str(legacy.get("python_source") or "") if isinstance(legacy, dict) else ""


GROUP_TOML_METADATA_KEYS = {
    "schema_version",
    "kind",
    "slug",
    "entry_slug",
    "internal_name",
    "name",
    "description",
    "source_country_code",
    "source_bundle",
    "source_python",
    "country_groups",
    "selection",
    "legacy",
    "groups",
}


def _top_level_group_names_from_data(data: dict, internal_name: str) -> list[str]:
    keys = [str(internal_name or "").strip()]
    keys.extend(
        key
        for key, value in data.items()
        if key not in GROUP_TOML_METADATA_KEYS and isinstance(value, list) and key not in keys
    )
    for key in keys:
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            continue
        names = []
        seen: set[str] = set()
        for item in value:
            name = str(item or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
    return []


def _top_level_group_assignment_names(data: dict) -> list[str]:
    if not isinstance(data, dict):
        return []
    return [
        str(key)
        for key, value in data.items()
        if key not in GROUP_TOML_METADATA_KEYS and isinstance(value, list) and all(isinstance(item, str) for item in value)
    ]


def _legacy_group_assignments(content: str) -> list[dict]:
    """Return top-level Python list assignments; dict assignments are subdivisions."""
    source = _legacy_python_source_from_content(content)
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    entries = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        names = []
        valid_list = True
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.append(item.value)
            else:
                valid_list = False
                break
        if not valid_list:
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            internal_name = target.id
            try:
                slug = _group_entry_slug(internal_name)
            except ValueError:
                continue
            entries.append(
                {
                    "slug": slug,
                    "internal_name": internal_name,
                    "name": _group_entry_display_name(internal_name),
                    "blocks": [{"country_code": "", "names": names}],
                    "record": None,
                    "record_slug": "",
                    "is_legacy": True,
                }
            )
    return entries


def _stored_group_entry_from_record(group: SubdivisionGroup, country_code: str) -> dict | None:
    try:
        data = tomllib.loads(group.content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    assignment_names = _top_level_group_assignment_names(data)
    internal_name = str(data.get("internal_name") or (assignment_names[0] if assignment_names else "")).strip()
    if not internal_name:
        source_country = _normalize_group_country_key(group.source_country_code or country_code)
        if source_country and group.slug.startswith(f"{source_country}_"):
            internal_name = group.slug[len(source_country) + 1 :]
        else:
            internal_name = group.slug
    try:
        internal_name = _group_internal_name(internal_name)
        entry_slug = _group_entry_slug(str(data.get("entry_slug") or internal_name))
    except ValueError:
        return None
    blocks = []
    country_groups = selection.get("country_groups") if isinstance(selection, dict) else None
    if isinstance(country_groups, list):
        for item in country_groups:
            if not isinstance(item, dict):
                continue
            block_country = _normalize_group_country_key(item.get("country_code") or country_code)
            names = [str(name) for name in (item.get("include_names") or item.get("names") or [])]
            sections = _group_sections_from_payload(item.get("sections") or [])
            section_names = {
                selected["name"]
                for section in sections
                for selected in section.get("selected", [])
                if selected.get("name")
            }
            blocks.append(
                {
                    "country_code": block_country,
                    "names": [name for name in names if name not in section_names],
                    "sections": sections,
                }
            )
    root_country_groups = data.get("country_groups") if isinstance(data.get("country_groups"), list) else None
    if root_country_groups:
        for item in root_country_groups:
            if not isinstance(item, dict):
                continue
            block_country = _normalize_group_country_key(item.get("country_code") or country_code)
            names = [str(name) for name in (item.get("include_names") or item.get("names") or [])]
            sections = _group_sections_from_payload(item.get("sections") or [])
            section_names = {
                selected["name"]
                for section in sections
                for selected in section.get("selected", [])
                if selected.get("name")
            }
            blocks.append(
                {
                    "country_code": block_country,
                    "names": [name for name in names if name not in section_names],
                    "sections": sections,
                }
            )
    if not blocks:
        names = [str(name) for name in (selection.get("include_names") or [])] if isinstance(selection, dict) else []
        if not names:
            names = _top_level_group_names_from_data(data, internal_name)
        blocks.append({"country_code": _normalize_group_country_key(group.source_country_code or country_code), "names": names})
    return {
        "slug": entry_slug,
        "internal_name": internal_name,
        "name": str(group.name or data.get("name") or internal_name),
        "blocks": blocks,
        "record": group,
        "record_slug": group.slug,
        "is_legacy": False,
    }


def _subdivision_group_record_entries(group: SubdivisionGroup, country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    legacy_entries = _legacy_group_assignments(group.content)
    if legacy_entries:
        for entry in legacy_entries:
            for block in entry["blocks"]:
                if not block.get("country_code"):
                    block["country_code"] = country_code
        return legacy_entries
    entry = _stored_group_entry_from_record(group, country_code)
    return [entry] if entry else []


def _subdivision_group_entries_for_country(groups: list[SubdivisionGroup], country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    entries_by_slug: dict[str, dict] = {}
    for group in groups:
        for entry in _subdivision_group_record_entries(group, country_code):
            municipality_count = sum(len(_group_block_selected_names(block)) for block in (entry.get("blocks") or []))
            entry = {**entry, "href": reverse(
                "ciudades_del_mundo:group_entry_edit",
                kwargs={"country_code": country_code, "group_slug": entry["slug"]},
            )}
            entry["municipality_count"] = municipality_count
            entry["municipality_count_text"] = _group_metric_text(municipality_count)
            # Standalone SQL rows win over virtual legacy rows with the same route id.
            if entry["slug"] not in entries_by_slug or not entry.get("is_legacy"):
                entries_by_slug[entry["slug"]] = entry
    return sorted(entries_by_slug.values(), key=lambda item: (item["internal_name"], item["name"]))


def _group_entry_for_country(country_code: str, group_slug: str, *, hydrate: bool = True) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    group_slug = _group_entry_slug(group_slug)
    record_slug = _country_group_record_slug(country_code, group_slug)
    record = SubdivisionGroup.objects.filter(slug=record_slug).first()
    if record:
        entry = _stored_group_entry_from_record(record, country_code)
        return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    direct_record = SubdivisionGroup.objects.filter(slug=group_slug).first()
    if direct_record and country_code in _subdivision_group_record_country_codes(direct_record):
        entry = _stored_group_entry_from_record(direct_record, country_code)
        return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    candidates = list(SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"))
    for group in candidates:
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            if entry["slug"] == group_slug or entry.get("record_slug") == group_slug:
                return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    return None


def _group_duplicate_entry(
    *,
    country_code: str,
    group_slug: str,
    exclude_record_slug: str = "",
    exclude_entry_slug: str = "",
) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    group_slug = _group_entry_slug(group_slug)
    exclude_record_slug = str(exclude_record_slug or "").strip()
    exclude_entry_slug = _group_entry_slug(exclude_entry_slug) if str(exclude_entry_slug or "").strip() else ""
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if exclude_record_slug and group.slug == exclude_record_slug:
            continue
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            entry_slug = _group_entry_slug(entry.get("slug") or "")
            if exclude_entry_slug and entry_slug == exclude_entry_slug:
                continue
            if entry_slug == group_slug:
                return entry
    return None


def _group_existing_key_options(
    *,
    country_code: str,
    exclude_record_slug: str = "",
    exclude_entry_slug: str = "",
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    rows = []
    seen: set[str] = set()
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if exclude_record_slug and group.slug == exclude_record_slug:
            continue
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            try:
                slug = _group_entry_slug(entry.get("slug") or "")
            except ValueError:
                continue
            if exclude_entry_slug and slug == exclude_entry_slug:
                continue
            if slug in seen:
                continue
            seen.add(slug)
            rows.append({"slug": slug, "internal_name": str(entry.get("internal_name") or "").strip()})
    return rows


def _group_entry_hydrated_from_sql(entry: dict | None, country_code: str) -> dict | None:
    if not entry:
        return None
    blocks = []
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or country_code)
        raw_names = [str(name).strip() for name in (block.get("names") or []) if str(name or "").strip()]
        sections = _group_sections_from_payload(block.get("sections") or [])
        if raw_names:
            resolved = _group_sections_for_flat_names(block_country, raw_names)
            existing_section_ids = {section.get("area_id") for section in sections}
            sections.extend(
                section
                for section in resolved["sections"]
                if section.get("area_id") and section.get("area_id") not in existing_section_ids
            )
            raw_names = resolved["unresolved_names"]
        blocks.append({**block, "country_code": block_country, "names": raw_names, "sections": sections})
    return {**entry, "blocks": blocks}


def _group_sections_for_flat_names(country_code: str, names: list[str]) -> dict:
    country_code = _normalize_group_country_key(country_code)
    cleaned_names = [str(name).strip() for name in names if str(name or "").strip()]
    wanted_keys = [_group_name_match_key(name) for name in cleaned_names]
    wanted_set = {key for key in wanted_keys if key}
    if not country_code or not wanted_set:
        return {"sections": [], "unresolved_names": names}

    try:
        condition = Q()
        unique_names = sorted(set(cleaned_names))
        if len(unique_names) <= 250:
            for name in unique_names:
                condition |= Q(name__iexact=name) | Q(code__iexact=name) | Q(id=name)
        else:
            condition = Q(name__in=unique_names) | Q(code__in=unique_names) | Q(id__in=unique_names)
        rows = list(
            _group_source_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .filter(condition)
            .select_related("parent")
            .only(
                "id",
                "country_code",
                "code",
                "name",
                "level",
                "entity_type",
                "parent_id",
                "parent__id",
                "parent__country_code",
                "parent__code",
                "parent__name",
                "parent__level",
                "parent__entity_type",
                "parent__parent_id",
            )
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {"sections": [], "unresolved_names": names}

    by_name: dict[str, list[AdminArea]] = {}
    for area in rows:
        key = _group_name_match_key(area.name)
        if key in wanted_set:
            by_name.setdefault(key, []).append(area)

    candidate_ids = [str(area.id) for candidates in by_name.values() for area in candidates]
    child_counts = _admin_area_child_counts(candidate_ids)
    selected_by_parent: dict[str, dict] = {}
    unresolved = []
    for original_name, name_key in zip(cleaned_names, wanted_keys):
        candidates = by_name.get(name_key) or []
        if not candidates:
            unresolved.append(original_name)
            continue
        area = sorted(candidates, key=lambda item: _group_area_name_candidate_rank(item, child_counts))[0]
        parent = area.parent
        if parent is None:
            unresolved.append(original_name)
            continue
        section = selected_by_parent.setdefault(parent.id, _group_section_payload_from_area(parent))
        section["selected"].append({"id": str(area.id), "name": _area_display_name(area)})

    return {
        "sections": list(selected_by_parent.values()),
        "unresolved_names": unresolved,
    }


def _group_area_name_candidate_rank(area: AdminArea, child_counts: dict[str, int]) -> tuple[int, int, int, str, str]:
    entity_type = str(area.entity_type or "").casefold()
    is_lowest_place = int(_group_level_is_lowest_place_only({entity_type}))
    has_children = int(child_counts.get(str(area.id), 0) > 0)
    return (
        -has_children,
        is_lowest_place,
        -(int(area.level or 0)),
        str(area.name or ""),
        str(area.id),
    )


def _group_section_payload_from_area(area: AdminArea) -> dict:
    label = _area_display_name(area)
    entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
    if entity_type:
        label = f"{label} ({entity_type})"
    metadata = _group_admin_area_metadata(str(area.id))
    return {
        "area_id": str(area.id),
        "area_name": _area_display_name(area),
        "area_label": label,
        "level": int(area.level or 0),
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": metadata.get("ancestor_ids") or [],
        "selected": [],
    }


def _group_name_match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


def _render_group_entry_toml(
    *,
    record_slug: str,
    entry_slug: str,
    internal_name: str,
    name: str,
    source_country_code: str,
    blocks: list[dict],
) -> str:
    clean_blocks = _group_blocks_with_required_country(blocks, fallback_country_code=source_country_code)

    compatibility_names = _group_selected_names_from_blocks(clean_blocks)
    lines = [
        f"source_country_code = {_toml_string(source_country_code)}",
        "",
        f"{internal_name} = {_toml_array(compatibility_names)}",
        "",
    ]
    for block in clean_blocks:
        block_names = _group_block_selected_names(block)
        lines.extend(
            [
                "[[country_groups]]",
                f"country_code = {_toml_string(block['country_code'])}",
                f"include_names = {_toml_array(block_names)}",
                "",
            ]
        )
        for section in block.get("sections") or []:
            selected = section.get("selected") or []
            lines.extend(
                [
                    "[[country_groups.sections]]",
                    f"level = {int(section.get('level') or 0)}",
                    f"area_id = {_toml_string(section.get('area_id') or '')}",
                    f"selected_ids = {_toml_array([item.get('id') or '' for item in selected])}",
                    f"selected_names = {_toml_array([item.get('name') or '' for item in selected])}",
                    "",
                ]
            )
    return "\n".join(lines)


def _rename_derived_subdivision_group_references(*, country_code: str, old_key: str, new_key: str) -> int:
    country_code = _normalize_group_country_key(country_code)
    old_key = _group_internal_name(old_key)
    new_key = _group_internal_name(new_key)
    if not country_code or not old_key or not new_key or old_key == new_key:
        return 0
    updated = 0
    records = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    for record in records:
        try:
            data = tomllib.loads(record.content or "")
        except tomllib.TOMLDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if not _replace_derived_subdivision_group_key(data, old_key=old_key, new_key=new_key):
            continue
        record.content = _render_derived_subdivision_toml_data(data)
        record.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _rename_subdivision_group_content_references(
    *,
    country_code: str,
    old_key: str,
    new_key: str,
    exclude_slugs: set[str] | None = None,
) -> int:
    country_code = _normalize_group_country_key(country_code)
    old_key = _group_internal_name(old_key)
    new_key = _group_internal_name(new_key)
    exclude_slugs = {str(slug) for slug in (exclude_slugs or set()) if str(slug)}
    if not country_code or not old_key or not new_key or old_key == new_key:
        return 0
    pattern = re.compile(rf"((?<![A-Za-z0-9_])|(?<=[\\][nr])){re.escape(old_key)}(?![A-Za-z0-9_])")
    updated = 0
    for group in SubdivisionGroup.objects.order_by("slug"):
        if group.slug in exclude_slugs:
            continue
        content = group.content or ""
        if old_key not in content:
            continue
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        new_content, replacements = pattern.subn(new_key, content)
        if not replacements:
            continue
        group.content = new_content
        group.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _replace_derived_subdivision_group_key(data: dict, *, old_key: str, new_key: str) -> bool:
    changed = False
    for table_key in ("include", "subtract"):
        changed = _replace_group_key_in_source_blocks(data.get(table_key), old_key=old_key, new_key=new_key) or changed
    changed = _replace_group_key_in_capital_blocks(data.get("capital_groups"), old_key=old_key, new_key=new_key) or changed
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if not isinstance(child, dict):
            continue
        for table_key in ("include", "subtract"):
            changed = _replace_group_key_in_source_blocks(child.get(table_key), old_key=old_key, new_key=new_key) or changed
        changed = _replace_group_key_in_capital_blocks(child.get("capital_groups"), old_key=old_key, new_key=new_key) or changed
    return changed


def _replace_group_key_in_source_blocks(blocks, *, old_key: str, new_key: str) -> bool:
    changed = False
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        groups, block_changed = _renamed_group_key_list(block.get("groups"), old_key=old_key, new_key=new_key)
        if block_changed:
            block["groups"] = groups
            changed = True
    return changed


def _replace_group_key_in_capital_blocks(blocks, *, old_key: str, new_key: str) -> bool:
    changed = False
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for field in ("group", "group_key"):
            value = str(block.get(field) or "").strip()
            if value and _group_internal_name(value) == old_key:
                block[field] = new_key
                changed = True
    return changed


def _renamed_group_key_list(values, *, old_key: str, new_key: str) -> tuple[list[str], bool]:
    if not isinstance(values, list):
        return [], False
    changed = False
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        normalized = _group_internal_name(raw)
        if normalized == old_key:
            candidate = new_key
            changed = True
        else:
            candidate = raw
        candidate_key = _group_internal_name(candidate)
        if candidate_key in seen:
            changed = True
            continue
        seen.add(candidate_key)
        result.append(candidate)
    return result, changed


def _stored_visual_asset_image_url(asset: dict | None) -> str:
    if not isinstance(asset, dict):
        return ""
    for key in ("image_url", "remote_url", "local_url"):
        value = str(asset.get(key) or "").strip()
        if value:
            return value
    commons_filename = str(asset.get("commons_filename") or "").strip()
    if commons_filename:
        return commons_file_url(commons_filename, width=900)
    return ""


def _subdivision_group_or_404(slug: str) -> SubdivisionGroup:
    slug = _normalize_recipe_slug(slug)
    try:
        return SubdivisionGroup.objects.get(slug=slug)
    except SubdivisionGroup.DoesNotExist as exc:
        raise Http404(_("No existe el grupo '%(slug)s'.") % {"slug": slug}) from exc


def group_export_toml(request, slug):
    """Export the source country's SQL-backed subdivision groups to one TOML bundle."""
    wants_json = _wants_json(request)
    route_slug = str(slug or "").strip().lower()
    group = SubdivisionGroup.objects.filter(slug=route_slug).first()
    if group:
        country_codes = sorted(_subdivision_group_record_country_codes(group))
        country_code = country_codes[0] if country_codes else "unknown"
        export_slugs = [group.slug]
        redirect_url = reverse("ciudades_del_mundo:group_edit", kwargs={"slug": group.slug})
    else:
        country_code = _normalize_group_country_key(route_slug)
        country_groups = _subdivision_groups_for_country(country_code)
        export_slugs = [item.slug for item in country_groups]
        redirect_url = reverse("ciudades_del_mundo:group_list")
        if not export_slugs:
            error = _("No hay grupos SQL para exportar en '%(country)s'.") % {"country": country_code}
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=404)
            messages.error(request, error)
            return redirect("ciudades_del_mundo:group_list")
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect_url
    try:
        exported = export_subdivision_groups_to_toml(force=True, slugs=export_slugs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
    else:
        if exported:
            message = _("Grupos exportados a subdivision_groups/groups/%(country)s.toml.") % {"country": country_code}
        else:
            message = _("El fichero subdivision_groups/groups/%(country)s.toml ya estaba actualizado.") % {"country": country_code}
        if wants_json:
            return JsonResponse({"ok": True, "message": message, "exported": bool(exported)})
        messages.success(request, message)
    return redirect_url


def group_import_toml_slug(request, slug):
    """Import one subdivision_groups/groups/<country>.toml seed bundle into SQL."""
    wants_json = _wants_json(request)
    slug = _normalize_recipe_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    group = SubdivisionGroup.objects.filter(slug=slug).first()
    seed_slug = _group_seed_file_slug_for_record(group) if group else slug
    seed_country = ""
    if group:
        country_codes = sorted(_subdivision_group_record_country_codes(group))
        seed_country = country_codes[0] if country_codes else _normalize_group_country_key(group.source_country_code)
    seed_keys = [f"{seed_country}/{seed_slug}"] if seed_country else []
    seed_keys.append(seed_slug)
    seed_paths = []
    for seed_key in seed_keys:
        seed_paths = bundled_subdivision_group_paths([seed_key])
        if seed_paths:
            break
    if not seed_paths:
        error = _("No existe el fichero subdivision_groups/groups/%(country)s.toml.") % {"country": seed_country or seed_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    if len(seed_paths) > 1:
        error = _("Hay mas de un TOML semilla para el grupo '%(slug)s'.") % {"slug": seed_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            imported_groups = import_subdivision_group_path_records(seed_paths[0], force=True)
        else:
            with write_lock:
                imported_groups = import_subdivision_group_path_records(seed_paths[0], force=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor as a form message.
        error = _("No se pudo importar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=500)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    target_slug = _country_group_record_slug(seed_country, seed_slug) if seed_country else slug
    group = next((record for record in imported_groups if record.slug == target_slug), None)
    if group is None:
        group = SubdivisionGroup.objects.filter(slug=target_slug).first() or SubdivisionGroup.objects.filter(slug=slug).first()
    if group is None:
        error = _("El TOML no contiene el grupo '%(slug)s'.") % {"slug": seed_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    message = _("Grupos importados desde subdivision_groups/groups/%(country)s.toml.") % {
        "country": seed_country or seed_paths[0].stem
    }
    redirect_url = _group_record_edit_url(group)
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "imported": True,
                "refresh": True,
                "redirect_url": redirect_url,
            }
        )
    messages.success(request, message)
    return redirect(redirect_url)


def _group_seed_file_slug_for_record(group: SubdivisionGroup | None) -> str:
    if not group:
        return ""
    entry = _stored_group_entry_from_record(group, _normalize_group_country_key(group.source_country_code))
    if entry:
        return _group_entry_slug(entry.get("slug") or entry.get("internal_name") or group.slug)
    country_code = _normalize_group_country_key(group.source_country_code)
    if country_code and group.slug.startswith(f"{country_code}_"):
        return _group_entry_slug(group.slug[len(country_code) + 1 :])
    return _group_entry_slug(group.slug)


def _group_record_edit_url(group: SubdivisionGroup) -> str:
    country_codes = sorted(_subdivision_group_record_country_codes(group))
    country_code = country_codes[0] if country_codes else _normalize_group_country_key(group.source_country_code)
    if country_code:
        entries = _subdivision_group_record_entries(group, country_code)
        if entries:
            return reverse(
                "ciudades_del_mundo:group_entry_edit",
                kwargs={"country_code": country_code, "group_slug": entries[0]["slug"]},
            )
    return reverse("ciudades_del_mundo:group_edit", kwargs={"slug": group.slug})


def _source_country_options_with_current(current_code: str) -> list[dict[str, str]]:
    current_code = str(current_code or "").strip().lower()
    options = _derived_source_country_options()
    if current_code and current_code not in {option["value"] for option in options}:
        options.append(
            {
                "value": current_code,
                "label": _display_name("", current_code, country_code=current_code),
                "root_id": "",
            }
        )
    return options


def group_new(request):
    """Compatibility route; visible creation is country-scoped."""
    country_code = _normalize_group_country_key(request.GET.get("source_country_code"))
    if country_code:
        return redirect("ciudades_del_mundo:group_entry_new", country_code=country_code)
    return redirect("ciudades_del_mundo:group_list")


def group_edit(request, slug):
    """Compatibility route for existing full-group SQL rows."""
    group = _subdivision_group_or_404(slug)
    country_codes = sorted(_subdivision_group_record_country_codes(group))
    country_code = country_codes[0] if country_codes else _normalize_group_country_key(group.source_country_code)
    if country_code:
        entries = _subdivision_group_record_entries(group, country_code)
        if entries:
            return redirect(
                "ciudades_del_mundo:group_entry_edit",
                country_code=country_code,
                group_slug=entries[0]["slug"],
            )
    raise Http404(_("No existe una agrupacion editable para '%(slug)s'.") % {"slug": slug})


def group_entry_new(request, country_code):
    """Create a reusable subdivision grouping inside one source country."""
    country_code = _normalize_group_country_key(country_code)
    return _group_entry_form(request, country_code=country_code, entry=None)


def group_entry_edit(request, country_code, group_slug):
    """Edit one country-scoped subdivision grouping."""
    country_code = _normalize_group_country_key(country_code)
    group_slug = _group_entry_slug(group_slug)
    entry = _group_entry_for_country(country_code, group_slug, hydrate=True)
    if not entry:
        raise Http404(_("No existe la agrupacion '%(slug)s'.") % {"slug": group_slug})
    return _group_entry_form(request, country_code=country_code, entry=entry)


def group_city_import_toml(request, country_code):
    """Open the city-group editor from the city panel import action."""
    country_code = _normalize_group_country_key(country_code)
    if request.method != "POST":
        return redirect("ciudades_del_mundo:group_list")
    return redirect(
        "ciudades_del_mundo:group_city_entry",
        country_code=country_code,
        city_slug="ciudades",
    )


def group_city_export_toml(request, country_code):
    """Export the city panel scaffold as an empty country TOML bundle."""
    country_code = _normalize_group_country_key(country_code)
    if request.method != "POST":
        return redirect("ciudades_del_mundo:group_list")
    lines = [f"source_country_code = {_toml_string(country_code)}", "", "[cities]"]
    response = HttpResponse("\n".join(lines).rstrip() + "\n", content_type="application/toml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{country_code}_cities.toml"'
    return response


def group_city_entry(request, country_code, city_slug):
    """Temporary city editor route using the group editor shell."""
    country_code = _normalize_group_country_key(country_code)
    city_slug = str(city_slug or "").strip()
    if city_slug.casefold() == "ciudades":
        return _group_entry_form(request, country_code=country_code, entry=None)
    entry = _group_city_entry_for_country(country_code, city_slug)
    if not entry:
        raise Http404(_("No existe la ciudad '%(slug)s'.") % {"slug": city_slug})
    return _group_entry_form(request, country_code=country_code, entry=entry)


def _group_city_entry_for_country(country_code: str, city_slug: str) -> dict | None:
    country_code = _normalize_group_country_key(country_code)
    city_slug = str(city_slug or "").strip()
    if not country_code or not city_slug:
        return None
    lookup_slug = _group_city_lookup_slug(city_slug)
    if not lookup_slug:
        return None
    code_piece = _group_internal_name(f"CITY_{lookup_slug}")
    return {
        "slug": _group_entry_slug(code_piece),
        "internal_name": code_piece,
        "name": code_piece,
        "blocks": [{"country_code": country_code, "names": []}],
        "record": None,
        "record_slug": "",
        "is_legacy": True,
    }


def _group_city_lookup_slug(value: object) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().casefold()).strip("_-")


def _group_entry_form(request, *, country_code: str, entry: dict | None):
    source_countries = _source_country_options_with_current(country_code)
    if entry:
        initial_internal_name = entry["internal_name"]
        initial_blocks = _group_blocks_with_required_country(
            entry["blocks"] or [{"country_code": country_code, "names": []}],
            fallback_country_code=country_code,
        )
        record = entry.get("record")
        mode = "edit"
        previous_internal_name = _group_internal_name(initial_internal_name)
    else:
        initial_internal_name = ""
        initial_blocks = [{"country_code": country_code, "names": []}]
        record = None
        mode = "new"
        previous_internal_name = ""

    form = {
        "internal_name": initial_internal_name,
        "country_code": country_code,
        "blocks_json": json.dumps(initial_blocks, ensure_ascii=False),
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            internal_name = _group_internal_name(form["internal_name"])
            group_slug = _group_entry_slug(internal_name)
            duplicate = _group_duplicate_entry(
                country_code=country_code,
                group_slug=group_slug,
                exclude_record_slug=str(record.slug) if record else "",
                exclude_entry_slug=str(entry.get("slug") or "") if entry else "",
            )
            if duplicate:
                raise ValueError(
                    _("Ya existe un grupo '%(name)s' para este pais.")
                    % {"name": duplicate.get("internal_name") or internal_name}
                )
            name = internal_name
            blocks = _group_blocks_from_json(form["blocks_json"], fallback_country_code=country_code)
            record_slug = _country_group_record_slug(country_code, group_slug)
            content = _render_group_entry_toml(
                record_slug=record_slug,
                entry_slug=group_slug,
                internal_name=internal_name,
                name=name,
                source_country_code=country_code,
                blocks=blocks,
            )
            _validate_plain_toml(content, expected_kind="subdivision_group")
            with transaction.atomic():
                if record is not None:
                    if record.slug != record_slug:
                        SubdivisionGroup.objects.filter(slug=record.slug).update(slug=record_slug)
                        record.slug = record_slug
                    group = record
                    group.name = name
                    group.source_country_code = country_code
                    group.description = ""
                    group.content = content
                    group.save(update_fields=["name", "source_country_code", "description", "content", "updated_at"])
                else:
                    group, _created = SubdivisionGroup.objects.update_or_create(
                        slug=record_slug,
                        defaults={
                            "name": name,
                            "source_country_code": country_code,
                            "description": "",
                            "content": content,
                        },
                    )
                if previous_internal_name and previous_internal_name != internal_name:
                    _rename_subdivision_group_content_references(
                        country_code=country_code,
                        old_key=previous_internal_name,
                        new_key=internal_name,
                        exclude_slugs={group.slug},
                    )
                    _rename_derived_subdivision_group_references(
                        country_code=country_code,
                        old_key=previous_internal_name,
                        new_key=internal_name,
                    )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Grupo '%(slug)s' guardado.") % {"slug": group.slug})
            redirect_url = reverse(
                "ciudades_del_mundo:group_entry_edit",
                kwargs={"country_code": country_code, "group_slug": group_slug},
            )
            return redirect(f"{redirect_url}?saved=1")

    data_slug = str(entry.get("slug") or "") if entry else ""

    return render(
        request,
        "ciudades_del_mundo/group_form.html",
        {
            "mode": mode,
            "record": record,
            "form": form,
            "source_countries": source_countries,
            "country_code": country_code,
            "source_data_url": reverse("ciudades_del_mundo:group_source_data"),
            "return_row_href": (
                reverse(
                    "ciudades_del_mundo:group_entry_edit",
                    kwargs={"country_code": country_code, "group_slug": data_slug},
                )
                if data_slug
                else ""
            ),
            "return_needs_refresh": request.GET.get("saved") == "1",
            "blocks": _group_blocks_for_context(form["blocks_json"], fallback_country_code=country_code),
            "existing_group_keys": _group_existing_key_options(
                country_code=country_code,
                exclude_record_slug=str(record.slug) if record else "",
                exclude_entry_slug=str(entry.get("slug") or "") if entry else "",
            ),
        },
    )


def _group_blocks_for_context(value: str, *, fallback_country_code: str) -> list[dict]:
    try:
        return _group_blocks_from_json(value, fallback_country_code=fallback_country_code)
    except ValueError:
        return [{"country_code": fallback_country_code, "names": []}]


def _group_sections_from_payload(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    sections = []
    seen_area_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        area_id = str(item.get("area_id") or item.get("id") or "").strip()
        if not area_id or area_id in seen_area_ids:
            continue
        seen_area_ids.add(area_id)
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        metadata = _group_admin_area_metadata(area_id)
        raw_selected = item.get("selected")
        selected = []
        if isinstance(raw_selected, list):
            for selected_item in raw_selected:
                if isinstance(selected_item, dict):
                    selected_id = str(selected_item.get("id") or "").strip()
                    selected_name = str(selected_item.get("name") or selected_item.get("label") or "").strip()
                else:
                    selected_id = ""
                    selected_name = str(selected_item or "").strip()
                if selected_name:
                    selected.append({"id": selected_id, "name": selected_name})
        else:
            selected_names = item.get("selected_names") or []
            selected_ids = item.get("selected_ids") or []
            if not isinstance(selected_names, list):
                selected_names = []
            if not isinstance(selected_ids, list):
                selected_ids = []
            for index, selected_name in enumerate(selected_names):
                selected_name = str(selected_name or "").strip()
                if selected_name:
                    selected.append(
                        {
                            "id": str(selected_ids[index] or "").strip() if index < len(selected_ids) else "",
                            "name": selected_name,
                        }
                    )
        ancestor_ids = item.get("ancestor_ids") or metadata.get("ancestor_ids") or []
        if not isinstance(ancestor_ids, list):
            ancestor_ids = []
        sections.append(
            {
                "area_id": area_id,
                "area_name": str(item.get("area_name") or item.get("name") or metadata.get("name") or "").strip(),
                "area_label": str(item.get("area_label") or metadata.get("label") or item.get("area_name") or item.get("name") or "").strip(),
                "level": level,
                "parent_id": str(item.get("parent_id") or metadata.get("parent_id") or "").strip(),
                "ancestor_ids": [str(ancestor_id) for ancestor_id in ancestor_ids if str(ancestor_id or "").strip()],
                "selected": selected,
            }
        )
    return sections


def _group_admin_area_metadata(area_id: str) -> dict:
    area_id = str(area_id or "").strip()
    if not area_id:
        return {}
    try:
        area = _visible_admin_areas().select_related("parent").filter(id=area_id).first()
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    if area is None:
        return {}
    name = _area_display_name(area)
    entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    ancestor_ids = []
    parent = area.parent
    guard = 0
    while parent is not None and guard < 20:
        ancestor_ids.append(str(parent.id))
        parent = parent.parent
        guard += 1
    return {
        "name": name,
        "label": label,
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": ancestor_ids,
    }


def _group_block_selected_names(block: dict) -> list[str]:
    names = []
    seen: set[str] = set()
    for name in block.get("names") or []:
        clean_name = str(name or "").strip()
        if clean_name and clean_name not in seen:
            seen.add(clean_name)
            names.append(clean_name)
    for section in block.get("sections") or []:
        for selected in section.get("selected") or []:
            clean_name = str(selected.get("name") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
            if clean_name and clean_name not in seen:
                seen.add(clean_name)
                names.append(clean_name)
    return names


def _group_selected_names_from_blocks(blocks: list[dict]) -> list[str]:
    names = []
    seen: set[str] = set()
    for block in blocks:
        for name in _group_block_selected_names(block):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _group_blocks_with_required_country(blocks: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = _normalize_group_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        country_code = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "names": [], "sections": []})
        grouped_block["names"].extend(str(name).strip() for name in (block.get("names") or []) if str(name or "").strip())
        grouped_block["sections"].extend(_group_sections_from_payload(block.get("sections") or []))
    if fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "names": [], "sections": []}
    ordered_codes = [fallback_country_code] + sorted(code for code in grouped if code != fallback_country_code)
    return [grouped[code] for code in ordered_codes]


def _group_blocks_from_json(value: str, *, fallback_country_code: str) -> list[dict]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    blocks = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        country_code = _normalize_group_country_key(item.get("country_code") or fallback_country_code)
        raw_names = item.get("names") or []
        if isinstance(raw_names, str):
            names = raw_names.splitlines()
        elif isinstance(raw_names, list):
            names = raw_names
        else:
            names = []
        blocks.append(
            {
                "country_code": country_code,
                "names": [str(name).strip() for name in names if str(name or "").strip()],
                "sections": _group_sections_from_payload(item.get("sections") or []),
            }
        )
    return _group_blocks_with_required_country(blocks, fallback_country_code=fallback_country_code)


def nuevo_area_list(request):
    """List built derived countries with aggregate comparison data."""
    stats = {
        row["country_code"]: row
        for row in NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"), seats=Sum("representatives"))
        .order_by("country_code")
    }
    roots = list(NuevoAdminArea.objects.filter(parent__isnull=True).order_by("country_code", "name"))
    root_rows = [
        {
            "root": root,
            "display_name": _display_name(root.name, root.country_code),
            "stats": stats.get(root.country_code, {}),
        }
        for root in roots
    ]
    context = {
        "root_rows": root_rows,
    }
    return render(request, "ciudades_del_mundo/nuevo_area_list.html", context)


def nuevo_area_detail(request, country_id):
    """Render filters for one `NuevoAdminArea` tree; table loads asynchronously."""
    country_id = _normalize_recipe_slug(country_id)
    try:
        root = NuevoAdminArea.objects.get(id=country_id)
    except NuevoAdminArea.DoesNotExist as exc:
        raise Http404(_("No existe NuevoAdminArea '%(country_id)s'.") % {"country_id": country_id}) from exc
    levels = (
        NuevoAdminArea.objects.filter(country_code=root.country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    context = {
        "root": root,
        "root_display_name": _display_name(root.name, root.country_code),
        "table_url": reverse("ciudades_del_mundo:nuevo_area_table", kwargs={"country_id": country_id}),
        "levels": levels,
        "entity_types": (
            NuevoAdminArea.objects.filter(country_code=root.country_code)
            .exclude(entity_type__isnull=True)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        ),
        "province_statuses": NuevoAdminArea.ProvinceStatus.choices,
        "parents": (
            NuevoAdminArea.objects.filter(country_code=root.country_code, children__isnull=False)
            .distinct()
            .order_by("level", "name")
        ),
        "sort_choices": _nuevo_sort_choices(),
    }
    return render(request, "ciudades_del_mundo/nuevo_area_detail.html", context)


def nuevo_area_table(request, country_id):
    """Render the asynchronous NuevoAdminArea comparison table partial."""
    country_id = _normalize_recipe_slug(country_id)
    try:
        root = NuevoAdminArea.objects.get(id=country_id)
    except NuevoAdminArea.DoesNotExist as exc:
        raise Http404(_("No existe NuevoAdminArea '%(country_id)s'.") % {"country_id": country_id}) from exc

    areas = _filtered_nuevo_areas(request, root)
    paginator = Paginator(areas, _page_size(request, default=75))
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [_comparison_row(area, root) for area in page_obj.object_list]
    return render(
        request,
        "ciudades_del_mundo/partials/nuevo_area_table.html",
        {
            "root": root,
            "rows": rows,
            "page_obj": page_obj,
            "querystring": _querystring_without_page(request),
        },
    )


def area_map_detail(request, source, area_id):
    """Render map and visual identity helpers for one source or derived area."""
    if source == "admin":
        area = (
            AdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(id=area_id)
            .first()
        )
    elif source == "derived":
        area = (
            NuevoAdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(id=area_id)
            .first()
        )
    else:
        raise Http404(_("Origen de mapa no soportado."))

    if area is None:
        raise Http404(_("No se encontro el area solicitada."))

    map_query = _area_search_query(area)
    language_code = getattr(request, "LANGUAGE_CODE", None)
    context = {
        "area": area,
        "source": source,
        "area_display_name": _area_display_name(area),
        "entity_type_display": _entity_type_label(
            getattr(area, "entity_type", ""),
            country_code=getattr(area, "country_code", ""),
        ),
        "parent_display_name": _area_display_name(area.parent) if getattr(area, "parent", None) else "",
        "most_populate_city_display_name": (
            _area_display_name(area.most_populate_city) if getattr(area, "most_populate_city", None) else ""
        ),
        "map_query": map_query,
        "wikidata_query": map_query,
        "country_display_name": _area_country_display_name(area),
        "capital_names": ", ".join(_area_capital_display_names(area, language_code)),
        "related_places_json": json.dumps(_area_related_places(area, language_code), ensure_ascii=False),
        "commons_flag_query": f"flag {area.name} {getattr(area, 'country_code', '')}",
        "commons_coat_query": f"coat of arms {area.name} {getattr(area, 'country_code', '')}",
    }
    return render(request, "ciudades_del_mundo/area_map_detail.html", context)


def visual_identity_entity_detail(request, kind, entity_type, entity_key):
    """Render the Ficha page for one stored asset using a readable entity URL."""
    kind = (kind or "image").lower()
    entity_type = str(entity_type or "").strip()
    entity_key = str(entity_key or "").strip().strip("/")
    asset = get_visual_asset_for_entity_kind(entity_type, entity_key, kind)
    if not asset:
        raise Http404(_("No se encontro la ficha solicitada."))
    return _render_visual_identity_asset(request, asset, kind=kind)


def visual_identity_detail(request, kind, filename):
    """Render legacy Ficha URLs that still pass a Commons filename or local path."""
    kind = (kind or "image").lower()
    safe_filename = str(filename or "").strip().lstrip("/")
    if not safe_filename:
        raise Http404(_("No se encontro la imagen solicitada."))

    asset = get_visual_asset_by_local_or_commons(safe_filename, kind=kind)
    if asset:
        return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)

    local_path = _identity_local_media_path(safe_filename)
    if local_path:
        asset = {
            "kind": kind,
            "entity_type": "",
            "entity_key": "",
            "entity_name": "",
            "commons_filename": _identity_commons_filename_for_local_path(local_path),
            "remote_url": "",
            "local_path": local_path,
            "local_url": _media_file_url(local_path),
            "image_url": _media_file_url(local_path),
            "translations": {},
            "source": "local",
            "source_url": "",
            "wikidata_id": "",
            "license_name": "",
            "author": "",
            "attribution": "",
        }
        return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)

    commons_filename = safe_filename.replace("_", " ") if "." in Path(safe_filename).name else safe_filename
    asset = {
        "kind": kind,
        "entity_type": "",
        "entity_key": "",
        "entity_name": "",
        "commons_filename": commons_filename,
        "remote_url": commons_file_url(commons_filename, width=1400),
        "local_path": "",
        "local_url": "",
        "image_url": commons_file_url(commons_filename, width=1400),
        "translations": {},
        "source": "wikimedia",
        "source_url": "",
        "wikidata_id": "",
        "license_name": "",
        "author": "",
        "attribution": "",
    }
    return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)


def _render_visual_identity_asset(request, asset: dict, *, kind: str, legacy_target: str = ""):
    labels = {
        "flag": _("Bandera"),
        "coat": _("Escudo"),
        "seal": _("Sello"),
        "locator": _("Mapa localizador"),
    }
    commons_filename = str(asset.get("commons_filename") or "")
    remote_url = str(asset.get("remote_url") or "")
    local_path = str(asset.get("local_path") or "")
    image_url = remote_url or str(asset.get("image_url") or "") or str(asset.get("local_url") or "")
    if not image_url and commons_filename:
        image_url = commons_file_url(commons_filename, width=1400)
    encoded_commons = quote((commons_filename or "").replace(" ", "_"), safe="/():,._-")
    commons_url = f"https://commons.wikimedia.org/wiki/File:{encoded_commons}" if encoded_commons else str(asset.get("source_url") or "")

    translations = _identity_translation_rows(asset.get("translations") or {})
    active_language = normalize_language_code(get_language())
    active_translation = _select_identity_translation(translations, active_language)
    description_text = _identity_visual_description_text(active_translation, kind=kind)
    entity_label = str(asset.get("entity_name") or asset.get("entity_key") or "")
    display_filename = commons_filename or Path(local_path).name or Path(legacy_target).name
    context = {
        "kind": kind,
        "kind_label": labels.get(kind, _("Imagen")),
        "description_label": _identity_description_label(kind),
        "asset": asset,
        "entity_label": entity_label,
        "filename": display_filename,
        "local_path": local_path,
        "image_url": image_url,
        "commons_url": commons_url,
        "wikidata_url": f"https://www.wikidata.org/wiki/{asset.get('wikidata_id')}" if asset.get("wikidata_id") else "",
        "source_label": "Wikimedia Commons" if commons_filename or remote_url else _("Archivo local"),
        "active_language": active_language,
        "active_translation": active_translation,
        "description_text": description_text,
        "has_visual_description": bool(description_text),
    }
    return render(request, "ciudades_del_mundo/visual_identity_detail.html", context)


def _identity_translation_rows(translations: dict) -> list[dict]:
    if not isinstance(translations, dict):
        return []
    rows = []
    for language, values in translations.items():
        if not isinstance(values, dict):
            continue
        title = str(values.get("title") or "")
        description = str(values.get("description") or "")
        blazon = str(values.get("blazon") or "")
        if not title and not description and not blazon:
            continue
        rows.append({
            "language": str(language),
            "title": title,
            "description": description,
            "blazon": blazon,
            "source": values.get("source") or "",
            "needs_review": bool(values.get("needs_review")),
        })
    preferred = ["es", "en", "fr", "de", "it", "pt", "ru", "sr", "sr_Latn", "ar"]
    order = {language: index for index, language in enumerate(preferred)}
    return sorted(rows, key=lambda row: (order.get(row["language"], 999), row["language"]))


def _select_identity_translation(rows: list[dict], language_code: str | None) -> dict:
    if not rows:
        return {}
    normalized = normalize_language_code(language_code or "")
    candidates = [normalized, normalized.split("-", 1)[0], "es", "en"]
    for candidate in candidates:
        if not candidate:
            continue
        for row in rows:
            if normalize_language_code(row.get("language") or "") == candidate:
                return row
    return rows[0]


def _identity_visual_description_text(translation: dict, *, kind: str = "") -> str:
    """Return the language-selected heraldic/flag explanation for the ficha.

    Generic Wikidata entity descriptions (for example "province of Spain") are
    useful metadata, but they are not the visual explanation requested on the
    ficha.  Only show text that is clearly curated for the visual identity: a
    blazon, configured/manual text, or test text.  This keeps the Description
    field language-specific without pretending a generic Wikidata description is
    a heraldic or vexillological explanation.
    """
    if not isinstance(translation, dict):
        return ""
    blazon = str(translation.get("blazon") or "").strip()
    if blazon:
        return blazon
    description = str(translation.get("description") or "").strip()
    if not description:
        return ""
    source = str(translation.get("source") or "").lower()
    curated_sources = ("config", "manual", "curated", "test")
    if source.startswith(curated_sources):
        return description
    if kind == "flag" and any(word in description.lower() for word in ("flag", "bandera", "drapeau", "flagge", "bandiera", "застав", "علم")):
        return description
    return ""


def _identity_description_label(kind: str):
    if kind == "flag":
        return _("Descripcion de la bandera")
    if kind in {"coat", "seal"}:
        return _("Descripcion heraldica")
    return _("Descripcion")


def _identity_local_media_path(value: str) -> str:
    """Return a safe MEDIA_ROOT-relative path if the identity target is local."""
    relative_path = str(value or "").replace("\\", "/").strip().lstrip("/")
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    media_prefix = media_url.strip("/") + "/"
    if relative_path.startswith(media_prefix):
        relative_path = relative_path[len(media_prefix):]
    if not relative_path.startswith("visual_assets/"):
        return ""
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root:
        return ""
    absolute = (media_root / relative_path).resolve()
    try:
        absolute.relative_to(media_root.resolve())
    except ValueError:
        return ""
    return relative_path if absolute.is_file() else ""


def _media_file_url(relative_path: str) -> str:
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    if not media_url.endswith("/"):
        media_url += "/"
    return urljoin(media_url, quote(str(relative_path or "").replace("\\", "/"), safe="/():,._-"))


def _identity_commons_filename_for_local_path(local_path: str) -> str:
    if not local_path:
        return ""
    try:
        if not visual_asset_tables_exist():
            return ""
    except Exception:
        return ""
    normalized = str(local_path).replace("\\", "/")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT commons_filename
                  FROM ciudades_del_mundo_visual_asset
                 WHERE REPLACE(local_path, '\\', '/') = %s
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                [normalized],
            )
            row = cursor.fetchone()
    except (OperationalError, ProgrammingError):
        return ""
    return str(row[0] or "") if row else ""


def stats_view(request):
    """Render the API-driven country statistics browser."""
    context = {
        "stats_countries_url": reverse("ciudades_del_mundo:api_country_summary"),
    }
    return render(request, "ciudades_del_mundo/stats.html", context)


def stats_data(request):
    """Return aggregate statistical chart payloads."""
    return JsonResponse({"charts": _stats_chart_payloads()})


def _stats_chart_payloads() -> dict[str, dict]:
    country_populations = [
        {"display_label": row["label"], "population": row["population"]}
        for row in _admin_root_population_rows()
    ]
    countries = list(
        _visible_admin_areas().values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("-population")
    )
    _apply_country_labels(countries, _admin_country_label_map())
    admin_levels = list(
        _visible_admin_areas().values("level").annotate(total=Count("id")).order_by("level")
    )
    nuevo_levels = list(
        NuevoAdminArea.objects.values("level").annotate(total=Count("id")).order_by("level")
    )
    merge_statuses = list(
        _visible_admin_areas().values("city_merge_status")
        .annotate(total=Count("id"))
        .order_by("city_merge_status")
    )
    return {
        "country_population": {
            "type": "bar",
            "items": _bar_rows(country_populations, "display_label", "population", limit=15),
        },
        "country_area": {
            "type": "bar",
            "items": _bar_rows(countries, "display_label", "total", limit=15),
        },
        "admin_levels": {
            "type": "bar",
            "items": _bar_rows(admin_levels, "level", "total", limit=8, prefix="L"),
        },
        "nuevo_levels": {
            "type": "bar",
            "items": _bar_rows(nuevo_levels, "level", "total", limit=8, prefix="L"),
        },
        "merge_status": {
            "type": "bar",
            "items": _merge_status_rows(merge_statuses),
        },
    }


def data_delete(request):
    """Delete scraped or derived data after explicit confirmation."""
    if request.method == "POST":
        kind = request.POST.get("kind")
        target = (request.POST.get("target") or "").strip()
        confirmed = request.POST.get("confirm") == "on"

        if not confirmed:
            messages.error(request, _("Marca la confirmacion antes de borrar datos."))
        elif not target:
            messages.error(request, _("Selecciona un objetivo para borrar."))
        elif kind == "admin-country":
            deleted, _ = AdminArea.objects.filter(country_code=target).delete()
            messages.success(
                request,
                _("Borradas %(deleted)s filas AdminArea para '%(target)s'.")
                % {"deleted": deleted, "target": target},
            )
        elif kind == "nuevo-country":
            deleted, _ = NuevoAdminArea.objects.filter(country_code=target).delete()
            messages.success(
                request,
                _("Borradas %(deleted)s filas NuevoAdminArea para '%(target)s'.")
                % {"deleted": deleted, "target": target},
            )
        else:
            messages.error(request, _("Tipo de borrado no soportado."))
        return redirect("ciudades_del_mundo:data_delete")

    context = {
        "admin_countries": _admin_country_options(),
        "nuevo_countries": _nuevo_country_options(),
    }
    return render(request, "ciudades_del_mundo/data_delete.html", context)


def task_list(request):
    """Render background task history."""
    return render(
        request,
        "ciudades_del_mundo/task_list.html",
        {"task_table_url": reverse("ciudades_del_mundo:task_table")},
    )


def task_table(request):
    """Render the asynchronous background task table partial."""
    tasks = _filtered_tasks(request, limit=200)
    return render(
        request,
        "ciudades_del_mundo/partials/task_table.html",
        _task_table_context(tasks, page_size=_page_size(request, default=25), compact=False),
    )


def task_detail(request, task_id):
    """Render one background task, auto-refreshing while active."""
    task = task_manager.get(task_id)
    if not task:
        raise Http404(_("Tarea no encontrada."))
    task_code = _task_error_info(task)
    return render(
        request,
        "ciudades_del_mundo/task_detail.html",
        {
            "task": task,
            "task_output": task_manager.output_text(task),
            "task_output_offset": task_manager.output_offset(task),
            "task_code": task_code,
            "task_status_code": task_code.get("code", ""),
            "task_status_code_description": task_code.get("description", ""),
            "task_status_code_severity": task_code.get("severity", ""),
        },
    )


def task_cancel(request, task_id):
    """Cancel an active background task."""
    wants_json = _wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Método no permitido.")}, status=405)
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    task = task_manager.cancel(task_id)
    if not task:
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Tarea no encontrada.")}, status=404)
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    message = _("Cancelacion solicitada para '%(label)s'.") % {"label": task.label}
    if wants_json:
        error_info = _task_error_info(task)
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "id": task.id,
                "label": task.label,
                "status": task.status,
                "is_active": task.is_active,
                "returncode": task.returncode,
                "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
                "status_url": reverse("ciudades_del_mundo:task_status", kwargs={"task_id": task.id}),
                "error_code": error_info["code"],
                "error_severity": error_info.get("severity", ""),
                "error_message": error_info.get("message", ""),
                "error_description": error_info["description"],
            }
        )
    messages.info(request, message)
    return redirect("ciudades_del_mundo:task_detail", task_id=task_id)


def _visible_admin_areas():
    return AdminArea.objects.exclude(city_merge_status=HIDDEN_CITY_MERGE_STATUS)


def _group_source_admin_areas():
    return _visible_admin_areas().filter(city_merge_status__in=GROUP_SOURCE_CITY_MERGE_STATUSES)


def _filtered_admin_areas(request):
    areas = _visible_admin_areas().select_related("parent", "most_populate_city").order_by("country_code", "level", "name")
    q = (request.GET.get("q") or "").strip()
    countries = _clean_list(request.GET.getlist("country"))
    levels = _clean_list(request.GET.getlist("level"))
    entity_types = _clean_list(request.GET.getlist("entity_type"))
    merge_statuses = _clean_list(request.GET.getlist("city_merge_status"))
    parent = (request.GET.get("parent") or "").strip()

    if q:
        areas = areas.filter(name__icontains=q)
    if countries:
        areas = areas.filter(country_code__in=countries)
    if levels:
        areas = areas.filter(level__in=levels)
    if entity_types:
        areas = areas.filter(entity_type__in=entity_types)
    if merge_statuses:
        areas = areas.filter(city_merge_status__in=merge_statuses)
    if parent:
        areas = areas.filter(parent__name__icontains=parent)
    areas = _apply_numeric_range(areas, "pop_latest", request.GET.get("min_pop"), request.GET.get("max_pop"))
    areas = _apply_numeric_range(areas, "area_km2", request.GET.get("min_area"), request.GET.get("max_area"))

    sort = request.GET.get("sort") or "country_level_name"
    sort_map = {
        "country_level_name": ("country_code", "level", "name"),
        "name": ("name",),
        "population_desc": ("-pop_latest", "name"),
        "population_asc": ("pop_latest", "name"),
        "area_desc": ("-area_km2", "name"),
        "area_asc": ("area_km2", "name"),
        "updated_desc": ("-updated_at", "name"),
    }
    return areas.order_by(*sort_map.get(sort, sort_map["country_level_name"]))


def _filtered_nuevo_areas(request, root: NuevoAdminArea):
    areas = (
        NuevoAdminArea.objects.filter(country_code=root.country_code)
        .select_related("parent", "most_populate_city", "depends_on")
        .prefetch_related("capitals")
        .order_by("level", "parent__name", "name")
    )
    q = (request.GET.get("q") or "").strip()
    levels = _clean_list(request.GET.getlist("level"))
    entity_types = _clean_list(request.GET.getlist("entity_type"))
    statuses = _clean_list(request.GET.getlist("province_status"))
    parents = _clean_list(request.GET.getlist("parent"))
    has_representatives = request.GET.get("has_representatives")

    if q:
        areas = areas.filter(name__icontains=q)
    if levels:
        areas = areas.filter(level__in=levels)
    if entity_types:
        areas = areas.filter(entity_type__in=entity_types)
    if statuses:
        areas = areas.filter(province_status__in=statuses)
    if parents:
        areas = areas.filter(parent_id__in=parents)
    if has_representatives == "yes":
        areas = areas.exclude(representatives__isnull=True)
    elif has_representatives == "no":
        areas = areas.filter(representatives__isnull=True)
    areas = _apply_numeric_range(areas, "pop_latest", request.GET.get("min_pop"), request.GET.get("max_pop"))
    areas = _apply_numeric_range(areas, "area_km2", request.GET.get("min_area"), request.GET.get("max_area"))

    sort = request.GET.get("sort") or "level_name"
    sort_map = {
        "level_name": ("level", "parent__name", "name"),
        "name": ("name",),
        "population_desc": ("-pop_latest", "name"),
        "population_asc": ("pop_latest", "name"),
        "area_desc": ("-area_km2", "name"),
        "area_asc": ("area_km2", "name"),
        "seats_desc": ("-representatives", "name"),
    }
    return areas.order_by(*sort_map.get(sort, sort_map["level_name"]))


def _clean_list(values):
    return [value for value in values if value not in (None, "")]


def _apply_numeric_range(queryset, field: str, minimum, maximum):
    try:
        if minimum not in (None, ""):
            queryset = queryset.filter(**{f"{field}__gte": minimum})
        if maximum not in (None, ""):
            queryset = queryset.filter(**{f"{field}__lte": maximum})
    except (TypeError, ValueError):
        return queryset
    return queryset


def _page_size(request, *, default: int, max_value: int = 200) -> int:
    try:
        value = int(request.GET.get("page_size") or default)
    except (TypeError, ValueError):
        return default
    return max(10, min(value, max_value))


def _country_browser_page_size(request) -> int:
    return _page_size(
        request,
        default=COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
        max_value=COUNTRY_BROWSER_MAX_PAGE_SIZE,
    )


def _new_country_option_page_size(request) -> int:
    return _page_size(
        request,
        default=NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
        max_value=NEW_COUNTRY_OPTION_MAX_PAGE_SIZE,
    )


def _country_browser_page(rows, page: int | str | None, page_size: int):
    paginator = Paginator(rows, max(1, min(int(page_size or COUNTRY_BROWSER_DEFAULT_PAGE_SIZE), COUNTRY_BROWSER_MAX_PAGE_SIZE)))
    return paginator.get_page(page)


def _pagination_payload(page_obj) -> dict:
    return {
        "page": page_obj.number,
        "page_size": page_obj.paginator.per_page,
        "total": page_obj.paginator.count,
        "num_pages": page_obj.paginator.num_pages,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
    }


def _querystring_without_page(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"{encoded}&" if encoded else ""


def _admin_sort_choices():
    return [
        ("country_level_name", _("Pais, nivel y nombre")),
        ("name", _("Nombre")),
        ("population_desc", _("Poblacion descendente")),
        ("population_asc", _("Poblacion ascendente")),
        ("area_desc", _("Area descendente")),
        ("area_asc", _("Area ascendente")),
        ("updated_desc", _("Actualizacion reciente")),
    ]


def _nuevo_sort_choices():
    return [
        ("level_name", _("Nivel y nombre")),
        ("name", _("Nombre")),
        ("population_desc", _("Poblacion descendente")),
        ("population_asc", _("Poblacion ascendente")),
        ("area_desc", _("Area descendente")),
        ("area_asc", _("Area ascendente")),
        ("seats_desc", _("Escanos descendente")),
    ]


def _area_search_query(area) -> str:
    parts = [area.name]
    parent = getattr(area, "parent", None)
    if parent:
        parts.append(parent.name)
    country_code = getattr(area, "country_code", None)
    if country_code:
        parts.append(country_code)
    return ", ".join(str(part) for part in parts if part)


def _area_display_name(area) -> str:
    dynamic = dynamic_area_name(area, get_language())
    if dynamic:
        return dynamic
    if getattr(area, "level", None) == 0:
        country_dynamic = dynamic_country_name(getattr(area, "country_code", ""), get_language())
        if country_dynamic:
            return country_dynamic
    return _display_name(
        getattr(area, "name", ""),
        getattr(area, "name", ""),
        country_code=getattr(area, "country_code", ""),
    )


def _area_capital_display_names(area, language_code: str | None = None) -> list[str]:
    capitals = getattr(area, "capitals", None)
    if not capitals:
        return []

    overrides = getattr(area, "capital_names_by_language", None) or {}
    language = (language_code or "").split("-", 1)[0]
    language_overrides = overrides.get(language, {}) if isinstance(overrides, dict) else {}
    names = []
    seen = set()
    for capital in capitals.all():
        name = language_overrides.get(capital.id) or _area_display_name(capital)
        marker = str(name or "").casefold()
        if marker and marker not in seen:
            seen.add(marker)
            names.append(name)
    return names


def _area_related_places(area, language_code: str | None = None) -> list[dict[str, str]]:
    places: list[dict[str, str]] = []

    def add(kind: str, name: str | None, query: str | None = None) -> None:
        name = (name or "").strip()
        query = (query or name).strip()
        if not name or not query:
            return
        key = (kind, query.lower())
        if any((place["kind"], place["query"].lower()) == key for place in places):
            return
        places.append({"kind": kind, "name": name, "query": query})

    country_code = getattr(area, "country_code", "")
    country_root = _area_country_root(area)
    if country_root:
        add(_("Pais"), _area_display_name(country_root), country_root.name)
    elif country_code:
        add(_("Pais"), str(country_code), str(country_code))

    parent = getattr(area, "parent", None)
    if parent:
        add(_("Region padre"), _area_display_name(parent), _area_search_query(parent))

    for capital_name in _area_capital_display_names(area, language_code):
        add(_("Capital registrada"), capital_name, f"{capital_name}, {_area_display_name(area)}")

    most_populated = getattr(area, "most_populate_city", None)
    if most_populated:
        add(_("Ciudad mayor registrada"), _area_display_name(most_populated), f"{most_populated.name}, {area.name}")

    return places


def _area_country_root(area):
    if getattr(area, "level", None) == 0:
        return area
    model = area.__class__
    country_code = getattr(area, "country_code", None)
    if not country_code:
        return None
    if not hasattr(model, "objects"):
        return None
    if model is NuevoAdminArea:
        return (
            NuevoAdminArea.objects.filter(country_code=country_code, parent__isnull=True)
            .order_by("name")
            .first()
        )
    if model is AdminArea:
        return _canonical_country_area(country_code)
    return model.objects.filter(country_code=country_code, level=0).order_by("name").first()


def _canonical_country_area(country_code: str) -> AdminArea | None:
    roots = _visible_admin_areas().filter(country_code=country_code, level=0, parent__isnull=True)

    # Prefer the explicit country row over other level-0 rows.  Some countries
    # (France is the common case) scrape regions at level 0 from another page.
    # If we only accepted a single root, the browser treated the actual country
    # as another child row and rendered "France > France".
    preferred = roots.filter(code=country_code).order_by("name").first()
    if preferred:
        return preferred

    infosection = roots.filter(annotations__icontains="CityPopulation section: infosection").order_by("name").first()
    if infosection:
        return infosection

    wikidata_id = _wikidata_country_id(country_code)
    if wikidata_id:
        by_wikidata = roots.filter(data_wd=wikidata_id).order_by("name").first()
        if by_wikidata:
            return by_wikidata

    if roots.count() == 1:
        return roots.first()
    return None


def _country_top_level_areas(country_code: str, root: AdminArea | None = None):
    if root:
        children = _visible_admin_areas().filter(parent=root)
        first_child_level = children.values_list("level", flat=True).order_by("level").first()
        if first_child_level is not None:
            return children.filter(level=first_child_level).order_by("name")
        next_level = (
            _visible_admin_areas().filter(country_code=country_code)
            .exclude(id=root.id)
            .values_list("level", flat=True)
            .order_by("level")
            .first()
        )
        if next_level is not None:
            return _visible_admin_areas().filter(country_code=country_code, level=next_level).exclude(id=root.id).order_by("name")
        return AdminArea.objects.none()
    return _visible_admin_areas().filter(country_code=country_code, level=0, parent__isnull=True).order_by("name")


def _country_total(country_code: str, field: str, root: AdminArea | None):
    if root:
        return getattr(root, field)
    return _country_top_level_areas(country_code, root).aggregate(total=Sum(field))["total"]


def _admin_country_summary_records() -> list[dict]:
    records = []
    codes = _visible_admin_areas().values_list("country_code", flat=True).distinct().order_by("country_code")
    for country_code in codes:
        root = _canonical_country_area(country_code)
        population = _country_total(country_code, "pop_latest", root)
        if not population:
            continue
        area = _country_total(country_code, "area_km2", root)
        source_name = root.name if root else ""
        records.append(
            {
                "code": country_code,
                "label": _display_name(source_name, country_code, country_code=country_code),
                "population": int(population or 0),
                "area_km2": _number_or_none(area),
                "root_id": root.id if root else "",
                "synthetic": root is None,
            }
        )
    return sorted(records, key=lambda row: row["population"], reverse=True)


def _admin_root_population_rows(
    *,
    detail_route: str = "ciudades_del_mundo:dashboard_country_detail",
    include_visual_assets: bool = False,
) -> list[dict]:
    rows = []
    for row in _admin_country_summary_records():
        item = {
            "code": row["code"],
            "label": row["label"],
            "population": row["population"],
            "area_km2": row["area_km2"],
            "wikidata_id": _wikidata_country_id(row["code"]),
            "detail_url": reverse(
                detail_route,
                kwargs={"country_code": row["code"]},
            ),
        }
        if include_visual_assets:
            visual_assets = _visual_assets_payload("country", row["code"], include_fallbacks=False)
            item["visual_assets"] = visual_assets
            item["flag_asset"] = visual_assets.get("flag", {})
            item["seal_asset"] = visual_assets.get("seal", {})
            item["coat_asset"] = visual_assets.get("coat", {}) or item["seal_asset"]
        rows.append(item)
    return rows


def _visual_assets_payload(entity_type: str, entity_key: str, *, include_fallbacks: bool = True) -> dict:
    try:
        return get_visual_assets_for_entity(entity_type, str(entity_key or ""), include_fallbacks=include_fallbacks)
    except (OperationalError, ProgrammingError):
        return {}
    except Exception:
        return {}


def _visual_assets_ready() -> bool:
    try:
        return visual_asset_tables_exist()
    except Exception:
        return False


def _country_detail_payload(
    country_code: str,
    selected_level: int | str | None = None,
    *,
    page: int | str | None = None,
    page_size: int = COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
) -> dict:
    root = _canonical_country_area(country_code)
    country_record = next(
        (row for row in _admin_country_summary_records() if row["code"] == country_code),
        {
            "code": country_code,
            "label": _display_name("", country_code, country_code=country_code),
            "population": 0,
            "area_km2": None,
        },
    )
    available_levels = _country_available_levels(country_code, root)
    selected_level, selected_entity_types, selected_filter = _resolve_country_level_selection(
        selected_level,
        available_levels,
    )

    population_total = country_record["population"]
    area_total = country_record["area_km2"] or 0
    row_queryset = _country_level_rows_queryset(
        country_code,
        root,
        selected_level,
        selected_entity_types,
    )
    page_obj = _country_browser_page(row_queryset, page, page_size)
    first_order = list(page_obj.object_list)
    rows = _country_table_rows_from_records(first_order, country_code, population_total, area_total)
    subdivision_count = _visible_admin_areas().filter(country_code=country_code).count() - (1 if root else 0)
    visual_assets = _visual_assets_payload("country", country_code, include_fallbacks=False)

    return {
        "country": {
            "id": root.id if root else "",
            "code": country_code,
            "name": country_record["label"],
            "official_name": country_record["label"],
            "entity_type": _entity_type_label(root.entity_type, country_code=country_code) if root else "",
            "level": root.level if root else None,
            "parent": "",
            "population": population_total,
            "area_km2": area_total,
            "density": _density(population_total, area_total),
            "capital": _capital_names_for_area(root),
            "subdivision_count": max(0, subdivision_count),
            "type_summary": _country_type_summary(country_code, root),
            "wikidata_query": root.name if root else country_record["label"],
            "wikidata_id": _wikidata_country_id(country_code),
            "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "admin", "area_id": root.id}) if root else "",
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": rows,
            "pagination": _pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _donut_payload(first_order, "area_km2", area_total),
            "cards": _first_order_cards(country_code, root, first_order, population_total, area_total),
            "child_groups": _country_first_order_child_groups(country_code, root, first_order, population_total, area_total),
            "pagination": _pagination_payload(page_obj),
        },
    }


def _country_first_order_areas_for_selection(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[AdminArea]:
    return list(_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types))


def _country_level_rows_queryset(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
):
    if selected_level is None:
        return _visible_admin_areas().none()
    rows = (
        _visible_admin_areas()
        .filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name", "code", "id")
    )
    if selected_entity_types:
        rows = rows.filter(entity_type__in=selected_entity_types)
    if root:
        rows = rows.exclude(id=root.id)
    return rows



def _country_first_order_child_groups(
    country_code: str,
    root: AdminArea | None,
    children: list[AdminArea],
    population_total,
    area_total,
) -> list[dict]:
    """Return country-level child panels split by real child level.

    The first country view uses a synthetic "first order" list that can mix
    levels when CityPopulation exposes branches unevenly.  Grouping here keeps
    Italy-like cases readable: one box per child level, while the country info
    panel spans the full height in the frontend.
    """
    if not children:
        return []
    grouped: dict[int, list[AdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _admin_area_child_group_label(country_code, level),
            "children": _admin_area_child_rows(rows, population_total, area_total),
        }
        for level, rows in sorted(grouped.items())
    ]

def _admin_area_detail_payload(area: AdminArea) -> dict:
    children = _admin_area_browser_children(area)
    return {
        "area": _admin_area_identity_payload(area, children),
        "children": _admin_area_child_rows(children, area.pop_latest, area.area_km2),
        "child_groups": _admin_area_child_groups(area, children),
    }


def _nuevo_country_root_or_404(country_id: str) -> NuevoAdminArea:
    country_id = str(country_id or "").strip()
    if not country_id:
        raise Http404(_("No existe NuevoAdminArea '%(area_id)s'.") % {"area_id": country_id})
    root = (
        NuevoAdminArea.objects.select_related("parent", "most_populate_city")
        .prefetch_related("capitals")
        .filter(id__iexact=country_id, parent__isnull=True)
        .order_by("name")
        .first()
    )
    if root is None:
        root = (
            NuevoAdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(country_code__iexact=country_id, parent__isnull=True)
            .order_by("name")
            .first()
        )
    if root is None:
        raise Http404(_("No existe NuevoAdminArea '%(area_id)s'.") % {"area_id": country_id})
    return root


def _nuevo_country_detail_payload(
    root: NuevoAdminArea,
    selected_level: int | str | None = None,
    *,
    page: int | str | None = None,
    page_size: int = COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
) -> dict:
    country_code = root.country_code
    population_total = _nuevo_country_total(country_code, "pop_latest", root) or 0
    area_total = _number_or_none(_nuevo_country_total(country_code, "area_km2", root)) or 0
    available_levels = _nuevo_country_available_levels(country_code, root)
    selected_level, selected_entity_types, selected_filter = _resolve_country_level_selection(
        selected_level,
        available_levels,
    )
    row_queryset = _nuevo_country_level_rows_queryset(
        country_code,
        root,
        selected_level,
        selected_entity_types,
    )
    page_obj = _country_browser_page(row_queryset, page, page_size)
    first_order = list(page_obj.object_list)
    visual_assets = _new_country_visual_assets_for_root(root, _new_country_source_code_for_built_code(country_code))

    return {
        "country": {
            "id": root.id,
            "code": country_code,
            "name": _area_display_name(root),
            "official_name": _area_display_name(root),
            "entity_type": _entity_type_label(root.entity_type, country_code=country_code),
            "level": root.level,
            "parent": "",
            "population": int(population_total or 0),
            "area_km2": area_total,
            "density": _number_or_none(root.density) or _density(population_total, area_total),
            "capital": _capital_names_for_area(root),
            "subdivision_count": max(0, NuevoAdminArea.objects.filter(country_code=country_code).count() - 1),
            "type_summary": _nuevo_country_type_summary(country_code, root),
            "wikidata_query": root.name,
            "wikidata_id": "",
            "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "derived", "area_id": root.id}),
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": _nuevo_country_table_rows_from_records(first_order, country_code, population_total, area_total),
            "pagination": _pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _donut_payload(first_order, "area_km2", area_total),
            "cards": _nuevo_first_order_cards(country_code, root, first_order, population_total, area_total),
            "child_groups": _nuevo_country_first_order_child_groups(
                country_code,
                root,
                first_order,
                population_total,
                area_total,
            ),
            "pagination": _pagination_payload(page_obj),
        },
    }


def _nuevo_first_order_areas_for_selection(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[NuevoAdminArea]:
    return list(_nuevo_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types))


def _nuevo_country_level_rows_queryset(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
):
    if selected_level is None:
        return NuevoAdminArea.objects.none()
    rows = (
        NuevoAdminArea.objects.filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name", "code", "id")
    )
    if selected_entity_types:
        rows = rows.filter(entity_type__in=selected_entity_types)
    if root:
        rows = rows.exclude(id=root.id)
    return rows


def _new_country_config_detail_payload(
    country: DerivedCountry,
    rows: list[dict],
    selected_level: int | str | None = None,
    *,
    available_levels: list[dict] | None = None,
    page: int | str | None = None,
    page_size: int = COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
) -> dict:
    """Render an unbuilt DerivedCountry config tree as the country-browser payload."""
    tree_rows = [row for row in rows if row.get("is_config_row") or row.get("is_assigned_subdivision")]
    config_rows = [row for row in tree_rows if row.get("is_config_row")]
    source_code = _new_country_config_rows_source_code(country, config_rows)
    built_code = _new_country_config_rows_built_code(country, config_rows)
    area_total, population_total = _new_country_detail_export_totals(country, config_rows)
    area_total_value = _number_or_none(area_total) or 0
    population_total_value = _new_country_json_number(population_total) or 0
    available_levels = available_levels or _new_country_config_available_levels(tree_rows, source_code)
    selected_level, selected_entity_types, selected_filter = _resolve_country_level_selection(
        selected_level,
        available_levels,
    )
    rows_by_tree_id = _new_country_config_rows_by_tree_id(tree_rows)
    children_by_parent = _new_country_config_children_by_parent(tree_rows)
    first_order_rows = _new_country_config_order_rows_for_selection(
        tree_rows,
        config_rows,
        selected_level,
        selected_entity_types,
    )
    page_obj = _country_browser_page(first_order_rows, page, page_size)
    first_order = list(page_obj.object_list)
    table_rows = [
        _new_country_config_browser_row(
            row,
            rows_by_tree_id,
            population_total_value,
            area_total_value,
            source_code,
            country_slug=country.slug,
            children_by_parent=children_by_parent,
        )
        for row in first_order
    ]
    visual_assets = {}
    country_entity_type = _("Pais nuevo")
    return {
        "country": {
            "id": country.slug,
            "code": built_code,
            "name": country.name,
            "official_name": _new_country_official_name(country_entity_type, country.name),
            "entity_type": country_entity_type,
            "level": 0,
            "parent": "",
            "population": population_total_value,
            "area_km2": area_total_value,
            "density": _density(population_total_value, area_total_value),
            "capital": _new_country_config_country_capital_names(config_rows),
            "subdivision_count": _new_country_config_country_subdivision_count(config_rows),
            "type_summary": _new_country_config_type_summary(tree_rows, source_code),
            "wikidata_query": country.name,
            "wikidata_id": "",
            "map_url": "",
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": table_rows,
            "pagination": _pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _new_country_config_donut_payload(first_order, "entity_population_value", population_total_value),
            "area_chart": _new_country_config_donut_payload(first_order, "entity_area_value", area_total_value),
            "cards": _new_country_config_first_order_cards(
                first_order,
                rows_by_tree_id,
                population_total_value,
                area_total_value,
                source_code,
                country.slug,
                children_by_parent,
            ),
            "child_groups": _new_country_config_first_order_child_groups(
                first_order,
                rows_by_tree_id,
                population_total_value,
                area_total_value,
                source_code,
                country.slug,
                children_by_parent,
            ),
            "pagination": _pagination_payload(page_obj),
        },
    }


def _new_country_config_rows_source_code(country: DerivedCountry, rows: list[dict]) -> str:
    source_code = _normalize_group_country_key(country.source_country_code)
    if source_code:
        return source_code
    for row in rows:
        source_code = _normalize_group_country_key(row.get("source_code"))
        if source_code:
            return source_code
    return ""


def _new_country_config_rows_built_code(country: DerivedCountry, rows: list[dict]) -> str:
    for row in rows:
        built_code = _normalize_group_country_key(row.get("built_code"))
        if built_code:
            return built_code
    return _normalize_group_country_key(country.slug)


def _new_country_json_number(value):
    value = _new_country_export_decimal(value)
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _new_country_config_rows_by_tree_id(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}


def _new_country_config_available_levels(rows: list[dict], source_code: str) -> list[dict]:
    grouped: dict[int, Counter[str]] = {}
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        entity_type = str(row.get("entity_type") or "").strip()
        grouped.setdefault(level, Counter())[entity_type] += 1
    return _new_country_config_available_levels_from_counts(grouped, source_code)


def _new_country_config_available_levels_for_configs(
    configs: list[DerivedCountryConfig],
) -> list[dict]:
    source_code = ""
    for config in configs:
        source_code = _new_country_config_source_code(config)
        if source_code:
            break
    return _new_country_config_available_levels_from_counts(
        _new_country_config_level_type_counts(configs),
        source_code,
    )


def _new_country_config_available_levels_from_counts(
    grouped: dict[int, Counter[str]],
    source_code: str,
) -> list[dict]:
    result = []
    for level, type_counts in sorted(grouped.items()):
        level_total = sum(type_counts.values())
        if level_total <= 0:
            continue
        entity_types = [
            _entity_type_label(entity_type, country_code=source_code) or _("Sin tipo")
            for entity_type in sorted(type_counts)
        ]
        entity_type_label = " / ".join(entity_types[:3])
        result.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": level},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return result


def _new_country_config_level_type_counts(
    configs: list[DerivedCountryConfig],
) -> dict[int, Counter[str]]:
    grouped: dict[int, Counter[str]] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}

    def add(level: int, entity_type: str, count: int = 1) -> None:
        try:
            clean_level = int(level or 0)
        except (TypeError, ValueError):
            return
        if clean_level <= 0 or count <= 0:
            return
        grouped.setdefault(clean_level, Counter())[str(entity_type or "").strip()] += int(count)

    for config in configs:
        data = _new_country_config_toml(config)
        entity = _new_country_config_first_entity(data)
        source_code = _new_country_config_source_code(config)
        config_level = _new_country_config_level(config)
        entity_type = str(entity.get("entity_type") or "").strip()
        add(config_level, entity_type)

        display_level = max(1, int(config_level or 1) + 1)
        source_ids = _new_country_config_selected_source_ids(data, entity)
        for direct_entity_type, total in _new_country_config_source_id_type_counts(source_ids).items():
            add(display_level, direct_entity_type, total)

        seen_derived: set[tuple[str, str]] = set()
        for selection in _new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code=source_code,
        ):
            record_country_code = _normalize_group_country_key(selection.get("country_code") or source_code)
            record_key = str(selection.get("key") or "").strip()
            if not record_country_code or not record_key:
                continue
            lookup_key = (record_country_code, record_key.casefold())
            if lookup_key in seen_derived:
                continue
            seen_derived.add(lookup_key)
            record = _derived_subdivision_source_record_for_key(record_country_code, record_key)
            if record is None:
                continue
            record_data = _derived_subdivision_record_toml(record)
            try:
                assigned_level = int(selection.get("level") or display_level)
            except (TypeError, ValueError):
                assigned_level = display_level
            assigned_level = max(display_level, max(1, min(9, assigned_level)))
            add(assigned_level, record_data.get("entity_type") or record.entity_type)
            source_count = _new_country_config_derived_source_row_count(
                record,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
            add(assigned_level + 1, "", source_count)
    return grouped


def _new_country_config_source_id_type_counts(source_ids: list[str]) -> Counter[str]:
    clean_ids = [str(value).strip() for value in source_ids if str(value or "").strip()]
    if not clean_ids:
        return Counter()
    try:
        return Counter(
            {
                str(row["entity_type"] or ""): int(row["total"] or 0)
                for row in _group_source_admin_areas()
                .filter(id__in=clean_ids)
                .values("entity_type")
                .annotate(total=Count("id"))
            }
        )
    except (OperationalError, ProgrammingError, ValueError):
        return Counter({"": len(clean_ids)})


def _new_country_config_derived_source_row_count(
    record: DerivedSubdivision,
    *,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> int:
    return len(
        _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    )


def _new_country_config_type_summary(rows: list[dict], source_code: str) -> list[dict]:
    counts: Counter[tuple[int, str]] = Counter()
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        counts[(level, str(row.get("entity_type") or ""))] += 1
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "entity_type": _entity_type_label(entity_type, country_code=source_code) or _("Sin tipo"),
            "total": total,
        }
        for (level, entity_type), total in sorted(counts.items())
    ]


def _new_country_config_country_capital_names(config_rows: list[dict]) -> list[str]:
    first_order = _new_country_config_first_order_rows(config_rows)
    names = _new_country_config_unique_capital_names(first_order)
    if names:
        return names
    return _new_country_config_unique_capital_names(config_rows)


def _new_country_config_unique_capital_names(rows: list[dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in _new_country_config_row_capital_names(row):
            marker = name.casefold()
            if marker and marker not in seen:
                seen.add(marker)
                names.append(name)
    return names


def _new_country_config_country_subdivision_count(config_rows: list[dict]) -> int:
    context = _new_country_export_legal_context()
    source_unit_ids: set[str] = set()
    for row in config_rows:
        source_unit_ids.update(_new_country_export_legal_source_unit_ids(row, context))
    return len(source_unit_ids) or len(config_rows)


def _new_country_config_first_order_rows(rows: list[dict]) -> list[dict]:
    top_rows = [row for row in rows if not str(row.get("tree_parent") or "")]
    if top_rows:
        return sorted(top_rows, key=lambda row: str(row.get("entity_display_name") or "").casefold())
    levels = sorted(
        {
            int(row.get("level") or 0)
            for row in rows
            if str(row.get("level") or "").isdigit() and int(row.get("level") or 0) > 0
        }
    )
    if not levels:
        return []
    first_level = levels[0]
    return sorted(
        [row for row in rows if int(row.get("level") or 0) == first_level],
        key=lambda row: str(row.get("entity_display_name") or "").casefold(),
    )


def _new_country_config_order_rows_for_selection(
    tree_rows: list[dict],
    config_rows: list[dict],
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[dict]:
    if selected_level is None:
        return _new_country_config_first_order_rows(config_rows)
    rows = [
        row
        for row in tree_rows
        if int(row.get("level") or 0) == selected_level
        and (
            not selected_entity_types
            or str(row.get("entity_type") or "") in selected_entity_types
        )
    ]
    if not rows:
        return _new_country_config_first_order_rows(config_rows)
    return sorted(rows, key=lambda row: str(row.get("entity_display_name") or "").casefold())


def _new_country_config_browser_row(
    row: dict,
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    *,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> dict:
    tree_id = str(row.get("tree_id") or "")
    entity_node = row.get("entity_node")
    parent = rows_by_tree_id.get(str(row.get("tree_parent") or ""))
    parent_node = parent.get("entity_node") if parent else None
    population = _new_country_json_number(row.get("entity_population_value"))
    area_km2 = _number_or_none(row.get("entity_area_value"))
    density = _number_or_none(row.get("entity_density_value")) or _density(population, area_km2)
    config_child_count = len((children_by_parent or {}).get(tree_id, []))
    dynamic_config_child_count = config_child_count or int(row.get("assigned_subdivision_child_count") or 0)
    if isinstance(entity_node, AdminArea):
        try:
            source_child_count = int(row.get("source_child_count"))
        except (TypeError, ValueError):
            source_child_count = _admin_area_browser_child_count(entity_node)
    else:
        source_child_count = 0
    new_area_child_count = _nuevo_area_browser_child_count(entity_node) if isinstance(entity_node, NuevoAdminArea) else 0
    child_count = dynamic_config_child_count or source_child_count or new_area_child_count
    detail_url = ""
    if isinstance(entity_node, NuevoAdminArea) and new_area_child_count:
        detail_url = _nuevo_area_detail_url(entity_node)
    elif isinstance(entity_node, AdminArea) and source_child_count:
        detail_url = _admin_area_detail_url(entity_node)
    if not detail_url and country_slug and (dynamic_config_child_count or row.get("has_assigned_subdivision_children")):
        detail_url = _new_country_config_node_detail_url(country_slug, tree_id)
    parent_detail_url = _nuevo_area_detail_url(parent_node) if isinstance(parent_node, NuevoAdminArea) else ""
    if not parent_detail_url and country_slug and parent:
        parent_tree_id = str(parent.get("tree_id") or "")
        if (children_by_parent or {}).get(parent_tree_id):
            parent_detail_url = _new_country_config_node_detail_url(country_slug, parent_tree_id)
    entity_type_label = _entity_type_label(
        str(row.get("entity_type") or ""),
        country_code=_normalize_group_country_key(
            getattr(entity_node, "country_code", "")
            or row.get("source_code")
            or source_code
        ),
    )
    name = str(row.get("entity_display_name") or row.get("display_name") or "")
    return {
        "id": tree_id,
        "code": str(row.get("display_code") or ""),
        "level": int(row.get("level") or 0),
        "detail_url": detail_url,
        "parent_id": str(parent.get("tree_id") or "") if parent else "",
        "parent_level": int(parent.get("level") or 0) if parent else None,
        "parent_detail_url": parent_detail_url,
        "name": name,
        "official_name": str(row.get("official_name") or "") or _new_country_official_name(entity_type_label, name),
        "area_km2": area_km2,
        "population": population,
        "density": density,
        "population_percent": _ratio_percent(population, population_total),
        "area_percent": _ratio_percent(area_km2, area_total),
        "parent": _new_country_config_parent_name(parent),
        "entity_type": entity_type_label,
        "capital": _new_country_config_row_capital_names(row),
        "subdivision_count": child_count,
        "annotations": "",
        "child_count": child_count,
    }


def _new_country_official_name(entity_type: str, base_name: str) -> str:
    entity_type = str(entity_type or "").strip()
    base_name = str(base_name or "").strip()
    if not entity_type:
        return base_name
    if not base_name:
        return entity_type
    entity_key = _group_name_match_key(entity_type)
    base_key = _group_name_match_key(base_name)
    if entity_key and (base_key == entity_key or base_key.startswith(f"{entity_key} ")):
        return base_name
    return f"{entity_type} {base_name}"


def _new_country_config_row_capital_names(row: dict) -> list[str]:
    text = _new_country_export_capitals(row)
    if not text:
        return []
    names = []
    seen = set()
    for raw_name in str(text).split(" | "):
        name = raw_name.strip()
        marker = name.casefold()
        if name and marker not in seen:
            seen.add(marker)
            names.append(name)
    return names


def _new_country_config_parent_name(parent: dict | None) -> str:
    if not parent:
        return ""
    return str(parent.get("entity_display_name") or parent.get("display_name") or "")


def _new_country_config_node_detail_payload(country: DerivedCountry, rows: list[dict], tree_id: str) -> dict:
    tree_rows = [row for row in rows if row.get("is_config_row") or row.get("is_assigned_subdivision")]
    rows_by_tree_id = _new_country_config_rows_by_tree_id(tree_rows)
    node = rows_by_tree_id.get(str(tree_id or ""))
    if not node:
        raise Http404(_("No existe entidad de pais nuevo '%(area_id)s'.") % {"area_id": tree_id})
    children_by_parent = _new_country_config_children_by_parent(tree_rows)
    source_code = _new_country_config_rows_source_code(country, [row for row in tree_rows if row.get("is_config_row")])
    children = children_by_parent.get(str(node.get("tree_id") or ""), [])
    population = _new_country_json_number(node.get("entity_population_value"))
    area_km2 = _number_or_none(node.get("entity_area_value"))
    return {
        "area": _new_country_config_browser_row(
            node,
            rows_by_tree_id,
            population,
            area_km2,
            source_code,
            country_slug=country.slug,
            children_by_parent=children_by_parent,
        ),
        "children": [
            _new_country_config_browser_row(
                child,
                rows_by_tree_id,
                population,
                area_km2,
                source_code,
                country_slug=country.slug,
                children_by_parent=children_by_parent,
            )
            for child in children
        ],
        "child_groups": _new_country_config_area_child_groups(
            country,
            children,
            rows_by_tree_id,
            children_by_parent,
            population,
            area_km2,
            source_code,
        ),
    }


def _new_country_config_area_child_groups(
    country: DerivedCountry,
    children: list[dict],
    rows_by_tree_id: dict[str, dict],
    children_by_parent: dict[str, list[dict]],
    population_total,
    area_total,
    source_code: str,
) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[dict]] = {}
    for child in children:
        try:
            level = int(child.get("level") or 0)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(level, []).append(child)
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "children": [
                _new_country_config_browser_row(
                    child,
                    rows_by_tree_id,
                    population_total,
                    area_total,
                    source_code,
                    country_slug=country.slug,
                    children_by_parent=children_by_parent,
                )
                for child in rows_for_level
            ],
        }
        for level, rows_for_level in sorted(grouped.items())
    ]


def _new_country_config_donut_payload(rows: list[dict], value_key: str, total) -> dict:
    items = []
    for row in rows:
        value = _number_or_none(row.get(value_key))
        if not value:
            continue
        items.append(
            {
                "key": str(row.get("tree_id") or row.get("display_code") or row.get("entity_display_name") or ""),
                "label": str(row.get("entity_display_name") or row.get("display_name") or ""),
                "value": value,
            }
        )
    return {
        "type": "donut",
        "total": _number_or_none(total),
        "items": items,
    }


def _new_country_config_first_order_cards(
    first_order: list[dict],
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> list[dict]:
    children_by_parent = children_by_parent or _new_country_config_children_by_parent(rows_by_tree_id.values())
    cards = []
    for row in first_order:
        payload = _new_country_config_browser_row(
            row,
            rows_by_tree_id,
            population_total,
            area_total,
            source_code,
            country_slug=country_slug,
            children_by_parent=children_by_parent,
        )
        config_child_count = len(children_by_parent.get(str(row.get("tree_id") or ""), []))
        if config_child_count:
            payload["child_count"] = config_child_count
        payload["children"] = []
        cards.append(payload)
    return cards


def _new_country_config_children_by_parent(rows) -> dict[str, list[dict]]:
    children_by_parent: dict[str, list[dict]] = {}
    for row in rows:
        parent_id = str(row.get("tree_parent") or "")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(row)
    for children in children_by_parent.values():
        children.sort(key=lambda row: str(row.get("entity_display_name") or "").casefold())
    return children_by_parent


def _new_country_config_share_row(
    row: dict,
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    *,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> dict:
    payload = _new_country_config_browser_row(
        row,
        rows_by_tree_id,
        population_total,
        area_total,
        source_code,
        country_slug=country_slug,
        children_by_parent=children_by_parent,
    )
    return {
        "name": payload["name"],
        "entity_type": payload["entity_type"],
        "population": payload["population"],
        "area_km2": payload["area_km2"],
        "population_percent": payload["population_percent"],
        "area_percent": payload["area_percent"],
        "child_count": payload.get("child_count", 0),
        "detail_url": payload.get("detail_url", ""),
    }


def _new_country_config_first_order_child_groups(
    first_order: list[dict],
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> list[dict]:
    children_by_parent = children_by_parent or _new_country_config_children_by_parent(rows_by_tree_id.values())
    grouped: dict[int, list[dict]] = {}
    for row in first_order:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(level, []).append(row)
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "children": [
                _new_country_config_browser_row(
                    row,
                    rows_by_tree_id,
                    population_total,
                    area_total,
                    source_code,
                    country_slug=country_slug,
                    children_by_parent=children_by_parent,
                )
                for row in rows_for_level
            ],
        }
        for level, rows_for_level in sorted(grouped.items())
    ]


def _nuevo_area_detail_payload(area: NuevoAdminArea) -> dict:
    children = _nuevo_area_browser_children(area)
    return {
        "area": _nuevo_area_identity_payload(area, children),
        "children": _nuevo_area_child_rows(children, area.pop_latest, area.area_km2),
        "child_groups": _nuevo_area_child_groups(area, children),
    }


def _nuevo_area_identity_payload(area: NuevoAdminArea, children: list[NuevoAdminArea] | None = None) -> dict:
    country_root = _area_country_root(area)
    visual_assets = _new_country_visual_assets_for_root(area, _new_country_source_code_for_built_code(area.country_code)) if area.level == 0 else _visual_assets_payload("admin_area", str(area.id))
    return {
        "id": area.id,
        "code": area.code,
        "country_code": area.country_code,
        "name": _area_display_name(area),
        "official_name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "parent": _area_display_name(area.parent) if area.parent else "",
        "population": int(area.pop_latest or 0) if area.pop_latest is not None else None,
        "area_km2": _number_or_none(area.area_km2),
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "capital": _capital_names_for_area(area),
        "subdivision_count": len(children) if children is not None else _nuevo_area_browser_child_count(area),
        "type_summary": [],
        "wikidata_query": _area_wikidata_query(area, country_root),
        "wikidata_id": "",
        "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "derived", "area_id": area.id}),
        "detail_url": _nuevo_area_detail_url(area),
        "visual_assets": visual_assets,
        "flag_asset": visual_assets.get("flag", {}),
        "seal_asset": visual_assets.get("seal", {}),
        "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
    }


def _nuevo_area_child_rows(children: list[NuevoAdminArea], population_total, area_total) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "level": child.level,
            "area_km2": _number_or_none(child.area_km2),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "density": _number_or_none(child.density) or _density(child.pop_latest, child.area_km2),
            "population_percent": _ratio_percent(child.pop_latest, population_total),
            "area_percent": _ratio_percent(child.area_km2, area_total),
            "child_count": _nuevo_area_browser_child_count(child),
            "detail_url": _nuevo_area_detail_url(child),
        }
        for child in children
    ]


def _nuevo_area_browser_children(area: NuevoAdminArea) -> list[NuevoAdminArea]:
    return list(
        NuevoAdminArea.objects.filter(parent=area)
        .select_related("parent")
        .order_by("name")
    )


def _nuevo_area_browser_child_count(area: NuevoAdminArea) -> int:
    return NuevoAdminArea.objects.filter(parent=area).count()


def _nuevo_area_child_groups(area: NuevoAdminArea, children: list[NuevoAdminArea]) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[NuevoAdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _nuevo_area_child_group_label(area.country_code, level),
            "children": _nuevo_area_child_rows(rows, area.pop_latest, area.area_km2),
        }
        for level, rows in sorted(grouped.items())
    ]


def _nuevo_country_top_level_areas(country_code: str, root: NuevoAdminArea | None = None):
    if root:
        children = NuevoAdminArea.objects.filter(parent=root)
        first_child_level = children.values_list("level", flat=True).order_by("level").first()
        if first_child_level is not None:
            return children.filter(level=first_child_level).order_by("name")
        next_level = (
            NuevoAdminArea.objects.filter(country_code=country_code)
            .exclude(id=root.id)
            .values_list("level", flat=True)
            .order_by("level")
            .first()
        )
        if next_level is not None:
            return NuevoAdminArea.objects.filter(country_code=country_code, level=next_level).exclude(id=root.id).order_by("name")
        return NuevoAdminArea.objects.none()
    return NuevoAdminArea.objects.filter(country_code=country_code, level=0, parent__isnull=True).order_by("name")


def _nuevo_country_total(country_code: str, field: str, root: NuevoAdminArea | None):
    if root and getattr(root, field) is not None:
        return getattr(root, field)
    return _nuevo_country_top_level_areas(country_code, root).aggregate(total=Sum(field))["total"]


def _nuevo_country_available_levels(country_code: str, root: NuevoAdminArea | None) -> list[dict]:
    levels = list(
        NuevoAdminArea.objects.filter(country_code=country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    rows = []
    for level in levels:
        if root and level == root.level:
            continue
        level_rows = NuevoAdminArea.objects.filter(country_code=country_code, level=level)
        if root:
            level_rows = level_rows.exclude(id=root.id)
        type_stats = _nuevo_country_level_type_stats(level_rows)
        level_total = sum(count for count, _rows_with_children in type_stats.values())
        if level_total <= 0:
            continue
        entity_type_values = tuple(sorted(type_stats))
        entity_types = [_entity_type_label(item, country_code=country_code) or _("Sin tipo") for item in entity_type_values]
        entity_type_label = " / ".join(entity_types[:3])
        rows.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": _display_level(level, root)},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return rows


def _nuevo_country_level_type_stats(level_rows) -> dict[str, tuple[int, int]]:
    counts = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in level_rows.values("entity_type").annotate(count=Count("id")).order_by("entity_type")
    }
    rows_with_children = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in (
            level_rows.filter(children__isnull=False)
            .values("entity_type")
            .annotate(count=Count("id", distinct=True))
            .order_by("entity_type")
        )
    }
    return {entity_type: (count, rows_with_children.get(entity_type, 0)) for entity_type, count in counts.items()}


def _nuevo_country_table_rows(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    population_total=0,
    area_total=0,
    selected_entity_types: tuple[str, ...] = (),
) -> list[dict]:
    rows = _nuevo_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types)
    return _nuevo_country_table_rows_from_records(rows, country_code, population_total, area_total)


def _nuevo_country_table_rows_from_records(
    rows,
    country_code: str,
    population_total=0,
    area_total=0,
) -> list[dict]:
    return [
        {
            "id": row.id,
            "level": row.level,
            "detail_url": _nuevo_area_detail_url(row),
            "parent_id": row.parent_id or "",
            "parent_level": row.parent.level if row.parent else None,
            "parent_detail_url": _nuevo_area_detail_url(row.parent) if row.parent else "",
            "name": _area_display_name(row),
            "area_km2": _number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _number_or_none(row.density) or _density(row.pop_latest, row.area_km2),
            "population_percent": _ratio_percent(row.pop_latest, population_total),
            "area_percent": _ratio_percent(row.area_km2, area_total),
            "parent": _area_display_name(row.parent) if row.parent else "",
            "entity_type": _entity_type_label(row.entity_type, country_code=country_code),
            "annotations": "",
        }
        for row in rows
    ]


def _nuevo_country_type_summary(country_code: str, root: NuevoAdminArea | None) -> list[dict]:
    rows = NuevoAdminArea.objects.filter(country_code=country_code)
    if root:
        rows = rows.exclude(id=root.id)
    grouped = rows.values("level", "entity_type").annotate(total=Count("id")).order_by("level", "entity_type")
    return [
        {
            "level": item["level"],
            "label": _("Nivel %(level)s") % {"level": _display_level(item["level"], root)},
            "entity_type": _entity_type_label(item["entity_type"], country_code=country_code) or _("Sin tipo"),
            "total": item["total"],
        }
        for item in grouped
    ]


def _nuevo_country_first_order_child_groups(
    country_code: str,
    root: NuevoAdminArea | None,
    children: list[NuevoAdminArea],
    population_total,
    area_total,
) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[NuevoAdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _nuevo_area_child_group_label(country_code, level),
            "children": _nuevo_area_child_rows(rows, population_total, area_total),
        }
        for level, rows in sorted(grouped.items())
    ]


def _nuevo_area_child_group_label(country_code: str, level: int) -> str:
    types = _nuevo_level_entity_types(country_code, level)
    base = _("Nivel %(level)s") % {"level": level}
    if types:
        return f"{base} - {', '.join(types[:3])}"
    return base


def _nuevo_level_entity_types(country_code: str, level: int) -> list[str]:
    return [
        _entity_type_label(item, country_code=country_code) or _("Sin tipo")
        for item in (
            NuevoAdminArea.objects.filter(country_code=country_code, level=level)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        )
    ]


def _nuevo_first_order_cards(
    country_code: str,
    root: NuevoAdminArea | None,
    first_order: list[NuevoAdminArea],
    population_total,
    area_total,
) -> list[dict]:
    child_level = _second_order_level(first_order)
    child_count = (
        NuevoAdminArea.objects.filter(country_code=country_code, level=child_level).count()
        if child_level is not None
        else 0
    )
    include_children = child_level is not None and child_count < 150
    return [
        _nuevo_first_order_card(area, population_total, area_total, include_children=include_children)
        for area in first_order
    ]


def _nuevo_first_order_card(area: NuevoAdminArea, population_total, area_total, *, include_children: bool = False) -> dict:
    population = int(area.pop_latest or 0) if area.pop_latest is not None else 0
    area_km2 = _number_or_none(area.area_km2) or 0
    return {
        "id": area.id,
        "name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "population": population,
        "area_km2": area_km2,
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "population_percent": _ratio_percent(population, population_total),
        "area_percent": _ratio_percent(area_km2, area_total),
        "child_count": _nuevo_area_browser_child_count(area),
        "detail_url": _nuevo_area_detail_url(area),
        "children": _nuevo_second_order_share_rows(area) if include_children else [],
    }


def _nuevo_second_order_share_rows(area: NuevoAdminArea) -> list[dict]:
    children = _nuevo_area_browser_children(area)
    return [
        {
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "area_km2": _number_or_none(child.area_km2),
            "population_percent": _ratio_percent(child.pop_latest, area.pop_latest),
            "area_percent": _ratio_percent(child.area_km2, area.area_km2),
        }
        for child in children
    ]


def _nuevo_area_detail_url(area: NuevoAdminArea) -> str:
    return reverse("ciudades_del_mundo:api_new_area_detail", kwargs={"area_id": area.id})


def _new_country_config_node_detail_url(country_slug: str, tree_id: str) -> str:
    return reverse(
        "ciudades_del_mundo:api_new_country_config_node_detail",
        kwargs={"country_slug": country_slug, "tree_id": tree_id},
    )


def _new_country_source_code_for_built_code(built_code: str) -> str:
    built_code = _normalize_group_country_key(built_code)
    if not built_code:
        return ""
    for config in DerivedCountryConfig.objects.select_related("country").all():
        if _new_country_config_built_code(config) == built_code:
            return _new_country_config_source_code(config)
    return ""


def _new_country_visual_assets_for_root(root: NuevoAdminArea, source_country_code: str = "") -> dict:
    candidates = [
        ("admin_area", str(root.id)),
        ("country", str(root.country_code)),
    ]
    source_country_code = _normalize_group_country_key(source_country_code)
    if source_country_code:
        candidates.append(("country", source_country_code))
    for entity_type, entity_key in candidates:
        assets = _visual_assets_payload(entity_type, entity_key, include_fallbacks=False)
        if _visual_assets_have_display_image(assets):
            return assets
    return {}


def _visual_assets_have_display_image(assets: dict | None) -> bool:
    if not isinstance(assets, dict):
        return False
    return any(_stored_visual_asset_image_url(assets.get(kind)) for kind in ("flag", "coat", "seal"))



def _admin_area_child_groups(area: AdminArea, children: list[AdminArea]) -> list[dict]:
    """Return direct browser children split by level for the country detail UI.

    Some CityPopulation pages attach lower-level rows to a higher-level parent
    because the source table does not expose the skipped administrative level.
    In that case the detail drawer should show one panel per child level rather
    than mixing, for example, communes and urban places in the same table.
    """
    if not children:
        return []

    grouped: dict[int, list[AdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)

    return [
        {
            "level": level,
            "label": _admin_area_child_group_label(area.country_code, level),
            "children": _admin_area_child_rows(rows, area.pop_latest, area.area_km2),
        }
        for level, rows in sorted(grouped.items())
    ]


def _admin_area_child_group_label(country_code: str, level: int) -> str:
    types = _level_entity_types(country_code, level)
    base = _("Nivel %(level)s") % {"level": level}
    if types:
        return f"{base} · {', '.join(types[:3])}"
    return base

def _admin_area_browser_children(area: AdminArea) -> list[AdminArea]:
    children = list(
        _visible_admin_areas()
        .filter(parent=area)
        .select_related("parent")
        .order_by("name")
    )
    extras = _same_name_root_children_with_descendants(area, {child.id for child in children})
    if not extras:
        return _collapse_same_name_flat_children(children)

    return _collapse_same_name_flat_children([*children, *extras])


def _collapse_same_name_flat_children(children: list[AdminArea]) -> list[AdminArea]:
    if not children:
        return children

    child_ids = [child.id for child in children]
    children_with_descendants = set(
        _visible_admin_areas()
        .filter(parent_id__in=child_ids)
        .values_list("parent_id", flat=True)
    )
    names_with_descendants = {
        _area_name_key(child.name)
        for child in children
        if child.id in children_with_descendants
    }
    collapsed = [
        child
        for child in children
        if _area_name_key(child.name) not in names_with_descendants or child.id in children_with_descendants
    ]
    return sorted(collapsed, key=lambda child: (_area_name_key(child.name), child.level, child.id))


def _same_name_root_children_with_descendants(area: AdminArea, existing_ids: set[str]) -> list[AdminArea]:
    root = _area_country_root(area)
    if not root or area.parent_id != root.id:
        return []

    candidates = list(
        _visible_admin_areas()
        .filter(country_code=area.country_code, parent=root, level__gt=area.level)
        .exclude(id__in=existing_ids)
        .select_related("parent")
        .order_by("name", "level")
    )
    if not candidates:
        return []

    candidate_ids = [candidate.id for candidate in candidates]
    candidates_with_descendants = set(
        _visible_admin_areas()
        .filter(parent_id__in=candidate_ids)
        .values_list("parent_id", flat=True)
    )
    area_name_key = _area_name_key(area.name)
    return [
        candidate
        for candidate in candidates
        if candidate.id in candidates_with_descendants and _area_name_key(candidate.name) == area_name_key
    ]


def _area_name_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _admin_area_browser_child_count(area: AdminArea) -> int:
    return len(_admin_area_browser_children(area))


def _admin_area_identity_payload(area: AdminArea, children: list[AdminArea] | None = None) -> dict:
    country_root = _area_country_root(area)
    visual_assets = _visual_assets_payload("admin_area", str(area.id))
    return {
        "id": area.id,
        "code": area.code,
        "country_code": area.country_code,
        "name": _area_display_name(area),
        "official_name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "parent": _area_display_name(area.parent) if area.parent else "",
        "population": int(area.pop_latest or 0) if area.pop_latest is not None else None,
        "area_km2": _number_or_none(area.area_km2),
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "capital": _capital_names_for_area(area),
        "subdivision_count": len(children) if children is not None else _admin_area_browser_child_count(area),
        "type_summary": [],
        "wikidata_query": _area_wikidata_query(area, country_root),
        "wikidata_id": "",
        "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "admin", "area_id": area.id}),
        "detail_url": _admin_area_detail_url(area),
        "visual_assets": visual_assets,
        "flag_asset": visual_assets.get("flag", {}),
        "seal_asset": visual_assets.get("seal", {}),
        "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
    }


def _admin_area_child_rows(children: list[AdminArea], population_total, area_total) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "level": child.level,
            "area_km2": _number_or_none(child.area_km2),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "density": _number_or_none(child.density) or _density(child.pop_latest, child.area_km2),
            "population_percent": _ratio_percent(child.pop_latest, population_total),
            "area_percent": _ratio_percent(child.area_km2, area_total),
            "child_count": _admin_area_browser_child_count(child),
            "detail_url": _admin_area_detail_url(child),
        }
        for child in children
    ]


def _area_wikidata_query(area: AdminArea, country_root: AdminArea | None = None) -> str:
    parts = [area.name]
    parent = area.parent
    if parent:
        parts.append(parent.name)
    if country_root and country_root.id != area.id:
        parts.append(country_root.name)
    elif area.country_code:
        parts.append(area.country_code)
    return ", ".join(str(part) for part in parts if part)


def _admin_area_detail_url(area: AdminArea) -> str:
    return reverse("ciudades_del_mundo:api_admin_area_detail", kwargs={"area_id": area.id})


def _resolve_country_level_selection(
    selected_value: int | str | None,
    available_levels: list[dict],
) -> tuple[int | None, tuple[str, ...], str]:
    if not available_levels:
        return None, (), ""
    parsed_level, parsed_types = _parse_country_level_filter(selected_value)
    by_filter = {str(item.get("filter_value") or item["value"]): item for item in available_levels}
    by_level = {int(item["value"]): item for item in available_levels}
    selected_key = str(selected_value or "").strip()
    selected = by_filter.get(selected_key) if selected_key else None
    if selected is None and parsed_level is not None:
        selected = by_level.get(parsed_level)
    if selected is None:
        selected = available_levels[0]
    filter_value = str(selected.get("filter_value") or selected["value"])
    level, entity_types = _parse_country_level_filter(filter_value)
    return level, entity_types, filter_value


def _parse_country_level_filter(value: int | str | None) -> tuple[int | None, tuple[str, ...]]:
    text = str(value or "").strip()
    if not text:
        return None, ()
    level_text, *raw_types = text.split("|")
    try:
        level = int(level_text)
    except (TypeError, ValueError):
        return None, ()
    entity_types = tuple(item for item in raw_types if item)
    return level, entity_types


def _country_available_levels(country_code: str, root: AdminArea | None) -> list[dict]:
    levels = list(
        _visible_admin_areas().filter(country_code=country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    rows = []
    for level in levels:
        if root and level == root.level:
            continue
        level_rows = _visible_admin_areas().filter(country_code=country_code, level=level)
        if root:
            level_rows = level_rows.exclude(id=root.id)
        type_stats = _country_level_type_stats(level_rows)
        level_total = sum(count for count, _rows_with_children in type_stats.values())
        if level_total <= 0:
            continue
        entity_type_values = tuple(sorted(type_stats))
        entity_types = [_entity_type_label(item, country_code=country_code) or _("Sin tipo") for item in entity_type_values]
        entity_type_label = " / ".join(entity_types[:3])
        rows.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": _display_level(level, root)},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return rows


def _country_level_type_stats(level_rows) -> dict[str, tuple[int, int]]:
    counts = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in level_rows.values("entity_type").annotate(count=Count("id")).order_by("entity_type")
    }
    rows_with_children = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in (
            level_rows.filter(children__isnull=False)
            .exclude(children__city_merge_status=AdminArea.CityMergeStatus.SOURCE)
            .values("entity_type")
            .annotate(count=Count("id", distinct=True))
            .order_by("entity_type")
        )
    }
    return {entity_type: (count, rows_with_children.get(entity_type, 0)) for entity_type, count in counts.items()}


def _display_level(level: int, root: AdminArea | None) -> int:
    if root is None:
        return level + 1
    return level


def _level_entity_types(country_code: str, level: int) -> list[str]:
    return [
        _entity_type_label(item, country_code=country_code) or _("Sin tipo")
        for item in (
            _visible_admin_areas().filter(country_code=country_code, level=level)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        )
    ]


def _country_table_rows(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    population_total=0,
    area_total=0,
    selected_entity_types: tuple[str, ...] = (),
) -> list[dict]:
    rows = _country_level_rows_queryset(country_code, root, selected_level, selected_entity_types)
    return _country_table_rows_from_records(rows, country_code, population_total, area_total)


def _country_table_rows_from_records(
    rows,
    country_code: str,
    population_total=0,
    area_total=0,
) -> list[dict]:
    return [
        {
            "id": row.id,
            "level": row.level,
            "detail_url": _admin_area_detail_url(row),
            "parent_id": row.parent_id or "",
            "parent_level": row.parent.level if row.parent else None,
            "parent_detail_url": _admin_area_detail_url(row.parent) if row.parent else "",
            "name": _area_display_name(row),
            "area_km2": _number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _number_or_none(row.density) or _density(row.pop_latest, row.area_km2),
            "population_percent": _ratio_percent(row.pop_latest, population_total),
            "area_percent": _ratio_percent(row.area_km2, area_total),
            "parent": _area_display_name(row.parent) if row.parent else "",
            "entity_type": _entity_type_label(row.entity_type, country_code=country_code),
            "annotations": row.annotations or "",
        }
        for row in rows
    ]


def _country_type_summary(country_code: str, root: AdminArea | None) -> list[dict]:
    rows = _visible_admin_areas().filter(country_code=country_code)
    if root:
        rows = rows.exclude(id=root.id)
    grouped = rows.values("level", "entity_type").annotate(total=Count("id")).order_by("level", "entity_type")
    return [
        {
            "level": item["level"],
            "label": _("Nivel %(level)s") % {"level": _display_level(item["level"], root)},
            "entity_type": _entity_type_label(item["entity_type"], country_code=country_code) or _("Sin tipo"),
            "total": item["total"],
        }
        for item in grouped
    ]


def _capital_names_for_area(area: AdminArea | None) -> list[str]:
    if not area:
        return []
    return [
        _area_display_name(capital)
        for capital in area.capitals.all().order_by("name")
    ]


def _wikidata_country_id(country_code: str) -> str:
    return {
        "albania": "Q222",
        "algeria": "Q262",
        "andorra": "Q228",
        "angola": "Q916",
        "anguilla": "Q25228",
        "antigua": "Q781",
        "argentina": "Q414",
        "armenia": "Q399",
        "aruba": "Q21203",
        "australia": "Q408",
        "austria": "Q40",
        "azerbaijan": "Q227",
        "bahamas": "Q778",
        "bahrain": "Q398",
        "bangladesh": "Q902",
        "barbados": "Q244",
        "belarus": "Q184",
        "belgium": "Q31",
        "belize": "Q242",
        "benin": "Q962",
        "bermuda": "Q23635",
        "bhutan": "Q917",
        "bolivia": "Q750",
        "bosnia": "Q225",
        "botswana": "Q963",
        "brazil": "Q155",
        "brunei": "Q921",
        "bulgaria": "Q219",
        "burkinafaso": "Q965",
        "burundi": "Q967",
        "cambodia": "Q424",
        "cameroon": "Q1009",
        "canada": "Q16",
        "capeverde": "Q1011",
        "caribbeannetherlands": "Q27561",
        "caymans": "Q5785",
        "centralafrica": "Q929",
        "chad": "Q657",
        "chile": "Q298",
        "china": "Q148",
        "colombia": "Q739",
        "comoros": "Q970",
        "congo": "Q971",
        "cook": "Q26988",
        "costarica": "Q800",
        "croatia": "Q224",
        "cuba": "Q241",
        "curacao": "Q25279",
        "cyprus": "Q229",
        "czechrep": "Q213",
        "denmark": "Q35",
        "djibouti": "Q977",
        "dominica": "Q784",
        "domrep": "Q786",
        "drcongo": "Q974",
        "ecuador": "Q736",
        "egypt": "Q79",
        "elsalvador": "Q792",
        "equatorialguinea": "Q983",
        "eritrea": "Q986",
        "estonia": "Q191",
        "eswatini": "Q1050",
        "ethiopia": "Q115",
        "falklands": "Q9648",
        "faroe": "Q4628",
        "fiji": "Q712",
        "finland": "Q33",
        "france": "Q142",
        "frenchguiana": "Q3769",
        "frenchpolynesia": "Q30971",
        "gabon": "Q1000",
        "gambia": "Q1005",
        "georgia": "Q230",
        "germany": "Q183",
        "ghana": "Q117",
        "gibraltar": "Q1410",
        "greece": "Q41",
        "greenland": "Q223",
        "grenada": "Q769",
        "guadeloupe": "Q17012",
        "guam": "Q16635",
        "guatemala": "Q774",
        "guernsey": "Q25230",
        "guinea": "Q1006",
        "guineabissau": "Q1007",
        "guyana": "Q734",
        "haiti": "Q790",
        "honduras": "Q783",
        "hungary": "Q28",
        "iceland": "Q189",
        "india": "Q668",
        "indonesia": "Q252",
        "iran": "Q794",
        "iraq": "Q796",
        "ireland": "Q27",
        "isleofman": "Q9676",
        "israel": "Q801",
        "italy": "Q38",
        "ivorycoast": "Q1008",
        "jamaica": "Q766",
        "japan": "Q17",
        "jersey": "Q785",
        "jordan": "Q810",
        "kazakhstan": "Q232",
        "kenya": "Q114",
        "kiribati": "Q710",
        "kosovo": "Q1246",
        "kuwait": "Q817",
        "kyrgyzstan": "Q813",
        "laos": "Q819",
        "latvia": "Q211",
        "lebanon": "Q822",
        "lesotho": "Q1013",
        "liberia": "Q1014",
        "libya": "Q1016",
        "liechtenstein": "Q347",
        "lithuania": "Q37",
        "luxembourg": "Q32",
        "malawi": "Q1020",
        "malaysia": "Q833",
        "maldives": "Q826",
        "mali": "Q912",
        "malta": "Q233",
        "marshall": "Q709",
        "martinique": "Q17054",
        "mauritania": "Q1025",
        "mauritius": "Q1027",
        "mayotte": "Q17063",
        "mexico": "Q96",
        "micronesia": "Q702",
        "moldova": "Q217",
        "monaco": "Q235",
        "mongolia": "Q711",
        "montenegro": "Q236",
        "montserrat": "Q13353",
        "morocco": "Q1028",
        "mozambique": "Q1029",
        "myanmar": "Q836",
        "namibia": "Q1030",
        "nauru": "Q697",
        "nepal": "Q837",
        "netherlands": "Q55",
        "newcaledonia": "Q33788",
        "newzealand": "Q664",
        "nicaragua": "Q811",
        "niger": "Q1032",
        "nigeria": "Q1033",
        "niue": "Q34020",
        "northkorea": "Q423",
        "northmacedonia": "Q221",
        "northmarianas": "Q16644",
        "norway": "Q20",
        "oman": "Q842",
        "pakistan": "Q843",
        "palau": "Q695",
        "palestine": "Q219060",
        "panama": "Q804",
        "papuanewguinea": "Q691",
        "paraguay": "Q733",
        "peru": "Q419",
        "philippines": "Q928",
        "pitcairn": "Q35672",
        "poland": "Q36",
        "portugal": "Q45",
        "puertorico": "Q1183",
        "qatar": "Q846",
        "reunion": "Q17070",
        "romania": "Q218",
        "russia": "Q159",
        "rwanda": "Q1037",
        "saintbarthelemy": "Q25362",
        "saintmartin": "Q126125",
        "samoa": "Q683",
        "sanmarino": "Q238",
        "saotome": "Q1039",
        "saudiarabia": "Q851",
        "senegal": "Q1041",
        "serbia": "Q403",
        "seychelles": "Q1042",
        "sierraleone": "Q1044",
        "singapore": "Q334",
        "sintmaarten": "Q26273",
        "slovakia": "Q214",
        "slovenia": "Q215",
        "solomon": "Q685",
        "somalia": "Q1045",
        "southafrica": "Q258",
        "southkorea": "Q884",
        "southsudan": "Q958",
        "spain": "Q29",
        "srilanka": "Q854",
        "sthelena": "Q34497",
        "stkittsnevis": "Q763",
        "stlucia": "Q760",
        "stpierremiquelon": "Q34617",
        "stvincent": "Q757",
        "sudan": "Q1049",
        "suriname": "Q730",
        "sweden": "Q34",
        "switzerland": "Q39",
        "syria": "Q858",
        "taiwan": "Q865",
        "tajikistan": "Q863",
        "tanzania": "Q924",
        "thailand": "Q869",
        "timor": "Q574",
        "togo": "Q945",
        "tokelau": "Q36823",
        "tonga": "Q678",
        "trinidad": "Q754",
        "tunisia": "Q948",
        "turkey": "Q43",
        "turkmenistan": "Q874",
        "turkscaicos": "Q18221",
        "tuvalu": "Q672",
        "uae": "Q878",
        "uganda": "Q1036",
        "uk": "Q145",
        "ukraine": "Q212",
        "uruguay": "Q77",
        "usa": "Q30",
        "usvirginislands": "Q11703",
        "uzbekistan": "Q265",
        "vanuatu": "Q686",
        "vaticancity": "Q237",
        "venezuela": "Q717",
        "vietnam": "Q881",
        "wallisfutuna": "Q35555",
        "westernsahara": "Q6250",
        "yemen": "Q805",
        "zambia": "Q953",
        "zimbabwe": "Q954",
    }.get(country_code, "")


def _donut_payload(areas: list[AdminArea], value_field: str, total) -> dict:
    items = []
    for area in areas:
        value = getattr(area, value_field)
        if not value:
            continue
        items.append(
            {
                "key": area.id,
                "label": _area_display_name(area),
                "value": _number_or_none(value),
            }
        )
    return {
        "type": "donut",
        "total": _number_or_none(total),
        "items": items,
    }


def _first_order_cards(country_code: str, root: AdminArea | None, first_order: list[AdminArea], population_total, area_total) -> list[dict]:
    child_level = _second_order_level(first_order)
    child_count = (
        _visible_admin_areas().filter(country_code=country_code, level=child_level).count()
        if child_level is not None
        else 0
    )
    include_children = child_level is not None and child_count < 150
    return [
        _first_order_card(area, population_total, area_total, include_children=include_children)
        for area in first_order
    ]


def _second_order_level(first_order: list[AdminArea]) -> int | None:
    levels = sorted({area.level for area in first_order})
    if len(levels) != 1:
        return None
    return levels[0] + 1


def _first_order_card(area: AdminArea, population_total, area_total, *, include_children: bool = False) -> dict:
    population = int(area.pop_latest or 0) if area.pop_latest is not None else 0
    area_km2 = _number_or_none(area.area_km2) or 0
    return {
        "id": area.id,
        "name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "population": population,
        "area_km2": area_km2,
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "population_percent": _ratio_percent(population, population_total),
        "area_percent": _ratio_percent(area_km2, area_total),
        "child_count": _admin_area_browser_child_count(area),
        "detail_url": _admin_area_detail_url(area),
        "children": _second_order_share_rows(area) if include_children else [],
    }


def _second_order_share_rows(area: AdminArea) -> list[dict]:
    children = _admin_area_browser_children(area)
    return [
        {
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "area_km2": _number_or_none(child.area_km2),
            "population_percent": _ratio_percent(child.pop_latest, area.pop_latest),
            "area_percent": _ratio_percent(child.area_km2, area.area_km2),
        }
        for child in children
    ]


def _looks_like_municipality(area: AdminArea) -> bool:
    text = " ".join(str(value or "").lower() for value in (area.entity_type, area.name))
    municipality_markers = (
        "municip",
        "commune",
        "comuna",
        "gemeinde",
        "gmina",
        "locality",
        "city",
        "town",
        "village",
    )
    return any(marker in text for marker in municipality_markers)


def _ratio_percent(value, total) -> float:
    try:
        if not total:
            return 0
        return round((float(value or 0) / float(total)) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


def _density(population, area) -> float | None:
    try:
        if not population or not area:
            return None
        return round(float(population) / float(area), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _number_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _admin_country_label_map() -> dict[str, str]:
    roots = _visible_admin_areas().filter(level=0).values_list("country_code", "name")
    return {country_code: _display_name(name, country_code, country_code=country_code) for country_code, name in roots}


def _nuevo_country_label_map() -> dict[str, str]:
    roots = NuevoAdminArea.objects.filter(level=0, parent__isnull=True).values_list("country_code", "name")
    return {country_code: _display_name(name, country_code) for country_code, name in roots}


def _admin_country_options() -> list[dict[str, str]]:
    codes = _visible_admin_areas().values_list("country_code", flat=True).distinct().order_by("country_code")
    return _country_options(codes, _admin_country_label_map())


def _nuevo_country_options() -> list[dict[str, str]]:
    codes = NuevoAdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code")
    return _country_options(codes, _nuevo_country_label_map())


def _country_options(codes, label_map: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "code": code,
            "label": label_map.get(code, _display_name("", code, country_code=code)),
        }
        for code in codes
    ]


def _apply_country_labels(rows: list[dict], label_map: dict[str, str]) -> None:
    for row in rows:
        country_code = row.get("country_code")
        row["display_label"] = label_map.get(country_code, _display_name("", country_code, country_code=country_code))


def _area_country_display_name(area) -> str:
    root = _area_country_root(area)
    if root:
        return _display_name(root.name, area.country_code, country_code=area.country_code)
    area_country_code = getattr(area, "country_code", "")
    return _display_name("", area_country_code, country_code=area_country_code)


def _display_name(name, fallback, *, country_code: str | None = None) -> str:
    fallback_value = str(fallback or "").strip()
    language = normalize_language_code(get_language())
    country_key = str(country_code or "").strip().lower()
    if country_key and (not str(name or "").strip() or fallback_value.lower() == country_key):
        dynamic = dynamic_country_name(country_key, language)
        if dynamic:
            return dynamic
    if language == "es":
        mapped = COUNTRY_NAME_ES_BY_CODE.get(fallback_value.lower())
        if mapped:
            return mapped
    value = str(name or "").strip()
    if not value:
        value = fallback_value
    if not value:
        return ""
    if "_" in value:
        value = value.replace("_", " ")
    if str(country_code or "").lower() == "spain":
        value = spain_name(value, language)
    translated = _(value)
    return translated


def _entity_type_label(entity_type: str | None, *, country_code: str | None = None) -> str:
    value = str(entity_type or "").strip()
    if not value:
        return ""
    language = normalize_language_code(get_language())
    dynamic = dynamic_entity_type_label(value, language, country_code=country_code)
    if dynamic:
        return dynamic
    if str(country_code or "").lower() == "spain":
        return spain_entity_type(value, language)
    return _(value)


def _wants_json(request) -> bool:
    accept = request.headers.get("accept", "")
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in accept


def _is_ajax(request) -> bool:
    return _wants_json(request)


def _config_form_context(mode: str, slug: str, content: str, active_task=None) -> dict:
    workflow = {
        "status_filter": "pending",
        "can_validate": False,
        "can_scrape": False,
        "can_clear": False,
        "can_stop": False,
        "rows": 0,
        "country_code": slug,
    }
    if mode == "edit" and slug:
        try:
            workflow = _decorate_config_workflow_flags(_config_summary_for_slug(slug))
        except (OperationalError, ProgrammingError):
            pass
    asset_editor_enabled = bool(mode == "edit" and int(workflow.get("rows") or 0) > 0)
    if mode == "edit":
        # Render a light shell first; the full TOML/manual data is hydrated via
        # config_editor_data so large configs do not block the initial page.
        parsed = _parse_config_editor_data(slug, "", asset_editor_enabled=False, include_config_cities=False)
        rendered_content = ""
    else:
        parsed = _parse_config_editor_data(
            slug,
            content,
            asset_editor_enabled=asset_editor_enabled,
            include_config_cities=False,
        )
        rendered_content = content
    return {
        "mode": mode,
        "slug": slug,
        "content": rendered_content,
        "active_task": active_task,
        "config_workflow": workflow,
        "manual": parsed,
        "editor_data_url": reverse("ciudades_del_mundo:config_editor_data", kwargs={"slug": slug}) if mode == "edit" and slug else "",
        "scrape_types": _scrape_type_choices(),
        "ai_enabled": AI_CONFIG_ENABLED,
        "ai_personal_login_enabled": AI_PERSONAL_LOGIN_ENABLED,
        "ai_provider_choices": _ai_provider_choices(),
    }


def _scrape_type_choices() -> list[tuple[str, str]]:
    return [
        ("cities", _("Cities: infosection + subdivisión superior + ciudades")),
        ("admin", _("Admin: infosection + subdivisión superior + subdivisión inferior")),
        ("citiesadmin", _("CitiesAdmin: infosection + subdivisión superior + subdivisión inferior + ciudades")),
    ]




def _normalize_page_source_for_form(value) -> str:
    source = str(value or "cities").strip().lower()
    return source if source in {"cities", "admin", "citiesadmin"} else "cities"


def _persist_manual_asset_overrides(slug: str, content: str) -> None:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return
    country_code = str(data.get("country_code") or slug or "").strip()
    rows = _asset_assignment_rows_for_form(data, country_code=country_code)
    save_config_asset_overrides(slug, country_code, rows)

def _ai_provider_choices() -> list[tuple[str, str]]:
    labels = {
        "chatgpt": "ChatGPT",
        "openai": "OpenAI",
        "claude": "Claude",
        "gemini": "Gemini",
    }
    return [(provider, labels.get(provider, provider.title())) for provider in AI_PROVIDER_LOGIN_URLS.keys()]


def _config_city_rows_for_country(country_code: str) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    municipal_level = effective_source_municipal_level(country_code)
    try:
        areas = list(
            _visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .only("id", "country_code", "code", "name", "level", "entity_type", "raw_entity_type", "pop_latest")
            .order_by("level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []

    rows = []
    for area in areas:
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            level = 0
        raw_type = str(area.entity_type or area.raw_entity_type or "").strip()
        type_key = raw_type.casefold()
        is_config_city = any(
            token in type_key
            for token in (
                "city",
                "town",
                "village",
                "municip",
                "commune",
                "comuna",
            )
        )
        if not is_config_city and municipal_level is not None:
            is_config_city = level == int(municipal_level)
        if not is_config_city:
            continue
        name = _area_display_name(area)
        population_text = _group_metric_text(area.pop_latest)
        rows.append(
            {
                "id": str(area.id),
                "code": str(area.code or ""),
                "name": name,
                "level": level,
                "type_text": raw_type or "",
                "population": area.pop_latest if area.pop_latest is not None else "",
                "population_text": population_text,
                "href": reverse(
                    "ciudades_del_mundo:area_map_detail",
                    kwargs={"source": "admin", "area_id": area.id},
                ),
                "search_text": " ".join(
                    str(part)
                    for part in (name, area.code, raw_type, level, population_text)
                    if part is not None and str(part).strip()
                ),
            }
        )
    return rows


def _config_city_builder_payload(country_code: str, data: dict, *, enabled: bool) -> dict:
    base_payload = {
        "enabled": False,
        "legal_level": "",
        "parent_level": "",
        "parent_options": [],
        "children_by_parent": {},
        "sections": [],
    }
    if not enabled:
        return base_payload
    country_code = _normalize_group_country_key(country_code)
    legal_level = _config_legal_subdivision_level(country_code, data)
    if legal_level is None or legal_level <= 0:
        return base_payload
    parent_level = legal_level - 1
    try:
        child_parent_ids = set(
            str(parent_id)
            for parent_id in (
                _visible_admin_areas()
                .filter(country_code__iexact=country_code, level=legal_level)
                .exclude(parent_id__isnull=True)
                .values_list("parent_id", flat=True)
                .distinct()
            )
            if parent_id
        )
        if not child_parent_ids:
            return base_payload
        parents = list(
            _visible_admin_areas()
            .filter(country_code__iexact=country_code, level=parent_level, id__in=child_parent_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return base_payload
    if not parents:
        return base_payload

    parent_lookup: dict[str, AdminArea] = {}
    parent_options = []
    for parent in parents:
        parent_id = str(parent.id)
        parent_payload = _config_city_area_payload(parent)
        parent_options.append(parent_payload)
        for key in _config_city_lookup_values(parent):
            parent_lookup.setdefault(key, parent)

    raw_cities = data.get("cities") if isinstance(data.get("cities"), list) else []
    section_parent_ids = {
        str(parent.id)
        for raw_city in raw_cities
        if isinstance(raw_city, dict)
        for parent in [_config_city_parent_from_toml(raw_city, parent_level=parent_level, parent_lookup=parent_lookup)]
        if parent
    }
    children_by_parent, child_lookup_by_parent = _config_city_children_payload_by_parent(
        country_code,
        parent_ids=section_parent_ids,
        legal_level=legal_level,
    )
    sections = _config_city_sections_from_toml(
        raw_cities,
        parent_level=parent_level,
        legal_level=legal_level,
        parent_lookup=parent_lookup,
        children_by_parent=children_by_parent,
        child_lookup_by_parent=child_lookup_by_parent,
    )

    return {
        "enabled": bool(parent_options),
        "legal_level": legal_level,
        "parent_level": parent_level,
        "country_code": country_code,
        "children_url": reverse("ciudades_del_mundo:group_source_data"),
        "parent_options": parent_options,
        "children_by_parent": children_by_parent,
        "sections": sections,
    }


def _config_city_children_payload_by_parent(
    country_code: str,
    *,
    parent_ids: set[str],
    legal_level: int,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    if not parent_ids:
        return {}, {}
    try:
        children = list(
            _visible_admin_areas()
            .filter(country_code__iexact=country_code, level=legal_level, parent_id__in=parent_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "pop_latest")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {}, {}
    children_by_parent: dict[str, list[dict]] = {}
    child_lookup_by_parent: dict[str, dict[str, dict]] = {}
    for child in children:
        parent_id = str(child.parent_id)
        child_payload = _config_city_area_payload(child)
        child_payload["population_text"] = _group_metric_text(child.pop_latest)
        children_by_parent.setdefault(parent_id, []).append(child_payload)
        lookup = child_lookup_by_parent.setdefault(parent_id, {})
        for key in _config_city_lookup_values(child):
            lookup.setdefault(key, child_payload)
    return children_by_parent, child_lookup_by_parent


def _config_legal_subdivision_level(country_code: str, data: dict) -> int | None:
    raw = data.get("LEGAL_SUBDIVISION") if isinstance(data, dict) else None
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if country_code in ORIGINAL_MUNICIPAL_LEVEL:
        return int(ORIGINAL_MUNICIPAL_LEVEL[country_code])
    return None


def _config_city_area_payload(area: AdminArea) -> dict:
    name = _area_display_name(area)
    entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(area.id),
        "code": str(area.code or ""),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
        "type_text": entity_type or str(area.entity_type or ""),
    }


def _config_city_lookup_values(area: AdminArea) -> list[str]:
    return [
        _config_city_lookup_key(value)
        for value in (area.id, area.code, area.name, _area_display_name(area))
        if str(value or "").strip()
    ]


def _config_city_lookup_key(value) -> str:
    return str(value or "").strip().casefold()


def _config_city_sections_from_toml(
    raw_cities,
    *,
    parent_level: int,
    legal_level: int,
    parent_lookup: dict[str, AdminArea],
    children_by_parent: dict[str, list[dict]],
    child_lookup_by_parent: dict[str, dict[str, dict]],
) -> list[dict]:
    sections = []
    for raw_city in raw_cities or []:
        if not isinstance(raw_city, dict):
            continue
        parent = _config_city_parent_from_toml(raw_city, parent_level=parent_level, parent_lookup=parent_lookup)
        if not parent:
            continue
        parent_id = str(parent.id)
        children = children_by_parent.get(parent_id) or []
        if not children:
            continue
        selected_children = _config_city_selected_children_from_toml(
            raw_city,
            children=children,
            lookup=child_lookup_by_parent.get(parent_id, {}),
        )
        if not selected_children:
            selected_children = children
        parent_payload = _config_city_area_payload(parent)
        sections.append(
            {
                "parent_id": parent_id,
                "parent_name": parent_payload["name"],
                "parent_label": parent_payload["label"],
                "parent_code": parent_payload["code"],
                "parent_level": parent_level,
                "parent_type": str(
                    raw_city.get("parent_type")
                    or raw_city.get("parent_entity_type")
                    or parent_payload["type_text"]
                ),
                "city": str(raw_city.get("city") or parent_payload["name"]),
                "code": str(raw_city.get("id") or parent_payload["code"] or parent_id),
                "level": int(raw_city.get("level") or legal_level),
                "type": str(raw_city.get("type") or "City"),
                "selected_ids": [child["id"] for child in selected_children],
                "communes": [child["code"] or child["id"] for child in selected_children],
                "keep_communes": bool(raw_city.get("keep_communes", False)),
            }
        )
    return sections


def _config_city_parent_from_toml(raw_city: dict, *, parent_level: int, parent_lookup: dict[str, AdminArea]) -> AdminArea | None:
    raw_from = raw_city.get("from") if isinstance(raw_city.get("from"), dict) else {}
    labels = []
    for level_key, level_labels in raw_from.items():
        try:
            is_parent_level = int(level_key) == int(parent_level)
        except (TypeError, ValueError):
            is_parent_level = False
        if not is_parent_level:
            continue
        if isinstance(level_labels, (list, tuple)):
            labels.extend(level_labels)
        else:
            labels.append(level_labels)
    if not labels:
        for level_labels in raw_from.values():
            if isinstance(level_labels, (list, tuple)):
                labels.extend(level_labels)
            else:
                labels.append(level_labels)
    for label in labels:
        parent = parent_lookup.get(_config_city_lookup_key(label))
        if parent:
            return parent
    return None


def _config_city_selected_children_from_toml(raw_city: dict, *, children: list[dict], lookup: dict[str, dict]) -> list[dict]:
    selected = []
    seen = set()
    for label in raw_city.get("communes") or []:
        child = lookup.get(_config_city_lookup_key(label))
        if child and child["id"] not in seen:
            selected.append(child)
            seen.add(child["id"])
    return selected


def _parse_config_editor_data(
    slug: str,
    content: str,
    *,
    asset_editor_enabled: bool = False,
    include_config_cities: bool = True,
) -> dict:
    data = {}
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    pages = []
    for page in data.get("pages") or []:
        raw_paths = page.get("path")
        include = page.get("include") if isinstance(page.get("include"), dict) else {}
        include_infosection = include.get("infosection", True)
        include_major = include.get("major_subdivision", include.get("admin1", True))
        include_minor = include.get("minor_subdivision", include.get("admin2", True))
        include_cities = include.get("cities", True)
        force_level = page.get("force_highest_level")
        parent_level = page.get("parent_level", page.get("force_parent_level"))
        repeat = page.get("repeat") if isinstance(page.get("repeat"), dict) else {}
        pages.append(
            {
                "paths": _page_paths_list_for_form(raw_paths),
                "paths_text": _page_paths_text_for_form(raw_paths),
                "source": _normalize_page_source_for_form(page.get("source", "admin")),
                "force_highest_level": "" if force_level is None else str(force_level),
                "parent_level": "" if parent_level is None else str(parent_level),
                "include_infosection": _bool_text(include_infosection),
                "include_major_subdivision": _bool_text(include_major),
                "include_minor_subdivision": _bool_text(include_minor),
                "include_cities": _bool_text(include_cities),
                "repeat_infosection": "" if repeat.get("infosection") is None else str(repeat.get("infosection")),
                "repeat_major_subdivision": "" if repeat.get("major_subdivision", repeat.get("admin1")) is None else str(repeat.get("major_subdivision", repeat.get("admin1"))),
                "repeat_minor_subdivision": "" if repeat.get("minor_subdivision", repeat.get("admin2")) is None else str(repeat.get("minor_subdivision", repeat.get("admin2"))),
                "repeat_cities": "" if repeat.get("cities") is None else str(repeat.get("cities")),
                "sum_to_root": _bool_text(page.get("sum_to_root", False)),
                "enabled": _bool_text(page.get("enabled", not page.get("disabled", False))),
            }
        )
    country_code = str(data.get("country_code") or slug or "")
    visual_assets = data.get("visual_assets") if isinstance(data.get("visual_assets"), dict) else {}
    asset_overrides = _asset_assignment_rows_for_form(data, country_code=country_code)
    persisted_asset_overrides = load_config_asset_overrides(slug) if slug else []
    if persisted_asset_overrides:
        asset_overrides = persisted_asset_overrides
    selected_entity_ids = [row.get("entity_id") for row in asset_overrides if row.get("entity_id")]
    selected_resource_qids = [
        row.get(key)
        for row in asset_overrides
        for key in ("flag_qid", "coat_qid")
        if row.get(key)
    ]
    return {
        "country_code": country_code,
        "wikidata_id": str(data.get("wikidata_id") or ""),
        "legal_subdivision": "" if data.get("LEGAL_SUBDIVISION") is None else str(data.get("LEGAL_SUBDIVISION")),
        "pages": pages,
        "visual_assets": {
            "bulk_country_wikidata": _bool_text(visual_assets.get("bulk_country_wikidata", True)),
            "strict_required": _bool_text(visual_assets.get("strict_required", True)),
            "required_kinds": _list_text_for_form(visual_assets.get("required_kinds") or ["flag", "coat"]),
            "required_levels": _list_text_for_form(visual_assets.get("required_levels") or []),
        },
        "asset_options": _asset_assignment_options_for_form(
            country_code,
            enabled=asset_editor_enabled,
            selected_entity_ids=selected_entity_ids,
            selected_resource_qids=selected_resource_qids,
        ),
        "asset_overrides": asset_overrides,
        "config_cities": [],
        "city_builder": _config_city_builder_payload(country_code, data, enabled=include_config_cities),
    }


def _raw_asset_overrides(data: dict) -> list[dict]:
    raw = (
        data.get("wikidata_asset_overrides")
        or data.get("visual_asset_overrides")
        or data.get("asset_overrides")
        or []
    )
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)]


def _asset_assignment_rows_for_form(data: dict, *, country_code: str) -> list[dict]:
    grouped: dict[str, dict[str, str]] = {}
    for override in _raw_asset_overrides(data):
        entity_id = _asset_assignment_entity_id_from_override(override, country_code=country_code)
        if not entity_id:
            continue
        level = "" if override.get("level") is None else str(override.get("level"))
        key = entity_id
        row = grouped.setdefault(
            key,
            {
                "level": level,
                "entity_id": entity_id,
                "flag_qid": "",
                "coat_qid": "",
            },
        )
        if level and not row.get("level"):
            row["level"] = level
        qid = str(override.get("wikidata_id") or override.get("qid") or override.get("wikidata") or "")
        kinds = _list_from_value(override.get("kinds") or override.get("kind") or [])
        if not kinds:
            kinds = ["flag", "coat"]
        for kind in kinds:
            kind = str(kind or "").strip().lower()
            if kind == "flag":
                row["flag_qid"] = qid
            elif kind == "coat":
                row["coat_qid"] = qid
    return list(grouped.values())


def _asset_assignment_entity_id_from_override(override: dict, *, country_code: str) -> str:
    ids = _list_from_value(
        override.get("ids")
        or override.get("id")
        or override.get("entity_keys")
        or override.get("entity_key")
    )
    if ids:
        return str(ids[0])
    try:
        queryset = _visible_admin_areas().filter(country_code=country_code)
        level = override.get("level")
        if level is not None and str(level).strip() != "":
            queryset = queryset.filter(level=int(level))
        codes = {value.casefold() for value in _list_from_value(override.get("codes") or override.get("code"))}
        names = {_normalized_text(value) for value in _list_from_value(override.get("names") or override.get("name"))}
        contains = [_normalized_text(value) for value in _list_from_value(override.get("name_contains") or override.get("names_contains"))]
        for area in queryset.order_by("level", "name", "id"):
            if codes and str(area.code or "").casefold() in codes:
                return str(area.id)
            area_names = {_normalized_text(area.name), _normalized_text(getattr(area, "official_name", "") or "")}
            if names and area_names & names:
                return str(area.id)
            haystack = " ".join(name for name in area_names if name)
            if contains and any(needle in haystack for needle in contains):
                return str(area.id)
    except Exception:
        return ""
    return ""


def _asset_assignment_options_for_form(
    country_code: str,
    *,
    enabled: bool,
    selected_entity_ids: list[str] | None = None,
    selected_resource_qids: list[str] | None = None,
) -> dict:
    """Return only the data needed to render the first asset editor paint.

    Large countries can have tens of thousands of AdminArea rows. The edit page
    must not load every entity and every resource just because the user enters
    /configs/{pais}/. Full lists are now requested on demand through
    config_source_entities?asset_options=1, filtered by level and selected
    entity.
    """
    base = {"enabled": bool(enabled), "levels": [], "entities": [], "flags": [], "coats": []}
    if not enabled or not country_code:
        return base
    base["levels"] = _asset_assignment_level_options(country_code)
    entity_ids = [str(value) for value in (selected_entity_ids or []) if str(value or "").strip()]
    qids = [str(value) for value in (selected_resource_qids or []) if str(value or "").strip()]
    if entity_ids:
        base["entities"] = _asset_assignment_entity_options(country_code, entity_ids=entity_ids)
    if qids:
        resources = _country_asset_resource_options(country_code, selected_qids=qids)
        base["flags"] = resources.get("flag", [])
        base["coats"] = resources.get("coat", [])
    return base


def _asset_assignment_options_payload(country_code: str, *, level: str | None = None, entity_id: str | None = None) -> dict:
    """Payload used by the correction editor to load options lazily."""
    enabled = bool(country_code and _visible_admin_areas().filter(country_code__iexact=country_code).exists())
    payload = {"enabled": enabled, "levels": [], "entities": [], "flags": [], "coats": []}
    if not enabled:
        return payload
    payload["levels"] = _asset_assignment_level_options(country_code)
    if level is not None and str(level).strip() != "":
        payload["entities"] = _asset_assignment_entity_options(country_code, level=level)
    selected_entity_id = str(entity_id or "").strip()
    if selected_entity_id:
        resources = _country_asset_resource_options(country_code, entity_id=selected_entity_id)
        payload["flags"] = resources.get("flag", [])
        payload["coats"] = resources.get("coat", [])
    return payload


def _asset_assignment_level_options(country_code: str) -> list[dict[str, str]]:
    if not country_code:
        return []
    try:
        rows = (
            _visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .values("level")
            .annotate(total=Count("id"))
            .order_by("level")
        )
        return [
            {"value": str(row["level"]), "label": _("Nivel %(level)s (%(count)s)") % {"level": row["level"], "count": row["total"]}}
            for row in rows
        ]
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _asset_assignment_entity_options(
    country_code: str,
    *,
    level: str | int | None = None,
    entity_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    if not country_code:
        return []
    try:
        queryset = (
            _visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .order_by("level", "name", "id")
            .only("id", "name", "level", "code")
        )
        ids = [int(value) for value in (entity_ids or []) if str(value or "").isdigit()]
        if ids:
            queryset = queryset.filter(id__in=ids)
        elif level is not None and str(level).strip() != "":
            queryset = queryset.filter(level=int(level))
        else:
            return []
        areas = list(queryset)
    except (OperationalError, ProgrammingError, TypeError, ValueError):
        return []
    if not areas:
        return []

    assets_by_entity = _admin_area_asset_kind_map([str(area.id) for area in areas])
    entities = []
    for area in areas:
        kinds = assets_by_entity.get(str(area.id), set())
        has_flag = "flag" in kinds
        has_coat = "coat" in kinds
        status = "complete"
        if not has_flag and not has_coat:
            status = "missing_both"
        elif not has_flag:
            status = "missing_flag"
        elif not has_coat:
            status = "missing_coat"
        entities.append(
            {
                "id": str(area.id),
                "level": str(int(area.level or 0)),
                "label": _area_display_name(area),
                "status": status,
                "status_label": _asset_entity_status_label(status),
            }
        )
    return entities

def _asset_entity_status_label(status: str) -> str:
    if status == "missing_both":
        return _("Sin bandera ni escudo")
    if status == "missing_flag":
        return _("Sin bandera")
    if status == "missing_coat":
        return _("Sin escudo")
    return _("Completo")


def _admin_area_asset_kind_map(area_ids: list[str]) -> dict[str, set[str]]:
    if not area_ids or not visual_asset_tables_exist():
        return {}
    area_ids = [str(area_id) for area_id in area_ids if str(area_id or "").strip()]
    result: dict[str, set[str]] = {}
    # SQLite has a hard limit on the number of SQL variables per statement.
    # Large countries such as Spain can have tens of thousands of AdminArea rows,
    # so the asset lookup must be split into small IN() batches. Keep the batch
    # intentionally low because some local SQLite builds are compiled with a
    # lower SQLITE_MAX_VARIABLE_NUMBER than the common 999/32766 defaults.
    batch_size = 100
    with connection.cursor() as cursor:
        for start in range(0, len(area_ids), batch_size):
            batch = area_ids[start : start + batch_size]
            if not batch:
                continue
            placeholders = ", ".join(["%s"] * len(batch))
            cursor.execute(
                f"""
                    SELECT entity_key, kind, commons_filename, remote_url, local_path, local_exists
                    FROM ciudades_del_mundo_visual_asset
                    WHERE entity_type = %s
                      AND entity_key IN ({placeholders})
                      AND kind IN (%s, %s)
                """,
                ["admin_area", *batch, "flag", "coat"],
            )
            for row in _dictfetchall(cursor):
                if _visual_asset_row_has_image(row):
                    result.setdefault(str(row.get("entity_key")), set()).add(str(row.get("kind")))
    return result


def _country_asset_resource_options(
    country_code: str,
    *,
    entity_id: str | None = None,
    selected_qids: list[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    result = {"flag": [], "coat": []}
    if not country_code or not visual_asset_tables_exist():
        return result

    selected_qids_set = {str(value) for value in (selected_qids or []) if str(value or "").strip()}
    selected_area = None
    current_entity_qids: set[str] = set()
    entity_id = str(entity_id or "").strip()
    if entity_id:
        try:
            selected_area = _visible_admin_areas().filter(country_code__iexact=country_code, id=int(entity_id)).only(
                "id", "name", "code", "level"
            ).first()
        except (OperationalError, ProgrammingError, TypeError, ValueError):
            selected_area = None

    resources: dict[tuple[str, str], dict[str, object]] = {}
    assigned: set[tuple[str, str]] = set()
    rows: list[dict] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT entity_type, entity_key, entity_name, kind, wikidata_id, commons_filename, remote_url, local_path, local_exists
                FROM ciudades_del_mundo_visual_asset
                WHERE country_code = %s
                  AND kind IN (%s, %s)
                  AND wikidata_id <> ''
                  AND (
                    entity_type = %s
                    OR entity_type = %s
                    OR (entity_type = %s AND entity_key = %s)
                  )
                ORDER BY kind, entity_name, commons_filename
            """,
            [country_code, "flag", "coat", "wikidata_country_resource", "country", "admin_area", entity_id],
        )
        rows = _dictfetchall(cursor)

    for row in rows:
        if not _visual_asset_row_has_image(row):
            continue
        kind = str(row.get("kind") or "")
        qid = str(row.get("wikidata_id") or "")
        if kind not in result or not qid:
            continue
        key = (kind, qid)
        if str(row.get("entity_type") or "") in {"admin_area", "country"}:
            assigned.add(key)
            if not entity_id or str(row.get("entity_key") or "") == entity_id:
                current_entity_qids.add(qid)
        if entity_id and not _asset_resource_matches_selected_entity(row, selected_area, selected_qids_set | current_entity_qids):
            continue
        label = str(row.get("entity_name") or "").strip() or qid
        filename = str(row.get("commons_filename") or "").strip()
        if filename and filename not in label:
            label = f"{label} — {filename}"
        resources.setdefault(key, {"value": qid, "label": label, "assigned": False})

    for key, option in resources.items():
        option["assigned"] = key in assigned
        result[key[0]].append(option)
    for kind in result:
        result[kind].sort(key=lambda item: (bool(item.get("assigned")), str(item.get("label") or "").casefold()))
    return result


def _asset_resource_matches_selected_entity(row: dict, area, forced_qids: set[str]) -> bool:
    qid = str(row.get("wikidata_id") or "")
    if qid and qid in forced_qids:
        return True
    if str(row.get("entity_type") or "") == "admin_area":
        return True
    if area is None:
        return False
    needles = set()
    for value in (getattr(area, "name", ""), getattr(area, "official_name", ""), getattr(area, "code", "")):
        needles.update(_name_match_variants(value))
    haystack_values = [
        row.get("entity_name"),
        row.get("commons_filename"),
        row.get("entity_key"),
    ]
    haystack = " ".join(_normalized_text(value) for value in haystack_values if str(value or "").strip())
    if not haystack:
        return False
    return any(needle and (needle in haystack or haystack in needle) for needle in needles)

def _list_from_value(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("\n", ",").split(",")
    return [str(item or "").strip() for item in raw_values if str(item or "").strip()]


def _visual_asset_row_has_image(row: dict) -> bool:
    return bool(
        row.get("commons_filename")
        or row.get("remote_url")
        or row.get("local_path")
        or row.get("local_exists")
    )


def _dictfetchall(cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _selected_ids_from_city_config(country_code: str, city_config: dict) -> list[str]:
    labels = [str(value) for value in city_config.get("communes") or [] if str(value).strip()]
    areas = _visible_admin_areas().filter(country_code=country_code).only("id", "name", "code", "level", "entity_type", "parent")
    by_key = {}
    for area in areas:
        for key in (area.id, area.code, area.name):
            by_key[str(key).casefold()] = area.id
    selected: list[str] = []
    for label in labels:
        match = by_key.get(label.casefold())
        if match and match not in selected:
            selected.append(match)

    if selected:
        return selected

    # Algunas configuraciones históricas sólo definen la unificación mediante
    # `from` + `district_types`, sin listar `communes`. En ese caso reconstruimos
    # la selección para que la tabla derecha no aparezca vacía al editar.
    raw_from = city_config.get("from") or {}
    if not isinstance(raw_from, dict):
        return selected
    district_types = {str(value) for value in city_config.get("district_types") or [] if str(value).strip()}
    for raw_level, raw_parent_labels in raw_from.items():
        try:
            parent_level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_parent_labels, (str, int)):
            parent_labels = [str(raw_parent_labels)]
        else:
            parent_labels = [str(value) for value in raw_parent_labels or [] if str(value).strip()]
        if not parent_labels:
            continue
        parent_keys = {label.casefold() for label in parent_labels}
        parents = [
            area.id
            for area in _visible_admin_areas().filter(country_code=country_code, level=parent_level).only("id", "name", "code")
            if str(area.name).casefold() in parent_keys or str(area.code).casefold() in parent_keys or str(area.id).casefold() in parent_keys
        ]
        if not parents:
            continue
        children = _visible_admin_areas().filter(country_code=country_code, parent_id__in=parents).only("id", "entity_type")
        if district_types:
            children = children.filter(entity_type__in=district_types)
        for area in children:
            if area.id not in selected:
                selected.append(area.id)
    return selected


def _source_entities_for_config(slug: str, *, level: str | int | None = None) -> list[dict]:
    if not slug:
        return []
    areas = (
        _visible_admin_areas()
        .filter(country_code__iexact=slug)
        .exclude(level=0)
        .select_related("parent")
        .order_by("level", "parent__name", "name")
    )
    if level is not None and str(level).strip():
        areas = areas.filter(level=int(level))
    rows = []
    for area in areas:
        parent = area.parent
        entity_type = _entity_type_label(area.entity_type, country_code=area.country_code)
        entity_type_filter = _source_entity_type_filter_label(area.entity_type, entity_type, area.country_code)
        rows.append(
            {
                "id": area.id,
                "name": _area_display_name(area),
                "raw_name": area.name,
                "level": area.level,
                "entity_type": entity_type,
                "raw_entity_type": area.entity_type,
                "entity_type_filter": entity_type_filter,
                "parent": _area_display_name(parent) if parent else "",
                "parent_key": parent.id if parent else "",
                "parent_level": parent.level if parent else None,
                "raw_parent": parent.name if parent else "",
            }
        )
    return rows


def _source_level_filter_options_for_country(country_code: str) -> list[dict]:
    if not country_code:
        return []
    levels: dict[str, dict[str, object]] = {}
    rows = (
        _group_source_admin_areas()
        .filter(country_code__iexact=country_code)
        .exclude(level=0)
        .values("level", "entity_type")
        .annotate(total=Count("id"))
        .order_by("level", "entity_type")
    )
    for row in rows:
        level_key = str(row["level"])
        level_data = levels.setdefault(level_key, {"count": 0, "types": {}})
        total = int(row["total"] or 0)
        level_data["count"] = int(level_data["count"]) + total
        raw_type = str(row["entity_type"] or "")
        display_type = _entity_type_label(raw_type, country_code=country_code)
        type_label = _source_entity_type_filter_label(raw_type, display_type, country_code)
        if type_label:
            types = level_data["types"]
            assert isinstance(types, dict)
            types[type_label] = int(types.get(type_label, 0)) + total
    return _source_level_rows_from_counts(levels)


def _source_level_filter_options(entities: list[dict]) -> list[dict]:
    levels: dict[str, dict[str, object]] = {}
    for entity in entities:
        level_key = str(entity.get("level") or "").strip()
        if not level_key:
            continue
        level_data = levels.setdefault(level_key, {"count": 0, "types": {}})
        level_data["count"] = int(level_data["count"]) + 1
        type_label = str(entity.get("entity_type_filter") or entity.get("entity_type") or "").strip()
        if type_label:
            types = level_data["types"]
            assert isinstance(types, dict)
            types[type_label] = int(types.get(type_label, 0)) + 1

    return _source_level_rows_from_counts(levels)


def _source_level_rows_from_counts(levels: dict[str, dict[str, object]]) -> list[dict]:
    rows = []
    for level in sorted(levels, key=_source_level_sort_key):
        level_data = levels[level]
        count = int(level_data["count"])
        types = level_data["types"]
        assert isinstance(types, dict)
        type_labels = sorted(types, key=lambda value: (-int(types[value]), value.casefold()))
        label = "/".join(type_labels) if type_labels else _("Nivel %(level)s") % {"level": level}
        rows.append({"value": level, "label": f"{label} ({count})"})
    return rows


def _source_level_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def _source_entity_type_filter_label(raw_type: str | None, display_type: str, country_code: str) -> str:
    if str(country_code or "").lower() == "spain" and str(raw_type or "") in {"Municipality seat", "Locality"}:
        return _("Cabecera de Municipio/Localidad")
    return display_type


def _source_parent_filter_options(entities: list[dict], *, level: str | None = None) -> list[dict]:
    parents: dict[str, dict[str, str]] = {}
    level_value = str(level or "").strip()
    if level_value == "1":
        return []
    for entity in entities:
        if level_value and str(entity.get("level") or "").strip() != level_value:
            continue
        parent_level = entity.get("parent_level")
        if parent_level is not None and int(parent_level) == 0:
            continue
        parent_key = str(entity.get("parent_key") or "").strip()
        if parent_key and parent_key not in parents:
            parents[parent_key] = {
                "value": parent_key,
                "label": str(entity.get("parent") or entity.get("raw_parent") or parent_key),
            }
    return sorted(parents.values(), key=lambda row: row["label"].casefold())


def _source_entity_type_filter_options(entities: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for entity in entities:
        value = str(entity.get("entity_type_filter") or entity.get("entity_type") or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [
        {"value": value, "label": f"{value} ({counts[value]})"}
        for value in sorted(counts, key=str.casefold)
    ]


def _config_content_from_request(request, slug: str, current_content: str, *, existing: bool) -> str:
    editor_mode = str(request.POST.get("editor_mode") or "file").strip().lower()
    if editor_mode == "manual":
        return _render_config_from_manual_post(slug, request.POST, current_content=current_content)
    if editor_mode == "file":
        return request.POST.get("content", current_content)
    raise ValueError(_("La sección Scrapping solo genera una previsualización. Para guardar, abre Manual o Archivo."))


def _render_config_from_manual_post(slug: str, post, *, current_content: str = "") -> str:
    country_code = (post.get("manual_country_code") or slug).strip() or slug
    legal_subdivision = (post.get("manual_legal_subdivision") or "").strip()
    pages = _manual_pages_from_post(post)
    cities_loaded = str(post.get("config_cities_loaded") or "").strip() == "1"
    configured_cities = _manual_cities_from_post(post, legal_subdivision=legal_subdivision) if cities_loaded else None
    visual_assets = _manual_visual_assets_from_post(post)
    asset_overrides = _manual_asset_overrides_from_post(post)
    if not pages:
        raise ValueError(_("Debes indicar al menos una ruta de scrapeo."))

    preserved = _preserved_manual_config_fragments(current_content, preserve_cities=not cities_loaded)
    lines = []
    if country_code and country_code != slug:
        lines.append(f"country_code = {_toml_string(country_code)}")
    if legal_subdivision:
        try:
            lines.append(f"LEGAL_SUBDIVISION = {int(legal_subdivision)}")
        except (TypeError, ValueError) as exc:
            raise ValueError(_("La subdivisión legal debe ser numérica.")) from exc
    lines.append("scrape_schema_version = 2")
    if preserved["top_level"]:
        lines.extend(preserved["top_level"])
    lines.append("")

    for page in pages:
        lines.extend(
            [
                "[[pages]]",
                f"source = {_toml_string(page['source'])}",
                f"path = {_toml_array(page['path'])}",
            ]
        )
        if page.get("enabled") is False:
            lines.append("enabled = false")
        if page.get("force_highest_level") is not None:
            lines.append(f"force_highest_level = {page['force_highest_level']}")
        if page.get("parent_level") is not None:
            lines.append(f"parent_level = {page['parent_level']}")
        if page.get("sum_to_root"):
            lines.append("sum_to_root = true")
        lines.append(f"include = {_toml_scalar_inline_table(page['include'])}")
        if page.get("repeat"):
            lines.append(f"repeat = {_toml_scalar_inline_table(page['repeat'])}")
        lines.append("")

    if configured_cities is not None:
        for city in configured_cities:
            lines.extend(
                [
                    "[[cities]]",
                    f"city = {_toml_string(city['city'])}",
                    f"id = {_toml_string(city['id'])}",
                    f"level = {int(city['level'])}",
                    f"type = {_toml_string(city['type'])}",
                    f"parent_type = {_toml_string(city['parent_type'])}",
                    f"from = {_toml_inline_table({int(city['parent_level']): [city['parent_name']]})}",
                ]
            )
            if city.get("communes"):
                lines.append(f"communes = {_toml_array(city['communes'])}")
            lines.append(f"keep_communes = {_toml_bool(city.get('keep_communes', False))}")
            lines.append("")

    if preserved["blocks"]:
        lines.extend(preserved["blocks"])
        if lines and lines[-1] != "":
            lines.append("")

    if visual_assets:
        lines.append("[visual_assets]")
        lines.append(f"bulk_country_wikidata = {_toml_bool(visual_assets.get('bulk_country_wikidata', True))}")
        lines.append(f"strict_required = {_toml_bool(visual_assets.get('strict_required', True))}")
        if visual_assets.get("required_kinds"):
            lines.append(f"required_kinds = {_toml_array(visual_assets['required_kinds'])}")
        if visual_assets.get("required_levels"):
            lines.append(f"required_levels = [{', '.join(str(int(level)) for level in visual_assets['required_levels'])}]")
        lines.append("")

    for override in asset_overrides:
        lines.append("[[wikidata_asset_overrides]]")
        for key in ("ids", "names", "name_contains", "codes", "kinds"):
            if override.get(key):
                lines.append(f"{key} = {_toml_array(override[key])}")
        if override.get("level") is not None:
            lines.append(f"level = {int(override['level'])}")
        lines.append(f"wikidata_id = {_toml_string(override['wikidata_id'])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _preserved_manual_config_fragments(content: str, *, preserve_cities: bool = False) -> dict[str, list[str]]:
    """Keep TOML sections that the visual manual editor does not own.

    The manual editor edits the country identity fields, [[pages]], [[cities]],
    [visual_assets] and explicit wikidata asset overrides.  Everything else
    (representation, entity merges, runtime synthetic rows, etc.) must survive a
    manual save/populate cycle.
    """
    managed_top_keys = {
        "country_code",
        "wikidata_id",
        "LEGAL_SUBDIVISION",
        "scrape_schema_version",
        "base_url",
    }
    managed_tables = {
        "pages",
        "visual_assets",
        "wikidata_asset_overrides",
        "visual_asset_overrides",
        "asset_overrides",
    }
    if not preserve_cities:
        managed_tables.add("cities")
    top_level: list[str] = []
    blocks: list[str] = []
    current_block: list[str] = []
    current_header = ""

    def flush_block() -> None:
        nonlocal current_block, current_header
        if not current_block:
            return
        header_name = _toml_header_name(current_header)
        if header_name not in managed_tables:
            while blocks and blocks[-1] == "" and current_block and current_block[0] == "":
                current_block = current_block[1:]
            blocks.extend(current_block)
            if blocks and blocks[-1] != "":
                blocks.append("")
        current_block = []
        current_header = ""

    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_block()
            current_header = stripped
            current_block = [raw_line]
            continue
        if current_block:
            current_block.append(raw_line)
            continue
        if not stripped or stripped.startswith("#"):
            if top_level and top_level[-1] != "":
                top_level.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key and key not in managed_top_keys:
            top_level.append(raw_line)
    flush_block()

    while top_level and top_level[-1] == "":
        top_level.pop()
    while blocks and blocks[-1] == "":
        blocks.pop()
    return {"top_level": top_level, "blocks": blocks}


def _toml_header_name(header: str) -> str:
    text = str(header or "").strip()
    while text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.split(".", 1)[0].strip()


def _manual_cities_from_post(post, *, legal_subdivision: str) -> list[dict]:
    raw = str(post.get("config_cities_json") or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(_("La configuración de ciudades no es JSON válido.")) from exc
    if not isinstance(payload, list):
        raise ValueError(_("La configuración de ciudades debe ser una lista."))

    fallback_level = None
    if str(legal_subdivision or "").strip():
        try:
            fallback_level = int(legal_subdivision)
        except (TypeError, ValueError):
            fallback_level = None

    cities = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        parent_name = str(item.get("parent_name") or "").strip()
        parent_level = _manual_city_int(item.get("parent_level"))
        city_name = str(item.get("city") or "").strip()
        if not city_name:
            raise ValueError(_("El nombre de la ciudad unificada no puede estar vacío."))
        city_code = str(item.get("code") or item.get("id") or item.get("parent_code") or "").strip()
        city_level = _manual_city_int(item.get("level"))
        if city_level is None:
            city_level = fallback_level if fallback_level is not None else (parent_level + 1 if parent_level is not None else None)
        communes = []
        for value in item.get("communes") or item.get("selected_codes") or []:
            text = str(value or "").strip()
            if text and text not in communes:
                communes.append(text)
        if not parent_name or parent_level is None or not city_name or not city_code or city_level is None or not communes:
            continue
        cities.append(
            {
                "city": city_name,
                "id": city_code,
                "level": city_level,
                "type": str(item.get("type") or "City").strip() or "City",
                "parent_level": parent_level,
                "parent_name": parent_name,
                "parent_type": str(item.get("parent_type") or "").strip(),
                "communes": communes,
                "keep_communes": _form_bool(item.get("keep_communes"), default=False),
            }
        )
    return cities


def _manual_city_int(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manual_visual_assets_from_post(post) -> dict:
    required_kinds = _list_from_text(post.get("visual_asset_required_kinds")) or ["flag", "coat"]
    required_levels = _int_list_from_text(post.get("visual_asset_required_levels"), label="required_levels")
    return {
        "bulk_country_wikidata": _form_bool(post.get("visual_asset_bulk_country_wikidata"), default=True),
        "strict_required": _form_bool(post.get("visual_asset_strict_required"), default=True),
        "required_kinds": required_kinds,
        "required_levels": required_levels,
    }


def _manual_asset_overrides_from_post(post) -> list[dict]:
    levels = post.getlist("asset_assignment_level")
    entity_ids = post.getlist("asset_assignment_entity_id")
    flag_qids = post.getlist("asset_assignment_flag_qid")
    coat_qids = post.getlist("asset_assignment_coat_qid")

    rows = []
    for index, values in enumerate(
        zip_longest(levels, entity_ids, flag_qids, coat_qids, fillvalue=""),
        start=1,
    ):
        raw_level, raw_entity_id, raw_flag_qid, raw_coat_qid = values
        entity_id = str(raw_entity_id or "").strip()
        flag_qid = str(raw_flag_qid or "").strip()
        coat_qid = str(raw_coat_qid or "").strip()
        if not entity_id and not flag_qid and not coat_qid and not str(raw_level or "").strip():
            continue
        if not entity_id:
            raise ValueError(_("La corrección de assets %(index)s debe seleccionar una entidad.") % {"index": index})
        level = str(raw_level or "").strip()
        level_value = None
        if level:
            try:
                level_value = int(level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El nivel de la corrección de assets %(index)s debe ser numérico.") % {"index": index}) from exc
        for kind, qid in (("flag", flag_qid), ("coat", coat_qid)):
            if not qid:
                continue
            row = {
                "ids": [entity_id],
                "wikidata_id": qid,
                "kinds": [kind],
            }
            if level_value is not None:
                row["level"] = level_value
            rows.append(row)
    return rows


def _form_bool(value, *, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "si", "sí", "on"}


def _int_list_from_text(value: str | None, *, label: str) -> list[int]:
    result = []
    for item in _list_from_text(value):
        try:
            result.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(_("%(label)s debe contener solo números separados por comas.") % {"label": label}) from exc
    return result



def _manual_page_from_payload(raw_page: dict, *, index: int) -> dict:
    if not isinstance(raw_page, dict):
        raise ValueError(_("La página %(index)s no tiene un formato válido.") % {"index": index})
    source = _normalize_page_source_for_form(raw_page.get("source"))
    raw_paths = raw_page.get("path", raw_page.get("paths", []))
    if isinstance(raw_paths, str):
        paths = _page_paths_from_text(raw_paths)
    elif isinstance(raw_paths, (list, tuple)):
        paths = [str(item).strip() for item in raw_paths if str(item or "").strip()]
    else:
        paths = []
    if not paths:
        return {}
    raw_level = str(raw_page.get("force_highest_level", "") or "").strip()
    level_int = None
    if raw_level:
        try:
            level_int = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("El nivel forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
    raw_parent_level = str(raw_page.get("parent_level", raw_page.get("force_parent_level", "")) or "").strip()
    parent_level_int = None
    if raw_parent_level:
        try:
            parent_level_int = int(raw_parent_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("El padre forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
    include = raw_page.get("include") if isinstance(raw_page.get("include"), dict) else {}
    page: dict[str, object] = {
        "enabled": _form_bool(raw_page.get("enabled"), default=True),
        "source": source,
        "path": paths,
        "sum_to_root": _form_bool(raw_page.get("sum_to_root"), default=False),
    }
    if level_int is not None:
        page["force_highest_level"] = level_int
    if parent_level_int is not None:
        page["parent_level"] = parent_level_int
    repeat = raw_page.get("repeat") if isinstance(raw_page.get("repeat"), dict) else {}
    page["include"] = {
        "infosection": _form_bool(include.get("infosection"), default=True),
        "major_subdivision": _form_bool(include.get("major_subdivision", include.get("admin1")), default=True),
    }
    if source in {"admin", "citiesadmin"}:
        page["include"]["minor_subdivision"] = _form_bool(
            include.get("minor_subdivision", include.get("admin2")),
            default=True,
        )
    if source in {"cities", "citiesadmin"}:
        page["include"]["cities"] = _form_bool(include.get("cities"), default=True)
    repeat_rows = _manual_repeat_dict(
        infosection=repeat.get("infosection", raw_page.get("repeat_infosection")),
        major_subdivision=repeat.get("major_subdivision", repeat.get("admin1", raw_page.get("repeat_major_subdivision"))),
        minor_subdivision=repeat.get("minor_subdivision", repeat.get("admin2", raw_page.get("repeat_minor_subdivision"))),
        cities=repeat.get("cities", raw_page.get("repeat_cities")),
    )
    if repeat_rows:
        page["repeat"] = repeat_rows
    return page


def _manual_repeat_dict(**values) -> dict[str, int]:
    repeats: dict[str, int] = {}
    for key, value in values.items():
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 1:
            repeats[key] = parsed
    return repeats


def _manual_pages_from_json(post) -> list[dict] | None:
    raw = str(post.get("pages_json") or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(_("No se pudo leer la configuración de páginas enviada por el formulario.")) from exc
    if not isinstance(payload, list):
        raise ValueError(_("La configuración de páginas enviada por el formulario no es una lista válida."))

    # La UI envía `pages_json` para conservar rutas múltiples y orden de filas.
    # Si el navegador conserva una versión antigua del JS, ese JSON puede traer
    # `source = cities` aunque el `<select name="page_source">` visible tenga
    # `citiesadmin`. Por seguridad, el backend toma el procedimiento directamente
    # del campo POST real, que es la fuente más fiable para este valor puntual.
    source_overrides = [
        _normalize_page_source_for_form(value)
        for value in post.getlist("page_source")
    ]
    enabled_overrides = [
        _form_bool(value, default=True)
        for value in post.getlist("page_enabled")
    ]
    force_level_overrides = [str(value or "").strip() for value in post.getlist("page_force_highest_level")]
    parent_level_overrides = [str(value or "").strip() for value in post.getlist("page_parent_level")]
    sum_to_root_overrides = [
        _form_bool(value, default=False)
        for value in post.getlist("page_sum_to_root")
    ]
    include_overrides = {
        "infosection": [_form_bool(value, default=True) for value in post.getlist("page_include_infosection")],
        "major_subdivision": [
            _form_bool(value, default=True)
            for value in post.getlist("page_include_major_subdivision")
        ],
        "minor_subdivision": [
            _form_bool(value, default=True)
            for value in (post.getlist("page_include_minor_subdivision") or post.getlist("page_include_admin2"))
        ],
        "cities": [_form_bool(value, default=True) for value in post.getlist("page_include_cities")],
    }
    repeat_overrides = {
        "infosection": [str(value or "").strip() for value in post.getlist("page_repeat_infosection")],
        "major_subdivision": [str(value or "").strip() for value in post.getlist("page_repeat_major_subdivision")],
        "minor_subdivision": [
            str(value or "").strip()
            for value in (post.getlist("page_repeat_minor_subdivision") or post.getlist("page_repeat_admin2"))
        ],
        "cities": [str(value or "").strip() for value in post.getlist("page_repeat_cities")],
    }

    pages: list[dict] = []
    for index, raw_page in enumerate(payload, start=1):
        if isinstance(raw_page, dict) and index <= len(source_overrides):
            raw_page = {**raw_page, "source": source_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(enabled_overrides):
            raw_page = {**raw_page, "enabled": enabled_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(force_level_overrides):
            raw_page = {**raw_page, "force_highest_level": force_level_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(parent_level_overrides):
            raw_page = {**raw_page, "parent_level": parent_level_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(sum_to_root_overrides):
            raw_page = {**raw_page, "sum_to_root": sum_to_root_overrides[index - 1]}
        if isinstance(raw_page, dict):
            include = raw_page.get("include") if isinstance(raw_page.get("include"), dict) else {}
            include = dict(include)
            for key, values in include_overrides.items():
                if index <= len(values):
                    include[key] = values[index - 1]
            raw_page = {**raw_page, "include": include}
            repeat = raw_page.get("repeat") if isinstance(raw_page.get("repeat"), dict) else {}
            repeat = dict(repeat)
            for key, values in repeat_overrides.items():
                if index <= len(values):
                    repeat[key] = values[index - 1]
            raw_page = {**raw_page, "repeat": repeat}
        page = _manual_page_from_payload(raw_page, index=index)
        if page:
            pages.append(page)
    return pages


def _manual_pages_from_post(post) -> list[dict]:
    pages_from_json = _manual_pages_from_json(post)
    if pages_from_json is not None:
        return pages_from_json
    page_paths = post.getlist("page_path") or post.getlist("page_url")
    page_sources = post.getlist("page_source")
    page_levels = post.getlist("page_force_highest_level")
    page_parent_levels = post.getlist("page_parent_level")
    page_enabled_values = post.getlist("page_enabled")
    include_infosections = post.getlist("page_include_infosection")
    include_major_subdivisions = post.getlist("page_include_major_subdivision")
    include_minor_subdivisions = post.getlist("page_include_minor_subdivision") or post.getlist("page_include_admin2")
    include_cities = post.getlist("page_include_cities")
    page_sum_to_roots = post.getlist("page_sum_to_root")
    repeat_infosections = post.getlist("page_repeat_infosection")
    repeat_major_subdivisions = post.getlist("page_repeat_major_subdivision")
    repeat_minor_subdivisions = post.getlist("page_repeat_minor_subdivision") or post.getlist("page_repeat_admin2")
    repeat_cities_values = post.getlist("page_repeat_cities")

    pages = []
    for index, values in enumerate(
        zip_longest(
            page_sources,
            page_paths,
            page_levels,
            page_parent_levels,
            page_enabled_values,
            include_infosections,
            include_major_subdivisions,
            include_minor_subdivisions,
            include_cities,
            page_sum_to_roots,
            repeat_infosections,
            repeat_major_subdivisions,
            repeat_minor_subdivisions,
            repeat_cities_values,
            fillvalue="",
        ),
        start=1,
    ):
        (
            source,
            raw_paths,
            raw_level,
            raw_parent_level,
            raw_enabled,
            raw_include_infosection,
            raw_include_major,
            raw_include_minor,
            raw_include_cities,
            raw_sum_to_root,
            raw_repeat_infosection,
            raw_repeat_major,
            raw_repeat_minor,
            raw_repeat_cities,
        ) = values
        paths = _page_paths_from_text(raw_paths)
        if not paths:
            continue
        source = _normalize_page_source_for_form(source)
        raw_level = str(raw_level or "").strip()
        level_int = None
        if raw_level:
            try:
                level_int = int(raw_level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El nivel forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
        raw_parent_level = str(raw_parent_level or "").strip()
        parent_level_int = None
        if raw_parent_level:
            try:
                parent_level_int = int(raw_parent_level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El padre forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
        page: dict[str, object] = {"source": source, "path": paths, "enabled": _form_bool(raw_enabled, default=True)}
        if level_int is not None:
            page["force_highest_level"] = level_int
        if parent_level_int is not None:
            page["parent_level"] = parent_level_int
        page["sum_to_root"] = _form_bool(raw_sum_to_root, default=False)
        page["include"] = {
            "infosection": _form_bool(raw_include_infosection, default=True),
            "major_subdivision": _form_bool(raw_include_major, default=True),
        }
        if source in {"admin", "citiesadmin"}:
            page["include"]["minor_subdivision"] = _form_bool(raw_include_minor, default=True)
        if source in {"cities", "citiesadmin"}:
            page["include"]["cities"] = _form_bool(raw_include_cities, default=True)
        repeat_rows = _manual_repeat_dict(
            infosection=raw_repeat_infosection,
            major_subdivision=raw_repeat_major,
            minor_subdivision=raw_repeat_minor,
            cities=raw_repeat_cities,
        )
        if repeat_rows:
            page["repeat"] = repeat_rows
        pages.append(page)
    return pages


def _page_paths_from_text(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = tomllib.loads(f"value = {text}\n").get("value")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(_("path debe ser una lista TOML válida o una ruta por línea.")) from exc
        values = parsed if isinstance(parsed, list) else [parsed]
    else:
        parts: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "," in stripped:
                parts.extend(part.strip() for part in stripped.split(","))
            else:
                parts.append(stripped)
        values = parts
    paths = []
    for value in values:
        path = str(value or "").strip()
        if path in {"/", ".", "<root>", "<raiz>", "<raíz>"}:
            path = ""
        path = path.strip("/")
        if path not in paths:
            paths.append(path)
    return paths


def _page_paths_text_for_form(value) -> str:
    return "\n".join(_page_paths_list_for_form(value))


def _page_paths_list_for_form(value) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    paths: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            text = "/"
        if text not in paths:
            paths.append(text)
    return paths


def _list_from_text(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = tomllib.loads(f"value = {text}\n").get("value")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(_("La lista debe ser TOML válida o valores separados por comas.")) from exc
        raw_values = parsed if isinstance(parsed, list) else [parsed]
    else:
        raw_values = []
        for line in text.splitlines():
            raw_values.extend(line.split(","))
    values = []
    for raw in raw_values:
        item = str(raw or "").strip()
        if item and item not in values:
            values.append(item)
    return values


def _int_mapping_from_text(value: str | None, *, label: str) -> dict[str, int]:
    text = str(value or "").strip()
    if not text:
        return {}
    if not text.startswith("{"):
        text = "{ " + text + " }"
    try:
        parsed = tomllib.loads(f"value = {text}\n").get("value")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(label)s debe tener formato TOML, por ejemplo: ts = 3, tl = 2") % {"label": label}) from exc
    if not isinstance(parsed, dict):
        raise ValueError(_("%(label)s debe ser un mapa TOML.") % {"label": label})
    result = {}
    for key, raw_value in parsed.items():
        try:
            result[str(key)] = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("%(label)s[%(key)s] debe ser numérico.") % {"label": label, "key": key}) from exc
    return result


def _optional_bool(value: str | None) -> bool | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "si", "sí"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(_("El valor booleano debe ser true o false."))


def _bool_text(value) -> str:
    return "true" if bool(value) else "false"


def _list_text_for_form(value) -> str:
    if not value:
        return ""
    values = value if isinstance(value, list) else [value]
    return ", ".join(str(item) for item in values)


def _mapping_text_for_form(value) -> str:
    if not value or not isinstance(value, dict):
        return ""
    return ", ".join(f"{key} = {value[key]}" for key in sorted(value))


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _parent_from_for_selected(selected: list[AdminArea]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for area in selected:
        parent = area.parent
        if not parent:
            continue
        labels = grouped.setdefault(parent.level, [])
        if parent.name not in labels:
            labels.append(parent.name)
    return grouped


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_key(value: str) -> str:
    text = str(value)
    if re.match(r"^[A-Za-z0-9_-]+$", text):
        return text
    return _toml_string(text)


def _toml_scalar_inline_table(mapping: dict[str, object]) -> str:
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        value = mapping[key]
        if isinstance(value, bool):
            rendered = _toml_bool(value)
        elif isinstance(value, int):
            rendered = str(value)
        else:
            rendered = _toml_string(str(value))
        parts.append(f"{_toml_key(str(key))} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def _toml_inline_table(mapping: dict[int, list[str]]) -> str:
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        parts.append(f"{int(key)} = {_toml_array(mapping[key])}")
    return "{ " + ", ".join(parts) + " }"


def _slugify_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower())
    return normalized.strip("-") or "city"



def _generate_citypopulation_config(slug: str) -> tuple[str, list[str]]:
    """Generate a TOML draft by probing the real CityPopulation country pages.

    The older helper only listed links found in ``/en/<slug>/`` and guessed the
    scraper type from the path.  This version opens the country index, selects
    routes that usually contain the official administrative / locality data,
    fetches those pages, detects their real table structure, and then writes a
    DB-ready TOML draft grouped by compatible page hints.
    """
    url = f"https://www.citypopulation.de/en/{slug}/"
    log = [_('Entrando en %(url)s') % {"url": url}]
    root_html = _read_citypopulation_html(url)
    root_probe = _citypopulation_page_probe(
        slug=slug,
        path="",
        html=root_html,
        url=url,
        category="root",
    )
    root_name = _citypopulation_root_name(root_probe, slug)

    candidates = _citypopulation_route_candidates(slug, root_html, base_url=url)
    selected = _select_citypopulation_candidates(candidates)
    log.append(
        _('%(total)s rutas encontradas; %(selected)s rutas útiles seleccionadas para comprobar.')
        % {"total": len(candidates), "selected": len(selected)}
    )

    probes: list[CityPopulationPageProbe] = []
    if _citypopulation_probe_has_data(root_probe):
        probes.append(root_probe)
        log.append(_('La página raíz contiene tablas o sección informativa útil.'))

    for candidate in selected:
        if len(probes) >= CITYPOPULATION_DISCOVERY_MAX_PAGES:
            log.append(
                _('Límite de comprobación alcanzado (%(limit)s páginas).')
                % {"limit": CITYPOPULATION_DISCOVERY_MAX_PAGES}
            )
            break
        candidate_url = _citypopulation_url_for_path(slug, candidate.path)
        try:
            html = _read_citypopulation_html(candidate_url)
        except (OSError, URLError, ValueError) as exc:
            log.append(_('Omitida %(path)s: %(error)s') % {"path": candidate.path or "/", "error": exc})
            continue
        probe = _citypopulation_page_probe(
            slug=slug,
            path=candidate.path,
            html=html,
            url=candidate_url,
            category=candidate.category,
        )
        if not _citypopulation_probe_has_data(probe):
            log.append(_('Omitida %(path)s: no se detectaron tablas de población útiles.') % {"path": candidate.path})
            continue
        probes.append(probe)

    if not probes:
        raise ValueError(_('No se encontraron páginas de población útiles para %(slug)s.') % {"slug": slug})

    generated_pages = _generated_pages_from_citypopulation_probes(probes, candidates)
    if not generated_pages:
        raise ValueError(_('No se pudo construir una configuración válida para %(slug)s.') % {"slug": slug})

    content = _render_citypopulation_generated_config(
        slug=slug,
        name=root_name,
        pages=generated_pages,
        legal_subdivision=_infer_citypopulation_legal_subdivision(generated_pages, probes),
    )
    log.append(_('Páginas TOML generadas: %(count)s') % {"count": len(generated_pages)})
    return content, log


def _read_citypopulation_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": CITYPOPULATION_GENERATOR_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=CITYPOPULATION_DISCOVERY_TIMEOUT) as response:  # noqa: S310 - user-triggered local scraping helper.
            return response.read().decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        raise ValueError(_('No se pudo leer CityPopulation: %(error)s') % {"error": exc}) from exc


def _citypopulation_route_candidates(slug: str, html: str, *, base_url: str) -> list[CityPopulationRouteCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(class_="cindex") or soup
    seen: set[str] = set()
    candidates: list[CityPopulationRouteCandidate] = []
    for link in root.find_all("a", href=True):
        path = _citypopulation_path_from_href(slug, link.get("href") or "", base_url=base_url)
        if path is None or path in seen:
            continue
        title_node = link.find("h3")
        title = _clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        description = _clean_text(" ".join(node.get_text(" ", strip=True) for node in link.find_all("p")[:2]))
        section = _citypopulation_previous_heading(link)
        category = _citypopulation_candidate_category(path, title=title, description=description, section=section)
        if category == "excluded":
            continue
        seen.add(path)
        candidates.append(
            CityPopulationRouteCandidate(
                path=path,
                title=title,
                description=description,
                section=section,
                category=category,
            )
        )
    return candidates


def _citypopulation_path_from_href(slug: str, href: str, *, base_url: str) -> str | None:
    href = str(href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc and parsed.netloc not in {"www.citypopulation.de", "citypopulation.de"}:
        return None
    marker = f"/en/{slug}/"
    path = parsed.path
    if marker not in path:
        return None
    relative = path.split(marker, 1)[1].strip("/")
    if not relative:
        return ""
    if relative.startswith(("maps/", "search/", "help/")):
        return None
    if ".." in relative or relative.endswith((".html", ".htm")):
        return None
    return relative


def _citypopulation_url_for_path(slug: str, path: str) -> str:
    path = str(path or "").strip("/")
    if path:
        return f"https://www.citypopulation.de/en/{slug}/{path}/"
    return f"https://www.citypopulation.de/en/{slug}/"


def _citypopulation_candidate_category(path: str, *, title: str = "", description: str = "", section: str = "") -> str:
    value = str(path or "").strip("/").casefold()
    text = f"{value} {title} {description} {section}".casefold()
    segments = [segment for segment in value.split("/") if segment]
    if not value:
        return "root"
    excluded_segments = {
        "agglo",
        "urbanareas",
        "metro",
        "combmetro",
        "ua",
        "census",
        "settlements",
        "townships",
        "map",
        "maps",
        "search",
        "help",
    }
    if any(segment in excluded_segments for segment in segments):
        return "excluded"
    if any(word in text for word in ("top 10", "urban area", "agglomeration", "metropolitan", "micropolitan")):
        return "excluded"
    if value in {"admin", "reg/admin", "prov/admin", "states/admin", "state/admin", "province/admin"}:
        return "main_admin"
    if value in {"cities", "city", "towns"}:
        return "main_cities"
    if value.endswith("/admin") or "/admin/" in value:
        return "regional_admin"
    if value.startswith(("localities/", "places/", "towns/")):
        return "localities"
    if value.startswith("cities/"):
        return "regional_cities"
    if not segments or len(segments) == 1:
        return "regional_detail"
    if "admin" in segments:
        return "regional_admin"
    return "other"


def _citypopulation_previous_heading(node) -> str:
    for previous in node.find_all_previous(["h2", "h1"]):
        text = _clean_text(previous.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _select_citypopulation_candidates(
    candidates: list[CityPopulationRouteCandidate],
) -> list[CityPopulationRouteCandidate]:
    candidate_by_path = {candidate.path: candidate for candidate in candidates}
    usable = [candidate for candidate in candidates if not _is_redundant_citypopulation_coded_admin(candidate, candidate_by_path)]
    caps = {
        "main_admin": 12,
        "main_cities": 6,
        "regional_admin": 90,
        "regional_detail": 80,
        "regional_cities": 40,
        "localities": 110,
        "other": 25,
        "root": 1,
    }
    order = ["main_admin", "main_cities", "regional_admin", "regional_detail", "regional_cities", "localities", "other"]
    selected: list[CityPopulationRouteCandidate] = []
    seen: set[str] = set()
    for category in order:
        rows = [candidate for candidate in usable if candidate.category == category]
        rows.sort(key=lambda candidate: _citypopulation_candidate_sort_key(candidate))
        for candidate in rows[: caps.get(category, 20)]:
            if candidate.path in seen:
                continue
            selected.append(candidate)
            seen.add(candidate.path)
            if len(selected) >= CITYPOPULATION_DISCOVERY_MAX_PAGES:
                return selected
    return selected


def _citypopulation_candidate_sort_key(candidate: CityPopulationRouteCandidate) -> tuple[int, str]:
    path = candidate.path.casefold()
    title_bonus = 0 if candidate.title else 1
    if path in {"admin", "cities", "reg/admin", "prov/admin", "states/admin"}:
        title_bonus -= 2
    return (title_bonus, _natural_sort_text(path))


def _is_redundant_citypopulation_coded_admin(
    candidate: CityPopulationRouteCandidate,
    candidate_by_path: dict[str, CityPopulationRouteCandidate],
) -> bool:
    """Skip URL variants like ``admin/01__foo`` when a cleaner route exists."""
    path = candidate.path.strip("/")
    match = re.search(r"(?:^|/)admin/[^/]*__(?P<name>[^/]+)$", path, flags=re.IGNORECASE)
    if not match:
        return False
    coded_key = _route_key(match.group("name"))
    if not coded_key:
        return False
    for other_path in candidate_by_path:
        if other_path == candidate.path:
            continue
        parts = [part for part in other_path.strip("/").split("/") if part]
        aliases = []
        if parts:
            aliases.append(parts[0])
            aliases.append(parts[-1])
            if parts[-1] == "admin" and len(parts) > 1:
                aliases.append(parts[-2])
        if any(_route_key(alias) == coded_key for alias in aliases):
            return True
    return False


def _route_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _natural_sort_text(value: str) -> str:
    return re.sub(r"\d+", lambda match: match.group(0).zfill(8), str(value or ""))


def _citypopulation_page_probe(
    *,
    slug: str,
    path: str,
    html: str,
    url: str,
    category: str,
) -> CityPopulationPageProbe:
    soup = BeautifulSoup(html, "html.parser")
    profile = detect_citypopulation_page_profile(soup)
    title_node = soup.find("title")
    h1_node = soup.find("h1")
    table_ids: list[str] = []
    row_count = 0
    tl_rows = 0
    ts_rows = 0
    has_population = False
    for table in soup.find_all("table"):
        table_id = str(table.get("id") or "")
        if table_id:
            table_ids.append(table_id)
        data_rows = sum(len(tbody.find_all("tr", recursive=False)) for tbody in table.find_all("tbody", recursive=False))
        if table_id in {"tl", "ts"}:
            row_count += data_rows
        if table_id == "tl":
            tl_rows = data_rows
        if table_id == "ts":
            ts_rows = data_rows
        if table.find(class_=lambda value: value and "rpop" in value.split()):
            has_population = True
    headings = tuple(
        _clean_text(node.get_text(" ", strip=True))
        for node in soup.find_all("h2")[:8]
        if _clean_text(node.get_text(" ", strip=True))
    )
    return CityPopulationPageProbe(
        path=path.strip("/"),
        url=url,
        title=_clean_text(title_node.get_text(" ", strip=True) if title_node else ""),
        h1=_clean_text(h1_node.get_text(" ", strip=True) if h1_node else slug),
        headings=headings,
        profile=profile,
        table_ids=tuple(table_ids),
        row_count=row_count,
        tl_rows=tl_rows,
        ts_rows=ts_rows,
        has_population=has_population,
        category=category,
    )


def _citypopulation_probe_has_data(probe: CityPopulationPageProbe) -> bool:
    if probe.profile.page_type == CityPopulationPageType.UNKNOWN:
        return False
    if probe.row_count > 0 and probe.has_population:
        return True
    return probe.profile.page_type == CityPopulationPageType.INFOSECTION and probe.profile.has_root


def _citypopulation_root_name(probe: CityPopulationPageProbe, slug: str) -> str:
    if probe.h1 and probe.h1.casefold() != slug.casefold():
        return probe.h1
    return _display_name("", slug, country_code=slug) or slug


def _generated_pages_from_citypopulation_probes(
    probes: list[CityPopulationPageProbe],
    candidates: list[CityPopulationRouteCandidate],
) -> list[CityPopulationGeneratedPage]:
    candidate_paths = {candidate.path for candidate in candidates}
    generated: list[CityPopulationGeneratedPage] = []
    seen: set[tuple] = set()
    for probe in probes:
        spec = _generated_page_from_citypopulation_probe(probe, candidate_paths=candidate_paths)
        if spec is None:
            continue
        key = (
            spec.path,
            spec.source,
            spec.lowest_level,
            spec.include_infosection,
            spec.include_major_subdivision,
            spec.include_minor_subdivision,
            spec.include_cities,
        )
        if key in seen:
            continue
        seen.add(key)
        generated.append(spec)
    return sorted(generated, key=_generated_page_sort_key)


def _generated_page_from_citypopulation_probe(
    probe: CityPopulationPageProbe,
    *,
    candidate_paths: set[str],
) -> CityPopulationGeneratedPage | None:
    source = _citypopulation_source_for_probe(probe)
    category = probe.category
    lowest_level = _citypopulation_lowest_level_for_probe(probe, candidate_paths=candidate_paths)
    include_infosection = True
    include_major_subdivision = True
    include_cities = True

    if category == "localities":
        include_infosection = False
        if probe.profile.has_ts:
            include_major_subdivision = False
            include_cities = True
        if source == "admin":
            source = "cities"
    elif category == "regional_detail" and f"{probe.path}/admin" in candidate_paths and probe.profile.has_ts:
        include_major_subdivision = False
        include_cities = True
        if source == "admin":
            source = "cities"
    elif category == "regional_cities" and probe.profile.has_ts and probe.profile.has_tl:
        include_major_subdivision = False
        include_cities = True
        if source == "admin":
            source = "cities"
    return CityPopulationGeneratedPage(
        path=probe.path,
        source=source,
        lowest_level=lowest_level,
        category=category,
        include_infosection=include_infosection,
        include_major_subdivision=include_major_subdivision,
        include_minor_subdivision=include_minor_subdivision,
        include_cities=include_cities,
    )

def _citypopulation_source_for_probe(probe: CityPopulationPageProbe) -> str:
    return "admin" if probe.profile.preferred_html_format == "admin" else "cities"


def _citypopulation_lowest_level_for_probe(
    probe: CityPopulationPageProbe,
    *,
    candidate_paths: set[str],
) -> int:
    path = probe.path.strip("/")
    category = probe.category
    if not path or category in {"main_admin", "main_cities"}:
        return 0
    if category == "regional_admin":
        # National admin variants like states/admin are roots; region/admin pages
        # start at the region/federal-subject level.
        if path in {"reg/admin", "prov/admin", "states/admin", "state/admin", "province/admin"}:
            return 0
        return 1
    if category == "regional_detail":
        if f"{path}/admin" in candidate_paths:
            return 2
        return 1
    if category == "regional_cities":
        return 1
    if category == "localities":
        return 3
    return 1


def _generated_page_sort_key(page: CityPopulationGeneratedPage) -> tuple[int, int, str]:
    order = {
        "root": 0,
        "main_cities": 1,
        "main_admin": 2,
        "regional_admin": 3,
        "regional_detail": 4,
        "regional_cities": 5,
        "localities": 6,
        "other": 7,
    }
    return (order.get(page.category, 99), page.lowest_level, _natural_sort_text(page.path))


def _render_citypopulation_generated_config(
    *,
    slug: str,
    name: str,
    pages: list[CityPopulationGeneratedPage],
    legal_subdivision: int | None,
) -> str:
    grouped: dict[tuple, list[str]] = {}
    page_by_key: dict[tuple, CityPopulationGeneratedPage] = {}
    for page in pages:
        key = (
            page.source,
            page.lowest_level,
            page.category,
            page.include_infosection,
            page.include_major_subdivision,
            page.include_minor_subdivision,
            page.include_cities,
        )
        grouped.setdefault(key, []).append(page.path)
        page_by_key[key] = page

    lines = [f"name = {_toml_string(name)}", f"country_code = {_toml_string(slug)}"]
    if legal_subdivision is not None:
        lines.append(f"LEGAL_SUBDIVISION = {int(legal_subdivision)}")
    lines.append("scrape_schema_version = 2")
    lines.append("")

    for key in sorted(grouped, key=lambda item: _generated_page_sort_key(page_by_key[item])):
        page = page_by_key[key]
        paths = sorted(grouped[key], key=_natural_sort_text)
        if page.source == "admin":
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "minor_subdivision": bool(page.include_minor_subdivision),
            }
        elif page.source == "citiesadmin":
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "minor_subdivision": bool(page.include_minor_subdivision),
                "cities": bool(page.include_cities),
            }
        else:
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "cities": bool(page.include_cities),
            }
        lines.append("[[pages]]")
        lines.append(f"source = {_toml_string(page.source)}")
        lines.append(f"path = {_toml_array(paths)}")
        if int(page.lowest_level) != _inferred_generated_page_level(slug, page.source, paths[0] if paths else ""):
            lines.append(f"force_highest_level = {int(page.lowest_level)}")
        lines.append(f"include = {_toml_scalar_inline_table(include)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"



def _inferred_generated_page_level(slug: str, source: str, path: str) -> int:
    segments = [segment for segment in str(path or "").strip("/").split("/") if segment]
    slug_key = str(slug or "").strip("/").casefold()
    if segments and slug_key and segments[0].casefold() == slug_key:
        segments = segments[1:]
    normalized_source = str(source or "").strip().lower()
    if not segments:
        return 0
    if segments == ["admin"]:
        return 0
    if normalized_source in {"cities", "citiesadmin"} and segments == ["cities"]:
        return 0
    if segments[0] in {"cities", "localities", "places", "towns"}:
        return 1
    return 1

def _infer_citypopulation_legal_subdivision(
    pages: list[CityPopulationGeneratedPage],
    probes: list[CityPopulationPageProbe],
) -> int | None:
    if not pages:
        return None
    probe_by_path = {probe.path: probe for probe in probes}
    max_level = 0
    has_locality_layer = False
    for page in pages:
        probe = probe_by_path.get(page.path)
        estimated = page.lowest_level
        if probe and probe.profile.admin_levels:
            estimated = max(estimated, page.lowest_level + max(probe.profile.admin_levels))
        elif probe and probe.profile.has_tl and probe.profile.has_ts:
            estimated = max(estimated, page.lowest_level + 2)
        elif probe and (probe.profile.has_tl or probe.profile.has_ts):
            estimated = max(estimated, page.lowest_level + 1)
        max_level = max(max_level, estimated)
        if page.category in {"localities", "regional_detail", "regional_cities"} and page.include_cities:
            has_locality_layer = True
    if max_level <= 0:
        return None
    if has_locality_layer and max_level > 1:
        return min(5, max(1, max_level - 1))
    return min(5, max(1, max_level))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def _config_summaries(limit: int | None = None) -> list[dict]:
    row_counts = _config_row_counts()
    if scraping_config_table_exists():
        records = ScrapingConfig.objects.order_by("slug")
        if limit:
            records = records[:limit]
        return [_config_summary_from_record(record, row_counts=row_counts) for record in records]
    return []


def _filtered_config_summaries(request) -> list[dict]:
    rows = _config_summaries()
    for row in rows:
        _decorate_config_workflow_flags(row)
    query = str(request.GET.get("q") or "").strip().casefold()
    status = str(request.GET.get("status") or "").strip().casefold()
    if query:
        rows = [
            row
            for row in rows
            if query in " ".join(
                [
                    str(row.get("country_label") or ""),
                    str(row.get("country_code") or ""),
                ]
            ).casefold()
        ]
    if status:
        rows = [row for row in rows if str(row.get("status_filter") or "pending") == status]
    return rows


def _filtered_tasks(request, *, limit: int):
    """Return task history filtered by the selected UI state, if present."""
    tasks = task_manager.list(limit=limit)
    status = str(request.GET.get("status") or "").strip().casefold()
    if not status:
        return tasks
    return [task for task in tasks if str(getattr(task, "status", "") or "").casefold() == status]


def _config_row_status(row: dict) -> str:
    """Return the normalized filter/sort status for one config row."""
    if row.get("error"):
        return "failed"
    status = str(row.get("task_display_status") or "").strip().casefold()
    if status in {"validating", "validated", "populating", "populated", "clearing"}:
        return status
    if status in {"failed", "invalid"}:
        return "failed"
    if status in {"running", "queued"}:
        return status
    return "pending"


def _can_validate_config_status(status: str) -> bool:
    """Validation is available from pending/failed states only."""
    return str(status or "pending").strip().casefold() in {"pending", "failed"}


def _can_scrape_config_status(status: str) -> bool:
    """Population is available from pending/failed/validated states, and re-population from populated."""
    return str(status or "pending").strip().casefold() in {"pending", "failed", "validated", "populated"}


def _can_validate_config_row(row: dict, status: str) -> bool:
    """Return whether the row can run validation in its current workflow state."""
    return _can_validate_config_status(status)


def _can_scrape_config_row(row: dict, status: str) -> bool:
    """Return whether the row can run population in its current workflow state."""
    return _can_scrape_config_status(status)


ACTIVE_CONFIG_OPERATION_STATUSES = {"validating", "populating", "clearing", "running", "queued"}


def _can_clear_config_row(row: dict, status: str) -> bool:
    """Clearing is available when data exists and no config operation is active."""
    if str(status or "").strip().casefold() in ACTIVE_CONFIG_OPERATION_STATUSES:
        return False
    try:
        return int(row.get("rows") or 0) > 0
    except (TypeError, ValueError):
        return False


def _config_task_key_for_row(row: dict) -> str:
    task = row.get("active_task") or row.get("clear_task") or row.get("scrape_task") or row.get("validate_task")
    return str(getattr(task, "key", "") or "")


def _decorate_config_workflow_flags(row: dict) -> dict:
    status = _config_row_status(row)
    row["status_filter"] = status
    error_info = _task_error_info(row.get("active_task"), validation_error=row.get("error") or "")
    row["error_code"] = error_info["code"]
    row["error_severity"] = error_info.get("severity", "")
    row["error_message"] = error_info.get("message", "")
    row["error_description"] = error_info["description"]
    row["can_validate"] = _can_validate_config_row(row, status)
    row["can_scrape"] = _can_scrape_config_row(row, status)
    row["can_resume"] = False
    row["can_clear"] = _can_clear_config_row(row, status)
    task = row.get("active_task")
    row["can_stop"] = status in ACTIVE_CONFIG_OPERATION_STATUSES and bool(
        task and getattr(task, "is_active", False)
    )
    return row


def _config_workflow_json_payload(row: dict) -> dict:
    """Return the config workflow fields that are safe to expose as JSON."""
    task = row.get("active_task")
    validate_task = row.get("validate_task")
    scrape_task = row.get("scrape_task")
    clear_task = row.get("clear_task")
    return {
        "status_filter": row.get("status_filter") or _config_row_status(row),
        "task_status": row.get("task_display_status") or _config_task_display_status(task),
        "task_is_active": bool(getattr(task, "is_active", False)) if task else False,
        "task_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}) if task else "",
        "validate_task_status": _config_task_display_status(validate_task),
        "validate_task_is_active": bool(getattr(validate_task, "is_active", False)) if validate_task else False,
        "scrape_task_status": _config_task_display_status(scrape_task),
        "scrape_task_is_active": bool(getattr(scrape_task, "is_active", False)) if scrape_task else False,
        "clear_task_status": _config_task_display_status(clear_task),
        "clear_task_is_active": bool(getattr(clear_task, "is_active", False)) if clear_task else False,
        "can_validate": bool(row.get("can_validate")),
        "can_scrape": bool(row.get("can_scrape")),
        "can_clear": bool(row.get("can_clear")),
        "can_resume": bool(row.get("can_resume")),
        "can_stop": bool(row.get("can_stop")),
        "error_code": row.get("error_code") or "",
        "error_severity": row.get("error_severity") or "",
        "error_message": row.get("error_message") or "",
        "error_description": row.get("error_description") or "",
    }


def _eligible_config_slugs_for_bulk(action: str) -> list[str]:
    rows = []
    for row in _config_summaries():
        _decorate_config_workflow_flags(row)
        rows.append(row)
    if action == "validate":
        return [str(row["slug"]) for row in rows if row.get("can_validate")]
    if action == "scrape":
        return [str(row["slug"]) for row in rows if row.get("can_scrape")]
    if action == "scrape-unpopulated":
        return [
            str(row["slug"])
            for row in rows
            if row.get("can_scrape") and str(row.get("status_filter") or "") != "populated"
        ]
    return []


def _config_summary_for_slug(
    slug: str,
    *,
    row_counts: dict[str, int] | None = None,
) -> dict:
    row_counts = row_counts or _config_row_counts()
    record = _config_record(slug)
    if record:
        return _config_summary_from_record(record, row_counts=row_counts)

    error = _(
        "No existe la configuración SQL '%(slug)s'. Ejecuta "
        "'py manage.py sync_scraping_configs %(slug)s' si debe importarse desde seeds TOML."
    ) % {"slug": slug}
    country_code = slug
    raw_name = slug
    validate_task = task_manager.latest_for_key(f"validate-config:{slug}")
    scrape_task = task_manager.latest_for_key(f"scrape:{slug}")
    clear_task = task_manager.latest_for_key(f"clear-config:{slug}")
    active_task = _latest_config_task(
        slug,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )
    bulk_task, bulk_status = _latest_bulk_config_progress(slug)
    if bulk_task and (not active_task or bulk_task.created_at > active_task.created_at):
        active_task = bulk_task
        task_display_status = bulk_status
    else:
        task_display_status = _config_task_display_status(active_task)
    if error:
        task_display_status = "failed"
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _display_name(raw_name, country_code, country_code=country_code),
        "pages": 0,
        "cities": 0,
        "rows": row_counts.get(country_code, 0),
        "is_valid": False,
        "active_task": active_task,
        "task_display_status": task_display_status,
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "clear_task": clear_task,
        "error": error,
    }


def _latest_bulk_config_progress(slug: str, *, record: ScrapingConfig | None = None):
    """Return the latest bulk task/progress affecting a config row, if any."""
    candidates = [
        task_manager.latest_for_key("scrape:all"),
        task_manager.latest_for_key("scrape:unpopulated"),
        task_manager.latest_for_key("validate-config:all"),
    ]
    if record is not None:
        updated_at = getattr(record, "updated_at", None)
        candidates = [
            task for task in candidates
            if task and (not updated_at or not task.created_at or task.created_at >= updated_at)
        ]
    else:
        candidates = [task for task in candidates if task]
    candidates.sort(key=lambda task: task.created_at, reverse=True)
    for task in candidates:
        progress = read_task_config_progress(task.id)
        item = progress.get(slug) if isinstance(progress, dict) else None
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status:
            terminal_status = _terminal_config_progress_status(task)
            if terminal_status and not getattr(task, "is_active", False):
                status = _progress_status_after_terminal_task(status, terminal_status)
            return task, status
    return None, ""


def _config_task_display_status(task) -> str:
    """Return the normalized per-config state for the ESTADO column."""
    if not task:
        return "pending"
    key = str(getattr(task, "key", "") or "")
    status = str(getattr(task, "status", "") or "")
    if status == "succeeded":
        if key.startswith("scrape:"):
            return "populated"
        if key.startswith("validate-config:"):
            return "validated"
        if key.startswith("clear-config:"):
            return "pending"
        if key.startswith("asset-corrections:"):
            return "populated"
    if status in {"running", "queued"}:
        if key.startswith("scrape:"):
            return "populating"
        if key.startswith("validate-config:"):
            return "validating"
        if key.startswith("clear-config:"):
            return "clearing"
    if status == "cancelled":
        return "pending"
    if status == "failed":
        return "failed"
    return status or "pending"


def _config_task_if_current(task, record: ScrapingConfig):
    """Ignore task statuses launched before the last config edit.

    Saved config content invalidates the previous validation/scrape result
    because that result belongs to older content. This keeps /configs/ from
    showing a stale "correcto" after a user modifies the configuration.
    """
    if not task:
        return None
    updated_at = getattr(record, "updated_at", None)
    created_at = getattr(task, "created_at", None)
    if updated_at and created_at and created_at < updated_at:
        return None
    return task


def _task_is_ignored(task, ignored_task) -> bool:
    return bool(task and ignored_task and getattr(task, "id", None) == getattr(ignored_task, "id", None))


def _latest_terminal_config_task(*tasks, ignored_task=None):
    terminal = [
        task
        for task in tasks
        if task
        and not _task_is_ignored(task, ignored_task)
        and str(getattr(task, "status", "") or "") in {"succeeded", "failed"}
    ]
    if not terminal:
        return None
    return max(terminal, key=lambda task: task.created_at)


def _stable_config_status(
    record: ScrapingConfig | None,
    *,
    rows: int,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
    ignored_task=None,
) -> str:
    """Return the durable state after ignoring a cancelled/current active task."""
    if record and record.validation_error and not record.is_valid:
        return "failed"

    latest_terminal = _latest_terminal_config_task(
        validate_task,
        scrape_task,
        clear_task,
        ignored_task=ignored_task,
    )
    if not latest_terminal:
        return "pending"

    key = str(getattr(latest_terminal, "key", "") or "")
    status = str(getattr(latest_terminal, "status", "") or "")
    if status == "failed":
        return "failed"
    if key.startswith("scrape:"):
        return "populated" if rows > 0 else "pending"
    if key.startswith("validate-config:"):
        return "validated"
    if key.startswith("clear-config:"):
        return "pending"
    return "pending"


def _task_progress_status_for_slug(task, slug: str) -> str:
    if not task or not slug:
        return ""
    progress = read_task_config_progress(getattr(task, "id", ""))
    item = progress.get(slug) if isinstance(progress, dict) else None
    if not isinstance(item, dict):
        return ""
    return str(item.get("status") or "").strip().casefold()


def _task_runs_direct_scrape(task) -> bool:
    args = list(getattr(task, "args", None) or [])
    return bool(args and str(args[0]) == "scrape_subdivisions_with_assets")


def _cancelled_config_status(
    slug: str,
    record: ScrapingConfig | None,
    *,
    rows: int,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
    task=None,
) -> str:
    """Map a cancelled task back to the workflow state it should reveal."""
    stable = _stable_config_status(
        record,
        rows=rows,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
        ignored_task=task,
    )
    key = str(getattr(task, "key", "") or "")
    if key.startswith("scrape:"):
        progress_status = _task_progress_status_for_slug(task, slug)
        if progress_status == "populating" or _task_runs_direct_scrape(task):
            return "validated"
    return stable


def _config_task_display_status_for_row(
    slug: str,
    record: ScrapingConfig | None,
    *,
    rows: int,
    active_task=None,
    validate_task=None,
    scrape_task=None,
    clear_task=None,
) -> str:
    """Return the row state following the explicit config lifecycle."""
    if active_task:
        task_status = str(getattr(active_task, "status", "") or "")
        display_status = _config_task_display_status(active_task)
        if task_status in {"running", "queued"}:
            if str(getattr(active_task, "key", "") or "").startswith("scrape:"):
                progress_status = _task_progress_status_for_slug(active_task, slug)
                if progress_status in {"validating", "populating"}:
                    return progress_status
                stable_before_scrape = _stable_config_status(
                    record,
                    rows=rows,
                    validate_task=validate_task,
                    scrape_task=scrape_task,
                    clear_task=clear_task,
                    ignored_task=active_task,
                )
                if not _task_runs_direct_scrape(active_task) and stable_before_scrape != "validated":
                    return "validating"
            return display_status
        if task_status == "cancelled":
            return _cancelled_config_status(
                slug,
                record,
                rows=rows,
                validate_task=validate_task,
                scrape_task=scrape_task,
                clear_task=clear_task,
                task=active_task,
            )
        if task_status == "failed":
            return "failed"
        if task_status == "succeeded":
            return display_status

    return _stable_config_status(
        record,
        rows=rows,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )


def _config_summary_from_record(record: ScrapingConfig, *, row_counts: dict[str, int] | None = None) -> dict:
    row_counts = row_counts or _config_row_counts()
    slug = record.slug
    country_code = record.country_code or slug
    raw_name = record.name or slug
    validate_task = _config_task_if_current(task_manager.latest_for_key(f"validate-config:{slug}"), record)
    scrape_task = _config_task_if_current(task_manager.latest_for_key(f"scrape:{slug}"), record)
    clear_task = _config_task_if_current(task_manager.latest_for_key(f"clear-config:{slug}"), record)
    active_task = _latest_config_task(
        slug,
        validate_task=validate_task,
        scrape_task=scrape_task,
        clear_task=clear_task,
    )
    rows = row_counts.get(country_code, 0)
    bulk_task, bulk_status = _latest_bulk_config_progress(slug, record=record)
    if bulk_task and (not active_task or bulk_task.created_at > active_task.created_at):
        active_task = bulk_task
        task_display_status = bulk_status
    else:
        task_display_status = _config_task_display_status_for_row(
            slug,
            record,
            rows=rows,
            active_task=active_task,
            validate_task=validate_task,
            scrape_task=scrape_task,
            clear_task=clear_task,
        )
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _display_name(raw_name, country_code, country_code=country_code),
        "pages": record.pages_count,
        "cities": record.cities_count,
        "rows": rows,
        "is_valid": bool(record.is_valid),
        "active_task": active_task,
        "task_display_status": task_display_status,
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "clear_task": clear_task,
        "error": record.validation_error if not record.is_valid else "",
    }


def _config_row_counts() -> dict[str, int]:
    return {
        row["country_code"]: row["total"]
        for row in _visible_admin_areas().values("country_code").annotate(total=Count("id"))
    }


def _latest_config_task(slug: str, *, validate_task=None, scrape_task=None, clear_task=None):
    tasks = [task for task in (validate_task, scrape_task, clear_task) if task]
    if not tasks:
        return None
    return max(tasks, key=lambda task: task.created_at)

def _recipe_summaries() -> list[dict]:
    rows = []
    for group, root, editable in (
        ("new", NEW_RECIPES_ROOT, True),
        ("historical", HISTORICAL_RECIPES_ROOT, False),
    ):
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            slug = path.stem
            built_count = NuevoAdminArea.objects.filter(country_code=slug).count()
            rows.append(
                {
                    "slug": slug,
                    "group": group,
                    "editable": editable,
                    "path": path,
                    "root_name": _literal_assignment(path, ("ROOT_NAME", "COUNTRY_NAME")) or slug,
                    "built_count": built_count,
                    "active_task": task_manager.latest_for_key(f"build:{slug}"),
                }
            )
    return rows


def _bar_rows(
    rows: list[dict],
    label_key: str,
    value_key: str,
    *,
    limit: int,
    prefix: str = "",
) -> list[dict]:
    cleaned = []
    for row in rows:
        value = row.get(value_key) or 0
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        cleaned.append({"label": f"{prefix}{row.get(label_key)}", "value": value})
    cleaned = sorted(cleaned, key=lambda item: item["value"], reverse=True)[:limit]
    maximum = max((item["value"] for item in cleaned), default=0)
    for item in cleaned:
        item["width"] = 0 if maximum == 0 else max(2, round(item["value"] * 100 / maximum))
    return cleaned


def _merge_status_rows(rows: list[dict]) -> list[dict]:
    labels = dict(AdminArea.CityMergeStatus.choices)
    normalized = [
        {
            "label": labels.get(row["city_merge_status"], row["city_merge_status"]),
            "value": row["total"] or 0,
        }
        for row in rows
    ]
    maximum = max((row["value"] for row in normalized), default=0)
    for row in normalized:
        row["width"] = 0 if maximum == 0 else max(2, round(row["value"] * 100 / maximum))
    return normalized


def _comparison_row(area: NuevoAdminArea, root: NuevoAdminArea) -> dict:
    return {
        "area": area,
        "pop_country_pct": _percent(area.pop_latest, root.pop_latest),
        "area_country_pct": _percent(area.area_km2, root.area_km2),
        "pop_parent_pct": _percent(area.pop_latest, area.parent.pop_latest if area.parent else None),
        "area_parent_pct": _percent(area.area_km2, area.parent.area_km2 if area.parent else None),
        "capitals": ", ".join(capital.name for capital in area.capitals.all()),
    }


def _percent(value, total) -> str:
    if value in (None, "") or total in (None, "", 0):
        return ""
    try:
        return f"{(float(value) / float(total)) * 100:.2f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def _normalize_config_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not CONFIG_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El slug de configuracion solo puede usar letras, numeros, guion y guion bajo."))
    return slug


def _normalize_recipe_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not RECIPE_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El codigo debe empezar por una letra y usar letras sin acentos, numeros o guion bajo, sin espacios."))
    return slug


def _normalize_new_country_code(value: str) -> str:
    try:
        return _normalize_recipe_slug(value)
    except ValueError as exc:
        raise ValueError(
            _("El codigo debe empezar por una letra y usar mayusculas sin acentos, numeros o guion bajo, sin espacios.")
        ) from exc


def _new_country_entity_code_from_form(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", text):
        raise ValueError(
            _("El codigo debe empezar por una letra y usar mayusculas sin acentos, numeros, guion o guion bajo, sin espacios.")
        )
    return text.upper()


def _new_country_slug_from_entity_code(value: str) -> str:
    return _normalize_new_country_code(str(value or "").replace("-", "_"))


def _queue_derived_seed_import_tasks(
    paths: list[Path],
    *,
    section: str,
    key_prefix: str,
    label_template: str,
) -> list[tuple[object, str]]:
    queued_tasks = []
    for path in paths:
        slug = _derived_seed_task_identifier(path, section=section)
        label = label_template % {"slug": slug}
        task = task_manager.start(
            key=f"{key_prefix}:{slug.replace('/', '_')}",
            label=label,
            args=["sync_derived_configs", section, slug, "--force"],
        )
        queued_tasks.append((task, label))
    return queued_tasks


def _derived_seed_task_identifier(path: Path, *, section: str) -> str:
    if section not in {"groups", "subdivisions"}:
        return path.stem
    parent = path.parent.name
    return f"{parent}/{path.stem}" if parent and path.parent.parent.name == section else path.stem


def _csv_text_values(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _new_country_config_visual_form(country: DerivedCountry, config: DerivedCountryConfig) -> dict:
    data = _new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = _normalize_group_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or country.source_country_code
    )
    parent_config_slug = _normalize_group_country_key(
        entity.get("parent_config_slug")
        or entity.get("parent")
        or data.get("parent_config_slug")
        or data.get("parent")
    )
    selected_derived_subdivisions = _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    selected_subdivision_keys = [
        _new_country_derived_subdivision_selection_value(item["country_code"], item["key"])
        for item in selected_derived_subdivisions
    ]
    use_subdivisions = bool(entity.get("use_selected_entities_as_children")) or bool(selected_derived_subdivisions)
    selected_ids = _new_country_config_selected_source_ids(data, entity)
    selected_child_config_slugs = _new_country_config_direct_child_slugs(country, config.slug)
    selection_json = json.dumps(
        {
            "source_country_code": source_country_code,
            "selected_ids": selected_ids,
            "use_subdivisions": use_subdivisions,
            "selected_subdivision_keys": selected_subdivision_keys,
            "selected_derived_subdivisions": selected_derived_subdivisions,
            "selected_child_config_slugs": selected_child_config_slugs,
        },
        ensure_ascii=False,
    )
    try:
        entity_level = int(entity.get("level") or _new_country_config_level(config))
    except (TypeError, ValueError):
        entity_level = _new_country_config_level(config)
    parent_level = _new_country_parent_level(country, parent_config_slug)
    return {
        "slug": _new_country_config_local_code(config, data=data),
        "name": str(entity.get("name") or data.get("name") or config.name or "").strip(),
        "source_country_code": source_country_code,
        "parent_config_slug": parent_config_slug,
        "parent_level": str(parent_level),
        "entity_level": str(entity_level or 1),
        "use_subdivisions": use_subdivisions,
        "entity_type": str(entity.get("entity_type") or "").strip() or "Subdivision",
        "capitals": _derived_subdivision_capital_values(source_country_code, entity),
        "content": config.content,
        "selection_json": selection_json,
    }


def _new_country_config_first_entity(data: dict) -> dict:
    entities = data.get("entities") if isinstance(data, dict) else None
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                return entity
    return {}


def _new_country_config_raw_entity_code(config: DerivedCountryConfig, data: dict | None = None) -> str:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    try:
        return _new_country_entity_code_from_form(str(entity.get("code") or config.slug))
    except ValueError:
        return derived_code_piece(config.slug)


def _new_country_config_local_code(config: DerivedCountryConfig, data: dict | None = None) -> str:
    return _new_country_config_strip_parent_code_prefix(config, _new_country_config_raw_entity_code(config, data=data))


def _new_country_config_strip_parent_code_prefix(config: DerivedCountryConfig, code: str) -> str:
    code = derived_code_piece(code)
    if not code:
        return ""
    parent_slug = _new_country_config_parent_slug(config)
    if not parent_slug:
        return code
    parent = config.country.configs.filter(slug=parent_slug).first()
    if parent is None:
        return code
    parent_code = _new_country_config_expected_full_code(parent, seen={config.slug})
    return _new_country_code_without_parent_prefix(code, parent_code)


def _new_country_code_without_parent_prefix(code: str, parent_code: str) -> str:
    code = derived_code_piece(code)
    parent_code = derived_code_piece(parent_code)
    prefix = f"{parent_code}-" if parent_code else ""
    if prefix and code.startswith(prefix):
        return code[len(prefix) :] or code
    return code


def _new_country_config_selected_source_ids(data: dict, entity: dict) -> list[str]:
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    result: list[str] = []
    seen: set[str] = set()

    def add_many(values) -> None:
        for value in _new_country_config_text_values(values):
            if value in seen:
                continue
            seen.add(value)
            result.append(value)

    selection_items = selection.get("items") if isinstance(selection, dict) else None
    if isinstance(selection_items, list):
        for item in selection_items:
            if isinstance(item, dict):
                add_many([item.get("id") or item.get("value")])
    if result:
        return result

    if isinstance(selection, dict):
        add_many(selection.get("include_ids"))
        add_many(selection.get("subtract_ids"))
    if result:
        return result

    for key in ("include", "subtract"):
        blocks = entity.get(key) if isinstance(entity.get(key), list) else []
        for block in blocks:
            if isinstance(block, dict):
                add_many(block.get("ids"))
    return result


def _new_country_config_selected_derived_subdivisions(
    entity: dict,
    *,
    fallback_country_code: str,
) -> list[dict[str, str | int]]:
    values: list[dict[str, str | int]] = []
    blocks = entity.get("include") if isinstance(entity.get("include"), list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = _normalize_group_country_key(block.get("country_code") or fallback_country_code)
        block_level = _new_country_config_include_block_level(block)
        for value in _new_country_config_text_values(block.get("derived_subdivisions")):
            entry: dict[str, str | int] = {"country_code": block_country, "key": value}
            if block_level is not None:
                entry["level"] = block_level
            values.append(entry)
    return _new_country_normalized_derived_subdivision_selections(
        values,
        fallback_country_code=fallback_country_code,
    )


def _new_country_config_include_block_level(block: dict) -> int | None:
    try:
        level = int(block.get("level") or 0)
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None
    return max(1, min(9, level))


def _new_country_config_selected_subdivision_keys(entity: dict) -> list[str]:
    return [
        item["key"]
        for item in _new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code="",
        )
    ]


def _new_country_config_text_values(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [str(value).strip() for value in values if str(value or "").strip()]


def _rename_new_country_config_parent_references(
    country: DerivedCountry,
    *,
    old_slug: str,
    new_slug: str,
    exclude_config: DerivedCountryConfig | None = None,
) -> int:
    old_slug = _normalize_group_country_key(old_slug)
    new_slug = _normalize_group_country_key(new_slug)
    if not old_slug or not new_slug or old_slug == new_slug:
        return 0
    updated = 0
    queryset = country.configs.select_for_update().order_by("slug")
    if exclude_config is not None:
        queryset = queryset.exclude(pk=exclude_config.pk)
    for child in queryset:
        if _new_country_config_parent_slug(child) != old_slug:
            continue
        content = _replace_new_country_parent_slug_in_content(child.content, old_slug=old_slug, new_slug=new_slug)
        _validate_plain_toml(content, expected_kind="derived_country_config")
        child.content = content
        child.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _replace_new_country_parent_slug_in_content(content: str, *, old_slug: str, new_slug: str) -> str:
    old_slug = _normalize_group_country_key(old_slug)
    new_slug = _normalize_group_country_key(new_slug)
    if not old_slug or not new_slug or old_slug == new_slug:
        return str(content or "")

    def replace_line(match: re.Match) -> str:
        value = str(match.group("value") or "")
        if _normalize_group_country_key(value) != old_slug:
            return match.group(0)
        return f"{match.group('prefix')}{_toml_string(new_slug)}{match.group('suffix') or ''}"

    return re.sub(
        r'(?m)^(?P<prefix>\s*(?:parent_config_slug|parent)\s*=\s*)(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        replace_line,
        str(content or ""),
    )


def _set_new_country_config_slug_in_content(content: str, *, slug: str) -> str:
    slug = _normalize_new_country_code(slug)
    text = str(content or "")
    value = _toml_string(slug)
    replaced = re.sub(
        r'(?m)^(?P<prefix>\s*slug\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        lambda match: f"{match.group('prefix')}{value}{match.group('suffix') or ''}",
        text,
        count=1,
    )
    if replaced != text:
        return replaced
    lines = text.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("kind ="):
            insert_at = index + 1
            break
    lines.insert(insert_at, f"slug = {value}")
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _set_toml_string_field_in_content(
    content: str,
    *,
    field: str,
    value: str,
    table_marker: str = "",
) -> str:
    text = str(content or "")
    assignment = f"{field} = {_toml_string(value)}"
    pattern = re.compile(
        rf'(?m)^(?P<prefix>\s*{re.escape(field)}\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$'
    )

    if not table_marker:
        first_table = re.search(r"(?m)^\s*\[", text)
        search_text = text[: first_table.start()] if first_table else text
        match = pattern.search(search_text)
        if match:
            return text[: match.start()] + assignment + (match.group("suffix") or "") + text[match.end() :]
        insert_at = first_table.start() if first_table else len(text)
        prefix = text[:insert_at]
        suffix = text[insert_at:]
        separator = "" if not prefix or prefix.endswith("\n") else "\n"
        return prefix + separator + assignment + ("\n" if suffix else "") + suffix

    marker_match = re.search(rf"(?m)^\s*{re.escape(table_marker)}\s*$", text)
    if marker_match is None:
        separator = "\n" if text and not text.endswith("\n") else ""
        return f"{text}{separator}{table_marker}\n{assignment}\n"

    section_start = marker_match.end()
    next_table = re.search(r"(?m)^\s*\[", text[section_start:])
    section_end = section_start + next_table.start() if next_table else len(text)
    section = text[section_start:section_end]
    match = pattern.search(section)
    if match:
        new_section = section[: match.start()] + assignment + (match.group("suffix") or "") + section[match.end() :]
    else:
        new_section = "\n" + assignment + section
    return text[:section_start] + new_section + text[section_end:]


def _clone_display_name(name: str) -> str:
    text = str(name or "").strip()
    suffix = _("Copia")
    return f"{text} {suffix}".strip() if text else suffix


def _new_country_clone_local_code(config: DerivedCountryConfig, data: dict, *, suffix_index: int = 1) -> str:
    entity = _new_country_config_first_entity(data)
    base_code = derived_code_piece(entity.get("code") or config.slug)
    if not base_code:
        base_code = derived_code_piece(config.slug)
    suffix = "COPIA" if suffix_index <= 1 else f"COPIA-{suffix_index}"
    if base_code.endswith(f"-{suffix}"):
        return base_code
    return f"{base_code}-{suffix}"


def _new_country_clone_slug_and_code(config: DerivedCountryConfig, data: dict) -> tuple[str, str]:
    base_slug = _normalize_new_country_code(f"{config.slug}_copia")
    suffix_index = 1
    candidate = base_slug
    while config.country.configs.filter(slug=candidate).exists():
        suffix_index += 1
        candidate = _normalize_new_country_code(f"{base_slug}_{suffix_index}")
    return candidate, _new_country_clone_local_code(config, data, suffix_index=suffix_index)


def _clone_new_country_config(config: DerivedCountryConfig) -> DerivedCountryConfig:
    data = _new_country_config_toml(config)
    clone_slug, clone_code = _new_country_clone_slug_and_code(config, data)
    clone_name = _clone_display_name(config.name)
    content = _set_new_country_config_slug_in_content(config.content, slug=clone_slug)
    content = _set_toml_string_field_in_content(content, field="name", value=clone_name)
    content = _set_toml_string_field_in_content(content, field="code", value=clone_code, table_marker="[[entities]]")
    content = _set_toml_string_field_in_content(content, field="name", value=clone_name, table_marker="[[entities]]")
    parsed = _validate_plain_toml(content, expected_kind="derived_country_config")
    return DerivedCountryConfig.objects.create(
        country=config.country,
        slug=clone_slug,
        name=clone_name,
        content=content.rstrip() + "\n",
        source_country_code=_normalize_group_country_key(parsed.get("source_country_code") or config.source_country_code),
        derived_country_code=_normalize_group_country_key(parsed.get("derived_country_code") or config.derived_country_code),
        is_active=config.is_active,
    )


def _new_country_collision_child_slug(
    country: DerivedCountry,
    *,
    parent_slug: str,
    child_config: DerivedCountryConfig,
) -> str:
    parent_slug = _normalize_new_country_code(parent_slug)
    child_code = _new_country_config_local_code(child_config)
    base = _normalize_new_country_code(f"{parent_slug}_{_new_country_slug_from_entity_code(child_code)}")
    reserved = set(country.configs.exclude(pk=child_config.pk).values_list("slug", flat=True))
    reserved.add(parent_slug)
    if base not in reserved:
        return base
    index = 2
    while True:
        candidate = _normalize_new_country_code(f"{base}_{index}")
        if candidate not in reserved:
            return candidate
        index += 1


def _rename_new_country_config_for_parent_code_collision(
    country: DerivedCountry,
    *,
    child_config: DerivedCountryConfig,
    parent_slug: str,
) -> str:
    old_slug = child_config.slug
    new_slug = _new_country_collision_child_slug(country, parent_slug=parent_slug, child_config=child_config)
    if old_slug == new_slug:
        return new_slug
    content = _set_new_country_config_slug_in_content(child_config.content, slug=new_slug)
    _validate_plain_toml(content, expected_kind="derived_country_config")
    child_config.slug = new_slug
    child_config.content = content
    child_config.save(update_fields=["slug", "content", "updated_at"])
    _rename_new_country_config_parent_references(
        country,
        old_slug=old_slug,
        new_slug=new_slug,
        exclude_config=child_config,
    )
    return new_slug


def _rename_new_country_selected_child_slug_collision(
    country: DerivedCountry,
    *,
    slug: str,
    use_subdivisions: bool,
    selected_child_config_slugs: list[str],
    current_config: DerivedCountryConfig | None = None,
) -> list[str]:
    colliding_children = country.configs.select_for_update().filter(slug=slug)
    if current_config is not None:
        colliding_children = colliding_children.exclude(pk=current_config.pk)
    colliding_child = colliding_children.first()
    if colliding_child is None:
        return selected_child_config_slugs
    if use_subdivisions or slug not in selected_child_config_slugs:
        raise ValueError(
            _("Ya existe una configuracion '%(slug)s' para este pais.") % {"slug": slug}
        )
    renamed_child_slug = _rename_new_country_config_for_parent_code_collision(
        country,
        child_config=colliding_child,
        parent_slug=slug,
    )
    return [
        renamed_child_slug if child_slug == slug else child_slug
        for child_slug in selected_child_config_slugs
    ]


def _new_country_selection_uses_subdivisions(selection_json: str) -> bool:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(selection, dict):
        return False
    return selection.get("use_subdivisions") in (True, "1", 1, "true", "True", "on")


def _new_country_selected_child_config_slugs_from_selection_json(selection_json: str) -> list[str]:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(selection, dict):
        return []
    values = (
        selection.get("selected_child_config_slugs")
        or selection.get("selected_config_slugs")
        or selection.get("selected_created_entity_slugs")
        or []
    )
    return _new_country_normalized_config_slugs(values)


def _new_country_child_config_slugs_for_capital_payload(
    payload: dict,
    *,
    country: DerivedCountry,
    current_config: DerivedCountryConfig | None = None,
) -> list[str]:
    if not isinstance(payload, dict):
        payload = {}
    selection_touched = _form_bool(payload.get("selection_touched"), default=False) or _form_bool(
        payload.get("child_config_selection_touched"),
        default=False,
    )
    for key in ("selected_child_config_slugs", "selected_config_slugs", "selected_created_entity_slugs"):
        if key in payload:
            slugs = _new_country_normalized_config_slugs(payload.get(key))
            if slugs or selection_touched or current_config is None:
                return slugs
            break
    if current_config is None:
        return []
    return _new_country_config_direct_child_slugs(country, current_config.slug)


def _new_country_selection_has_child_config_slugs(selection_json: str) -> bool:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(selection, dict):
        return False
    return any(
        key in selection
        for key in ("selected_child_config_slugs", "selected_config_slugs", "selected_created_entity_slugs")
    )


def _new_country_normalized_config_slugs(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            slug = _normalize_new_country_code(str(value or ""))
        except ValueError:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        result.append(slug)
    return result


def _new_country_config_direct_child_slugs(country: DerivedCountry, parent_slug: str) -> list[str]:
    parent_slug = _normalize_group_country_key(parent_slug)
    if not parent_slug:
        return []
    slugs: list[str] = []
    for config in country.configs.order_by("name", "slug"):
        if _new_country_config_parent_slug(config) == parent_slug:
            slugs.append(config.slug)
    return slugs


def _new_country_created_entity_queryset(
    country: DerivedCountry,
    search_term: str,
    *,
    current_config: DerivedCountryConfig | None = None,
):
    queryset = country.configs.order_by("name", "slug").only(
        "id",
        "country",
        "slug",
        "name",
        "source_country_code",
        "derived_country_code",
        "content",
    )
    excluded_slugs: set[str] = set()
    if current_config is not None:
        excluded_slugs.add(current_config.slug)
        excluded_slugs.update(_new_country_config_parent_chain(current_config))
    if excluded_slugs:
        queryset = queryset.exclude(slug__in=excluded_slugs)
    search_term = str(search_term or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(slug__icontains=search_term)
            | Q(name__icontains=search_term)
            | Q(source_country_code__icontains=search_term)
            | Q(derived_country_code__icontains=search_term)
            | Q(content__icontains=search_term)
        )
    return queryset


def _new_country_created_entity_options_from_records(records: list[DerivedCountryConfig]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for config in records:
        data = _new_country_config_toml(config)
        entity = _new_country_config_first_entity(data)
        entity_type = str(entity.get("entity_type") or "").strip()
        level = _new_country_config_level(config)
        materialized = _new_country_config_materialized_entity(config, data=data)
        area_value, population_value, density_value = _new_country_config_entity_metric_values(
            data=data,
            entity_node=materialized,
        )
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        density_text = _group_metric_text(density_value)
        options.append(
            {
                "value": config.slug,
                "slug": config.slug,
                "name": str(entity.get("name") or config.name or config.slug).strip(),
                "type_text": entity_type or _("Sin tipo"),
                "level": level,
                "area_text": area_text,
                "population_text": population_text,
                "density_text": density_text,
                "search_text": " ".join(
                    [
                        config.slug,
                        config.name,
                        str(entity.get("name") or ""),
                        entity_type,
                        str(level),
                        area_text,
                        population_text,
                        density_text,
                    ]
                ),
            }
        )
    return options


def _new_country_config_stored_metric_values(entity: dict) -> tuple[object, object, object]:
    area_value = _new_country_entity_number(entity, "area_km2", "area")
    population_value = _new_country_entity_number(entity, "pop_latest", "population")
    density_value = _new_country_entity_number(entity, "density")
    return area_value, population_value, density_value


def _new_country_entity_number(entity: dict, *keys: str):
    if not isinstance(entity, dict):
        return None
    for key in keys:
        if key not in entity:
            continue
        number = _number_or_none(entity.get(key))
        if number is not None:
            return number
    return None


def _new_country_config_preview_metric_values(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
) -> tuple[object, object]:
    cache_key = (str(config.country_id or ""), config.slug)
    seen = set(seen or set())
    if cache_key in seen:
        return None, None
    seen.add(cache_key)
    try:
        source_ids = _new_country_config_preview_source_ids(config, data=data)
    except (OperationalError, ProgrammingError, ValueError):
        source_ids = set()
    if source_ids:
        try:
            aggregate = _group_source_admin_areas().filter(id__in=source_ids).aggregate(
                area=Sum("area_km2"),
                population=Sum("pop_latest"),
            )
        except (OperationalError, ProgrammingError, ValueError):
            return None, None
        return aggregate.get("area"), aggregate.get("population")

    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    area_value, population_value = _new_country_config_own_derived_subdivision_metric_values(config, data=data)
    parent_slug = _normalize_group_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        child_area, child_population = _new_country_config_preview_metric_values(child, seen=seen)
        area_value = _add_optional_metric(area_value, child_area)
        population_value = _add_optional_metric(population_value, child_population)
    seen.remove(cache_key)
    return area_value, population_value


def _new_country_config_own_derived_subdivision_metric_values(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> tuple[object, object]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = _normalize_group_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    selections = _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    if not selections:
        return None, None
    records = []
    seen_records = set()
    for item in selections:
        record = _derived_subdivision_source_record_for_key(item["country_code"], item["key"])
        if record is None or record.pk in seen_records:
            continue
        seen_records.add(record.pk)
        records.append(record)
    if not records:
        return None, None
    data_by_slug = {record.slug: _derived_subdivision_record_toml(record) for record in records}
    code_counts = _new_country_derived_subdivision_code_counts(records, data_by_slug=data_by_slug)
    materialized_rows = _derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    area_value = None
    population_value = None
    for record in records:
        record_area, record_population = _derived_subdivision_metric_values(
            record,
            data_by_slug.get(record.slug) or {},
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_value = _add_optional_metric(area_value, record_area)
        population_value = _add_optional_metric(population_value, record_population)
    return area_value, population_value


def _add_optional_metric(total, value):
    if value is None:
        return total
    if total is None:
        return value
    return total + value


def _new_country_config_preview_source_ids(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    config_source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    cache_key = (str(config.country_id or ""), config.slug)
    config_source_ids_cache = config_source_ids_cache if config_source_ids_cache is not None else {}
    if cache_key in config_source_ids_cache:
        return set(config_source_ids_cache[cache_key])
    seen = set(seen or set())
    if cache_key in seen:
        return set()
    seen.add(cache_key)
    source_ids_cache = source_ids_cache if source_ids_cache is not None else {}
    group_entry_cache = group_entry_cache if group_entry_cache is not None else {}
    group_entries_cache = group_entries_cache if group_entries_cache is not None else {}

    entity = _new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    default_country_code = _normalize_group_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    include_ids = _derived_subdivision_blocks_source_ids(
        entity.get("include"),
        default_country_code=default_country_code,
        seen=set(),
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    subtract_ids = _derived_subdivision_blocks_source_ids(
        entity.get("subtract"),
        default_country_code=default_country_code,
        seen=set(),
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    result = include_ids - subtract_ids
    if not result:
        result = _new_country_config_derived_subdivision_source_ids(
            entity,
            default_country_code=default_country_code,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    if not result:
        result = _new_country_config_selection_source_ids(
            selection,
            default_country_code=default_country_code,
        )

    parent_slug = _normalize_group_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        result.update(
            _new_country_config_preview_source_ids(
                child,
                seen=seen,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
                config_source_ids_cache=config_source_ids_cache,
            )
        )
    seen.remove(cache_key)
    config_source_ids_cache[cache_key] = set(result)
    return result


def _new_country_config_derived_subdivision_source_ids(
    entity: dict,
    *,
    default_country_code: str,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> set[str]:
    result: set[str] = set()
    for item in _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=default_country_code,
    ):
        result.update(
            _derived_subdivision_source_ids_for_key(
                item["country_code"],
                item["key"],
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
        )
    return result


def _new_country_config_selection_source_ids(selection: dict, *, default_country_code: str) -> set[str]:
    if not isinstance(selection, dict):
        return set()
    include_ids: list[str] = []
    subtract_ids: list[str] = []
    items = selection.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("value") or "").strip()
            if not item_id:
                continue
            operation = str(item.get("operation") or "add").strip().lower()
            if operation == "subtract":
                subtract_ids.append(item_id)
            else:
                include_ids.append(item_id)
    else:
        include_ids = _new_country_config_text_values(selection.get("include_ids"))
        subtract_ids = _new_country_config_text_values(selection.get("subtract_ids"))
    return _new_country_config_admin_area_ids_to_source_ids(
        include_ids,
        default_country_code=default_country_code,
    ) - _new_country_config_admin_area_ids_to_source_ids(
        subtract_ids,
        default_country_code=default_country_code,
    )


def _new_country_config_admin_area_ids_to_source_ids(
    area_ids: list[str],
    *,
    default_country_code: str,
) -> set[str]:
    ids = {str(value).strip() for value in area_ids if str(value or "").strip()}
    if not ids:
        return set()
    try:
        areas = list(
            _group_source_admin_areas()
            .filter(id__in=ids)
            .only("id", "country_code", "level", "parent_id", "city_merge_status")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return set()
    areas_by_country: dict[str, list[AdminArea]] = {}
    default_country_code = _normalize_group_country_key(default_country_code)
    for area in areas:
        country_code = _normalize_group_country_key(area.country_code or default_country_code)
        if country_code:
            areas_by_country.setdefault(country_code, []).append(area)
    return _new_country_legal_source_unit_ids_for_source_areas_by_country(areas_by_country)


def _new_country_legal_source_unit_ids_for_source_areas(areas: Iterable[AdminArea]) -> set[str]:
    areas_by_country: dict[str, list[AdminArea]] = {}
    for area in areas:
        country_code = _normalize_group_country_key(getattr(area, "country_code", ""))
        if country_code:
            areas_by_country.setdefault(country_code, []).append(area)
    return _new_country_legal_source_unit_ids_for_source_areas_by_country(areas_by_country)


def _new_country_legal_source_unit_ids_for_source_areas_by_country(
    areas_by_country: dict[str, list[AdminArea]],
) -> set[str]:
    result: set[str] = set()
    for country_code, country_areas in areas_by_country.items():
        result.update(_derived_subdivision_areas_to_capital_source_ids(country_areas, country_code=country_code))
    return result


def _new_country_config_materialized_entity(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> NuevoAdminArea | None:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    built_code = _new_country_config_built_code(config)
    code_candidates = _new_country_config_materialized_code_candidates(config, data=data)
    if built_code and code_candidates:
        match = (
            NuevoAdminArea.objects.filter(country_code=built_code, code__in=code_candidates)
            .exclude(parent__isnull=True)
            .order_by("level", "id")
            .first()
        )
        if match:
            return match
    name = str(entity.get("name") or config.name or "").strip()
    if not built_code or not name:
        return None
    return (
        NuevoAdminArea.objects.filter(country_code=built_code, name__iexact=name)
        .exclude(parent__isnull=True)
        .order_by("level", "id")
        .first()
    )


def _new_country_config_materialized_code_candidates(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> list[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    candidates = []

    def add(value) -> None:
        code = derived_code_piece(value)
        if code and code not in candidates:
            candidates.append(code)

    local_full_code = _new_country_config_expected_full_code(config, data=data)
    add(local_full_code)
    built_code = _new_country_config_built_code(config)
    root_code = derived_country_root_code(built_code, data.get("root_code") or data.get("code") or built_code)
    if root_code and local_full_code and not local_full_code.startswith(f"{root_code}-"):
        add(f"{root_code}-{local_full_code}")
    add(entity.get("code"))
    add(config.slug)
    return candidates


def _new_country_metric_refresh_slugs_for_child_sync(
    country: DerivedCountry,
    *,
    parent_config: DerivedCountryConfig,
    selected_child_slugs: list[str],
) -> list[str]:
    selected = set(_new_country_normalized_config_slugs(selected_child_slugs))
    if not selected:
        return []
    slugs: list[str] = []
    seen: set[str] = set()
    parent_slug = _normalize_group_country_key(parent_config.slug)
    for child in country.configs.filter(slug__in=selected).order_by("slug"):
        old_parent_slug = _new_country_config_parent_slug(child)
        if old_parent_slug and old_parent_slug != parent_slug and old_parent_slug not in seen:
            seen.add(old_parent_slug)
            slugs.append(old_parent_slug)
    return slugs


def _refresh_new_country_config_metrics_for_slugs(country: DerivedCountry, config_slugs: list[str]) -> int:
    changed = 0
    visited: set[tuple[str, str]] = set()
    for config_slug in config_slugs:
        normalized_slug = _normalize_group_country_key(config_slug)
        if not normalized_slug:
            continue
        config = country.configs.filter(slug=normalized_slug).first()
        if config is None:
            continue
        changed += _refresh_new_country_config_metrics_with_ancestors(country, config, visited=visited)
    return changed


def _refresh_new_country_config_metrics_with_ancestors(
    country: DerivedCountry,
    config: DerivedCountryConfig,
    *,
    visited: set[tuple[str, str]] | None = None,
) -> int:
    changed = 0
    visited = visited if visited is not None else set()
    current = config
    while current is not None:
        cache_key = (str(country.pk or ""), current.slug)
        if cache_key in visited:
            break
        visited.add(cache_key)
        changed += _refresh_new_country_config_metrics(current)
        parent_slug = _new_country_config_parent_slug(current)
        current = country.configs.filter(slug=parent_slug).first() if parent_slug else None
    return changed


def _refresh_new_country_config_metrics(config: DerivedCountryConfig) -> int:
    data = _new_country_config_toml(config)
    area_value, population_value = _new_country_config_preview_metric_values(config, data=data)
    content = _set_new_country_entity_metrics_in_content(
        config.content,
        area_value=area_value,
        population_value=population_value,
    )
    if content == config.content:
        return 0
    _validate_plain_toml(content, expected_kind="derived_country_config")
    config.content = content
    config.save(update_fields=["content", "updated_at"])
    return 1


def _new_country_config_expected_full_code(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[str] | None = None,
) -> str:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    code = _new_country_config_raw_entity_code(config, data=data)
    if not code:
        return ""
    parent_slug = _new_country_config_parent_slug(config)
    if parent_slug:
        seen = set(seen or set())
        if config.slug in seen:
            return code
        seen.add(config.slug)
        parent = config.country.configs.filter(slug=parent_slug).first()
        if parent is not None:
            parent_code = _new_country_config_expected_full_code(parent, seen=seen)
            code = _new_country_code_without_parent_prefix(code, parent_code)
            if parent_code and code:
                return f"{parent_code}-{code}"
            return code or parent_code
    return code


def _sync_new_country_config_children(
    country: DerivedCountry,
    *,
    parent_config: DerivedCountryConfig,
    selected_child_slugs: list[str],
) -> int:
    parent_slug = _normalize_group_country_key(parent_config.slug)
    selected = set(_new_country_normalized_config_slugs(selected_child_slugs))
    if parent_slug in selected:
        raise ValueError(_("Una division no puede ser hija de si misma."))
    parent_chain = _new_country_config_parent_chain(parent_config)
    invalid = selected.intersection(parent_chain)
    if invalid:
        raise ValueError(_("No se puede seleccionar una entidad padre como hija."))

    configs = list(country.configs.select_for_update().order_by("slug"))
    by_slug = {config.slug: config for config in configs}
    missing = sorted(slug for slug in selected if slug not in by_slug)
    if missing:
        raise ValueError(_("La seleccion contiene entidades creadas que no existen."))

    changed = 0
    direct_children = {config.slug for config in configs if _new_country_config_parent_slug(config) == parent_slug}
    target_slugs = selected.union(direct_children)
    for child_slug in sorted(target_slugs):
        child = by_slug.get(child_slug)
        if child is None or child.slug == parent_slug:
            continue
        new_parent_slug = parent_slug if child_slug in selected else ""
        changed += _set_new_country_config_parent_and_level(
            country,
            child,
            parent_config_slug=new_parent_slug,
        )
        changed += _cascade_new_country_config_levels(country, child.slug, by_slug=by_slug)
    return changed


def _reparent_new_country_config_children_before_delete(
    country: DerivedCountry,
    *,
    deleted_slug: str,
    new_parent_slug: str,
) -> list[str]:
    deleted_slug = _normalize_group_country_key(deleted_slug)
    new_parent_slug = _normalize_group_country_key(new_parent_slug)
    configs = list(country.configs.select_for_update().order_by("slug"))
    reparented_slugs: list[str] = []
    for child in configs:
        if child.slug == deleted_slug or _new_country_config_parent_slug(child) != deleted_slug:
            continue
        _set_new_country_config_parent_and_level(country, child, parent_config_slug=new_parent_slug)
        reparented_slugs.append(child.slug)

    if not reparented_slugs:
        return []

    by_slug = {config.slug: config for config in configs if config.slug != deleted_slug}
    for child_slug in reparented_slugs:
        if child_slug in by_slug:
            _cascade_new_country_config_levels(country, child_slug, by_slug=by_slug)
    return reparented_slugs


def _set_new_country_config_parent_and_level(
    country: DerivedCountry,
    config: DerivedCountryConfig,
    *,
    parent_config_slug: str,
) -> int:
    parent_config_slug = _normalize_group_country_key(parent_config_slug)
    level = _new_country_parent_level(country, parent_config_slug) + 1
    local_code = _new_country_config_local_code(config)
    content = _set_toml_string_field_in_content(
        config.content,
        field="code",
        value=local_code,
        table_marker="[[entities]]",
    )
    content = _set_new_country_parent_slug_in_content(content, parent_config_slug=parent_config_slug)
    content = _set_new_country_entity_level_in_content(content, level=level)
    if content == config.content:
        return 0
    _validate_plain_toml(content, expected_kind="derived_country_config")
    config.content = content
    config.save(update_fields=["content", "updated_at"])
    return 1


def _cascade_new_country_config_levels(
    country: DerivedCountry,
    parent_slug: str,
    *,
    by_slug: dict[str, DerivedCountryConfig] | None = None,
    seen: set[str] | None = None,
) -> int:
    parent_slug = _normalize_group_country_key(parent_slug)
    if not parent_slug:
        return 0
    by_slug = by_slug or {config.slug: config for config in country.configs.order_by("slug")}
    seen = set(seen or set())
    if parent_slug in seen:
        raise ValueError(_("%(country)s tiene padres circulares entre sus divisiones.") % {"country": country.slug})
    seen.add(parent_slug)
    parent_level = _new_country_parent_level(country, parent_slug)
    changed = 0
    for child in list(by_slug.values()):
        if _new_country_config_parent_slug(child) != parent_slug:
            continue
        level = parent_level + 1
        content = _set_new_country_entity_level_in_content(child.content, level=level)
        if content != child.content:
            _validate_plain_toml(content, expected_kind="derived_country_config")
            child.content = content
            child.save(update_fields=["content", "updated_at"])
            changed += 1
        changed += _cascade_new_country_config_levels(country, child.slug, by_slug=by_slug, seen=seen)
    seen.remove(parent_slug)
    return changed


def _set_new_country_parent_slug_in_content(content: str, *, parent_config_slug: str) -> str:
    parent_config_slug = _normalize_group_country_key(parent_config_slug)
    text = str(content or "")
    value = _toml_string(parent_config_slug)
    replaced = re.sub(
        r'(?m)^(?P<prefix>\s*parent_config_slug\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        lambda match: f"{match.group('prefix')}{value}{match.group('suffix') or ''}",
        text,
    )
    return replaced


def _set_new_country_entity_level_in_content(content: str, *, level: int) -> str:
    level = max(1, min(5, int(level or 1)))
    lines = str(content or "").splitlines()
    result: list[str] = []
    in_entity = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[[entities]]":
            in_entity = True
            replaced = False
            result.append(line)
            continue
        if in_entity and stripped.startswith("[") and stripped != "[[entities]]":
            if not replaced:
                result.append(f"level = {level}")
                replaced = True
            in_entity = False
        if in_entity and re.match(r"^\s*level\s*=", line):
            result.append(re.sub(r"^(\s*level\s*=\s*)\d+(\s*(?:#.*)?)$", rf"\g<1>{level}\2", line))
            replaced = True
            continue
        result.append(line)
    if in_entity and not replaced:
        result.append(f"level = {level}")
    suffix = "\n" if str(content or "").endswith("\n") else ""
    updated = "\n".join(result) + suffix
    return _set_new_country_derived_include_levels_in_content(updated, level=level + 1)


def _set_new_country_derived_include_levels_in_content(content: str, *, level: int) -> str:
    level = max(1, min(5, int(level or 1)))
    lines = str(content or "").splitlines()
    result: list[str] = []
    block: list[str] = []
    in_include = False

    def flush_block() -> None:
        nonlocal block, in_include
        if not in_include:
            result.extend(block)
            block = []
            return
        has_derived_subdivisions = any(re.match(r"^\s*derived_subdivisions\s*=", line) for line in block)
        if not has_derived_subdivisions:
            result.extend(block)
            block = []
            in_include = False
            return
        replaced = False
        output: list[str] = []
        insert_at = 1
        for index, line in enumerate(block):
            if re.match(r"^\s*country_code\s*=", line):
                insert_at = index + 1
            if re.match(r"^\s*level\s*=", line):
                output.append(re.sub(r"^(\s*level\s*=\s*)\d+(\s*(?:#.*)?)$", rf"\g<1>{level}\2", line))
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.insert(insert_at, f"level = {level}")
        result.extend(output)
        block = []
        in_include = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_block()
            if stripped == "[[entities.include]]":
                in_include = True
                block = [line]
            else:
                result.append(line)
            continue
        if in_include:
            block.append(line)
        else:
            result.append(line)
    flush_block()
    suffix = "\n" if str(content or "").endswith("\n") else ""
    return "\n".join(result) + suffix


def _set_new_country_entity_metrics_in_content(
    content: str,
    *,
    area_value,
    population_value,
) -> str:
    metric_lines = [
        line
        for line in (
            _new_country_metric_toml_line("area_km2", area_value),
            _new_country_metric_toml_line("pop_latest", population_value, integer=True),
            _new_country_metric_toml_line("density", _density(population_value, area_value)),
        )
        if line
    ]
    metric_keys = {"area_km2", "area", "pop_latest", "population", "density"}
    lines = str(content or "").splitlines()
    result: list[str] = []
    in_entity = False
    entity_seen = False
    metrics_written = False

    def write_metrics() -> None:
        nonlocal metrics_written
        result.extend(metric_lines)
        metrics_written = True

    for line in lines:
        stripped = line.strip()
        if stripped == "[[entities]]":
            if in_entity and not metrics_written:
                write_metrics()
            in_entity = not entity_seen
            entity_seen = True
            result.append(line)
            continue
        if in_entity and stripped.startswith("["):
            if not metrics_written:
                write_metrics()
            in_entity = False
        if in_entity and re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line):
            key = line.split("=", 1)[0].strip()
            if key in metric_keys:
                continue
        result.append(line)
    if in_entity and not metrics_written:
        write_metrics()
    if not entity_seen:
        return str(content or "")
    suffix = "\n" if str(content or "").endswith("\n") else ""
    return "\n".join(result) + suffix


def _new_country_metric_toml_line(key: str, value, *, integer: bool = False) -> str:
    rendered = _new_country_toml_number(value, integer=integer)
    return f"{key} = {rendered}" if rendered is not None else ""


def _new_country_toml_number(value, *, integer: bool = False) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if integer:
        return str(int(round(number)))
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _derived_country_content_from_create_post(
    *,
    country: DerivedCountry,
    slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
    raw_content: str,
    selection_json: str,
    parent_config_slug: str = "",
    entity_level: int = 1,
    entity_code: str | None = None,
    entity_type: str = "",
    capitals=None,
) -> str:
    selection_json = str(selection_json or "").strip()
    if selection_json:
        try:
            selection = json.loads(selection_json)
        except json.JSONDecodeError as exc:
            raise ValueError(_("La seleccion visual no es JSON valido: %(error)s") % {"error": exc}) from exc
        selected_ids = selection.get("selected_ids") if isinstance(selection, dict) else None
        use_subdivisions = bool(selection.get("use_subdivisions")) if isinstance(selection, dict) else False
        selected_subdivision_keys = (
            selection.get("selected_subdivision_keys") if isinstance(selection, dict) else []
        ) or []
        selected_derived_subdivisions = (
            selection.get("selected_derived_subdivisions") if isinstance(selection, dict) else []
        ) or []
        selected_child_config_slugs = (
            selection.get("selected_child_config_slugs") if isinstance(selection, dict) else []
        ) or []
        if not isinstance(selected_ids, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones."))
        if not isinstance(selected_subdivision_keys, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones creadas."))
        if not isinstance(selected_derived_subdivisions, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones creadas."))
        if not isinstance(selected_child_config_slugs, list):
            raise ValueError(_("La seleccion visual debe contener una lista de entidades creadas."))
        selected_derived_subdivisions = _new_country_normalized_derived_subdivision_selections(
            selected_derived_subdivisions or selected_subdivision_keys,
            fallback_country_code=source_country_code,
        )
        selected_child_config_slugs = _new_country_normalized_config_slugs(selected_child_config_slugs)
        entity_mode = "intermediate" if not use_subdivisions and selected_child_config_slugs else "final"
        if not str(source_country_code or "").strip():
            raise ValueError(_("El pais fuente es obligatorio."))
        return render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug=slug,
            name=name,
            source_country_code=source_country_code,
            derived_country_code=derived_country_code,
            selected_ids=[str(value) for value in selected_ids],
            selected_derived_subdivisions=selected_derived_subdivisions,
            entity_name=name,
            entity_code=entity_code or slug,
            entity_type=entity_type,
            entity_mode=entity_mode,
            use_subdivisions=use_subdivisions,
            parent_config_slug=parent_config_slug,
            entity_level=entity_level,
            capitals=_normalized_derived_subdivision_capital_values(capitals),
        )

    content = str(raw_content or "").strip()
    if content:
        return content
    return _default_derived_country_toml(
        country=country,
        slug=slug,
        name=name,
        source_country_code=source_country_code,
        derived_country_code=derived_country_code,
    )


def _derived_source_country_options() -> list[dict[str, str]]:
    options = []
    seen = set()
    try:
        roots = (
            _visible_admin_areas()
            .filter(level=0)
            .order_by("name", "country_code", "id")
            .only("id", "country_code", "name", "level")
        )
        for root in roots:
            code = str(root.country_code or "").strip().lower()
            if not code or code in seen:
                continue
            seen.add(code)
            options.append(
                {
                    "value": code,
                    "label": _display_name(root.name, code, country_code=code),
                    "root_id": str(root.id),
                }
            )
        if options:
            return options
        country_codes = (
            _visible_admin_areas()
            .values_list("country_code", flat=True)
            .distinct()
            .order_by("country_code")
        )
        return [
            {"value": str(code), "label": _display_name("", code, country_code=str(code)), "root_id": ""}
            for code in country_codes
            if str(code or "").strip()
        ]
    except (OperationalError, ProgrammingError):
        return []


def _source_country_root_for_code(country_code: str) -> AdminArea | None:
    country_code = str(country_code or "").strip().lower()
    if not country_code:
        return None
    return (
        _visible_admin_areas()
        .filter(country_code__iexact=country_code, level=0)
        .order_by("parent_id", "name", "id")
        .first()
    )


def _source_country_display_label(country_code: str) -> str:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return ""
    root = _source_country_root_for_code(country_code)
    if root:
        return _display_name(root.name, country_code, country_code=country_code)
    return _display_name("", country_code, country_code=country_code)


def _new_country_detail_url(country: DerivedCountry) -> str:
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_detail",
            kwargs={"source_country_code": source_country_code, "country_slug": country.slug},
        )
    return reverse("ciudades_del_mundo:new_country_detail_legacy", kwargs={"country_slug": country.slug})


def _validate_new_country_source_or_404(country: DerivedCountry, source_country_code: str) -> None:
    source_country_code = _normalize_group_country_key(source_country_code)
    expected_source = _normalize_group_country_key(country.source_country_code)
    if expected_source and source_country_code and source_country_code != expected_source:
        raise Http404(_("El pais nuevo no pertenece a '%(country)s'.") % {"country": source_country_code})


def _new_country_source_kwargs(country: DerivedCountry) -> dict[str, str]:
    return {
        "source_country_code": _normalize_group_country_key(country.source_country_code),
        "country_slug": country.slug,
    }


def _new_country_config_new_url(country: DerivedCountry) -> str:
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse("ciudades_del_mundo:new_country_config_new", kwargs=_new_country_source_kwargs(country))
    return reverse("ciudades_del_mundo:new_country_config_new_legacy", kwargs={"country_slug": country.slug})


def _new_country_detail_export_excel_url(country: DerivedCountry) -> str:
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse("ciudades_del_mundo:new_country_detail_export_excel", kwargs=_new_country_source_kwargs(country))
    return reverse("ciudades_del_mundo:new_country_detail_export_excel_legacy", kwargs={"country_slug": country.slug})


def _new_country_config_edit_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_edit",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_edit_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_delete_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_delete",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_delete_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_clone_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_clone",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_clone_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_task_url(config: DerivedCountryConfig, action: str) -> str:
    country = config.country
    source_country_code = _normalize_group_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:start_new_country_config_task",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug, "action": action},
        )
    return reverse(
        "ciudades_del_mundo:start_new_country_config_task_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug, "action": action},
    )


def _new_country_config_parent_options(
    country: DerivedCountry,
    current_config: DerivedCountryConfig | None = None,
) -> list[dict[str, object]]:
    excluded = {current_config.slug} if current_config else set()
    if current_config:
        excluded.update(_new_country_config_descendant_slugs(country, current_config.slug))
    options: list[dict[str, object]] = [
        {"value": "", "label": _("%(name)s (Nivel 0)") % {"name": country.name}, "level": 0}
    ]
    for config in country.configs.order_by("name", "slug"):
        if config.slug in excluded:
            continue
        level = _new_country_config_level(config)
        options.append(
            {
                "value": config.slug,
                "label": _("%(name)s (Nivel %(level)s)") % {"name": config.name, "level": level},
                "level": level,
            }
        )
    return options


def _validate_new_country_config_parent(
    country: DerivedCountry,
    parent_config_slug: str,
    current_config: DerivedCountryConfig | None = None,
) -> str:
    parent_config_slug = _normalize_group_country_key(parent_config_slug)
    if not parent_config_slug:
        return ""
    if current_config and parent_config_slug == current_config.slug:
        raise ValueError(_("Una division no puede ser padre de si misma."))
    try:
        parent = country.configs.get(slug=parent_config_slug)
    except DerivedCountryConfig.DoesNotExist as exc:
        raise ValueError(_("El padre seleccionado no existe.")) from exc
    if current_config and current_config.slug in _new_country_config_parent_chain(parent):
        raise ValueError(_("El padre seleccionado crearia una referencia circular."))
    return parent.slug


def _new_country_parent_level(country: DerivedCountry, parent_config_slug: str) -> int:
    parent_config_slug = _normalize_group_country_key(parent_config_slug)
    if not parent_config_slug:
        return 0
    try:
        return _new_country_config_level(country.configs.get(slug=parent_config_slug))
    except DerivedCountryConfig.DoesNotExist:
        return 0


def _new_country_config_level(config: DerivedCountryConfig) -> int:
    data = _new_country_config_toml(config)
    for entity in data.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        try:
            level = int(entity.get("level"))
        except (TypeError, ValueError):
            level = 0
        if level > 0:
            return level
    parent_slug = _new_country_config_parent_slug(config)
    if parent_slug:
        return _new_country_parent_level(config.country, parent_slug) + 1
    return 1


def _new_country_config_parent_slug(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    for entity in data.get("entities") or []:
        if isinstance(entity, dict):
            parent_slug = _normalize_group_country_key(entity.get("parent_config_slug") or entity.get("parent"))
            if parent_slug:
                return parent_slug
    return _normalize_group_country_key(data.get("parent_config_slug") or data.get("parent"))


def _new_country_config_parent_chain(config: DerivedCountryConfig) -> set[str]:
    chain: set[str] = set()
    parent_slug = _new_country_config_parent_slug(config)
    while parent_slug and parent_slug not in chain:
        chain.add(parent_slug)
        parent = config.country.configs.filter(slug=parent_slug).first()
        if parent is None:
            break
        parent_slug = _new_country_config_parent_slug(parent)
    return chain


def _new_country_config_descendant_slugs(country: DerivedCountry, config_slug: str) -> set[str]:
    descendants: set[str] = set()
    changed = True
    while changed:
        changed = False
        for config in country.configs.all():
            if config.slug in descendants:
                continue
            parent_slug = _new_country_config_parent_slug(config)
            if parent_slug == config_slug or parent_slug in descendants:
                descendants.add(config.slug)
                changed = True
    return descendants


def _new_country_selected_derived_subdivisions_from_selection_json(
    selection_json: str,
    *,
    fallback_country_code: str,
) -> list[dict[str, str]]:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(selection, dict):
        return []
    return _new_country_normalized_derived_subdivision_selections(
        selection.get("selected_derived_subdivisions")
        or selection.get("selected_subdivision_keys")
        or selection.get("derived_subdivisions")
        or [],
        fallback_country_code=fallback_country_code,
    )


def _new_country_selected_subdivision_keys_from_selection_json(
    selection_json: str,
    *,
    fallback_country_code: str = "",
) -> list[str]:
    return [
        _new_country_derived_subdivision_selection_value(item["country_code"], item["key"])
        for item in _new_country_selected_derived_subdivisions_from_selection_json(
            selection_json,
            fallback_country_code=fallback_country_code,
        )
    ]


def _new_country_derived_subdivision_selection_value(country_code: str, subdivision_key: str) -> str:
    return _derived_subdivision_record_source_item_id(country_code, subdivision_key)


def _new_country_normalized_derived_subdivision_selections(
    values,
    *,
    fallback_country_code: str,
) -> list[dict[str, str | int]]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result: list[dict[str, str | int]] = []
    seen: dict[tuple[str, str], dict[str, str | int]] = {}
    fallback_country_code = _normalize_group_country_key(fallback_country_code)
    for value in values:
        country_code = ""
        subdivision_key = ""
        selection_level: int | None = None
        if isinstance(value, dict):
            encoded_id = str(value.get("id") or value.get("value") or "").strip()
            encoded_country, encoded_key = _new_country_split_derived_subdivision_value(encoded_id)
            country_code = _normalize_group_country_key(
                value.get("country_code") or value.get("source_country_code") or encoded_country or fallback_country_code
            )
            subdivision_key = str(
                value.get("key")
                or value.get("derived_subdivision_key")
                or value.get("internal_name")
                or encoded_key
                or value.get("code")
                or ""
            ).strip()
            try:
                raw_level = int(value.get("level") or 0)
            except (TypeError, ValueError):
                raw_level = 0
            if raw_level > 0:
                selection_level = max(1, min(9, raw_level))
        else:
            encoded_country, encoded_key = _new_country_split_derived_subdivision_value(str(value or "").strip())
            country_code = _normalize_group_country_key(encoded_country or fallback_country_code)
            subdivision_key = encoded_key or str(value or "").strip()
        try:
            subdivision_key = _group_internal_name(subdivision_key)
        except ValueError:
            continue
        marker = (country_code, subdivision_key.casefold())
        if not country_code:
            continue
        if marker in seen:
            if selection_level is not None and "level" not in seen[marker]:
                seen[marker]["level"] = selection_level
            continue
        entry: dict[str, str | int] = {"country_code": country_code, "key": subdivision_key}
        if selection_level is not None:
            entry["level"] = selection_level
        seen[marker] = entry
        result.append(entry)
    return result


def _new_country_split_derived_subdivision_value(value: str) -> tuple[str, str]:
    value = str(value or "").strip()
    if not value.startswith("derived-subdivision::"):
        return "", value
    parts = value.split("::", 2)
    if len(parts) < 3:
        return "", ""
    return _normalize_group_country_key(parts[1]), parts[2]


def _new_country_normalized_subdivision_keys(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result = []
    seen = set()
    for value in values:
        key = str(value or "").strip()
        marker = key.casefold()
        if not key or marker in seen:
            continue
        seen.add(marker)
        result.append(key)
    return result


def _new_country_capital_source_ids(
    country_code: str,
    selected_subdivision_keys: list[str] | None = None,
    *,
    country: DerivedCountry | None = None,
    selected_source_ids=None,
    selected_derived_subdivisions=None,
    selected_child_config_slugs=None,
    require_selection: bool = False,
) -> set[str] | None:
    selections = _new_country_normalized_derived_subdivision_selections(
        selected_derived_subdivisions if selected_derived_subdivisions is not None else selected_subdivision_keys,
        fallback_country_code=country_code,
    )
    child_config_slugs = _new_country_normalized_config_slugs(selected_child_config_slugs)
    selected_source_ids = _new_country_normalized_subdivision_keys(selected_source_ids)
    has_explicit_scope = bool(selections or child_config_slugs or selected_source_ids)
    if not has_explicit_scope:
        return set() if require_selection else None
    source_ids: set[str] = set()
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}
    source_ids.update(
        _new_country_config_admin_area_ids_to_source_ids(
            selected_source_ids,
            default_country_code=country_code,
        )
    )
    for item in selections:
        item_country_code = item["country_code"]
        item_key = item["key"]
        source_ids.update(
            _derived_subdivision_source_ids_for_key(
                item_country_code,
                item_key,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
        )
        source_ids.update(_derived_subdivision_declared_capital_ids_for_key(item_country_code, item_key))
    source_ids.update(
        _new_country_child_config_source_ids(
            country,
            child_config_slugs,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    )
    return source_ids


def _new_country_config_capital_options_payload(
    country_code: str,
    *,
    country: DerivedCountry | None = None,
    selected_ids=None,
    selected_source_ids=None,
    selected_subdivision_keys=None,
    selected_derived_subdivisions=None,
    selected_child_config_slugs=None,
    search_term: str = "",
    use_subdivisions: bool = False,
) -> list[dict]:
    country_code = _normalize_group_country_key(country_code)
    if not country_code:
        return []
    source_ids = _new_country_capital_source_ids(
        country_code,
        _new_country_normalized_subdivision_keys(selected_subdivision_keys),
        country=country,
        selected_source_ids=selected_source_ids,
        selected_derived_subdivisions=selected_derived_subdivisions,
        selected_child_config_slugs=selected_child_config_slugs,
        require_selection=bool(use_subdivisions),
    )
    return _derived_subdivision_capital_options(
        country_code,
        source_ids=source_ids,
        selected_ids=selected_ids,
        search_term=search_term,
    )


def _new_country_child_config_source_ids(
    country: DerivedCountry | None,
    selected_child_config_slugs,
    *,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> set[str]:
    if country is None:
        return set()
    slugs = _new_country_normalized_config_slugs(selected_child_config_slugs)
    if not slugs:
        return set()
    try:
        configs = {
            config.slug: config
            for config in country.configs.filter(slug__in=slugs).select_related("country")
        }
    except (OperationalError, ProgrammingError, ValueError):
        return set()
    result: set[str] = set()
    config_source_ids_cache: dict[tuple[str, str], set[str]] = {}
    for slug in slugs:
        config = configs.get(slug)
        if config is None:
            continue
        try:
            result.update(
                _new_country_config_preview_source_ids(
                    config,
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                    config_source_ids_cache=config_source_ids_cache,
                )
            )
            result.update(_new_country_config_declared_capital_source_ids(config))
        except (OperationalError, ProgrammingError, ValueError):
            continue
    return result


def _new_country_config_declared_capital_source_ids(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
) -> set[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    cache_key = (str(config.country_id or ""), config.slug)
    seen = set(seen or set())
    if cache_key in seen:
        return set()
    seen.add(cache_key)

    entity = _new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = _normalize_group_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    result = _declared_capital_ids_from_data(source_country_code, entity)
    for item in _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    ):
        result.update(
            _derived_subdivision_declared_capital_ids_for_key(
                item["country_code"],
                item["key"],
            )
        )

    parent_slug = _normalize_group_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        result.update(_new_country_config_declared_capital_source_ids(child, seen=seen))
    seen.remove(cache_key)
    return result


def _new_country_derived_subdivision_options(source_country_code: str) -> list[dict[str, str]]:
    source_country_code = _normalize_group_country_key(source_country_code)
    try:
        records = list(_new_country_derived_subdivision_queryset(""))
    except (OperationalError, ProgrammingError):
        return []
    return _new_country_derived_subdivision_options_from_records(records)


def _new_country_created_subdivision_country_options() -> list[dict[str, str]]:
    try:
        country_codes = list(
            DerivedSubdivision.objects.exclude(source_country_code="")
            .values_list("source_country_code", flat=True)
            .distinct()
            .order_by("source_country_code")
        )
    except (OperationalError, ProgrammingError):
        return []
    options = []
    seen = set()
    for raw_code in country_codes:
        code = _normalize_group_country_key(raw_code)
        if not code or code in seen:
            continue
        seen.add(code)
        options.append({"value": code, "label": _source_country_display_label(code)})
    return options


def _new_country_derived_subdivision_queryset(search_term: str, *, country_code: str = ""):
    queryset = DerivedSubdivision.objects.order_by("source_country_code", "name", "slug").only(
        "slug",
        "internal_name",
        "name",
        "code",
        "entity_type",
        "source_country_code",
        "content",
    )
    country_code = _normalize_group_country_key(country_code)
    if country_code:
        queryset = queryset.filter(source_country_code__iexact=country_code)
    search_term = str(search_term or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(slug__icontains=search_term)
            | Q(internal_name__icontains=search_term)
            | Q(name__icontains=search_term)
            | Q(code__icontains=search_term)
            | Q(entity_type__icontains=search_term)
            | Q(source_country_code__icontains=search_term)
            | Q(content__icontains=search_term)
        )
    return queryset


def _new_country_derived_subdivision_options_from_records(
    records: list[DerivedSubdivision],
) -> list[dict[str, str]]:
    data_by_slug = {record.slug: _derived_subdivision_record_toml(record) for record in records}
    code_counts = _new_country_derived_subdivision_code_counts(records, data_by_slug=data_by_slug)
    materialized_rows = _derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    options = []
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        area_value, population_value = _derived_subdivision_metric_values(
            record,
            data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        density_text = _group_metric_text(_density(population_value, area_value))
        try:
            level = _derived_subdivision_level(data.get("level") or 1)
        except ValueError:
            level = 1
        entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
        type_text = entity_type or _("Sin tipo")
        record_country_code = _normalize_group_country_key(record.source_country_code)
        record_key = _derived_subdivision_source_key(record, data)
        country_label = _source_country_display_label(record_country_code)
        clean_name = _new_country_clean_created_subdivision_name(record.name)
        selection_name = str(record.name or "").strip() or clean_name or str(record.internal_name or record.slug).strip()
        content_search_text = _new_country_derived_subdivision_content_search_text(data)
        options.append(
            {
                "value": _new_country_derived_subdivision_selection_value(record_country_code, record_key),
                "country_code": record_country_code,
                "country_label": country_label,
                "key": record_key,
                "label": _new_country_derived_subdivision_label(record),
                "name": clean_name,
                "selection_name": selection_name,
                "code": str(record.code or record.internal_name or record.slug),
                "entity_type": entity_type,
                "level": level,
                "type_text": type_text,
                "area_text": area_text,
                "population_text": population_text,
                "density_text": density_text,
                "search_text": " ".join(
                    [
                        record.slug,
                        record.internal_name,
                        record.name,
                        clean_name,
                        selection_name,
                        record.entity_type,
                        record.code,
                        record.source_country_code,
                        country_label,
                        entity_type,
                        str(level),
                        type_text,
                        area_text,
                        population_text,
                        density_text,
                        content_search_text,
                    ]
                ),
            }
        )
    return options


def _new_country_source_area_options_from_records(records: list[AdminArea]) -> list[dict[str, str]]:
    options = []
    country_labels: dict[str, str] = {}
    for area in records:
        country_code = _normalize_group_country_key(area.country_code)
        if country_code not in country_labels:
            country_labels[country_code] = _source_country_display_label(country_code)
        name = _area_display_name(area)
        entity_type = _entity_type_label(area.entity_type, country_code=country_code)
        parent = getattr(area, "parent", None)
        parent_label = _area_display_name(parent) if parent else ""
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append(
            {
                "value": str(area.id),
                "id": str(area.id),
                "country_code": country_code,
                "country_label": country_labels[country_code],
                "name": name,
                "label": label,
                "code": str(area.code or ""),
                "level": int(area.level or 0),
                "type_text": entity_type,
                "entity_type": entity_type,
                "parent_id": str(area.parent_id or ""),
                "parent_label": parent_label,
            }
        )
    return options


def _new_country_derived_subdivision_content_search_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    values: list[str] = []

    def add(value) -> None:
        if value in (None, ""):
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
            return
        text = str(value or "").strip()
        if text:
            values.append(text)

    for key in ("internal_name", "name", "code", "entity_type", "parent_code", "capitals"):
        add(data.get(key))
    for section_name in ("include", "subtract"):
        blocks = data.get(section_name) if isinstance(data.get(section_name), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("country_code", "names", "ids", "codes", "groups", "derived_subdivisions", "expressions"):
                add(block.get(key))
    return " ".join(values)


def _new_country_derived_subdivision_code_counts(
    records: list[DerivedSubdivision],
    *,
    data_by_slug: dict[str, dict],
) -> Counter:
    country_codes = {
        _normalize_group_country_key((data_by_slug.get(record.slug) or {}).get("source_country_code") or record.source_country_code)
        for record in records
    }
    country_codes = {country_code for country_code in country_codes if country_code}
    if not country_codes:
        return Counter()
    try:
        count_records = list(
            DerivedSubdivision.objects.filter(source_country_code__in=country_codes).only(
                "slug",
                "internal_name",
                "code",
                "source_country_code",
                "content",
            )
        )
    except (OperationalError, ProgrammingError):
        count_records = records
    count_data_by_slug = {
        record.slug: data_by_slug.get(record.slug) or _derived_subdivision_record_toml(record)
        for record in count_records
    }
    return Counter(
        _derived_subdivision_requested_code_count_key(record, count_data_by_slug.get(record.slug) or {})
        for record in count_records
    )


def _new_country_derived_subdivision_label(row: DerivedSubdivision) -> str:
    code = str(row.code or row.internal_name or row.slug).strip()
    suffix = f" - {code}" if code else ""
    name = _new_country_clean_created_subdivision_name(row.name) or str(row.internal_name or row.slug).strip()
    return f"{name}{suffix}"


def _new_country_clean_created_subdivision_name(value: str | None) -> str:
    original = str(value or "").strip()
    if not original:
        return ""
    cleaned = re.sub(r"\s*\([^)]*\)", "", original).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned or original


def _admin_area_child_counts(parent_ids: list[str], *, group_source_only: bool = False) -> dict[str, int]:
    parent_ids = [str(value) for value in parent_ids if str(value or "").strip()]
    if not parent_ids:
        return {}
    try:
        queryset = _group_source_admin_areas() if group_source_only else _visible_admin_areas()
        return {
            str(row["parent_id"]): int(row["total"] or 0)
            for row in queryset
            .filter(parent_id__in=parent_ids)
            .values("parent_id")
            .annotate(total=Count("id"))
        }
    except (OperationalError, ProgrammingError):
        return {}


def _derived_country_or_404(slug: str) -> DerivedCountry:
    try:
        return DerivedCountry.objects.get(slug=_normalize_recipe_slug(slug))
    except DerivedCountry.DoesNotExist as exc:
        raise Http404(_("No existe el pais nuevo '%(slug)s'.") % {"slug": slug}) from exc


def _derived_country_config_or_404(country: DerivedCountry, slug: str) -> DerivedCountryConfig:
    try:
        return country.configs.get(slug=_normalize_recipe_slug(slug))
    except DerivedCountryConfig.DoesNotExist as exc:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug}) from exc


def _validate_plain_toml(content: str, *, expected_kind: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("TOML invalido."))
    kind = str(data.get("kind") or "").strip()
    if kind and kind != expected_kind:
        raise ValueError(_("El TOML debe usar kind = '%(kind)s'.") % {"kind": expected_kind})
    return data


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _default_derived_country_toml(
    *,
    country: DerivedCountry,
    slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
) -> str:
    source_country_code = str(source_country_code or "").strip().lower()
    return "\n".join(
        [
            "schema_version = 1",
            'kind = "derived_country_config"',
            f"country = {_toml_string(country.slug)}",
            f"slug = {_toml_string(slug)}",
            f"name = {_toml_string(name)}",
            f"source_country_code = {_toml_string(source_country_code)}",
            f"derived_country_code = {_toml_string(derived_country_code)}",
            "groups = []",
            "",
            "[selection]",
            f"source_country_code = {_toml_string(source_country_code)}",
            "include_ids = []",
            "subtract_ids = []",
            "include_codes = []",
            "subtract_codes = []",
            "group_slugs = []",
            "",
        ]
    )


def _config_record(slug: str) -> ScrapingConfig | None:
    if scraping_config_table_exists():
        return ScrapingConfig.objects.filter(slug=slug).first()
    return None


def _web_task_table_exists() -> bool:
    """Return whether the persisted task table is migrated and usable."""
    try:
        from ciudades_del_mundo.models import WebTask

        return WebTask._meta.db_table in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def _ensure_config_available_for_task(slug: str, *, import_from_toml: bool = False) -> tuple[bool, str]:
    """Ensure a SQL config can be used before launching validate/scrape."""
    if scraping_config_table_exists():
        try:
            if ScrapingConfig.objects.filter(slug=slug).exists():
                return True, ""
        except (OperationalError, ProgrammingError) as exc:
            return False, _(
                "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
            ) % {"error": exc}
        if import_from_toml:
            return _import_missing_scraping_config_from_toml(slug)

    return False, _(
        "No existe la configuración SQL '%(slug)s'. Ejecuta "
        "'py manage.py sync_scraping_configs %(slug)s' para importarla desde seeds TOML."
    ) % {"slug": slug}


def _ensure_bulk_configs_available_for_task() -> tuple[bool, str]:
    """Bootstrap bundled TOML configs before bulk config actions when SQL is empty."""
    if not scraping_config_table_exists():
        return False, ""
    try:
        if ScrapingConfig.objects.exists():
            return True, ""
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            ensure_initial_scraping_configs(force=False)
        else:
            with write_lock:
                ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
    except Exception as exc:  # noqa: BLE001 - surfaced to AJAX instead of a debug page.
        return False, _("No se pudo importar el TOML: %(error)s") % {"error": exc}
    return True, ""


def _import_missing_scraping_config_from_toml(slug: str) -> tuple[bool, str]:
    """Import the matching TOML seed for a missing SQL config row."""
    if not scraping_config_table_exists():
        return _ensure_config_available_for_task(slug, import_from_toml=False)
    if not bundled_toml_config_paths([slug]):
        return _ensure_config_available_for_task(slug, import_from_toml=False)
    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            sync_scraping_configs_from_toml(force=False, only_if_empty=False, slugs=[slug])
        else:
            with write_lock:
                sync_scraping_configs_from_toml(force=False, only_if_empty=False, slugs=[slug])
        if ScrapingConfig.objects.filter(slug=slug).exists():
            return True, ""
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
    except Exception as exc:  # noqa: BLE001 - surfaced to AJAX instead of a debug page.
        return False, _("No se pudo importar el TOML: %(error)s") % {"error": exc}
    return _ensure_config_available_for_task(slug, import_from_toml=False)


def _config_exists(slug: str) -> bool:
    return _config_record(slug) is not None


def _config_country_code_for_slug(slug: str) -> str:
    record = _config_record(slug)
    if not record:
        return slug
    if record.country_code:
        return record.country_code
    try:
        data = tomllib.loads(record.content)
    except tomllib.TOMLDecodeError:
        return slug
    return str(data.get("country_code") or slug or "")


def _recipe_path(slug: str, *, group: str, must_exist: bool = True) -> Path:
    root = NEW_RECIPES_ROOT if group == "new" else HISTORICAL_RECIPES_ROOT
    path = root / f"{slug}.py"
    if must_exist and not path.is_file():
        raise Http404(_("No existe la receta '%(slug)s'.") % {"slug": slug})
    return path


def _any_recipe_path(slug: str) -> Path:
    for group in ("new", "historical"):
        path = _recipe_path(slug, group=group, must_exist=False)
        if path.is_file():
            return path
    raise Http404(_("No existe la receta '%(slug)s'.") % {"slug": slug})


def _validate_config_text(slug: str, content: str) -> None:
    try:
        data = tomllib.loads(content)
        pages = parse_pages(
            data.get("pages"),
            slug=slug,
            schema_version=int(data.get("scrape_schema_version", 1)),
        )
        if not pages:
            raise ValueError(_("La configuracion debe definir al menos una pagina."))
        RepresentationConfig.from_mapping(data.get("representation"))
        parse_cities(data.get("cities"))
        parse_entity_merges(data.get("entity_merges", data.get("merge_entities")))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    except Exception as exc:
        raise ValueError(_("Configuracion invalida: %(error)s") % {"error": exc}) from exc


def _render_recipe_from_form(form: dict) -> str:
    try:
        divisions = json.loads(form["divisions_json"])
    except json.JSONDecodeError as exc:
        raise ValueError(_("DIVISIONS no es JSON valido: %(error)s") % {"error": exc}) from exc
    if not isinstance(divisions, (list, dict)):
        raise ValueError(_("DIVISIONS debe ser una lista o un objeto JSON."))
    divisions = _numeric_keys_to_int(divisions)

    lines = [
        '"""Derived hierarchy recipe created from the web interface."""',
        "",
    ]
    if form.get("source_country"):
        lines.append(f"SOURCE_COUNTRY = {form['source_country']!r}")
    if form.get("root_name"):
        lines.append(f"ROOT_NAME = {form['root_name']!r}")
    if form.get("municipal_level"):
        lines.append(f"MUNICIPAL_LEVEL = {int(form['municipal_level'])}")

    representation = {}
    if form.get("representation_level"):
        representation["level"] = int(form["representation_level"])
    if form.get("representation_total"):
        representation["total"] = int(form["representation_total"])
    if form.get("representation_min"):
        representation["min"] = int(form["representation_min"])
    if representation:
        representation["system"] = "dhondt"
        lines.append(f"REPRESENTATION = {pformat(representation, sort_dicts=False)}")

    lines.append(f"DIVISIONS = {pformat(divisions, width=100, sort_dicts=False)}")
    lines.append("")
    return "\n".join(lines)


def _numeric_keys_to_int(value):
    if isinstance(value, list):
        return [_numeric_keys_to_int(item) for item in value]
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            if isinstance(key, str) and key.isdigit():
                key = int(key)
            converted[key] = _numeric_keys_to_int(item)
        return converted
    return value


def _validate_recipe_text(content: str, *, filename: str) -> None:
    try:
        tree = ast.parse(content, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as exc:
        raise ValueError(_("Python invalido: %(error)s") % {"error": exc}) from exc

    assignment_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignment_names.add(target.id)
    if "DIVISIONS" not in assignment_names:
        raise ValueError(_("La receta debe definir DIVISIONS."))


def _literal_assignment(path: Path, names: tuple[str, ...]) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                return str(value)
    return None
