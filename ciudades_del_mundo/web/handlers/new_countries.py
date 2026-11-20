"""HTTP endpoints for new countries."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors
from ciudades_del_mundo.services import derived_config_mutations as config_mutations
from ciudades_del_mundo.web import http as http
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web.queries import nuevo_areas as _queries_nuevo_areas
from django.db import OperationalError, ProgrammingError
from django.http import Http404, JsonResponse

import json
import tomllib
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, CharField, IntegerField, Q, Value, When
from django.db.models.functions import Concat as django_concat
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea
from ciudades_del_mundo.web.queries import groups as country_group_queries
from ciudades_del_mundo.services.derived_config_seeds import bundled_new_country_config_paths, new_country_seed_source, export_new_countries_to_toml, _render_new_country_seed
from ciudades_del_mundo.services.derived_country_group_sync import sync_automatic_group_subtractions
from ciudades_del_mundo.services import new_country_entities as entity_service
from ciudades_del_mundo.services.nuevo_admin_builder import effective_source_municipal_level
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.forms import new_countries as _forms_new_countries
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import exports as _queries_exports
from ciudades_del_mundo.web.queries import new_country_browser as _queries_new_country_browser
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import tasks as _queries_tasks
from ciudades_del_mundo.web import view_support as _view_support


@missing_data_as_404
def api_new_country_summary(request):
    """Return derived-country summary cards for the shared country browser."""
    return JsonResponse(_queries_new_countries._new_country_summary_payload())


@missing_data_as_404
def api_new_country_detail(request, country_id):
    """Return one built new country using the country-browser JSON contract."""
    root = _queries_nuevo_areas.get_nuevo_country_root(country_id)
    return JsonResponse(
        _queries_nuevo_areas._nuevo_country_detail_payload(
            root,
            request.GET.get("level"),
            page=request.GET.get("page"),
            page_size=http._country_browser_page_size(request),
        )
    )


@missing_data_as_404
def api_new_country_container_detail(request, country_slug):
    """Return one DerivedCountry using the shared country-browser JSON contract."""
    country = _queries_new_countries.get_derived_country(country_slug)
    requested_level = request.GET.get("level")
    page_size = http._country_browser_page_size(request)
    configs = list(country.configs.order_by("name", "slug"))
    available_levels = _queries_new_country_browser._new_country_config_available_levels_for_configs(configs)
    selected_level, _selected_entity_types, _selected_filter = _queries_browser._resolve_country_level_selection(
        requested_level,
        available_levels,
    )
    rows = _queries_new_countries._new_country_config_rows(
        configs,
        include_assigned_subdivisions=selected_level is not None,
        target_levels={selected_level} if selected_level is not None else None,
    )
    root = _queries_exports._new_country_detail_export_root(country, rows)
    if root is not None:
        return JsonResponse(
            _queries_nuevo_areas._nuevo_country_detail_payload(
                root,
                requested_level,
                page=request.GET.get("page"),
                page_size=page_size,
            )
        )
    return JsonResponse(
        _queries_new_country_browser._new_country_config_detail_payload(
            country,
            rows,
            requested_level,
            available_levels=available_levels,
            page=request.GET.get("page"),
            page_size=page_size,
        )
    )


@missing_data_as_404
def api_new_country_config_tree(request, country_slug):
    """Return a bounded page of editable DerivedCountryConfig tree rows."""
    country = _queries_new_countries.get_derived_country(country_slug)
    configs = list(country.configs.order_by("name", "slug"))
    rows = _queries_new_countries._new_country_config_tree_request_rows(
        country,
        configs,
        parent_tree_id=request.GET.get("parent", ""),
        selected_level=request.GET.get("level", ""),
    )
    page_obj = _queries_browser._country_browser_page(rows, request.GET.get("page"), http._country_browser_page_size(request))
    focus = request.GET.get("focus", "")
    if focus:
        position = next((index for index, row in enumerate(rows) if row.get("tree_id") == focus), None)
        if position is not None:
            page_obj = _queries_browser._country_browser_page(rows, position // http._country_browser_page_size(request) + 1, http._country_browser_page_size(request))
    return JsonResponse(
        {
            "ok": True,
            "country": country.slug,
            "rows": [_queries_new_countries._new_country_config_tree_row_payload(row) for row in page_obj.object_list],
            "levels": _queries_new_country_browser._new_country_config_available_levels_for_configs(configs),
            "pagination": _queries_browser._pagination_payload(page_obj),
        }
    )


@missing_data_as_404
def api_new_country_config_node_detail(request, country_slug, tree_id):
    """Return one unbuilt DerivedCountryConfig tree node as an area detail."""
    country = _queries_new_countries.get_derived_country(country_slug)
    configs = list(country.configs.order_by("name", "slug"))
    target_slug = _queries_new_countries._new_country_config_node_target_slug(country.slug, tree_id)
    assigned_slugs = None
    source_descendant_slugs = None
    if target_slug:
        direct_child_slugs = {
            config.slug
            for config in configs
            if _queries_new_countries._new_country_config_parent_slug(config) == target_slug
        }
        assigned_slugs = {target_slug, *direct_child_slugs}
        source_descendant_slugs = {target_slug}
    rows = _queries_new_countries._new_country_config_rows(
        configs,
        include_assigned_subdivisions=True,
        assigned_subdivision_config_slugs=assigned_slugs,
        source_descendant_config_slugs=source_descendant_slugs,
    )
    return JsonResponse(_queries_new_country_browser._new_country_config_node_detail_payload(country, rows, tree_id))


@missing_data_as_404
def new_country_import_toml(request):
    """Queue one SQL import task per new-country TOML seed."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect("ciudades_del_mundo:new_country_list")
    paths = bundled_new_country_config_paths()
    label_template = _("Importar pais nuevo TOML: %(slug)s")
    try:
        source = query_parameters.normalize_country_key(request.POST.get("source_country_code"))
        if source:
            paths = [path for path in paths if new_country_seed_source(path) == source]
        queued_tasks = _queries_tasks._queue_derived_seed_import_tasks(
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
            return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
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


@missing_data_as_404
def new_country_export_toml(request):
    """Export all, source-country, container or individual SQL configurations."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    countries = DerivedCountry.objects.prefetch_related("configs").order_by("slug")
    source = query_parameters.normalize_country_key(request.POST.get("source_country_code"))
    country_slug = str(request.POST.get("country_slug") or "").strip()
    config_slug = str(request.POST.get("config_slug") or "").strip()
    if country_slug:
        country = _queries_new_countries.get_derived_country(country_slug)
        countries = [country]
    if source:
        countries = [country for country in countries if _queries_new_countries._new_country_container_source_code(country) == source]
    config_ids = None
    if config_slug:
        if not country_slug:
            return JsonResponse({"ok": False, "error": _("No se pudo identificar el pais de la exportacion.")}, status=400)
        config_ids = {config.pk for country in countries for config in country.configs.all() if config.slug == config_slug}
    try:
        paths = export_new_countries_to_toml(countries=countries, config_ids=config_ids)
    except (ValueError, OSError) as exc:
        error = _("No se pudo exportar el TOML: %(error)s") % {"error": exc}
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("ciudades_del_mundo:new_country_list")
    message = _("Datos guardados.") if paths else _("Sin datos.")
    if wants_json:
        return JsonResponse({"ok": bool(paths), "message": message, "exported": len(paths)}, status=200 if paths else 404)
    messages.success(request, message)
    return redirect("ciudades_del_mundo:new_country_list")


@missing_data_as_404
def new_country_list(request):
    """Render source-country cards with editable derived-country containers."""
    countries = list(DerivedCountry.objects.prefetch_related("configs").order_by("name", "slug"))
    return render(
        request,
        "ciudades_del_mundo/new_country_list.html",
        {
            "country_cards": _queries_new_countries._new_country_cards(countries),
            "seed_count": len(bundled_new_country_config_paths()),
        },
    )


@missing_data_as_404
def new_country_new(request, source_country_code=""):
    """Create a derived-country container."""
    context_source_country_code = query_parameters.normalize_country_key(
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
            slug = _queries_new_countries._normalize_new_country_code(form["slug"])
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
            return redirect(_queries_new_countries._new_country_detail_url(country))
    return render(
        request,
        "ciudades_del_mundo/new_country_form.html",
        {
            "form": form,
            "mode": "country",
            "source_country_label": _queries_browser._source_country_display_label(form["source_country_code"]),
        },
    )


@missing_data_as_404
def new_country_detail(request, source_country_code="", country_slug=""):
    """List configurations for one derived-country container."""
    country = _queries_new_countries.get_derived_country(country_slug)
    source_country_code = query_parameters.normalize_country_key(source_country_code)
    expected_source = query_parameters.normalize_country_key(country.source_country_code)
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
        return redirect(_queries_new_countries._new_country_detail_url(country))
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
            "tree_levels": _queries_new_countries._new_country_config_level_options(configs),
            "country_detail_url": _queries_new_countries._new_country_detail_url(country),
            "export_excel_url": _queries_new_countries._new_country_detail_export_excel_url(country),
            "new_country_config_new_url": _queries_new_countries._new_country_config_new_url(country),
            "source_country_label": _queries_browser._source_country_display_label(country.source_country_code),
            "root_display_code": entity_service.full_area_code(entity_service.materialized_country_root(country)) if entity_service.materialized_country_root(country) else country.slug.upper(),
        },
    )


@missing_data_as_404
def new_country_detail_legacy(request, country_slug):
    """Redirect old /new-countries/<region>/ URLs to /new-countries/<country>/<region>/."""
    country = _queries_new_countries.get_derived_country(country_slug)
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return redirect(_queries_new_countries._new_country_detail_url(country))
    return new_country_detail(request, country_slug=country_slug)


@missing_data_as_404
def new_country_config_delete(request, source_country_code="", country_slug="", config_slug=""):
    """Delete one SQL new-country entity configuration."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    record_slug = config.slug
    parent_slug = _queries_new_countries._new_country_config_parent_slug(config)
    with transaction.atomic():
        reparented_slugs = _forms_new_countries._reparent_new_country_config_children_before_delete(
            country,
            deleted_slug=record_slug,
            new_parent_slug=parent_slug,
        )
        materialized_deleted = config_mutations._delete_materialized_rows_for_new_country_config(**_queries_new_countries.new_country_materialized_scope(config))
        config.delete()
        auto_group_refresh_slugs = sync_automatic_group_subtractions(country)
        _forms_new_countries._refresh_new_country_config_metrics_for_slugs(
            country,
            [parent_slug, *reparented_slugs, *auto_group_refresh_slugs],
        )
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "deleted": True,
                "country_slug": country.slug,
                "config_slug": record_slug,
                "reparented_slugs": reparented_slugs,
                "materialized_deleted": materialized_deleted,
                "redirect_url": _queries_new_countries._new_country_detail_url(country),
            }
        )
    messages.success(request, _("Entidad eliminada."))
    return redirect(_queries_new_countries._new_country_detail_url(country))


@missing_data_as_404
def new_country_config_delete_legacy(request, country_slug, config_slug):
    """Redirect old delete URLs to the source-qualified new-country delete route."""
    country = _queries_new_countries.get_derived_country(country_slug)
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return new_country_config_delete(
            request,
            source_country_code=source_country_code,
            country_slug=country_slug,
            config_slug=config_slug,
        )
    return new_country_config_delete(request, country_slug=country_slug, config_slug=config_slug)


@missing_data_as_404
def new_country_config_clone(request, source_country_code="", country_slug="", config_slug=""):
    """Clone one SQL new-country entity configuration."""
    wants_json = http._wants_json(request)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return HttpResponseNotAllowed(["POST"])
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    try:
        with transaction.atomic():
            clone = _forms_new_countries._clone_new_country_config(config)
            auto_group_refresh_slugs = sync_automatic_group_subtractions(country)
            _forms_new_countries._refresh_new_country_config_metrics_for_slugs(
                country,
                [clone.slug, *auto_group_refresh_slugs],
            )
    except ValueError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect(_queries_new_countries._new_country_detail_url(country))
    redirect_url = _queries_new_countries._new_country_config_edit_url(clone)
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


@missing_data_as_404
def new_country_config_clone_legacy(request, country_slug, config_slug):
    """Redirect old clone URLs to the source-qualified new-country clone route."""
    country = _queries_new_countries.get_derived_country(country_slug)
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return new_country_config_clone(
            request,
            source_country_code=source_country_code,
            country_slug=country_slug,
            config_slug=config_slug,
        )
    return new_country_config_clone(request, country_slug=country_slug, config_slug=config_slug)


@missing_data_as_404
@xframe_options_sameorigin
def new_country_config_new(request, source_country_code="", country_slug=""):
    """Create a TOML configuration for one derived country."""
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    if request.method == "POST" and request.GET.get("delete_area_id"):
        return _delete_saved_country_entity(request, country)
    if request.GET.get("embedded") == "1" and request.GET.get("edit_area_id"):
        try:
            area = entity_service.parent_area_for_country(country, request.GET["edit_area_id"])
            if not area.parent_id:
                raise ValueError(_("La entidad padre no pertenece a este país."))
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return _embedded_country_entity_editor(request, country, entity_service.config_for_area(country, area), area)
    fixed_source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    embedded = request.GET.get("embedded") == "1"
    manual_template = embedded and request.GET.get("kind") == "template"
    wants_json = http._wants_json(request)
    parent_area_id = str(request.POST.get("parent_area_id") or request.GET.get("parent_area_id") or "").strip()
    try:
        parent_area = entity_service.parent_area_for_country(country, parent_area_id)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    saved_root = entity_service.materialized_country_root(country)
    initial_parent_slug = str(request.GET.get("parent_config_slug") or "").strip()
    if parent_area is not None:
        parent_config = entity_service.config_for_area(country, parent_area)
        initial_parent_slug = parent_config.slug if parent_config else ""
    try:
        initial_parent_slug = _queries_browser._validate_new_country_config_parent(country, initial_parent_slug)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
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
        "parent_config_slug": initial_parent_slug,
        "parent_level": str(parent_area.level if parent_area else _queries_new_countries._new_country_parent_level(country, initial_parent_slug)),
        "entity_level": str((parent_area.level if parent_area else _queries_new_countries._new_country_parent_level(country, initial_parent_slug)) + 1),
        "use_subdivisions": "",
        "entity_type": "Subdivision",
        "capitals": [],
        "content": "",
        "selection_json": "",
    }
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        if manual_template:
            form["selection_json"] = '{"selected_ids":[],"country_boxes":true}'
        form["capitals"] = _queries_subdivision_capitals._derived_subdivision_capital_values_from_post(request.POST)
        if fixed_source_country_code:
            form["source_country_code"] = fixed_source_country_code
        if parent_area is not None:
            form["parent_config_slug"] = initial_parent_slug
        try:
            requested_code = _queries_new_countries._new_country_entity_code_from_form(form["slug"])
            slug = _queries_new_countries._new_country_slug_from_entity_code(requested_code)
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = saved_root.country_code if saved_root else _queries_new_countries._normalize_new_country_code(country.slug)
            use_subdivisions = _forms_new_countries._new_country_selection_uses_subdivisions(form["selection_json"])
            sync_child_configs = _forms_new_countries._new_country_selection_has_child_config_slugs(form["selection_json"])
            selected_child_config_slugs = _forms_new_countries._new_country_selected_child_config_slugs_from_selection_json(
                form["selection_json"]
            )
            if use_subdivisions:
                selected_child_config_slugs = []
            if any(entity_service.is_country_bridge(record) for record in country.configs.filter(slug__in=selected_child_config_slugs)):
                raise ValueError(_("No se puede seleccionar una entidad padre como hija."))
            parent_config_slug = _queries_browser._validate_new_country_config_parent(country, form["parent_config_slug"])
            parent_level = _queries_new_countries._new_country_parent_level(country, parent_config_slug)
            if parent_area is not None:
                parent_level = parent_area.level
            if embedded and (parent_config_slug or parent_area is not None):
                prefix = parent_config_slug or _queries_new_countries._new_country_slug_from_entity_code(parent_area.code)
                slug = f"{prefix}__{slug}"
            content = _forms_new_countries._derived_country_content_from_create_post(
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
            if manual_template:
                data = tomllib.loads(content)
                _forms_new_countries._set_manual_country_entity_metrics(data["entities"][0], request.POST)
                data["aggregate_children"] = True
                content = _render_new_country_seed(data)
            _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
            with transaction.atomic():
                selected_child_config_slugs = _forms_new_countries._rename_new_country_selected_child_slug_collision(
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
                    metric_refresh_slugs = _forms_new_countries._new_country_metric_refresh_slugs_for_child_sync(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                    _forms_new_countries._sync_new_country_config_children(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                else:
                    metric_refresh_slugs = []
                auto_group_refresh_slugs = sync_automatic_group_subtractions(country)
                _forms_new_countries._refresh_new_country_config_metrics_for_slugs(
                    country,
                    [config.slug, *metric_refresh_slugs, *auto_group_refresh_slugs],
                )
                if saved_root is not None:
                    actual_parent = parent_area or saved_root
                    if parent_config_slug and parent_area is None:
                        parent_record = country.configs.get(slug=parent_config_slug)
                        parent_data = _queries_new_countries._new_country_config_toml(parent_record)
                        actual_parent = (
                            entity_service.parent_area_for_country(country, parent_data["materialized_area_id"])
                            if parent_data.get("materialized_area_id")
                            else _queries_new_countries._new_country_config_materialized_entity(parent_record, data=parent_data)
                        )
                        if actual_parent is None:
                            raise ValueError(_("La entidad padre no pertenece a este país."))
                    entity_service.materialize_created_entity(config, actual_parent)
        except ValueError as exc:
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
        else:
            if wants_json:
                area_id = _queries_new_countries._new_country_config_toml(config).get("materialized_area_id")
                tree_id = _queries_new_countries._new_country_materialized_tree_id(country, area_id) if area_id else _queries_new_countries._new_country_config_tree_id(config)
                return JsonResponse({"ok": True, "slug": config.slug, "country": country.slug, "tree_id": tree_id})
            messages.success(request, _("Configuracion '%(slug)s' creada.") % {"slug": config.slug})
            return redirect(_queries_new_countries._new_country_config_edit_url(config))
    elif not form["content"]:
        form["content"] = _queries_new_countries._default_derived_country_toml(
            country=country,
            slug="nueva_configuracion",
            name="Nueva configuracion",
            source_country_code=country.source_country_code,
            derived_country_code=country.slug,
        )
    return render(
        request,
        "ciudades_del_mundo/new_country_entity_sources.html" if embedded else "ciudades_del_mundo/new_country_config_create.html",
        {
            "country": country,
            "form": form,
            "config": None,
            "embedded": embedded,
            "manual_template": manual_template,
            "parent_area_id": parent_area_id,
            "parent_display_code": entity_service.full_area_code(parent_area) if parent_area else (
                _queries_new_countries._new_country_config_full_display_code(country.configs.get(slug=initial_parent_slug)) if initial_parent_slug else (entity_service.full_area_code(saved_root) if saved_root else country.slug.upper())
            ),
            "country_detail_url": _queries_new_countries._new_country_detail_url(country),
            "new_country_config_new_url": _queries_new_countries._new_country_config_new_url(country),
            "parent_options": _queries_new_countries._new_country_config_parent_options(country),
            "created_entity_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_entity_options",
                kwargs=_queries_new_countries._new_country_source_kwargs(country),
            ),
            "derived_subdivision_options": [],
            "selected_derived_subdivision_keys": _queries_new_countries._new_country_selected_subdivision_keys_from_selection_json(
                form.get("selection_json", ""),
                fallback_country_code=form["source_country_code"],
            ),
            "capital_options": [] if embedded else _queries_new_countries._new_country_config_capital_options_payload(
                form["source_country_code"],
                country=country,
                selected_ids=form.get("capitals", []),
                selected_derived_subdivisions=_queries_new_countries._new_country_selected_derived_subdivisions_from_selection_json(
                    form.get("selection_json", ""),
                    fallback_country_code=form["source_country_code"],
                ),
                selected_child_config_slugs=(
                    []
                    if _forms_scraping._form_bool(form.get("use_subdivisions"), default=False)
                    else _forms_new_countries._new_country_selected_child_config_slugs_from_selection_json(form.get("selection_json", ""))
                ),
                use_subdivisions=bool(form.get("use_subdivisions")),
            ),
            "capital_options_url": reverse(
                "ciudades_del_mundo:new_country_config_capital_options",
                kwargs=_queries_new_countries._new_country_source_kwargs(country),
            ),
            "derived_subdivision_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_subdivision_options",
                kwargs=_queries_new_countries._new_country_source_kwargs(country),
            ),
            "source_area_options_url": reverse(
                "ciudades_del_mundo:new_country_config_source_area_options",
                kwargs=_queries_new_countries._new_country_source_kwargs(country),
            ),
            "source_area_countries": _queries_browser._derived_source_country_options(),
            "source_countries": [] if embedded else _queries_new_countries._new_country_created_subdivision_country_options(),
            "source_country_label": _queries_browser._source_country_display_label(form["source_country_code"]),
            "children_url": reverse("ciudades_del_mundo:new_country_source_children"),
        },
    )


@missing_data_as_404
def new_country_config_created_subdivision_options(request, source_country_code="", country_slug=""):
    """Return paginated created-subdivision options for a new-country config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    search_term = str(request.GET.get("q") or request.GET.get("term") or "").strip()
    country_filter = query_parameters.normalize_country_key(request.GET.get("country") or request.GET.get("country_code"))
    if request.GET.get("compact") == "1":
        records = _queries_new_countries._new_country_derived_subdivision_queryset(search_term, country_code=country_filter) if country_filter else _queries_new_countries._new_country_derived_subdivision_queryset("").none()
        saved = NuevoAdminArea.objects.filter(country_code=country_filter, level__gt=0, name__icontains=search_term)
        definitions = records.order_by().annotate(option_id=django_concat(Value("recipe:"), "slug", output_field=CharField())).values_list("name", "option_id")
        areas = saved.order_by().annotate(option_id=django_concat(Value("area:"), "id")).values_list("name", "option_id")
        page = Paginator(definitions.union(areas).order_by("name", "option_id"), http._new_country_option_page_size(request)).get_page(request.GET.get("page"))
        results = [{"name": name, "key": key, "country_code": country_filter} for name, key in page.object_list]
        return JsonResponse({"results": results, "page": page.number, "has_next": page.has_next(), "num_pages": page.paginator.num_pages, "total": page.paginator.count})
    try:
        queryset = _queries_new_countries._new_country_derived_subdivision_queryset(search_term, country_code=country_filter)
        paginator = Paginator(queryset, http._new_country_option_page_size(request))
        page_obj = paginator.get_page(request.GET.get("page"))
        options = _queries_new_countries._new_country_derived_subdivision_options_from_records(list(page_obj.object_list))
    except (OperationalError, ProgrammingError):
        return JsonResponse(
            {
                "ok": True,
                "results": [],
                "page": 1,
                "page_size": _view_support.NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
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


@missing_data_as_404
def new_country_config_source_area_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return paginated SQL AdminArea options for a new-country config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    if config_slug and config_slug != "new":
        _queries_new_countries.get_derived_country_config(country, config_slug)
    country_filter = query_parameters.normalize_country_key(
        request.GET.get("country")
        or request.GET.get("country_code")
        or country.source_country_code
        or source_country_code
    )
    page_size = http._new_country_option_page_size(request)
    if request.GET.get("kind") == "groups":
        search = request.GET.get("q", "").casefold()
        entries = country_group_queries._subdivision_group_entries_for_country(
            country_group_queries._subdivision_groups_for_country(country_filter), country_filter,
        )
        records = [{"slug": entry["slug"], "name": entry["name"] or entry["internal_name"]}
                   for entry in entries if search in f'{entry["name"]} {entry["internal_name"]}'.casefold()]
        page = Paginator(records, page_size).get_page(request.GET.get("page"))
        return JsonResponse({"results": list(page.object_list), "page": page.number, "has_next": page.has_next(), "num_pages": page.paginator.num_pages, "total": page.paginator.count})
    if request.GET.get("compact") == "1":
        areas = area_selectors.group_source_admin_areas().filter(country_code=country_filter, level__gt=0)
        raw_level = request.GET.get("level", "")
        levels = []
        if len(raw_level) <= 3 and raw_level.isdecimal() and 0 < int(raw_level) <= 100:
            level = int(raw_level)
        else:
            levels = list(areas.order_by("level").values_list("level", flat=True).distinct())
            level = levels[0] if levels else 0
        search = request.GET.get("q", "").strip()
        records = areas.filter(level=level)
        if search:
            records = records.filter(Q(name__icontains=search) | Q(code__icontains=search))
        records = records.order_by("name", "pk").values("id", "name", "code", "parent__name")
        page = Paginator(records, page_size).get_page(request.GET.get("page"))
        results = [{**row, "parent_label": row["parent__name"]} for row in page.object_list]
        return JsonResponse({
            "levels": [{"value": value, "label": _("Nivel %(level)s") % {"level": value}} for value in levels],
            "level": level, "results": results, "page": page.number,
            "has_next": page.has_next(), "num_pages": page.paginator.num_pages, "total": page.paginator.count,
        })
    try:
        level_options = _forms_scraping._source_level_filter_options_for_country(country_filter) if country_filter else []
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
        queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_filter, level=level)
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
        options = _queries_new_countries._new_country_source_area_options_from_records(list(page_obj.object_list))
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


@missing_data_as_404
def new_country_config_created_entity_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return paginated created new-country entity options for a config form."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    current_config = None
    if config_slug and config_slug != "new":
        current_config = _queries_new_countries.get_derived_country_config(country, config_slug)
    search_term = str(request.GET.get("q") or request.GET.get("term") or "").strip()
    try:
        queryset = _queries_new_countries._new_country_created_entity_queryset(
            country,
            search_term,
            current_config=current_config,
        )
        queryset = queryset.exclude(pk__in=[record.pk for record in country.configs.all() if entity_service.is_country_bridge(record)])
        paginator = Paginator(queryset, http._new_country_option_page_size(request))
        page_obj = paginator.get_page(request.GET.get("page"))
        options = _queries_new_countries._new_country_created_entity_options_from_records(list(page_obj.object_list))
    except (OperationalError, ProgrammingError):
        return JsonResponse(
            {
                "ok": True,
                "results": [],
                "page": 1,
                "page_size": _view_support.NEW_COUNTRY_OPTION_DEFAULT_PAGE_SIZE,
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


@missing_data_as_404
def new_country_config_capital_options(request, source_country_code="", country_slug="", config_slug=""):
    """Return capital options for a new-country config creation form."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    current_config = None
    if config_slug and config_slug != "new":
        current_config = _queries_new_countries.get_derived_country_config(country, config_slug)
    country_code = query_parameters.normalize_country_key(country.source_country_code or source_country_code)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    if body.get("country_boxes") is True:
        return _country_box_capital_options(country, current_config, body)
    selected_values = _queries_subdivision_capitals._derived_subdivision_capital_values_from_payload(body)
    current_data = None
    current_entity = None

    def current_config_entity() -> dict:
        nonlocal current_data, current_entity
        if current_config is None:
            return {}
        if current_data is None:
            current_data = _queries_new_countries._new_country_config_toml(current_config)
        if current_entity is None:
            current_entity = _forms_new_countries._new_country_config_first_entity(current_data)
        return current_entity

    selection_touched = _forms_scraping._form_bool(body.get("selection_touched"), default=False)
    source_selection_touched = selection_touched or _forms_scraping._form_bool(
        body.get("source_selection_touched"),
        default=False,
    )
    derived_selection_touched = selection_touched or _forms_scraping._form_bool(
        body.get("derived_subdivision_selection_touched"),
        default=False,
    )
    selected_derived_subdivisions = _queries_new_countries._new_country_normalized_derived_subdivision_selections(
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
            _forms_new_countries._new_country_config_selected_derived_subdivisions(
                current_entity,
                fallback_country_code=country_code,
            )
        )
    if current_config is not None and use_subdivisions and not selected_derived_subdivisions and not derived_selection_touched:
        selected_derived_subdivisions = _forms_new_countries._new_country_config_selected_derived_subdivisions(
            current_config_entity(),
            fallback_country_code=country_code,
        )
    selected_source_ids = body.get("selected_ids") if isinstance(body.get("selected_ids"), list) else []
    if current_config is not None and use_subdivisions and not selected_source_ids and not source_selection_touched:
        if current_data is None:
            current_data = _queries_new_countries._new_country_config_toml(current_config)
        selected_source_ids = _forms_new_countries._new_country_config_selected_source_ids(
            current_data,
            current_config_entity(),
        )
    selected_child_config_slugs = [] if use_subdivisions else _forms_new_countries._new_country_child_config_slugs_for_capital_payload(
        body,
        country=country,
        current_config=current_config,
    )
    search_term = str(body.get("query") or body.get("term") or "").strip()
    options = _queries_new_countries._new_country_config_capital_options_payload(
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


@missing_data_as_404
def new_country_config_new_legacy(request, country_slug):
    """Redirect old config-create URLs to the source-qualified route."""
    country = _queries_new_countries.get_derived_country(country_slug)
    return redirect(_queries_new_countries._new_country_config_new_url(country))


def _country_box_capital_options(country, config, body):
    try:
        area = entity_service.parent_area_for_country(country, str(body.get("area_id") or ""))
        content = _forms_new_countries._derived_country_content_from_create_post(
            country=country, slug="capital_preview", name="Capital", source_country_code=country.source_country_code,
            derived_country_code=country.slug, raw_content="", selection_json=json.dumps(body),
        )
        entity = tomllib.loads(content)["entities"][0]
        candidates = entity_service.capital_candidate_ids(country, entity, area, include_current=not body.get("selection_changed"))
        if config:
            child_slugs = _forms_new_countries._new_country_config_direct_child_slugs(country, config.slug)
            candidates.update(_queries_new_countries._new_country_child_config_source_ids(
                country, child_slugs, source_ids_cache={}, group_entry_cache={}, group_entries_cache={},
            ))
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    search = str(body.get("query") or "").strip()
    rows = area_selectors.group_source_admin_areas().filter(pk__in=candidates)
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(code__icontains=search))
    options = [{"value": row["pk"], "label": row["name"]} for row in rows.order_by("name", "pk").values("pk", "name")[:50]]
    return JsonResponse({"capital_options": options})


def _delete_saved_country_entity(request, country):
    try:
        with transaction.atomic():
            area = entity_service.parent_area_for_country(country, request.GET["delete_area_id"])
            if not area.parent_id:
                raise ValueError(_("La entidad padre no pertenece a este país."))
            config = entity_service.config_for_area(country, area)
            parent = entity_service.config_for_area(country, area.parent)
            parent_slug = parent.slug if parent else ""
            if config:
                _forms_new_countries._reparent_new_country_config_children_before_delete(
                    country, deleted_slug=config.slug, new_parent_slug=parent_slug,
                )
                config.delete()
            entity_service.delete_saved_entity(country, area)
            sync_automatic_group_subtractions(country)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    redirect_url = _queries_new_countries._new_country_detail_url(country)
    if http._wants_json(request):
        return JsonResponse({"ok": True, "deleted": True, "redirect_url": redirect_url})
    return redirect(redirect_url)


def _embedded_country_entity_editor(request, country, config, area=None):
    data = _queries_new_countries._new_country_config_toml(config) if config else {}
    area = area or (entity_service.parent_area_for_country(country, data["materialized_area_id"]) if data.get("materialized_area_id") else None)
    entity = _forms_new_countries._new_country_config_first_entity(data)
    manual_template = entity.get("manual_template", False)
    source = config.source_country_code if config else country.source_country_code
    code = _forms_new_countries._new_country_config_local_code(config) if config else area.code
    prefix = entity_service.full_area_code(area.parent) if area and area.parent_id else country.slug.upper()
    full_code = entity_service.full_area_code(area) if area else _queries_new_countries._new_country_config_full_display_code(config)
    if area and not config:
        code = full_code.removeprefix(prefix + "-")
    if not area and "-" in full_code:
        prefix = full_code.rsplit("-", 1)[0]
    form = {"slug": code, "name": area.name if area else config.name,
            "entity_type": (area.entity_type if area else entity.get("entity_type")) or "",
            "source_country_code": source,
            "manual_population": entity.get("manual_population", ""),
            "manual_area": entity.get("manual_area", ""),
            "capitals": entity.get("capitals", []) if config else list(area.capitals.values_list("pk", flat=True))}
    if request.method == "POST":
        try:
            name = request.POST.get("name", "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            kind = request.POST.get("entity_type", "").strip()
            capitals = _queries_subdivision_capitals._derived_subdivision_capital_values_from_post(request.POST)
            capitals_changed = capitals != form["capitals"]
            payload = json.loads(request.POST.get("selection_json") or "{}")
            changed = not manual_template and payload.get("selection_changed") is True
            with transaction.atomic():
                if manual_template:
                    _forms_new_countries._set_manual_country_entity_metrics(entity, request.POST)
                if changed:
                    content = _forms_new_countries._derived_country_content_from_create_post(
                        country=country, slug=config.slug if config else "entidad", name=name,
                        source_country_code=source, derived_country_code=config.derived_country_code if config else area.country_code,
                        raw_content="", selection_json=json.dumps(payload), entity_code=code,
                        entity_type=kind, capitals=capitals, entity_level=area.level if area else _queries_new_countries._new_country_config_level(config),
                    )
                    generated = tomllib.loads(content)
                    for key in ("include", "subtract", "mode", "use_selected_entities_as_children"):
                        entity.pop(key, None)
                        if key in generated["entities"][0]:
                            entity[key] = generated["entities"][0][key]
                    data["selection"] = generated["selection"]
                    data["aggregate_children"] = not bool(entity.get("include"))
                entity.update(name=name, entity_type=kind, capitals=capitals)
                if config:
                    if not data.get("entities"):
                        data["entities"] = [entity]
                    data["name"] = config.name = name
                    config.content = _render_new_country_seed(data)
                    config.save(update_fields=["content", "name", "updated_at"])
                if area:
                    entity_service.update_saved_entity(country, area, entity, replace_sources=changed, replace_capitals=capitals_changed)
                elif config:
                    _forms_new_countries._refresh_new_country_config_metrics_for_slugs(country, [config.slug])
        except (ValueError, AttributeError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        tree_id = (_queries_new_countries._new_country_materialized_tree_id(country, area.pk) if area
                   else _queries_new_countries._new_country_config_tree_id(config))
        return JsonResponse({"ok": True, "tree_id": tree_id, "country": country.slug})

    selected_ids = (_forms_new_countries._new_country_config_selected_source_ids(data, entity) if config
                    else list(area.municipios_originales.values_list("pk", flat=True)))
    if area and not manual_template:
        selected_ids = set(selected_ids) | entity_service.branch_source_ids(area)
    selections = [{"kind": "original", "country_code": row["country_code"], "key": row["id"], "label": row["name"]}
                  for row in AdminArea.objects.filter(pk__in=selected_ids).values("id", "name", "country_code")]
    for item in _forms_new_countries._new_country_config_selected_derived_subdivisions(entity, fallback_country_code=source):
        from ciudades_del_mundo.web.queries.subdivision_sources import _derived_subdivision_source_record_for_key
        record = _derived_subdivision_source_record_for_key(item["country_code"], item["key"])
        if record:
            selections.append({"kind": "created", "country_code": item["country_code"], "key": f"recipe:{record.pk}", "label": record.name})
    for block in entity.get("include", []):
        for key in block.get("groups", []):
            selections.append({"kind": "groups", "country_code": block.get("country_code") or source,
                               "key": country_group_queries._group_entry_slug(key), "label": key})
    kwargs = _queries_new_countries._new_country_source_kwargs(country)
    capital_route = "new_country_config_capital_options"
    capital_kwargs = kwargs
    if config:
        capital_route = "new_country_config_edit_capital_options"
        capital_kwargs = {**kwargs, "config_slug": config.slug}
    return render(request, "ciudades_del_mundo/new_country_entity_sources.html", {
        "embedded": True, "editing": True, "manual_template": manual_template, "form": form, "initial_selections": selections,
        "parent_display_code": prefix, "source_country_label": _queries_browser._source_country_display_label(source),
        "source_area_countries": _queries_browser._derived_source_country_options(),
        "source_area_options_url": reverse("ciudades_del_mundo:new_country_config_source_area_options", kwargs=kwargs),
        "capital_options_url": reverse("ciudades_del_mundo:" + capital_route, kwargs=capital_kwargs),
        "capital_area_id": area.pk if area else "",
        "capital_labels": dict(AdminArea.objects.filter(pk__in=form["capitals"]).values_list("pk", "name")),
        "derived_subdivision_options_url": reverse("ciudades_del_mundo:new_country_config_created_subdivision_options", kwargs=kwargs),
    })


@missing_data_as_404
@xframe_options_sameorigin
def new_country_config_edit(request, source_country_code="", country_slug="", config_slug=""):
    """Edit one TOML-backed derived-country configuration."""
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    if request.GET.get("embedded") == "1":
        return _embedded_country_entity_editor(request, country, config)
    form = _forms_new_countries._new_country_config_visual_form(country, config)
    fixed_source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if request.method == "POST":
        form.update({key: request.POST.get(key, "") for key in form if key != "capitals"})
        form["capitals"] = _queries_subdivision_capitals._derived_subdivision_capital_values_from_post(request.POST)
        if fixed_source_country_code:
            form["source_country_code"] = fixed_source_country_code
        try:
            requested_code = _queries_new_countries._new_country_entity_code_from_form(form["slug"])
            current_code = _forms_new_countries._new_country_config_local_code(config)
            requested_slug = _queries_new_countries._new_country_slug_from_entity_code(requested_code)
            slug = config.slug if requested_code == current_code else requested_slug
            name = str(form["name"] or "").strip()
            if not name:
                raise ValueError(_("El nombre es obligatorio."))
            derived_code = _queries_new_countries._normalize_new_country_code(country.slug)
            use_subdivisions = _forms_new_countries._new_country_selection_uses_subdivisions(form["selection_json"])
            sync_child_configs = _forms_new_countries._new_country_selection_has_child_config_slugs(form["selection_json"])
            selected_child_config_slugs = _forms_new_countries._new_country_selected_child_config_slugs_from_selection_json(
                form["selection_json"]
            )
            if use_subdivisions:
                selected_child_config_slugs = []
            parent_config_slug = _queries_browser._validate_new_country_config_parent(
                country,
                form["parent_config_slug"],
                current_config=config,
            )
            parent_level = _queries_new_countries._new_country_parent_level(country, parent_config_slug)
            content = _forms_new_countries._derived_country_content_from_create_post(
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
            _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
            old_slug = config.slug
            previous_content = config.content
            with transaction.atomic():
                if old_slug != slug:
                    selected_child_config_slugs = _forms_new_countries._rename_new_country_selected_child_slug_collision(
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
                    _forms_new_countries._rename_new_country_config_parent_references(
                        country,
                        old_slug=old_slug,
                        new_slug=slug,
                        exclude_config=config,
                    )
                if sync_child_configs:
                    metric_refresh_slugs = _forms_new_countries._new_country_metric_refresh_slugs_for_child_sync(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                    _forms_new_countries._sync_new_country_config_children(
                        country,
                        parent_config=config,
                        selected_child_slugs=selected_child_config_slugs,
                    )
                else:
                    metric_refresh_slugs = []
                auto_group_refresh_slugs = sync_automatic_group_subtractions(
                    country,
                    previous_contents={config.slug: previous_content},
                )
                _forms_new_countries._refresh_new_country_config_metrics_for_slugs(
                    country,
                    [config.slug, *metric_refresh_slugs, *auto_group_refresh_slugs],
                )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Configuracion '%(slug)s' guardada.") % {"slug": config.slug})
            return redirect(_queries_new_countries._new_country_config_edit_url(config))
    return render(
        request,
        "ciudades_del_mundo/new_country_config_create.html",
        {
            "country": country,
            "config": config,
            "form": form,
            "country_detail_url": _queries_new_countries._new_country_detail_url(country),
            "new_country_config_new_url": _queries_new_countries._new_country_config_new_url(country),
            "parent_options": _queries_new_countries._new_country_config_parent_options(country, current_config=config),
            "created_entity_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_created_entity_options",
                kwargs={**_queries_new_countries._new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "derived_subdivision_options": [],
            "selected_derived_subdivision_keys": _queries_new_countries._new_country_selected_subdivision_keys_from_selection_json(
                form.get("selection_json", ""),
                fallback_country_code=form["source_country_code"],
            ),
            "capital_options": _queries_new_countries._new_country_config_capital_options_payload(
                form["source_country_code"],
                country=country,
                selected_ids=form.get("capitals", []),
                selected_derived_subdivisions=_queries_new_countries._new_country_selected_derived_subdivisions_from_selection_json(
                    form.get("selection_json", ""),
                    fallback_country_code=form["source_country_code"],
                ),
                selected_child_config_slugs=(
                    []
                    if _forms_scraping._form_bool(form.get("use_subdivisions"), default=False)
                    else _forms_new_countries._new_country_selected_child_config_slugs_from_selection_json(form.get("selection_json", ""))
                ),
                use_subdivisions=bool(form.get("use_subdivisions")),
            ),
            "capital_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_capital_options",
                kwargs={**_queries_new_countries._new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "derived_subdivision_options_url": reverse(
                "ciudades_del_mundo:new_country_config_created_subdivision_options",
                kwargs=_queries_new_countries._new_country_source_kwargs(country),
            ),
            "source_area_options_url": reverse(
                "ciudades_del_mundo:new_country_config_edit_source_area_options",
                kwargs={**_queries_new_countries._new_country_source_kwargs(country), "config_slug": config.slug},
            ),
            "source_area_countries": _queries_browser._derived_source_country_options(),
            "source_countries": _queries_new_countries._new_country_created_subdivision_country_options(),
            "source_country_label": _queries_browser._source_country_display_label(form["source_country_code"]),
            "children_url": reverse("ciudades_del_mundo:new_country_source_children"),
        },
    )


@missing_data_as_404
def new_country_config_edit_legacy(request, country_slug, config_slug):
    """Redirect old config-edit URLs to the source-qualified route."""
    country = _queries_new_countries.get_derived_country(country_slug)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    return redirect(_queries_new_countries._new_country_config_edit_url(config))


@missing_data_as_404
def start_new_country_config_task(request, source_country_code="", country_slug="", config_slug="", action=""):
    """Start build/export tasks for one SQL new-country configuration."""
    wants_json = http._wants_json(request)
    country = _queries_new_countries.get_derived_country(country_slug)
    _validate_new_country_source_or_404(country, source_country_code)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    if request.method != "POST":
        if wants_json:
            return JsonResponse({"ok": False, "error": _("Metodo no soportado.")}, status=405)
        return redirect(_queries_new_countries._new_country_detail_url(country))

    built_code = _queries_new_countries._new_country_config_built_code(config)
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
            return redirect(_queries_new_countries._new_country_detail_url(country))
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
        return redirect(_queries_new_countries._new_country_detail_url(country))
    except Exception as exc:  # noqa: BLE001 - AJAX must not receive a Django HTML debug page.
        if wants_json:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        raise

    if wants_json:
        return JsonResponse(_queries_tasks._task_started_payload(task, label=label))
    messages.info(request, _("Tarea lanzada: %(label)s.") % {"label": label})
    return redirect("ciudades_del_mundo:task_detail", task_id=task.id)


@missing_data_as_404
def start_new_country_config_task_legacy(request, country_slug, config_slug, action):
    """Redirect old config-task URLs to the source-qualified route."""
    country = _queries_new_countries.get_derived_country(country_slug)
    config = _queries_new_countries.get_derived_country_config(country, config_slug)
    return redirect(_queries_new_countries._new_country_config_task_url(config, action))


@missing_data_as_404
def new_country_source_children(request):
    """Return source `AdminArea` children for the derived-country creation tree."""
    country_code = str(request.GET.get("country_code") or "").strip().lower()
    parent_id = str(request.GET.get("parent_id") or "").strip()
    if not country_code:
        return JsonResponse({"children": []})
    try:
        legal_level = effective_source_municipal_level(country_code)
        if parent_id:
            parent = area_selectors.group_source_admin_areas().get(id=parent_id, country_code__iexact=country_code)
            if (legal_level is not None and int(parent.level or 0) >= legal_level
                    and parent.city_merge_status == AdminArea.CityMergeStatus.NONE):
                children = AdminArea.objects.none()
            else:
                children = area_selectors.group_source_admin_areas().filter(parent=parent)
        else:
            root = _queries_browser._source_country_root_for_code(country_code)
            if root and legal_level == 0:
                children = area_selectors.group_source_admin_areas().filter(id=root.id)
            elif root:
                children = area_selectors.group_source_admin_areas().filter(parent=root)
            else:
                children = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code, level=1)
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
                "city_merge_status",
            )
        )
    except (AdminArea.DoesNotExist, OperationalError, ProgrammingError, ValueError):
        rows = []
        legal_level = None
    child_counts = _queries_browser._admin_area_child_counts([str(row.id) for row in rows], group_source_only=True)
    return JsonResponse(
        {
            "children": [
                {
                    "id": str(row.id),
                    "code": str(row.code or ""),
                    "name": str(row.name or ""),
                    "label": _queries_browser._area_display_name(row),
                    "level": int(row.level or 0),
                    "entity_type": str(row.entity_type or ""),
                    "parent_id": str(row.parent_id or ""),
                    "has_children": (
                        (legal_level is None or int(row.level or 0) < legal_level
                         or row.city_merge_status in (AdminArea.CityMergeStatus.UNIFIED, AdminArea.CityMergeStatus.SOURCE))
                        and child_counts.get(str(row.id), 0) > 0
                    ),
                }
                for row in rows
            ]
        }
    )


def _validate_new_country_source_or_404(country: DerivedCountry, source_country_code: str) -> None:
    source_country_code = query_parameters.normalize_country_key(source_country_code)
    expected_source = query_parameters.normalize_country_key(country.source_country_code)
    if expected_source and source_country_code and source_country_code != expected_source:
        raise Http404(_("El pais nuevo no pertenece a '%(country)s'.") % {"country": source_country_code})
