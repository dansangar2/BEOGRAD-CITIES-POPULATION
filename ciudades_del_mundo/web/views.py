"""Operational web views for managing local geography data."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from pprint import pformat
import re
import tomllib

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse

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


def dashboard(request):
    """Render project summary metrics and current task state."""
    countries = list(
        AdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    derived = list(
        NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"), seats=Sum("representatives"))
        .order_by("country_code")
    )
    context = {
        "admin_area_count": AdminArea.objects.count(),
        "nuevo_area_count": NuevoAdminArea.objects.count(),
        "seat_count": NuevoAdminArea.objects.exclude(representatives__isnull=True).count(),
        "countries": countries,
        "configs": _config_summaries(limit=8),
        "recent_tasks": task_manager.list(limit=8),
        "top_country_bars": _bar_rows(countries, "country_code", "population", limit=10),
        "derived_bars": _bar_rows(derived, "country_code", "population", limit=10),
    }
    return render(request, "ciudades_del_mundo/dashboard.html", context)


def admin_area_list(request):
    """Render a searchable, paginated list of persisted `AdminArea` rows."""
    areas = AdminArea.objects.select_related("parent").order_by("country_code", "level", "name")
    country = request.GET.get("country")
    level = request.GET.get("level")
    q = request.GET.get("q")

    if country:
        areas = areas.filter(country_code=country)
    if level not in (None, ""):
        areas = areas.filter(level=level)
    if q:
        areas = areas.filter(name__icontains=q)

    paginator = Paginator(areas, 50)
    context = {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "countries": AdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code"),
        "selected_country": country or "",
        "selected_level": level or "",
        "query": q or "",
    }
    return render(request, "ciudades_del_mundo/admin_area_list.html", context)


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
                raise ValueError(f"Ya existe una configuracion para '{slug}'.")
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Configuracion '{slug}' creada.")
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
            messages.success(request, f"Configuracion '{slug}' guardada.")
            if active_scrape and active_scrape.is_active:
                replacement = task_manager.start(
                    key=f"scrape:{slug}",
                    label=f"Popular datos: {slug}",
                    args=active_scrape.args,
                )
                messages.info(
                    request,
                    "Habia un scraping activo para este fichero; se cancelo y se lanzo otra tarea.",
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
        label = f"Validar configuracion: {slug}"
        args = ["validate_subdivision_configs", slug]
    elif action == "list-pages":
        key = f"list-pages:{slug}"
        label = f"Ver URLs de scraping: {slug}"
        args = ["scrape_subdivisions", "--list-pages", slug]
    elif action == "scrape":
        key = f"scrape:{slug}"
        label = f"Popular datos: {slug}"
        args = ["scrape_subdivisions", slug]
    else:
        raise Http404("Accion de configuracion no soportada.")

    task = task_manager.start(key=key, label=label, args=args)
    messages.info(request, f"Tarea lanzada: {label}.")
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
                raise ValueError(f"Ya existe una receta nueva para '{slug}'.")
            content = _render_recipe_from_form(form)
            _validate_recipe_text(content, filename=str(path))
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Receta '{slug}' creada.")
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
            messages.success(request, f"Receta '{slug}' guardada.")
            if active_build and active_build.is_active:
                replacement = task_manager.start(
                    key=f"build:{slug}",
                    label=f"Crear subdivisiones: {slug}",
                    args=active_build.args,
                )
                messages.info(
                    request,
                    "Habia una construccion activa para esta receta; se cancelo y se relanzo.",
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
                messages.error(request, "El ano de poblacion debe ser numerico.")
                return redirect("ciudades_del_mundo:recipe_list")
            args.extend(["--population-year", population_year])
        key = f"build:{slug}"
        label = f"Crear subdivisiones: {slug}"
    elif action == "export-csv":
        args = ["export_nuevoadmin_csv", "--country-id", slug]
        key = f"export-csv:{slug}"
        label = f"Exportar CSV: {slug}"
    elif action == "export-excel":
        args = ["export_nuevoadmin_excel", "--country-id", slug]
        key = f"export-excel:{slug}"
        label = f"Exportar Excel: {slug}"
    else:
        raise Http404("Accion de receta no soportada.")

    task = task_manager.start(key=key, label=label, args=args)
    messages.info(request, f"Tarea lanzada: {label}.")
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
    root_rows = [{"root": root, "stats": stats.get(root.country_code, {})} for root in roots]
    context = {
        "root_rows": root_rows,
    }
    return render(request, "ciudades_del_mundo/nuevo_area_list.html", context)


def nuevo_area_detail(request, country_id):
    """Render a comparable table for one `NuevoAdminArea` country tree."""
    country_id = _normalize_recipe_slug(country_id)
    try:
        root = NuevoAdminArea.objects.get(id=country_id)
    except NuevoAdminArea.DoesNotExist as exc:
        raise Http404(f"No existe NuevoAdminArea '{country_id}'.") from exc

    areas = (
        NuevoAdminArea.objects.filter(country_code=root.country_code)
        .select_related("parent", "most_populate_city", "depends_on")
        .prefetch_related("capitals")
        .order_by("level", "parent__name", "name")
    )
    level = request.GET.get("level")
    q = request.GET.get("q")
    if level not in (None, ""):
        areas = areas.filter(level=level)
    if q:
        areas = areas.filter(name__icontains=q)

    paginator = Paginator(areas, 75)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [_comparison_row(area, root) for area in page_obj.object_list]
    levels = (
        NuevoAdminArea.objects.filter(country_code=root.country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    context = {
        "root": root,
        "rows": rows,
        "page_obj": page_obj,
        "levels": levels,
        "selected_level": level or "",
        "query": q or "",
    }
    return render(request, "ciudades_del_mundo/nuevo_area_detail.html", context)


def stats_view(request):
    """Render aggregate statistical charts."""
    countries = list(
        AdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("-population")
    )
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
    context = {
        "country_population_bars": _bar_rows(countries, "country_code", "population", limit=15),
        "country_area_bars": _bar_rows(countries, "country_code", "total", limit=15),
        "admin_level_bars": _bar_rows(admin_levels, "level", "total", limit=8, prefix="L"),
        "nuevo_level_bars": _bar_rows(nuevo_levels, "level", "total", limit=8, prefix="L"),
        "merge_status_bars": _merge_status_rows(merge_statuses),
    }
    return render(request, "ciudades_del_mundo/stats.html", context)


def data_delete(request):
    """Delete scraped or derived data after explicit confirmation."""
    if request.method == "POST":
        kind = request.POST.get("kind")
        target = (request.POST.get("target") or "").strip()
        confirmed = request.POST.get("confirm") == "on"

        if not confirmed:
            messages.error(request, "Marca la confirmacion antes de borrar datos.")
        elif not target:
            messages.error(request, "Selecciona un objetivo para borrar.")
        elif kind == "admin-country":
            deleted, _ = AdminArea.objects.filter(country_code=target).delete()
            messages.success(request, f"Borradas {deleted} filas AdminArea para '{target}'.")
        elif kind == "nuevo-country":
            deleted, _ = NuevoAdminArea.objects.filter(country_code=target).delete()
            messages.success(request, f"Borradas {deleted} filas NuevoAdminArea para '{target}'.")
        else:
            messages.error(request, "Tipo de borrado no soportado.")
        return redirect("ciudades_del_mundo:data_delete")

    context = {
        "admin_countries": AdminArea.objects.values_list("country_code", flat=True)
        .distinct()
        .order_by("country_code"),
        "nuevo_countries": NuevoAdminArea.objects.values_list("country_code", flat=True)
        .distinct()
        .order_by("country_code"),
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
        raise Http404("Tarea no encontrada.")
    return render(request, "ciudades_del_mundo/task_detail.html", {"task": task})


def task_cancel(request, task_id):
    """Cancel an active background task."""
    if request.method != "POST":
        return redirect("ciudades_del_mundo:task_detail", task_id=task_id)
    task = task_manager.cancel(task_id)
    if task:
        messages.info(request, f"Cancelacion solicitada para '{task.label}'.")
    return redirect("ciudades_del_mundo:task_detail", task_id=task_id)


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
        raise ValueError("El slug de configuracion solo puede usar letras, numeros, guion y guion bajo.")
    return slug


def _normalize_recipe_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not RECIPE_SLUG_RE.fullmatch(slug):
        raise ValueError("El slug de receta debe ser un identificador Python en minusculas.")
    return slug


def _config_path(slug: str, *, must_exist: bool = True) -> Path:
    path = SUBDIVISIONS_ROOT / f"{slug}.toml"
    if must_exist and not path.is_file():
        raise Http404(f"No existe la configuracion '{slug}'.")
    return path


def _recipe_path(slug: str, *, group: str, must_exist: bool = True) -> Path:
    root = NEW_RECIPES_ROOT if group == "new" else HISTORICAL_RECIPES_ROOT
    path = root / f"{slug}.py"
    if must_exist and not path.is_file():
        raise Http404(f"No existe la receta '{slug}'.")
    return path


def _any_recipe_path(slug: str) -> Path:
    for group in ("new", "historical"):
        path = _recipe_path(slug, group=group, must_exist=False)
        if path.is_file():
            return path
    raise Http404(f"No existe la receta '{slug}'.")


def _validate_config_text(slug: str, content: str) -> None:
    try:
        data = tomllib.loads(content)
        pages = parse_pages(data.get("pages"), slug=slug)
        if not pages:
            raise ValueError("La configuracion debe definir al menos una pagina.")
        RepresentationConfig.from_mapping(data.get("representation"))
        parse_cities(data.get("cities"))
        parse_entity_merges(data.get("entity_merges", data.get("merge_entities")))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"TOML invalido: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Configuracion invalida: {exc}") from exc


def _render_recipe_from_form(form: dict) -> str:
    try:
        divisions = json.loads(form["divisions_json"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"DIVISIONS no es JSON valido: {exc}") from exc
    if not isinstance(divisions, (list, dict)):
        raise ValueError("DIVISIONS debe ser una lista o un objeto JSON.")
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
        raise ValueError(f"Python invalido: {exc}") from exc

    assignment_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignment_names.add(target.id)
    if "DIVISIONS" not in assignment_names:
        raise ValueError("La receta debe definir DIVISIONS.")


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
