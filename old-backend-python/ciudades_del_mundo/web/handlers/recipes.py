"""HTTP endpoints for recipes."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.web import http as http

import json
from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.queries import tasks as _queries_tasks


@missing_data_as_404
def recipe_list(request):
    """List available derived hierarchy recipes."""
    context = {
        "recipes": _forms_recipes._recipe_summaries(),
        "recent_tasks": task_manager.list(limit=12),
    }
    return render(request, "ciudades_del_mundo/recipe_list.html", context)


@missing_data_as_404
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
            slug = _forms_recipes._normalize_recipe_slug(form["slug"])
            path = _forms_recipes._recipe_path(slug, group="new", must_exist=False)
            if path.exists():
                raise ValueError(_("Ya existe una receta nueva para '%(slug)s'.") % {"slug": slug})
            content = _forms_recipes._render_recipe_from_form(form)
            _forms_recipes._validate_recipe_text(content, filename=str(path))
            path.write_text(content, encoding="utf-8")
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Receta '%(slug)s' creada.") % {"slug": slug})
            return redirect("ciudades_del_mundo:recipe_edit", slug=slug)

    return render(request, "ciudades_del_mundo/recipe_new.html", {"form": form})


@missing_data_as_404
def recipe_edit(request, slug):
    """Edit a Python recipe in `new_subdivisions`."""
    slug = _forms_recipes._normalize_recipe_slug(slug)
    path = _forms_recipes._recipe_path(slug, group="new")
    active_build = task_manager.latest_for_key(f"build:{slug}")

    if request.method == "POST":
        content = request.POST.get("content", "")
        try:
            _forms_recipes._validate_recipe_text(content, filename=str(path))
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


@missing_data_as_404
def start_recipe_task(request, slug, action):
    """Start build/export tasks for a derived recipe."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:recipe_list")

    slug = _forms_recipes._normalize_recipe_slug(slug)
    if action == "build":
        _forms_recipes._any_recipe_path(slug)
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
        return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)
