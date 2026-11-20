"""HTTP endpoints for subdivisions."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.services import derived_config_mutations as config_mutations
from ciudades_del_mundo.services.nuevo_admin_builder import ORIGINAL_MUNICIPAL_LEVEL
from ciudades_del_mundo.web import http as http
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from django.db import OperationalError, ProgrammingError
from django.http import JsonResponse

import json
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import DerivedSubdivision
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.derived_config_seeds import bundled_derived_subdivision_paths, import_derived_subdivision_path_records
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web.queries import tasks as _queries_tasks


@missing_data_as_404
def derived_subdivision_import_toml(request):
    """Import or queue SQL refreshes from derived-subdivision TOML seeds."""
    wants_json = http._wants_json(request)
    queue_task = request.POST.get("queue_task") == "1"
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = query_parameters.normalize_country_key(request.POST.get("country_code"))
    paths = bundled_derived_subdivision_paths()
    if country_code:
        paths = _queries_subdivisions._derived_subdivision_seed_paths_for_country(paths, country_code)
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
        queued_tasks = _queries_tasks._queue_derived_seed_import_tasks(
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
            return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
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


@missing_data_as_404
def derived_subdivision_build(request, country_code: str):
    """Queue a build task that materializes derived subdivisions for one country."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    country_code = query_parameters.normalize_country_key(country_code)
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
        return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
    messages.info(request, _("Encolada la poblacion de nuevas divisiones para %(country)s.") % {"country": country_code})
    return redirect("ciudades_del_mundo:task_list")


@missing_data_as_404
def derived_subdivision_list(request):
    """List fictional or historical subdivision definitions."""
    records = list(DerivedSubdivision.objects.order_by("source_country_code", "name", "slug"))
    seed_paths = bundled_derived_subdivision_paths()
    return render(
        request,
        "ciudades_del_mundo/derived_subdivision_list.html",
        {
            "country_cards": _queries_subdivisions._derived_subdivision_country_cards(records, seed_paths=seed_paths),
            "seed_count": len(seed_paths),
        },
    )


@missing_data_as_404
def derived_subdivision_country_detail_data(request, country_code):
    """Return the lazily loaded /subdivisions/ detail tables for one source country."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return JsonResponse({"ok": False, "error": _("No se pudo identificar el pais.")}, status=404)
    return JsonResponse(_queries_subdivisions._derived_subdivision_country_detail_payload(country_code))


@missing_data_as_404
def derived_subdivision_new(request, country_code):
    """Create one fictional or historical subdivision definition."""
    country_code = query_parameters.normalize_country_key(country_code)
    return _derived_subdivision_form(request, country_code=country_code, record=None, requested_slug="new")


@missing_data_as_404
def derived_subdivision_edit(request, country_code, subdivision_slug):
    """Edit one fictional or historical subdivision definition."""
    country_code = query_parameters.normalize_country_key(country_code)
    subdivision_slug = _queries_groups._group_entry_slug(subdivision_slug)
    if request.method == "GET":
        return _derived_subdivision_form(
            request,
            country_code=country_code,
            record=None,
            requested_slug=subdivision_slug,
        )
    record = _queries_subdivisions._derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        raise Http404(_("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug})
    return _derived_subdivision_form(
        request,
        country_code=country_code,
        record=record,
        requested_slug=subdivision_slug,
    )


@missing_data_as_404
def derived_subdivision_delete(request, country_code, subdivision_slug):
    """Delete one SQL derived-subdivision definition from the groups table."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country_code = query_parameters.normalize_country_key(country_code)
    subdivision_slug = _queries_groups._group_entry_slug(subdivision_slug)
    record = _queries_subdivisions._derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        error = _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    record_slug = record.slug
    with transaction.atomic():
        built_country, code_candidates = _queries_subdivisions._derived_subdivision_materialized_code_candidates(record)
        materialized_deleted = config_mutations._delete_materialized_rows_for_derived_subdivision(
            country_code=built_country, code_candidates=code_candidates,
        )
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


@missing_data_as_404
def derived_subdivision_clone(request, country_code, subdivision_slug):
    """Clone one SQL derived-subdivision definition."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country_code = query_parameters.normalize_country_key(country_code)
    subdivision_slug = _queries_groups._group_entry_slug(subdivision_slug)
    record = _queries_subdivisions._derived_subdivision_for_country(country_code, subdivision_slug)
    if not record:
        error = _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=404)
        raise Http404(error)
    try:
        with transaction.atomic():
            clone = config_mutations.create_derived_subdivision_clone(_queries_browser.derived_subdivision_clone_values(record))
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("ciudades_del_mundo:derived_subdivision_list")
    clone_country_code = query_parameters.normalize_country_key(clone.source_country_code)
    clone_entry_slug = _queries_subdivisions._derived_subdivision_entry_slug(clone)
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


@missing_data_as_404
def derived_subdivision_form_data(request, country_code, subdivision_slug):
    """Return dynamic form data for one derived-subdivision editor."""
    country_code = query_parameters.normalize_country_key(country_code)
    subdivision_slug = _queries_groups._group_entry_slug(subdivision_slug)
    if subdivision_slug == "new":
        record = None
        mode = "new"
    else:
        record = _queries_subdivisions._derived_subdivision_for_country(country_code, subdivision_slug)
        mode = "edit"
        if not record:
            return JsonResponse(
                {
                    "ok": False,
                    "error": _("No existe la subdivision '%(slug)s'.") % {"slug": subdivision_slug},
                },
                status=404,
            )
    return JsonResponse(_forms_subdivisions._derived_subdivision_form_payload(country_code=country_code, record=record, mode=mode))


@missing_data_as_404
def derived_subdivision_capital_options(request, country_code, subdivision_slug):
    """Return capital options constrained to the current derived-subdivision source selection."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country_code = query_parameters.normalize_country_key(country_code)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    content = str(body.get("content") or "").strip()
    if not content:
        record = None if subdivision_slug == "new" else _queries_subdivisions._derived_subdivision_for_country(country_code, subdivision_slug)
        content = str(record.content or "") if record else ""
    if str(body.get("source_dirty") or body.get("derived_source_dirty") or "") == "1":
        try:
            if body.get("include_ids_json") is not None or body.get("include_ids") is not None:
                content = _queries_subdivision_sources._derived_subdivision_content_with_visual_source_ids(
                    content,
                    include_ids_json=body.get("include_ids") or body.get("include_ids_json") or "[]",
                    subtract_ids_json=body.get("subtract_ids") or body.get("subtract_ids_json") or "[]",
                    country_code=country_code,
                )
            else:
                content = _queries_subdivision_sources._derived_subdivision_content_with_visual_source_blocks(
                    content,
                    include_blocks_json=body.get("include_blocks") or body.get("include_blocks_json") or "[]",
                    subtract_blocks_json=body.get("subtract_blocks") or body.get("subtract_blocks_json") or "[]",
                    country_code=country_code,
                )
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    selected_values = _queries_subdivision_capitals._derived_subdivision_capital_values_from_payload(body)
    search_term = str(body.get("query") or body.get("term") or "").strip()
    search_key = _queries_subdivision_capitals._derived_subdivision_capital_search_key(search_term)
    if len(search_key) >= 2:
        try:
            options = _queries_subdivision_capitals._derived_subdivision_capital_search_options(
                country_code,
                content=content,
                selected_ids=selected_values,
                search_term=search_term,
            )
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    else:
        options = _queries_subdivision_capitals._derived_subdivision_capital_options(country_code, selected_ids=selected_values)
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
    country_code = query_parameters.normalize_country_key(country_code)
    wants_json = http._wants_json(request)
    if record:
        mode = "edit"
        form = _forms_subdivisions._derived_subdivision_form_initial(country_code=country_code, record=record)
    elif requested_slug and requested_slug != "new":
        mode = "edit"
        root_code = _forms_subdivisions._derived_subdivision_root_code(country_code)
        try:
            internal_name = _queries_groups._group_internal_name(requested_slug)
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
        root_code = _forms_subdivisions._derived_subdivision_root_code(country_code)
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
            "content": _queries_browser._default_derived_subdivision_toml(
                country_code=country_code,
                internal_name="NUEVA_SUBDIVISION",
                parent_code=root_code,
            ),
        }

    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        form["capitals"] = _queries_subdivision_capitals._derived_subdivision_capital_values_from_post(request.POST)
        form["capital"] = form["capitals"][0] if form["capitals"] else ""
        form["source_country_code"] = country_code
        form["parent_code"] = _forms_subdivisions._derived_subdivision_root_code(country_code)
        form["code_suffix"] = str(request.POST.get("code_suffix") or "").strip()
        form["level"] = str(request.POST.get("level") or "").strip()
        form["use_selected_entities_as_children"] = _forms_scraping._form_bool(
            request.POST.get("use_selected_entities_as_children"),
            default=False,
        )
        try:
            internal_name = _queries_groups._group_internal_name(form["internal_name"])
            entry_slug = _queries_groups._group_entry_slug(internal_name)
            source_country_code = country_code
            if not source_country_code:
                raise ValueError(_("El pais es obligatorio."))
            level = _forms_subdivisions._derived_subdivision_level(form["level"])
            parent_code = _forms_subdivisions._derived_subdivision_root_code(source_country_code)
            code = _forms_subdivisions._derived_subdivision_compose_code(
                country_code=source_country_code,
                parent_code=parent_code,
                code_value=form.get("code_suffix") or internal_name,
            )
            name = str(form["name"] or "").strip() or _queries_groups._group_entry_display_name(internal_name)
            raw_content = str(form["content"] or "").strip() or _queries_browser._default_derived_subdivision_toml(
                country_code=source_country_code,
                internal_name=internal_name,
                name=name,
                code=code,
                entity_type=form.get("entity_type", ""),
            )
            if str(request.POST.get("derived_source_dirty") or "") == "1":
                if request.POST.get("include_ids_json") is not None:
                    raw_content = _queries_subdivision_sources._derived_subdivision_content_with_visual_source_ids(
                        raw_content,
                        include_ids_json=request.POST.get("include_ids_json", "[]"),
                        subtract_ids_json=request.POST.get("subtract_ids_json", "[]"),
                        country_code=source_country_code,
                    )
                else:
                    raw_content = _queries_subdivision_sources._derived_subdivision_content_with_visual_source_blocks(
                        raw_content,
                        include_blocks_json=request.POST.get("include_blocks_json", "[]"),
                        subtract_blocks_json=request.POST.get("subtract_blocks_json", "[]"),
                        country_code=source_country_code,
                    )
            content = _forms_subdivisions._render_derived_subdivision_form_toml(
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
            _queries_browser._validate_derived_subdivision_toml(content)
            slug = _queries_subdivisions._derived_subdivision_record_slug(source_country_code, entry_slug)
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
                    "subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(target),
                },
            )
            if wants_json:
                payload = _forms_subdivisions._derived_subdivision_form_save_payload(
                    country_code=target.source_country_code,
                    record=target,
                )
                payload.update(
                    {
                        "message": message,
                        "redirect_url": redirect_url,
                        "country_code": target.source_country_code,
                        "subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(target),
                        "data_url": reverse(
                            "ciudades_del_mundo:derived_subdivision_form_data",
                            kwargs={
                                "country_code": target.source_country_code,
                                "subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(target),
                            },
                        ),
                        "capital_options_url": reverse(
                            "ciudades_del_mundo:derived_subdivision_capital_options",
                            kwargs={
                                "country_code": target.source_country_code,
                                "subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(target),
                            },
                        ),
                    }
                )
                return JsonResponse(payload)
            messages.success(request, message)
            return redirect(f"{redirect_url}?saved=1")

    load_dynamic = request.method == "GET"
    if load_dynamic:
        parent_options = _forms_subdivisions._derived_subdivision_root_parent_options(country_code)
        capital_options = []
        include_items = _queries_subdivision_sources._derived_subdivision_default_source_items(country_code)
        subtract_items = _queries_subdivision_sources._derived_subdivision_default_source_items(country_code)
    else:
        parent_options = _forms_subdivisions._derived_subdivision_root_parent_options(country_code)
        capital_options = _queries_subdivision_capitals._derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        )
        include_items = _queries_subdivision_sources._derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "include",
            fallback_country_code=country_code,
        )
        subtract_items = _queries_subdivision_sources._derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "subtract",
            fallback_country_code=country_code,
        )
    data_slug = requested_slug or ("new" if mode == "new" else _queries_subdivisions._derived_subdivision_entry_slug(record))
    return render(
        request,
        "ciudades_del_mundo/derived_subdivision_form.html",
        {
            "mode": mode,
            "record": record,
            "form": form,
            "country_code": country_code,
            "country_label": _queries_browser._display_name("", country_code, country_code=country_code),
            "root_code": _forms_subdivisions._derived_subdivision_root_code(country_code),
            "parent_options": parent_options,
            "parent_options_json": parent_options,
            "capital_options": capital_options,
            "include_items": include_items,
            "subtract_items": subtract_items,
            "source_countries": _queries_groups._source_country_options_with_current(country_code),
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
