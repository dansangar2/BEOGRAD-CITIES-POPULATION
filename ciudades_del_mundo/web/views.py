"""Operational web views for managing local geography data."""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
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
from django.db import OperationalError, ProgrammingError, connection
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
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
from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
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

from ciudades_del_mundo.services.scraping_configs import (
    bundled_toml_config_paths,
    ensure_initial_scraping_configs,
    export_scraping_configs_to_toml,
    scraping_config_bootstrap_status,
    scraping_config_table_exists,
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
from ciudades_del_mundo.services.derived_config_seeds import (
    bundled_new_country_config_paths,
    bundled_subdivision_group_paths,
    render_derived_country_selection_toml,
)
from ciudades_del_mundo.services.status_codes import (
    get_program_message_catalog,
    program_message_payload,
)

from .spain_translations import normalize_language_code, spain_entity_type, spain_name
from .task_progress import read_task_config_progress
from .tasks import task_manager


COUNTRY_LEVEL_OPTION_MAX_ROWS = 500




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
    return JsonResponse(_country_detail_payload(country_code, request.GET.get("level")))


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
            upsert_scraping_config(slug, content)
            _persist_manual_asset_overrides(slug, content)
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
            upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _persist_manual_asset_overrides(slug, content)
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

    config_ready, config_error = _ensure_config_available_for_task(slug)
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
            upsert_scraping_config(slug, content, source_path=config_record.source_path)
            _persist_manual_asset_overrides(slug, content)
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
    asset_editor_enabled = bool(int(workflow.get("rows") or 0) > 0)
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
    if request.method != "POST":
        return redirect("ciudades_del_mundo:recipe_list")

    slug = _normalize_recipe_slug(slug)
    if action == "build":
        _any_recipe_path(slug)
        args = ["build_new_subdivisions", "--country-id", slug]
        population_year = (request.POST.get("population_year") or "").strip()
        if population_year:
            if not population_year.isdigit():
                messages.error(request, _("El ano de poblacion debe ser numerico."))
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
        raise Http404(_("Accion de receta no soportada."))

    task = task_manager.start(key=key, label=label, args=args)
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


def new_country_import_toml(request):
    """Queue one SQL import task per new-country TOML seed."""
    if request.method != "POST":
        return redirect("ciudades_del_mundo:new_country_list")
    paths = bundled_new_country_config_paths()
    task_count = _queue_derived_seed_import_tasks(
        paths,
        section="new-countries",
        key_prefix="import-new-country",
        label_template=_("Importar pais nuevo TOML: %(slug)s"),
    )
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
    if request.method != "POST":
        return redirect("ciudades_del_mundo:group_list")
    paths = bundled_subdivision_group_paths()
    task_count = _queue_derived_seed_import_tasks(
        paths,
        section="groups",
        key_prefix="import-group",
        label_template=_("Importar grupo TOML: %(slug)s"),
    )
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
    """List derived-country containers and their editable TOML variants."""
    countries = (
        DerivedCountry.objects.annotate(config_count=Count("configs"))
        .order_by("name", "slug")
    )
    return render(
        request,
        "ciudades_del_mundo/new_country_list.html",
        {"countries": countries, "seed_count": len(bundled_new_country_config_paths())},
    )


def new_country_new(request):
    """Create a derived-country container."""
    form = {"slug": "", "name": "", "source_country_code": "", "description": ""}
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            slug = _normalize_recipe_slug(form["slug"])
            if DerivedCountry.objects.filter(slug=slug).exists():
                raise ValueError(_("Ya existe un pais nuevo con slug '%(slug)s'.") % {"slug": slug})
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
            return redirect("ciudades_del_mundo:new_country_detail", country_slug=country.slug)
    return render(request, "ciudades_del_mundo/new_country_form.html", {"form": form, "mode": "country"})


def new_country_detail(request, country_slug):
    """List configurations for one derived-country container."""
    country = _derived_country_or_404(country_slug)
    configs = country.configs.order_by("name", "slug")
    rows = []
    for config in configs:
        built_code = config.derived_country_code or config.slug
        root = NuevoAdminArea.objects.filter(country_code=built_code, parent__isnull=True).order_by("name").first()
        rows.append(
            {
                "config": config,
                "built_code": built_code,
                "root": root,
                "built_count": NuevoAdminArea.objects.filter(country_code=built_code).count(),
            }
        )
    return render(
        request,
        "ciudades_del_mundo/new_country_detail.html",
        {"country": country, "rows": rows},
    )


def new_country_config_new(request, country_slug):
    """Create a TOML configuration for one derived country."""
    country = _derived_country_or_404(country_slug)
    source_country_code = str(
        request.POST.get("source_country_code")
        or request.GET.get("source_country_code")
        or country.source_country_code
        or ""
    ).strip().lower()
    form = {
        "slug": "",
        "name": "",
        "source_country_code": source_country_code,
        "derived_country_code": "",
        "content": "",
        "is_active": "1",
        "selection_json": "",
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            slug = _normalize_recipe_slug(form["slug"])
            if country.configs.filter(slug=slug).exists():
                raise ValueError(_("Ya existe una configuracion '%(slug)s' para este pais.") % {"slug": slug})
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = _normalize_recipe_slug(form["derived_country_code"] or slug)
            content = _derived_country_content_from_create_post(
                country=country,
                slug=slug,
                name=name,
                source_country_code=form["source_country_code"],
                derived_country_code=derived_code,
                raw_content=form["content"],
                selection_json=form["selection_json"],
            )
            _validate_plain_toml(content, expected_kind="derived_country_config")
            config = DerivedCountryConfig.objects.create(
                country=country,
                slug=slug,
                name=name,
                source_country_code=str(form["source_country_code"] or "").strip().lower(),
                derived_country_code=derived_code,
                content=content,
                is_active=bool(request.POST.get("is_active")),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' creada.") % {"slug": config.slug})
            return redirect(
                "ciudades_del_mundo:new_country_config_edit",
                country_slug=country.slug,
                config_slug=config.slug,
            )
    elif not form["content"]:
        form["content"] = _default_derived_country_toml(
            country=country,
            slug="nueva_configuracion",
            name="Nueva configuracion",
            source_country_code=country.source_country_code,
            derived_country_code="nueva_configuracion",
        )
    return render(
        request,
        "ciudades_del_mundo/new_country_config_create.html",
        {
            "country": country,
            "form": form,
            "config": None,
            "source_countries": _derived_source_country_options(),
            "children_url": reverse("ciudades_del_mundo:new_country_source_children"),
        },
    )


def new_country_config_edit(request, country_slug, config_slug):
    """Edit one TOML-backed derived-country configuration."""
    country = _derived_country_or_404(country_slug)
    config = _derived_country_config_or_404(country, config_slug)
    form = {
        "slug": config.slug,
        "name": config.name,
        "source_country_code": config.source_country_code,
        "derived_country_code": config.derived_country_code,
        "content": config.content,
        "is_active": "1" if config.is_active else "",
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = _normalize_recipe_slug(form["derived_country_code"] or config.slug)
            content = str(form["content"] or "").strip()
            _validate_plain_toml(content, expected_kind="derived_country_config")
            config.name = name
            config.source_country_code = str(form["source_country_code"] or "").strip().lower()
            config.derived_country_code = derived_code
            config.content = content
            config.is_active = bool(request.POST.get("is_active"))
            config.save()
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' guardada.") % {"slug": config.slug})
            return redirect(
                "ciudades_del_mundo:new_country_config_edit",
                country_slug=country.slug,
                config_slug=config.slug,
            )
    return render(
        request,
        "ciudades_del_mundo/new_country_config_form.html",
        {"country": country, "config": config, "form": form},
    )


def new_country_config_view(request, country_slug, config_slug):
    """Show the built NuevoAdminArea tree attached to one derived-country config."""
    country = _derived_country_or_404(country_slug)
    config = _derived_country_config_or_404(country, config_slug)
    built_code = config.derived_country_code or config.slug
    root = NuevoAdminArea.objects.filter(country_code=built_code, parent__isnull=True).order_by("name").first()
    stats = NuevoAdminArea.objects.filter(country_code=built_code).aggregate(
        total=Count("id"),
        population=Sum("pop_latest"),
        seats=Sum("representatives"),
    )
    return render(
        request,
        "ciudades_del_mundo/new_country_config_view.html",
        {"country": country, "config": config, "built_code": built_code, "root": root, "stats": stats},
    )


def new_country_source_children(request):
    """Return source `AdminArea` children for the derived-country creation tree."""
    country_code = str(request.GET.get("country_code") or "").strip().lower()
    parent_id = str(request.GET.get("parent_id") or "").strip()
    if not country_code:
        return JsonResponse({"children": []})
    try:
        if parent_id:
            parent = _visible_admin_areas().get(id=parent_id, country_code__iexact=country_code)
            children = _visible_admin_areas().filter(parent=parent)
        else:
            root = _source_country_root_for_code(country_code)
            if root:
                children = _visible_admin_areas().filter(parent=root)
            else:
                children = _visible_admin_areas().filter(country_code__iexact=country_code, level=1)
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
    child_counts = _admin_area_child_counts([str(row.id) for row in rows])
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
                    "has_children": child_counts.get(str(row.id), 0) > 0,
                }
                for row in rows
            ]
        }
    )


def group_list(request):
    """List reusable TOML groups for historical/custom subdivisions."""
    groups = SubdivisionGroup.objects.order_by("name", "slug")
    return render(
        request,
        "ciudades_del_mundo/group_list.html",
        {"groups": groups, "seed_count": len(bundled_subdivision_group_paths())},
    )



def group_new(request):
    """Create a reusable subdivision group."""
    form = {"slug": "", "name": "", "source_country_code": "", "description": "", "content": ""}
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            slug = _normalize_recipe_slug(form["slug"])
            if SubdivisionGroup.objects.filter(slug=slug).exists():
                raise ValueError(_("Ya existe un grupo '%(slug)s'.") % {"slug": slug})
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            content = str(form["content"] or "").strip() or _default_group_toml(
                slug=slug,
                name=name,
                source_country_code=form["source_country_code"],
            )
            _validate_plain_toml(content, expected_kind="subdivision_group")
            group = SubdivisionGroup.objects.create(
                slug=slug,
                name=name,
                source_country_code=str(form["source_country_code"] or "").strip().lower(),
                description=str(form["description"] or "").strip(),
                content=content,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Grupo '%(slug)s' creado.") % {"slug": group.slug})
            return redirect("ciudades_del_mundo:group_edit", slug=group.slug)
    elif not form["content"]:
        form["content"] = _default_group_toml(
            slug="nuevo_grupo",
            name="Nuevo grupo",
            source_country_code="",
        )
    return render(request, "ciudades_del_mundo/group_form.html", {"form": form, "group": None})


def group_edit(request, slug):
    """Edit a reusable subdivision group."""
    slug = _normalize_recipe_slug(slug)
    try:
        group = SubdivisionGroup.objects.get(slug=slug)
    except SubdivisionGroup.DoesNotExist as exc:
        raise Http404(_("No existe el grupo '%(slug)s'.") % {"slug": slug}) from exc
    form = {
        "slug": group.slug,
        "name": group.name,
        "source_country_code": group.source_country_code,
        "description": group.description,
        "content": group.content,
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form})
        try:
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            content = str(form["content"] or "").strip()
            _validate_plain_toml(content, expected_kind="subdivision_group")
            group.name = name
            group.source_country_code = str(form["source_country_code"] or "").strip().lower()
            group.description = str(form["description"] or "").strip()
            group.content = content
            group.save()
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Grupo '%(slug)s' guardado.") % {"slug": group.slug})
            return redirect("ciudades_del_mundo:group_edit", slug=group.slug)
    return render(request, "ciudades_del_mundo/group_form.html", {"group": group, "form": form})


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


def _page_size(request, *, default: int) -> int:
    try:
        value = int(request.GET.get("page_size") or default)
    except (TypeError, ValueError):
        return default
    return max(10, min(value, 200))


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
    for capital in capitals.all():
        names.append(language_overrides.get(capital.id) or _area_display_name(capital))
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


def _country_detail_payload(country_code: str, selected_level: int | str | None = None) -> dict:
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

    first_order = list(_country_top_level_areas(country_code, root).select_related("parent").order_by("name"))
    population_total = country_record["population"]
    area_total = country_record["area_km2"] or 0
    rows = _country_table_rows(country_code, root, selected_level, population_total, area_total, selected_entity_types)
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
        },
        "first_order": {
            "population_chart": _donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _donut_payload(first_order, "area_km2", area_total),
            "cards": _first_order_cards(country_code, root, first_order, population_total, area_total),
            "child_groups": _country_first_order_child_groups(country_code, root, first_order, population_total, area_total),
        },
    }



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
        partial_level = False
        if level_total <= COUNTRY_LEVEL_OPTION_MAX_ROWS:
            entity_type_values = tuple(sorted(type_stats))
            filter_value = str(level)
        else:
            partial_level = True
            entity_type_values = tuple(
                sorted(
                    entity_type
                    for entity_type, (count, rows_with_children) in type_stats.items()
                    if count <= COUNTRY_LEVEL_OPTION_MAX_ROWS and rows_with_children > 0
                )
            )
            if not entity_type_values:
                break
            level_total = sum(type_stats[entity_type][0] for entity_type in entity_type_values)
            filter_value = "|".join([str(level), *entity_type_values])
        entity_types = [_entity_type_label(item, country_code=country_code) or _("Sin tipo") for item in entity_type_values]
        entity_type_label = " / ".join(entity_types[:3])
        rows.append(
            {
                "value": level,
                "filter_value": filter_value,
                "label": entity_type_label or _("Nivel %(level)s") % {"level": _display_level(level, root)},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
        if partial_level:
            break
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
    if selected_level is None:
        return []
    rows = (
        _visible_admin_areas().filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name")
    )
    if selected_entity_types:
        rows = rows.filter(entity_type__in=selected_entity_types)
    if root:
        rows = rows.exclude(id=root.id)
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
        parsed = _parse_config_editor_data(slug, "", asset_editor_enabled=False)
        rendered_content = ""
    else:
        parsed = _parse_config_editor_data(slug, content, asset_editor_enabled=asset_editor_enabled)
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


def _parse_config_editor_data(slug: str, content: str, *, asset_editor_enabled: bool = False) -> dict:
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
        _visible_admin_areas()
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
    visual_assets = _manual_visual_assets_from_post(post)
    asset_overrides = _manual_asset_overrides_from_post(post)
    if not pages:
        raise ValueError(_("Debes indicar al menos una ruta de scrapeo."))

    preserved = _preserved_manual_config_fragments(current_content)
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


def _preserved_manual_config_fragments(content: str) -> dict[str, list[str]]:
    """Keep TOML sections that the visual manual editor does not own.

    The manual editor only edits the country identity fields, [[pages]],
    [visual_assets] and explicit wikidata asset overrides.  Everything else
    (representation, configured city merges, entity merges, runtime synthetic
    rows, etc.) must survive a manual save/populate cycle.
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
        raise ValueError(_("El slug de receta debe ser un identificador Python en minusculas."))
    return slug


def _queue_derived_seed_import_tasks(
    paths: list[Path],
    *,
    section: str,
    key_prefix: str,
    label_template: str,
) -> int:
    count = 0
    for path in paths:
        slug = path.stem
        task_manager.start(
            key=f"{key_prefix}:{slug}",
            label=label_template % {"slug": slug},
            args=["sync_derived_configs", section, slug, "--force"],
        )
        count += 1
    return count


def _derived_country_content_from_create_post(
    *,
    country: DerivedCountry,
    slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
    raw_content: str,
    selection_json: str,
) -> str:
    selection_json = str(selection_json or "").strip()
    if selection_json:
        try:
            selection = json.loads(selection_json)
        except json.JSONDecodeError as exc:
            raise ValueError(_("La seleccion visual no es JSON valido: %(error)s") % {"error": exc}) from exc
        selected_ids = selection.get("selected_ids") if isinstance(selection, dict) else None
        if not isinstance(selected_ids, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones."))
        if not str(source_country_code or "").strip():
            raise ValueError(_("El pais fuente es obligatorio."))
        return render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug=slug,
            name=name,
            source_country_code=source_country_code,
            derived_country_code=derived_country_code,
            selected_ids=[str(value) for value in selected_ids],
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


def _admin_area_child_counts(parent_ids: list[str]) -> dict[str, int]:
    parent_ids = [str(value) for value in parent_ids if str(value or "").strip()]
    if not parent_ids:
        return {}
    try:
        return {
            str(row["parent_id"]): int(row["total"] or 0)
            for row in _visible_admin_areas()
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


def _default_group_toml(*, slug: str, name: str, source_country_code: str) -> str:
    return "\n".join(
        [
            "schema_version = 1",
            'kind = "subdivision_group"',
            f"slug = {_toml_string(slug)}",
            f"name = {_toml_string(name)}",
            f"source_country_code = {_toml_string(str(source_country_code or '').strip().lower())}",
            "",
            "[selection]",
            f"source_country_code = {_toml_string(str(source_country_code or '').strip().lower())}",
            "include_ids = []",
            "subtract_ids = []",
            "include_codes = []",
            "subtract_codes = []",
            "include_levels = []",
            "include_names = []",
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


def _ensure_config_available_for_task(slug: str) -> tuple[bool, str]:
    """Ensure a SQL config can be used before launching validate/scrape."""
    if scraping_config_table_exists():
        try:
            if ScrapingConfig.objects.filter(slug=slug).exists():
                return True, ""
        except (OperationalError, ProgrammingError) as exc:
            return False, _(
                "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
            ) % {"error": exc}

    return False, _(
        "No existe la configuración SQL '%(slug)s'. Ejecuta "
        "'py manage.py sync_scraping_configs %(slug)s' para importarla desde seeds TOML."
    ) % {"slug": slug}


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
