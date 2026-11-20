"""HTTP endpoints for groups."""

from __future__ import annotations
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.services import derived_config_mutations as config_mutations
from ciudades_del_mundo.services.derived_config_seeds import bundled_subdivision_group_paths
from ciudades_del_mundo.web import http as http
from ciudades_del_mundo.web.queries import groups as _queries_groups
from django.db import OperationalError, ProgrammingError, transaction
from django.http import Http404
from django.urls import reverse

import json
from django.contrib import messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import SubdivisionGroup
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.derived_config_seeds import bundled_derived_subdivision_paths, import_subdivision_group_path_records
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import tasks as _queries_tasks


@missing_data_as_404
def group_import_toml(request):
    """Queue one SQL import task per subdivision-group TOML seed."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:group_list")
    country_code = query_parameters.normalize_country_key(request.POST.get("country_code"))
    paths = bundled_subdivision_group_paths()
    if country_code:
        paths = _queries_groups._subdivision_group_seed_paths_for_country(paths, country_code)
    label_template = _("Importar grupo TOML: %(slug)s")
    try:
        queued_tasks = _queries_tasks._queue_derived_seed_import_tasks(
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
            return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
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


@missing_data_as_404
def group_source_data(request):
    """Return source hierarchy options for the country-scoped group editor."""
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        country_code = query_parameters.normalize_country_key(body.get("country_code"))
        if not country_code:
            return JsonResponse({"country_code": "", "names": [], "sections": []})
        raw_names = body.get("names") or []
        if not isinstance(raw_names, list):
            raw_names = []
        names = [str(name).strip() for name in raw_names if str(name or "").strip()]
        sections = _queries_groups._group_sections_from_payload(body.get("sections") or [])
        if names:
            resolved = _queries_groups._group_sections_for_flat_names(country_code, names)
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
    current_subdivision_country = query_parameters.normalize_country_key(request.GET.get("current_subdivision_country"))
    descendant_parent_ids = [
        parent_id.strip()
        for parent_id in str(request.GET.get("descendant_parent_ids") or "").split(",")
        if parent_id.strip()
    ]
    group_entries_cache: dict[str, list[dict]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    if source_mode == "descendants":
        children = _queries_groups._group_source_descendant_options(country_code, descendant_parent_ids)
        if include_groups:
            descendant_levels = _queries_subdivision_sources._source_item_levels(children)
            children = _queries_subdivision_sources._source_items_with_group_options(
                children,
                _queries_subdivision_sources._derived_subdivision_group_source_options(
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
            descendant_levels = _queries_subdivision_sources._source_item_levels(children)
            children = _queries_subdivision_sources._source_items_with_group_options(
                children,
                _queries_subdivision_sources._derived_subdivision_source_options(
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
                "root": _queries_groups._group_source_root_payload(country_code),
                "levels": _queries_groups._group_source_item_level_options(country_code),
                "sections": [],
                "children": children,
            }
        )
    if source_mode == "items":
        payload = {
            "root": _queries_groups._group_source_root_payload(country_code),
            "levels": _queries_groups._group_source_item_level_options(country_code),
            "sections": [],
            "children": [],
        }
        if level:
            sections = _queries_groups._group_source_item_options(country_code, level, parent_id=section_parent_id)
            if section_parent_id and not sections:
                payload["sections"] = []
                return JsonResponse(payload)
            if include_groups:
                parent_ids = [section_parent_id] if section_parent_id else []
                sections = _queries_subdivision_sources._source_items_with_group_options(
                    sections,
                    _queries_subdivision_sources._derived_subdivision_group_source_options(
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
                sections = _queries_subdivision_sources._source_items_with_group_options(
                    sections,
                    _queries_subdivision_sources._derived_subdivision_source_options(
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

    payload = {"root": _queries_groups._group_source_root_payload(country_code), "levels": _queries_groups._group_source_level_options(country_code), "sections": [], "children": []}
    if level:
        payload["sections"] = _queries_groups._group_source_section_options(country_code, level, parent_id=section_parent_id)
    if parent_id:
        children = _queries_groups._group_source_child_options(country_code, parent_id)
        if include_groups:
            direct_child_levels = _queries_subdivision_sources._source_item_levels(children)
            direct_group_level = direct_child_levels[0] if direct_child_levels else None
            children = _queries_subdivision_sources._source_items_with_group_options(
                children,
                _queries_subdivision_sources._derived_subdivision_group_source_options(
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
            direct_child_levels = _queries_subdivision_sources._source_item_levels(children)
            direct_child_level = direct_child_levels[0] if direct_child_levels else None
            if direct_child_level is not None:
                children = _queries_subdivision_sources._source_items_with_group_options(
                    children,
                    _queries_subdivision_sources._derived_subdivision_source_options(
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


@missing_data_as_404
def group_list(request):
    """List reusable TOML groups for historical/custom subdivisions."""
    groups = list(SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"))
    seed_records = _queries_groups._subdivision_group_seed_records()
    subdivision_seed_paths = bundled_derived_subdivision_paths()
    country_cards = _queries_groups._group_country_cards(
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


@missing_data_as_404
def group_country_detail_data(request, country_code):
    """Return the lazily loaded /groups/ detail tables for one source country."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return JsonResponse({"ok": False, "error": _("No se pudo identificar el pais.")}, status=404)
    return JsonResponse(_queries_groups._group_country_detail_payload(country_code))


@missing_data_as_404
def group_import_toml_slug(request, slug):
    """Import one subdivision_groups/groups/<country>.toml seed bundle into SQL."""
    wants_json = http._wants_json(request)
    slug = _forms_recipes._normalize_recipe_slug(slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:group_edit", slug=slug)
    group = SubdivisionGroup.objects.filter(slug=slug).first()
    seed_slug = _queries_groups._group_seed_file_slug_for_record(group) if group else slug
    seed_country = ""
    if group:
        country_codes = sorted(_queries_groups._subdivision_group_record_country_codes(group))
        seed_country = country_codes[0] if country_codes else query_parameters.normalize_country_key(group.source_country_code)
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
    target_slug = _queries_browser._country_group_record_slug(seed_country, seed_slug) if seed_country else slug
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
    redirect_url = _queries_groups._group_record_edit_url(group)
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


@missing_data_as_404
def group_new(request):
    """Compatibility route; visible creation is country-scoped."""
    country_code = query_parameters.normalize_country_key(request.GET.get("source_country_code"))
    if country_code:
        return redirect("ciudades_del_mundo:group_entry_new", country_code=country_code)
    return redirect("ciudades_del_mundo:group_list")


@missing_data_as_404
def group_edit(request, slug):
    """Compatibility route for existing full-group SQL rows."""
    group = _queries_groups.get_subdivision_group(slug)
    country_codes = sorted(_queries_groups._subdivision_group_record_country_codes(group))
    country_code = country_codes[0] if country_codes else query_parameters.normalize_country_key(group.source_country_code)
    if country_code:
        entries = _queries_groups._subdivision_group_record_entries(group, country_code)
        if entries:
            return redirect(
                "ciudades_del_mundo:group_entry_edit",
                country_code=country_code,
                group_slug=entries[0]["slug"],
            )
    raise Http404(_("No existe una agrupacion editable para '%(slug)s'.") % {"slug": slug})


@missing_data_as_404
def group_entry_new(request, country_code):
    """Create a reusable subdivision grouping inside one source country."""
    country_code = query_parameters.normalize_country_key(country_code)
    return _group_entry_form(request, country_code=country_code, entry=None)


@missing_data_as_404
def group_entry_edit(request, country_code, group_slug):
    """Edit one country-scoped subdivision grouping."""
    country_code = query_parameters.normalize_country_key(country_code)
    group_slug = _queries_groups._group_entry_slug(group_slug)
    entry = _queries_groups._group_entry_for_country(country_code, group_slug, hydrate=True)
    if not entry:
        raise Http404(_("No existe la agrupacion '%(slug)s'.") % {"slug": group_slug})
    return _group_entry_form(request, country_code=country_code, entry=entry)


@missing_data_as_404
def group_city_import_toml(request, country_code):
    """Open the city-group editor from the city panel import action."""
    country_code = query_parameters.normalize_country_key(country_code)
    if request.method != "POST":
        return redirect("ciudades_del_mundo:group_list")
    return redirect(
        "ciudades_del_mundo:group_city_entry",
        country_code=country_code,
        city_slug="ciudades",
    )


@missing_data_as_404
def group_city_entry(request, country_code, city_slug):
    """Temporary city editor route using the group editor shell."""
    country_code = query_parameters.normalize_country_key(country_code)
    city_slug = str(city_slug or "").strip()
    if city_slug.casefold() == "ciudades":
        return _group_entry_form(request, country_code=country_code, entry=None)
    entry = _queries_groups._group_city_entry_for_country(country_code, city_slug)
    if not entry:
        raise Http404(_("No existe la ciudad '%(slug)s'.") % {"slug": city_slug})
    return _group_entry_form(request, country_code=country_code, entry=entry)


def _group_entry_form(request, *, country_code: str, entry: dict | None):
    source_countries = _queries_groups._source_country_options_with_current(country_code)
    if entry:
        initial_internal_name = entry["internal_name"]
        initial_blocks = _queries_groups._group_blocks_with_required_country(
            entry["blocks"] or [{"country_code": country_code, "names": []}],
            fallback_country_code=country_code,
        )
        record = entry.get("record")
        mode = "edit"
        previous_internal_name = _queries_groups._group_internal_name(initial_internal_name)
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
            internal_name = _queries_groups._group_internal_name(form["internal_name"])
            group_slug = _queries_groups._group_entry_slug(internal_name)
            duplicate = _queries_groups._group_duplicate_entry(
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
            blocks = _queries_groups._group_blocks_from_json(form["blocks_json"], fallback_country_code=country_code)
            record_slug = _queries_browser._country_group_record_slug(country_code, group_slug)
            content = _queries_browser._render_group_entry_toml(
                record_slug=record_slug,
                entry_slug=group_slug,
                internal_name=internal_name,
                name=name,
                source_country_code=country_code,
                blocks=blocks,
            )
            _queries_browser._validate_plain_toml(content, expected_kind="subdivision_group")
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
                    config_mutations._rename_subdivision_group_content_references(
                        country_code=country_code,
                        old_key=_queries_groups._group_internal_name(previous_internal_name),
                        new_key=_queries_groups._group_internal_name(internal_name),
                        exclude_slugs={group.slug},
                        group_country_codes=_queries_groups._subdivision_group_record_country_codes,
                    )
                    config_mutations._rename_derived_subdivision_group_references(
                        country_code=country_code,
                        replace_references=_queries_browser._replace_derived_subdivision_group_key,
                        render_content=_forms_subdivisions._render_derived_subdivision_toml_data,
                        old_key=_queries_groups._group_internal_name(previous_internal_name),
                        new_key=_queries_groups._group_internal_name(internal_name),
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
            "blocks": _queries_groups._group_blocks_for_context(form["blocks_json"], fallback_country_code=country_code),
            "existing_group_keys": _queries_groups._group_existing_key_options(
                country_code=country_code,
                exclude_record_slug=str(record.slug) if record else "",
                exclude_entry_slug=str(entry.get("slug") or "") if entry else "",
            ),
        },
    )
