"""Operational web views for managing local geography data."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from pprint import pformat
import re
import tomllib
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
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
from ciudades_del_mundo.models import AdminArea, NuevoAdminArea

from .tasks import task_manager


CONFIG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RECIPE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
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
        AdminArea.objects.values("country_code")
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
        "admin_area_count": AdminArea.objects.count(),
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


def api_derived_summary(request):
    """Return derived-country population rows for API-driven views."""
    return JsonResponse(_derived_summary_payload())


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
    if not AdminArea.objects.filter(country_code=country_code).exists():
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
            AdminArea.objects.exclude(entity_type__isnull=True)
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
    """List TOML scraping configs with operational actions."""
    context = {
        "configs": _config_summaries(),
        "recent_tasks": task_manager.list(limit=12),
    }
    return render(request, "ciudades_del_mundo/config_list.html", context)


def config_new(request):
    """Create a new TOML scraping config."""
    default_content = (
        'name = "Nuevo pais"\n'
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
    if request.method == "POST":
        slug = _normalize_config_slug(request.POST.get("slug", ""))
        content = request.POST.get("content", "")
        try:
            _validate_config_text(slug, content)
            path = _config_path(slug, must_exist=False)
            if path.exists():
                raise ValueError(_("Ya existe una configuracion para '%(slug)s'.") % {"slug": slug})
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' creada.") % {"slug": slug})
            return redirect("ciudades_del_mundo:config_edit", slug=slug)
    else:
        slug = ""
        content = default_content

    return render(
        request,
        "ciudades_del_mundo/config_form.html",
        {
            "mode": "new",
            "slug": slug,
            "content": content,
        },
    )


def config_edit(request, slug):
    """Edit a TOML scraping config and restart active scrape work if needed."""
    slug = _normalize_config_slug(slug)
    path = _config_path(slug)
    active_scrape = task_manager.latest_for_key(f"scrape:{slug}")

    if request.method == "POST":
        content = request.POST.get("content", "")
        try:
            _validate_config_text(slug, content)
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' guardada.") % {"slug": slug})
            if active_scrape and active_scrape.is_active:
                replacement = task_manager.start(
                    key=f"scrape:{slug}",
                    label=_("Popular datos: %(slug)s") % {"slug": slug},
                    args=active_scrape.args,
                )
                messages.info(
                    request,
                    _("Habia un scraping activo para este fichero; se cancelo y se lanzo otra tarea."),
                )
                return redirect("ciudades_del_mundo:task_detail", task_id=replacement.id)
            return redirect("ciudades_del_mundo:config_edit", slug=slug)

    return render(
        request,
        "ciudades_del_mundo/config_form.html",
        {
            "mode": "edit",
            "slug": slug,
            "content": path.read_text(encoding="utf-8"),
            "active_task": active_scrape,
        },
    )


def start_config_task(request, slug, action):
    """Start validation, URL listing or scraping for one TOML config."""
    if request.method != "POST":
        return HttpResponseRedirect(reverse("ciudades_del_mundo:config_list"))

    slug = _normalize_config_slug(slug)
    _config_path(slug)

    if action == "validate":
        key = f"validate-config:{slug}"
        label = _("Validar configuracion: %(slug)s") % {"slug": slug}
        args = ["validate_subdivision_configs", slug]
    elif action == "list-pages":
        key = f"list-pages:{slug}"
        label = _("Ver URLs de scraping: %(slug)s") % {"slug": slug}
        args = ["scrape_subdivisions", "--list-pages", slug]
    elif action == "scrape":
        key = f"scrape:{slug}"
        label = _("Popular datos: %(slug)s") % {"slug": slug}
        args = ["scrape_subdivisions", slug]
    else:
        raise Http404(_("Accion de configuracion no soportada."))

    task = task_manager.start(key=key, label=label, args=args)
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


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
        AdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("-population")
    )
    _apply_country_labels(countries, _admin_country_label_map())
    admin_levels = list(
        AdminArea.objects.values("level").annotate(total=Count("id")).order_by("level")
    )
    nuevo_levels = list(
        NuevoAdminArea.objects.values("level").annotate(total=Count("id")).order_by("level")
    )
    merge_statuses = list(
        AdminArea.objects.values("city_merge_status")
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
        {"tasks": task_manager.list(limit=50)},
    )


def task_detail(request, task_id):
    """Render one background task, auto-refreshing while active."""
    task = task_manager.get(task_id)
    if not task:
        raise Http404(_("Tarea no encontrada."))
    return render(request, "ciudades_del_mundo/task_detail.html", {"task": task})


def task_cancel(request, task_id):
    """Cancel an active background task."""
    if request.method != "POST":
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    task = task_manager.cancel(task_id)
    if task:
        messages.info(request, _("Cancelacion solicitada para '%(label)s'.") % {"label": task.label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task_id)


def _filtered_admin_areas(request):
    areas = AdminArea.objects.select_related("parent", "most_populate_city").order_by("country_code", "level", "name")
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


def _area_capital_display_names(area, language_code: str | None = None) -> list[str]:
    capitals = getattr(area, "capitals", None)
    if not capitals:
        return []

    overrides = getattr(area, "capital_names_by_language", None) or {}
    language = (language_code or "").split("-", 1)[0]
    language_overrides = overrides.get(language, {}) if isinstance(overrides, dict) else {}
    names = []
    for capital in capitals.all():
        names.append(language_overrides.get(capital.id) or capital.name)
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
        add(_("Pais"), country_root.name, country_root.name)
    elif country_code:
        add(_("Pais"), str(country_code), str(country_code))

    parent = getattr(area, "parent", None)
    if parent:
        add(_("Region padre"), parent.name, _area_search_query(parent))

    for capital_name in _area_capital_display_names(area, language_code):
        add(_("Capital registrada"), capital_name, f"{capital_name}, {area.name}")

    most_populated = getattr(area, "most_populate_city", None)
    if most_populated:
        add(_("Ciudad mayor registrada"), most_populated.name, f"{most_populated.name}, {area.name}")

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
    return model.objects.filter(country_code=country_code, level=0).order_by("name").first()


def _canonical_country_area(country_code: str) -> AdminArea | None:
    roots = AdminArea.objects.filter(country_code=country_code, level=0, parent__isnull=True)
    preferred = roots.filter(code=country_code).order_by("name").first()
    if preferred:
        return preferred
    if roots.count() == 1:
        return roots.first()
    return None


def _country_top_level_areas(country_code: str, root: AdminArea | None = None):
    if root:
        children = AdminArea.objects.filter(parent=root)
        first_child_level = children.values_list("level", flat=True).order_by("level").first()
        if first_child_level is not None:
            return children.filter(level=first_child_level).order_by("name")
        next_level = (
            AdminArea.objects.filter(country_code=country_code)
            .exclude(id=root.id)
            .values_list("level", flat=True)
            .order_by("level")
            .first()
        )
        if next_level is not None:
            return AdminArea.objects.filter(country_code=country_code, level=next_level).exclude(id=root.id).order_by("name")
        return AdminArea.objects.none()
    return AdminArea.objects.filter(country_code=country_code, level=0, parent__isnull=True).order_by("name")


def _country_total(country_code: str, field: str, root: AdminArea | None):
    if root:
        return getattr(root, field)
    return _country_top_level_areas(country_code, root).aggregate(total=Sum(field))["total"]


def _admin_country_summary_records() -> list[dict]:
    records = []
    codes = AdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code")
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
                "label": _display_name(source_name, country_code),
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
        {"code": country_code, "label": _display_name("", country_code), "population": 0, "area_km2": None},
    )
    available_levels = _country_available_levels(country_code, root)
    if selected_level not in [item["value"] for item in available_levels]:
        selected_level = available_levels[0]["value"] if available_levels else None

    first_order = list(_country_top_level_areas(country_code, root).select_related("parent").order_by("name"))
    population_total = country_record["population"]
    area_total = country_record["area_km2"] or 0
    rows = _country_table_rows(country_code, root, selected_level, population_total, area_total)
    subdivision_count = AdminArea.objects.filter(country_code=country_code).count() - (1 if root else 0)

    return {
        "country": {
            "code": country_code,
            "name": country_record["label"],
            "official_name": country_record["label"],
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


def _country_available_levels(country_code: str, root: AdminArea | None) -> list[dict]:
    levels = (
        AdminArea.objects.filter(country_code=country_code)
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
        item or _("Sin tipo")
        for item in (
            AdminArea.objects.filter(country_code=country_code, level=level)
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
        AdminArea.objects.filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name")
    )
    if root:
        rows = rows.exclude(id=root.id)
    return [
        {
            "name": _display_name(row.name, row.name),
            "area_km2": _number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _number_or_none(row.density) or _density(row.pop_latest, row.area_km2),
            "population_percent": _ratio_percent(row.pop_latest, population_total),
            "area_percent": _ratio_percent(row.area_km2, area_total),
            "parent": _display_name(row.parent.name, row.parent.name) if row.parent else "",
            "entity_type": _(row.entity_type) if row.entity_type else "",
        }
        for row in rows
    ]


def _country_type_summary(country_code: str, root: AdminArea | None) -> list[dict]:
    rows = AdminArea.objects.filter(country_code=country_code)
    if root:
        rows = rows.exclude(id=root.id)
    grouped = rows.values("level", "entity_type").annotate(total=Count("id")).order_by("level", "entity_type")
    return [
        {
            "level": item["level"],
            "label": _("Nivel %(level)s") % {"level": _display_level(item["level"], root)},
            "entity_type": _(item["entity_type"] or _("Sin tipo")),
            "total": item["total"],
        }
        for item in grouped
    ]


def _capital_names_for_area(area: AdminArea | None) -> list[str]:
    if not area:
        return []
    return [_display_name(capital.name, capital.name) for capital in area.capitals.all().order_by("name")]


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
                "label": _display_name(area.name, area.name),
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
        AdminArea.objects.filter(country_code=country_code, level=child_level).count()
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
        "name": _display_name(area.name, area.name),
        "entity_type": _(area.entity_type) if area.entity_type else "",
        "population": population,
        "area_km2": area_km2,
        "population_percent": _ratio_percent(population, population_total),
        "area_percent": _ratio_percent(area_km2, area_total),
        "children": _second_order_share_rows(area) if include_children else [],
    }


def _second_order_share_rows(area: AdminArea) -> list[dict]:
    children = (
        area.children.exclude(city_merge_status=AdminArea.CityMergeStatus.SOURCE)
        .select_related("parent")
        .order_by("name")
    )
    return [
        {
            "name": _display_name(child.name, child.name),
            "entity_type": _(child.entity_type) if child.entity_type else "",
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
    roots = AdminArea.objects.filter(level=0).values_list("country_code", "name")
    return {country_code: _display_name(name, country_code) for country_code, name in roots}


def _nuevo_country_label_map() -> dict[str, str]:
    roots = NuevoAdminArea.objects.filter(level=0, parent__isnull=True).values_list("country_code", "name")
    return {country_code: _display_name(name, country_code) for country_code, name in roots}


def _admin_country_options() -> list[dict[str, str]]:
    codes = AdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code")
    return _country_options(codes, _admin_country_label_map())


def _nuevo_country_options() -> list[dict[str, str]]:
    codes = NuevoAdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code")
    return _country_options(codes, _nuevo_country_label_map())


def _country_options(codes, label_map: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "code": code,
            "label": label_map.get(code, _display_name("", code)),
        }
        for code in codes
    ]


def _apply_country_labels(rows: list[dict], label_map: dict[str, str]) -> None:
    for row in rows:
        country_code = row.get("country_code")
        row["display_label"] = label_map.get(country_code, _display_name("", country_code))


def _area_country_display_name(area) -> str:
    root = _area_country_root(area)
    if root:
        return _display_name(root.name, area.country_code)
    return _display_name("", getattr(area, "country_code", ""))


def _display_name(name, fallback) -> str:
    fallback_value = str(fallback or "").strip()
    language = (get_language() or "").split("-", 1)[0]
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
    translated = _(value)
    return translated


def _config_summaries(limit: int | None = None) -> list[dict]:
    repository = PythonScrapingConfigRepository()
    row_counts = {
        row["country_code"]: row["total"]
        for row in AdminArea.objects.values("country_code").annotate(total=Count("id"))
    }
    rows = []
    slugs = repository.list_slugs()
    if limit:
        slugs = slugs[:limit]
    for slug in slugs:
        error = None
        try:
            config = repository.get(slug)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI.
            config = None
            error = str(exc)
        country_code = config.country_code if config else slug
        rows.append(
            {
                "slug": slug,
                "name": config.name if config else slug,
                "country_code": country_code,
                "pages": len(config.pages) if config else 0,
                "cities": len(config.cities) if config else 0,
                "rows": row_counts.get(country_code, 0),
                "active_task": task_manager.latest_for_key(f"scrape:{slug}"),
                "error": error,
            }
        )
    return rows


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


def _config_path(slug: str, *, must_exist: bool = True) -> Path:
    path = SUBDIVISIONS_ROOT / f"{slug}.toml"
    if must_exist and not path.is_file():
        raise Http404(_("No existe la configuracion '%(slug)s'.") % {"slug": slug})
    return path


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
