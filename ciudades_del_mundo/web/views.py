"""Operational web views for managing local geography data."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from pprint import pformat
import re
import tomllib
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import urlopen

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError, connection
from django.db.models import Count, Sum
from django.http import Http404, HttpResponseRedirect, JsonResponse
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
from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.models import AdminArea, NuevoAdminArea, ScrapingConfig

from ciudades_del_mundo.services.scraping_configs import (
    ensure_initial_scraping_configs,
    parse_config_metadata,
    scraping_config_bootstrap_status,
    scraping_config_table_exists,
    upsert_scraping_config,
)

from .spain_translations import normalize_language_code, spain_entity_type, spain_name
from .tasks import task_manager


CONFIG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RECIPE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HIDDEN_CITY_MERGE_STATUS = 3
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
SUBDIVISIONS_ROOT = PACKAGE_ROOT / "subdivisions"
NEW_RECIPES_ROOT = PACKAGE_ROOT / "new_subdivisions"
HISTORICAL_RECIPES_ROOT = PACKAGE_ROOT / "historical_divisions"
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
    return JsonResponse(_country_summary_payload(detail_route="ciudades_del_mundo:api_country_detail"))


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


def _task_payload(task, *, include_output: bool = False) -> dict:
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
    }
    if include_output:
        payload["output"] = task_manager.output_text(task)[-4000:]
    return payload


def dashboard_population_data(request):
    """Return root-country population rows for the dashboard pie chart."""
    return JsonResponse(_country_summary_payload(detail_route="ciudades_del_mundo:dashboard_country_detail"))


def _country_summary_payload(*, detail_route: str) -> dict:
    countries = _admin_root_population_rows(detail_route=detail_route)
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
    try:
        selected_level = int(request.GET.get("level")) if request.GET.get("level") not in (None, "") else None
    except (TypeError, ValueError):
        selected_level = None
    return JsonResponse(_country_detail_payload(country_code, selected_level))


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
    """Render the asynchronous SQL/TOML config table partial."""
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
    tasks = task_manager.list(limit=100)
    return render(
        request,
        "ciudades_del_mundo/partials/config_tasks_table.html",
        _task_table_context(tasks, page_size=_page_size(request, default=10), compact=True),
    )


def config_new(request):
    """Create a new SQL-backed scraping config."""
    default_content = (
        'name = "Nuevo país"\n'
        "LEGAL_SUBDIVISION = 2\n\n"
        "[[pages]]\n"
        'source = "admin"\n'
        'path = ["admin"]\n'
        "lowest_level = 0\n\n"
        "# [[cities]]\n"
        '# city = "Ciudad Unificada"\n'
        '# id = "city-code"\n'
        "# level = 3\n"
        '# type = "City"\n'
        '# district_types = ["District"]\n'
        '# from = { 2 = ["Provincia"] }\n'
        "# keep_communes = false\n"
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
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuración '%(slug)s' creada.") % {"slug": slug})
            return redirect("ciudades_del_mundo:config_edit", slug=slug)
    return render(request, "ciudades_del_mundo/config_form.html", _config_form_context("new", slug, content))


def config_edit(request, slug):
    """Edit a SQL-backed scraping config and restart active scrape work if needed."""
    slug = _normalize_config_slug(slug)
    config_record = _config_record(slug)
    if config_record is None:
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    active_scrape = task_manager.latest_for_key(f"scrape:{slug}")
    content = config_record.content

    if request.method == "POST":
        try:
            content = _config_content_from_request(request, slug, content, existing=True)
            _validate_config_text(slug, content)
            upsert_scraping_config(slug, content, source_path=config_record.source_path)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuración '%(slug)s' guardada.") % {"slug": slug})
            if active_scrape and active_scrape.is_active:
                replacement = task_manager.start(
                    key=f"scrape:{slug}",
                    label=_("Popular datos: %(slug)s") % {"slug": slug},
                    args=active_scrape.args,
                )
                messages.info(
                    request,
                    _("Había un scraping activo para esta configuración; se canceló y se lanzó otra tarea."),
                )
                return redirect("ciudades_del_mundo:task_detail", task_id=replacement.id)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)

    return render(request, "ciudades_del_mundo/config_form.html", _config_form_context("edit", slug, content, active_scrape))


def start_config_task(request, slug, action):
    """Start validation or scraping for one SQL/TOML config."""
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

    if action == "validate":
        key = f"validate-config:{slug}"
        label = _("Validar configuración: %(slug)s") % {"slug": slug}
        args = ["validate_subdivision_configs", slug]
    elif action == "scrape":
        key = f"scrape:{slug}"
        label = _("Popular datos: %(slug)s") % {"slug": slug}
        args = ["scrape_subdivisions", slug]
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


def task_status(request, task_id):
    """Return current state for a web-launched task."""
    task = task_manager.get(task_id)
    if not task:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": _("No existe la tarea solicitada.")}, status=404)
        raise Http404(_("No existe la tarea solicitada."))
    return JsonResponse(
        {
            "id": task.id,
            "label": task.label,
            "status": task.status,
            "is_active": task.is_active,
            "returncode": task.returncode,
            "output": task_manager.output_text(task)[-4000:],
            "detail_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}),
        }
    )


def config_summary(request, slug):
    """Return an updated row summary for one config after an async task."""
    slug = _normalize_config_slug(slug)
    row = _config_summary_for_slug(slug)
    task = row.get("active_task")
    validate_task = row.get("validate_task")
    scrape_task = row.get("scrape_task")
    return JsonResponse(
        {
            "slug": row["slug"],
            "name": row["name"],
            "country_label": row["country_label"],
            "pages": row["pages"],
            "cities": row["cities"],
            "rows": row["rows"],
            "task_status": task.status if task else "",
            "task_is_active": task.is_active if task else False,
            "task_url": reverse("ciudades_del_mundo:task_detail", kwargs={"task_id": task.id}) if task else "",
            "validate_task_status": validate_task.status if validate_task else "",
            "validate_task_is_active": validate_task.is_active if validate_task else False,
            "scrape_task_status": scrape_task.status if scrape_task else "",
            "scrape_task_is_active": scrape_task.is_active if scrape_task else False,
            "error": row.get("error") or "",
        }
    )


def config_source_entities(request, slug):
    """Return populated source entities for the manual city-unification UI."""
    slug = _normalize_config_slug(slug)
    if not _config_exists(slug):
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    entities = _source_entities_for_config(_config_country_code_for_slug(slug))
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
    return JsonResponse({"ok": True, "content": content, "log": "\n".join(log_lines)})


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


def visual_identity_detail(request, kind, filename):
    """Render a local placeholder detail page for a flag or coat image."""
    kind = (kind or "image").lower()
    labels = {
        "flag": _("Bandera"),
        "coat": _("Escudo"),
    }
    safe_filename = str(filename or "").strip()
    if not safe_filename:
        raise Http404(_("No se encontro la imagen solicitada."))
    encoded_filename = quote(safe_filename.replace(" ", "_"), safe="/():,._-")
    context = {
        "kind": kind,
        "kind_label": labels.get(kind, _("Imagen")),
        "filename": safe_filename,
        "image_url": f"https://commons.wikimedia.org/wiki/Special:FilePath/{encoded_filename}?width=1400",
        "commons_url": f"https://commons.wikimedia.org/wiki/File:{encoded_filename}",
    }
    return render(request, "ciudades_del_mundo/visual_identity_detail.html", context)


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
    tasks = task_manager.list(limit=200)
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
    return render(
        request,
        "ciudades_del_mundo/task_detail.html",
        {
            "task": task,
            "task_output": task_manager.output_text(task),
        },
    )


def task_cancel(request, task_id):
    """Cancel an active background task."""
    if request.method != "POST":
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    task = task_manager.cancel(task_id)
    if task:
        messages.info(request, _("Cancelacion solicitada para '%(label)s'.") % {"label": task.label})
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
        return _visible_admin_areas().filter(country_code=country_code, level=0).order_by("name").first()
    return model.objects.filter(country_code=country_code, level=0).order_by("name").first()


def _canonical_country_area(country_code: str) -> AdminArea | None:
    roots = _visible_admin_areas().filter(country_code=country_code, level=0, parent__isnull=True)
    preferred = roots.filter(code=country_code).order_by("name").first()
    if preferred:
        return preferred
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


def _admin_root_population_rows(*, detail_route: str = "ciudades_del_mundo:dashboard_country_detail") -> list[dict]:
    return [
        {
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
        for row in _admin_country_summary_records()
    ]


def _country_detail_payload(country_code: str, selected_level: int | None = None) -> dict:
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
    if selected_level not in [item["value"] for item in available_levels]:
        selected_level = available_levels[0]["value"] if available_levels else None

    first_order = list(_country_top_level_areas(country_code, root).select_related("parent").order_by("name"))
    population_total = country_record["population"]
    area_total = country_record["area_km2"] or 0
    rows = _country_table_rows(country_code, root, selected_level, population_total, area_total)
    subdivision_count = _visible_admin_areas().filter(country_code=country_code).count() - (1 if root else 0)

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
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "table": {
            "rows": rows,
        },
        "first_order": {
            "population_chart": _donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _donut_payload(first_order, "area_km2", area_total),
            "cards": _first_order_cards(country_code, root, first_order, population_total, area_total),
        },
    }


def _admin_area_detail_payload(area: AdminArea) -> dict:
    children = list(
        _visible_admin_areas()
        .filter(parent=area)
        .select_related("parent")
        .order_by("name")
    )
    return {
        "area": _admin_area_identity_payload(area, children),
        "children": _admin_area_child_rows(children, area.pop_latest, area.area_km2),
    }


def _admin_area_identity_payload(area: AdminArea, children: list[AdminArea] | None = None) -> dict:
    country_root = _area_country_root(area)
    return {
        "id": area.id,
        "code": area.code,
        "country_code": area.country_code,
        "name": _display_name(area.name, area.name, country_code=area.country_code),
        "official_name": _display_name(area.name, area.name, country_code=area.country_code),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "parent": _display_name(area.parent.name, area.parent.name, country_code=area.country_code) if area.parent else "",
        "population": int(area.pop_latest or 0) if area.pop_latest is not None else None,
        "area_km2": _number_or_none(area.area_km2),
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "capital": _capital_names_for_area(area),
        "subdivision_count": len(children) if children is not None else _visible_admin_areas().filter(parent=area).count(),
        "type_summary": [],
        "wikidata_query": _area_wikidata_query(area, country_root),
        "wikidata_id": "",
        "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "admin", "area_id": area.id}),
        "detail_url": _admin_area_detail_url(area),
    }


def _admin_area_child_rows(children: list[AdminArea], population_total, area_total) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": _display_name(child.name, child.name, country_code=child.country_code),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "level": child.level,
            "area_km2": _number_or_none(child.area_km2),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "density": _number_or_none(child.density) or _density(child.pop_latest, child.area_km2),
            "population_percent": _ratio_percent(child.pop_latest, population_total),
            "area_percent": _ratio_percent(child.area_km2, area_total),
            "child_count": _visible_admin_areas().filter(parent=child).count(),
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


def _country_available_levels(country_code: str, root: AdminArea | None) -> list[dict]:
    levels = (
        _visible_admin_areas().filter(country_code=country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    rows = []
    for level in levels:
        if root and level == root.level:
            continue
        entity_types = _level_entity_types(country_code, level)
        rows.append(
            {
                "value": level,
                "label": _("Nivel %(level)s") % {"level": _display_level(level, root)},
                "entity_type": ", ".join(entity_types[:3]),
            }
        )
    return rows


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
) -> list[dict]:
    if selected_level is None:
        return []
    rows = (
        _visible_admin_areas().filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name")
    )
    if root:
        rows = rows.exclude(id=root.id)
    return [
        {
            "name": _display_name(row.name, row.name, country_code=country_code),
            "area_km2": _number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _number_or_none(row.density) or _density(row.pop_latest, row.area_km2),
            "population_percent": _ratio_percent(row.pop_latest, population_total),
            "area_percent": _ratio_percent(row.area_km2, area_total),
            "parent": _display_name(row.parent.name, row.parent.name, country_code=country_code) if row.parent else "",
            "entity_type": _entity_type_label(row.entity_type, country_code=country_code),
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
        _display_name(capital.name, capital.name, country_code=area.country_code)
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
                "label": _display_name(area.name, area.name, country_code=area.country_code),
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
        "name": _display_name(area.name, area.name, country_code=area.country_code),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "population": population,
        "area_km2": area_km2,
        "density": _number_or_none(area.density) or _density(area.pop_latest, area.area_km2),
        "population_percent": _ratio_percent(population, population_total),
        "area_percent": _ratio_percent(area_km2, area_total),
        "child_count": _visible_admin_areas().filter(parent=area).count(),
        "detail_url": _admin_area_detail_url(area),
        "children": _second_order_share_rows(area) if include_children else [],
    }


def _second_order_share_rows(area: AdminArea) -> list[dict]:
    children = (
        area.children.exclude(city_merge_status=AdminArea.CityMergeStatus.SOURCE)
        .exclude(city_merge_status=HIDDEN_CITY_MERGE_STATUS)
        .select_related("parent")
        .order_by("name")
    )
    return [
        {
            "name": _display_name(child.name, child.name, country_code=child.country_code),
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
    if str(country_code or "").lower() == "spain":
        return spain_entity_type(value, language)
    return _(value)


def _wants_json(request) -> bool:
    accept = request.headers.get("accept", "")
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in accept


def _is_ajax(request) -> bool:
    return _wants_json(request)


def _config_form_context(mode: str, slug: str, content: str, active_task=None) -> dict:
    parsed = _parse_config_editor_data(slug, content)
    selected_ids = parsed["selected_ids"]
    city_level = str(parsed.get("city", {}).get("level") or "")
    country_code = str(parsed.get("country_code") or slug or "")
    source_level_choices = _source_level_filter_options_for_country(country_code) if mode == "edit" else []
    initial_source_level = source_level_choices[0]["value"] if source_level_choices else None
    initial_source_entities = (
        _source_entities_for_config(country_code, level=initial_source_level)
        if initial_source_level
        else []
    )
    return {
        "mode": mode,
        "slug": slug,
        "content": content,
        "active_task": active_task,
        "manual": parsed,
        "manual_selected_ids_json": json.dumps(selected_ids, ensure_ascii=False),
        # Las entidades disponibles se cargan por endpoint para evitar insertar
        # un JSON enorme en /configs/{country}/ y para poder reconstruir filtros
        # traducidos sin romper la pestaña Manual.
        "source_entities_json": json.dumps(initial_source_entities, ensure_ascii=False),
        "source_level_choices": source_level_choices,
        "scrape_types": _scrape_type_choices(),
        "city_level_choices": [
            {"value": str(level), "selected": str(level) == city_level}
            for level in range(0, 6)
        ],
        "ai_enabled": AI_CONFIG_ENABLED,
        "ai_personal_login_enabled": AI_PERSONAL_LOGIN_ENABLED,
        "ai_provider_choices": _ai_provider_choices(),
    }


def _scrape_type_choices() -> list[tuple[str, str]]:
    return [
        ("admin", _("Admin")),
        ("table", _("Tabla")),
        ("double", _("Doble")),
        ("cities", _("Ciudades")),
        ("infosection", _("Sección informativa")),
    ]


def _ai_provider_choices() -> list[tuple[str, str]]:
    labels = {
        "chatgpt": "ChatGPT",
        "openai": "OpenAI",
        "claude": "Claude",
        "gemini": "Gemini",
    }
    return [(provider, labels.get(provider, provider.title())) for provider in AI_PROVIDER_LOGIN_URLS.keys()]


def _parse_config_editor_data(slug: str, content: str) -> dict:
    data = {}
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    pages = []
    for page in data.get("pages") or []:
        raw_paths = page.get("path")
        if raw_paths is None:
            raw_paths = [""]
        elif not isinstance(raw_paths, list):
            raw_paths = [raw_paths]
        for raw_path in raw_paths:
            pages.append(
                {
                    "path": str(raw_path or ""),
                    "source": str(page.get("source", page.get("html_format", "admin"))),
                    "lowest_level": int(page.get("lowest_level", page.get("level", 0)) or 0),
                }
            )
    cities = data.get("cities") or []
    first_city = cities[0] if cities else {}
    country_code = str(data.get("country_code") or slug or "")
    selected_ids = _selected_ids_from_city_config(country_code, first_city)
    return {
        "name": str(data.get("name") or ""),
        "country_code": country_code,
        "legal_subdivision": "" if data.get("LEGAL_SUBDIVISION") is None else str(data.get("LEGAL_SUBDIVISION")),
        "pages": pages,
        "city": {
            "name": str(first_city.get("city") or ""),
            "id": str(first_city.get("id") or ""),
            "level": "" if first_city.get("level") is None else str(first_city.get("level")),
            "type": str(first_city.get("type") or "City"),
        },
        "selected_ids": selected_ids,
    }


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
                "name": _display_name(area.name, area.name, country_code=area.country_code),
                "raw_name": area.name,
                "level": area.level,
                "entity_type": entity_type,
                "raw_entity_type": area.entity_type,
                "entity_type_filter": entity_type_filter,
                "parent": _display_name(parent.name, parent.name, country_code=area.country_code) if parent else "",
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
    editor_mode = request.POST.get("editor_mode") or "file"
    if editor_mode == "manual":
        return _render_config_from_manual_post(slug, request.POST)
    return request.POST.get("content", current_content)


def _render_config_from_manual_post(slug: str, post) -> str:
    name = (post.get("manual_name") or "").strip()
    country_code = (post.get("manual_country_code") or slug).strip() or slug
    legal_subdivision = (post.get("manual_legal_subdivision") or "").strip()
    page_levels = post.getlist("page_level")
    page_urls = post.getlist("page_url")
    page_sources = post.getlist("page_source")
    pages = []
    for level, url, source in zip(page_levels, page_urls, page_sources, strict=False):
        url = str(url or "").strip()
        source = str(source or "").strip() or "admin"
        if not url:
            continue
        try:
            level_int = int(level or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("El nivel de scrapeo debe ser numérico.")) from exc
        pages.append({"source": source, "path": url, "lowest_level": level_int})
    if not pages:
        raise ValueError(_("Debes indicar al menos una ruta de scrapeo."))

    lines = []
    if name:
        lines.append(f"name = {_toml_string(name)}")
    if country_code and country_code != slug:
        lines.append(f"country_code = {_toml_string(country_code)}")
    if legal_subdivision:
        try:
            lines.append(f"LEGAL_SUBDIVISION = {int(legal_subdivision)}")
        except (TypeError, ValueError) as exc:
            raise ValueError(_("La subdivisión legal debe ser numérica.")) from exc
    lines.append("")

    for page in pages:
        lines.extend(
            [
                "[[pages]]",
                f"source = {_toml_string(page['source'])}",
                f"path = {_toml_string(page['path'])}",
                f"lowest_level = {page['lowest_level']}",
                "",
            ]
        )

    selected_ids = _json_list(post.get("selected_city_entities"))
    city_name = (post.get("city_name") or "").strip()
    if selected_ids and city_name:
        selected = list(
            _visible_admin_areas()
            .filter(country_code=country_code, id__in=selected_ids)
            .select_related("parent")
            .order_by("level", "name")
        )
        if selected:
            city_id = (post.get("city_id") or _slugify_code(city_name)).strip()
            city_type = (post.get("city_type") or "City").strip()
            try:
                city_level = int(post.get("city_level") or max(min(area.level for area in selected) - 1, 0))
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El nivel de ciudad unificada debe ser numérico.")) from exc
            parent_from = _parent_from_for_selected(selected)
            district_types = sorted({area.entity_type for area in selected if area.entity_type})
            communes = [area.name for area in selected]
            lines.extend(
                [
                    "[[cities]]",
                    f"city = {_toml_string(city_name)}",
                    f"id = {_toml_string(city_id)}",
                    f"level = {city_level}",
                    f"type = {_toml_string(city_type)}",
                    f"district_types = {_toml_array(district_types)}",
                    f"from = {_toml_inline_table(parent_from)}",
                    f"communes = {_toml_array(communes)}",
                    "keep_communes = false",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


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
    url = f"https://www.citypopulation.de/en/{slug}/"
    log = [_("Buscando rutas en %(url)s") % {"url": url}]
    try:
        with urlopen(url, timeout=20) as response:  # noqa: S310 - user-triggered local scraping helper.
            html = response.read().decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        raise ValueError(_("No se pudo leer CityPopulation: %(error)s") % {"error": exc}) from exc

    paths = _citypopulation_paths_from_html(slug, html, base_url=url)
    if not paths:
        raise ValueError(_("No se encontraron rutas útiles para %(slug)s.") % {"slug": slug})
    log.append(_("Rutas detectadas: %(count)s") % {"count": len(paths)})
    grouped: dict[tuple[str, int], list[str]] = {}
    for path in paths:
        source, level = _guess_scrape_source(path)
        grouped.setdefault((source, level), []).append(path)

    lines = [f"name = {_toml_string(_display_name('', slug, country_code=slug))}", "", "LEGAL_SUBDIVISION = 2", ""]
    for (source, level), values in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        lines.extend(
            [
                "[[pages]]",
                f"source = {_toml_string(source)}",
                f"path = {_toml_array(values)}",
                f"lowest_level = {level}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n", log


def _citypopulation_paths_from_html(slug: str, html: str, *, base_url: str) -> list[str]:
    raw_links = re.findall(r"href=[\"']([^\"'#?]+)", html, flags=re.IGNORECASE)
    seen = set()
    paths = []
    for href in raw_links:
        absolute = urljoin(base_url, href)
        marker = f"/en/{slug}/"
        if marker not in absolute:
            continue
        path = absolute.split(marker, 1)[1].strip("/")
        if not path or path.startswith(("maps", "search", "help")):
            continue
        if path.endswith(('.html', '.htm')):
            continue
        if path not in seen:
            seen.add(path)
            paths.append(path)
    if "admin" not in seen:
        paths.insert(0, "admin")
    return paths[:120]


def _guess_scrape_source(path: str) -> tuple[str, int]:
    value = path.strip("/").lower()
    if value in {"", "admin"} or value.endswith("/admin"):
        return "admin", 0
    if "localit" in value:
        return "double", 3
    if "cities" in value or "city" in value:
        return "cities", 3
    return "table", 1


def _config_summaries(limit: int | None = None) -> list[dict]:
    row_counts = _config_row_counts()
    if scraping_config_table_exists() and ScrapingConfig.objects.exists():
        records = ScrapingConfig.objects.order_by("slug")
        if limit:
            records = records[:limit]
        return [_config_summary_from_record(record, row_counts=row_counts) for record in records]

    repository = PythonScrapingConfigRepository()
    rows = []
    slugs = repository.list_slugs()
    if limit:
        slugs = slugs[:limit]
    for slug in slugs:
        rows.append(_config_summary_for_slug(slug, repository=repository, row_counts=row_counts))
    return rows


def _filtered_config_summaries(request) -> list[dict]:
    rows = _config_summaries()
    query = str(request.GET.get("q") or "").strip().casefold()
    if query:
        rows = [
            row
            for row in rows
            if query in " ".join(
                [
                    str(row.get("slug") or ""),
                    str(row.get("name") or ""),
                    str(row.get("country_label") or ""),
                    str(row.get("country_code") or ""),
                ]
            ).casefold()
        ]
    return rows


def _config_summary_for_slug(
    slug: str,
    *,
    repository: PythonScrapingConfigRepository | None = None,
    row_counts: dict[str, int] | None = None,
) -> dict:
    row_counts = row_counts or _config_row_counts()
    record = _config_record(slug)
    if record:
        return _config_summary_from_record(record, row_counts=row_counts)

    repository = repository or PythonScrapingConfigRepository()
    error = None
    try:
        config = repository.get(slug)
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI.
        config = None
        error = str(exc)
    country_code = config.country_code if config else slug
    raw_name = config.name if config else slug
    validate_task = task_manager.latest_for_key(f"validate-config:{slug}")
    scrape_task = task_manager.latest_for_key(f"scrape:{slug}")
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _display_name(raw_name, country_code, country_code=country_code),
        "pages": len(config.pages) if config else 0,
        "cities": len(config.cities) if config else 0,
        "rows": row_counts.get(country_code, 0),
        "active_task": _latest_config_task(slug, validate_task=validate_task, scrape_task=scrape_task),
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "error": error,
    }


def _config_summary_from_record(record: ScrapingConfig, *, row_counts: dict[str, int] | None = None) -> dict:
    row_counts = row_counts or _config_row_counts()
    slug = record.slug
    country_code = record.country_code or slug
    raw_name = record.name or slug
    validate_task = task_manager.latest_for_key(f"validate-config:{slug}")
    scrape_task = task_manager.latest_for_key(f"scrape:{slug}")
    return {
        "slug": slug,
        "name": raw_name,
        "country_code": country_code,
        "country_label": _display_name(raw_name, country_code, country_code=country_code),
        "pages": record.pages_count,
        "cities": record.cities_count,
        "rows": row_counts.get(country_code, 0),
        "active_task": _latest_config_task(slug, validate_task=validate_task, scrape_task=scrape_task),
        "validate_task": validate_task,
        "scrape_task": scrape_task,
        "error": record.validation_error if not record.is_valid else "",
    }


def _config_row_counts() -> dict[str, int]:
    return {
        row["country_code"]: row["total"]
        for row in _visible_admin_areas().values("country_code").annotate(total=Count("id"))
    }


def _latest_config_task(slug: str, *, validate_task=None, scrape_task=None):
    tasks = [
        validate_task if validate_task is not None else task_manager.latest_for_key(f"validate-config:{slug}"),
        scrape_task if scrape_task is not None else task_manager.latest_for_key(f"scrape:{slug}"),
    ]
    tasks = [task for task in tasks if task]
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


def _config_record(slug: str) -> ScrapingConfig | None:
    if scraping_config_table_exists():
        record = ScrapingConfig.objects.filter(slug=slug).first()
        if record:
            return record
    path = SUBDIVISIONS_ROOT / f"{slug}.toml"
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata = parse_config_metadata(slug, content)
    return ScrapingConfig(
        slug=slug,
        country_code=metadata.country_code,
        name=metadata.name,
        content=content,
        content_hash="",
        source_path=str(path),
        pages_count=metadata.pages_count,
        cities_count=metadata.cities_count,
        has_representation=metadata.has_representation,
        is_valid=metadata.is_valid,
        validation_error=metadata.validation_error,
    )


def _web_task_table_exists() -> bool:
    """Return whether the persisted task table is migrated and usable."""
    try:
        from ciudades_del_mundo.models import WebTask

        return WebTask._meta.db_table in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def _ensure_config_available_for_task(slug: str) -> tuple[bool, str]:
    """Ensure a config can be used before launching validate/scrape.

    The UI is SQL-first, but this deliberately repairs interrupted bootstrap runs
    and falls back to bundled TOML files instead of returning an HTML 404 page to
    AJAX callers.
    """
    path = SUBDIVISIONS_ROOT / f"{slug}.toml"
    if scraping_config_table_exists():
        try:
            ensure_initial_scraping_configs(force=False)
            if ScrapingConfig.objects.filter(slug=slug).exists():
                return True, ""
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                upsert_scraping_config(slug, content, source_path=str(path.relative_to(settings.BASE_DIR)))
                return True, ""
        except (OperationalError, ProgrammingError) as exc:
            return False, _(
                "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
            ) % {"error": exc}
        except OSError as exc:
            return False, _("No se pudo leer el TOML de '%(slug)s': %(error)s") % {"slug": slug, "error": exc}
        except Exception as exc:  # noqa: BLE001 - turn backend failures into JSON-safe messages.
            return False, str(exc)

    if _config_record(slug) is not None:
        return True, ""
    return False, _("No existe la configuracion '%(slug)s' ni en SQL ni como TOML.") % {"slug": slug}


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
        pages = parse_pages(data.get("pages"), slug=slug)
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
