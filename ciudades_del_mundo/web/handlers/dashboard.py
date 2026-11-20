"""HTTP endpoints for dashboard."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404, query_params

from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors
from ciudades_del_mundo.web import http as http
from ciudades_del_mundo.web.queries import browser as _queries_browser

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.queries import configs as _queries_configs
from ciudades_del_mundo.web.queries import nuevo_areas as _queries_nuevo_areas


@missing_data_as_404
def dashboard(request):
    """Render project summary metrics and current task state."""
    countries = list(
        area_selectors.visible_admin_areas().values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    admin_country_labels = _queries_browser._admin_country_label_map()
    _queries_browser._apply_country_labels(countries, admin_country_labels)
    derived = list(
        NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    nuevo_country_labels = _queries_nuevo_areas._nuevo_country_label_map()
    _queries_browser._apply_country_labels(derived, nuevo_country_labels)
    context = {
        "admin_area_count": area_selectors.visible_admin_areas().count(),
        "admin_country_count": len(countries),
        "nuevo_area_count": NuevoAdminArea.objects.count(),
        "nuevo_country_count": len(derived),
        "countries": countries,
        "configs": _queries_configs._config_summaries(limit=8),
        "recent_tasks": task_manager.list(limit=8),
        "dashboard_population_url": reverse("ciudades_del_mundo:api_country_summary"),
        "dashboard_derived_url": reverse("ciudades_del_mundo:api_derived_summary"),
        "dashboard_country_detail_base_url": reverse(
            "ciudades_del_mundo:api_country_detail",
            kwargs={"country_code": "__country__"},
        ),
        "derived_bars": _queries_browser._bar_rows(derived, "display_label", "population", limit=10),
    }
    return render(request, "ciudades_del_mundo/dashboard.html", context)


@missing_data_as_404
def api_country_summary(request):
    """Return imported country summary data for API-driven views."""
    return JsonResponse(
        _queries_browser._country_summary_payload(
            detail_route="ciudades_del_mundo:api_country_detail",
            include_visual_assets=True,
        )
    )


@missing_data_as_404
def api_country_detail(request, country_code):
    """Return one imported country's dashboard/detail payload."""
    return _country_detail_response(request, country_code)


@missing_data_as_404
def api_admin_area_detail(request, area_id):
    """Return one imported area and its direct visible child subdivisions."""
    area = (
        area_selectors.visible_admin_areas()
        .select_related("parent")
        .prefetch_related("capitals")
        .filter(id=area_id)
        .first()
    )
    if not area:
        raise Http404(_("No existe AdminArea '%(area_id)s'.") % {"area_id": area_id})
    return JsonResponse(_queries_browser._admin_area_detail_payload(area))


@missing_data_as_404
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
    return JsonResponse(_queries_nuevo_areas._nuevo_area_detail_payload(area))


@missing_data_as_404
def api_derived_summary(request):
    """Return derived-country population rows for API-driven views."""
    return JsonResponse(_queries_browser._derived_summary_payload())


@missing_data_as_404
def dashboard_population_data(request):
    """Return root-country population rows for the dashboard pie chart."""
    return JsonResponse(_queries_browser._country_summary_payload(detail_route="ciudades_del_mundo:dashboard_country_detail"))


@missing_data_as_404
def dashboard_derived_data(request):
    """Return derived-country population rows for the dashboard bar chart."""
    return JsonResponse(_queries_browser._derived_summary_payload())


@missing_data_as_404
def dashboard_country_detail(request, country_code):
    """Return dashboard detail data for one imported country."""
    return _country_detail_response(request, country_code)


@missing_data_as_404
def admin_area_list(request):
    """Render filters for persisted `AdminArea` rows; table loads asynchronously."""
    context = {
        "table_url": reverse("ciudades_del_mundo:admin_area_table"),
        "countries": _queries_browser._admin_country_options(),
        "entity_types": (
            area_selectors.visible_admin_areas().exclude(entity_type__isnull=True)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        ),
        "merge_statuses": AdminArea.CityMergeStatus.choices,
        "sort_choices": _queries_browser._admin_sort_choices(),
    }
    return render(request, "ciudades_del_mundo/admin_area_list.html", context)


@missing_data_as_404
def admin_area_table(request):
    """Render the asynchronous AdminArea table partial."""
    areas = area_selectors.filtered_admin_areas(query_params(request, list_fields=("country", "level", "entity_type", "city_merge_status")))
    paginator = Paginator(areas, http._page_size(request, default=50))
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "ciudades_del_mundo/partials/admin_area_table.html",
        {
            "page_obj": page_obj,
            "querystring": http._querystring_without_page(request),
        },
    )


@missing_data_as_404
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
            "display_name": _queries_browser._display_name(root.name, root.country_code),
            "stats": stats.get(root.country_code, {}),
        }
        for root in roots
    ]
    context = {
        "root_rows": root_rows,
    }
    return render(request, "ciudades_del_mundo/nuevo_area_list.html", context)


@missing_data_as_404
def nuevo_area_detail(request, country_id):
    """Render filters for one `NuevoAdminArea` tree; table loads asynchronously."""
    country_id = _forms_recipes._normalize_recipe_slug(country_id)
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
        "root_display_name": _queries_browser._display_name(root.name, root.country_code),
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
        "sort_choices": _queries_nuevo_areas._nuevo_sort_choices(),
    }
    return render(request, "ciudades_del_mundo/nuevo_area_detail.html", context)


@missing_data_as_404
def nuevo_area_table(request, country_id):
    """Render the asynchronous NuevoAdminArea comparison table partial."""
    country_id = _forms_recipes._normalize_recipe_slug(country_id)
    try:
        root = NuevoAdminArea.objects.get(id=country_id)
    except NuevoAdminArea.DoesNotExist as exc:
        raise Http404(_("No existe NuevoAdminArea '%(country_id)s'.") % {"country_id": country_id}) from exc

    areas = area_selectors.filtered_nuevo_areas(query_params(request, list_fields=("level", "entity_type", "province_status", "parent")), root)
    paginator = Paginator(areas, http._page_size(request, default=75))
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [_queries_browser._comparison_row(area, root) for area in page_obj.object_list]
    return render(
        request,
        "ciudades_del_mundo/partials/nuevo_area_table.html",
        {
            "root": root,
            "rows": rows,
            "page_obj": page_obj,
            "querystring": http._querystring_without_page(request),
        },
    )


@missing_data_as_404
def stats_view(request):
    """Render the API-driven country statistics browser."""
    context = {
        "stats_countries_url": reverse("ciudades_del_mundo:api_country_summary"),
    }
    return render(request, "ciudades_del_mundo/stats.html", context)


@missing_data_as_404
def stats_data(request):
    """Return aggregate statistical chart payloads."""
    return JsonResponse({"charts": _queries_browser._stats_chart_payloads()})


@missing_data_as_404
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
        "admin_countries": _queries_browser._admin_country_options(),
        "nuevo_countries": _queries_nuevo_areas._nuevo_country_options(),
    }
    return render(request, "ciudades_del_mundo/data_delete.html", context)


def _country_detail_response(request, country_code):
    country_code = country_code.lower()
    if not area_selectors.visible_admin_areas().filter(country_code=country_code).exists():
        raise Http404(_("No existe el pais '%(country_code)s'.") % {"country_code": country_code})
    return JsonResponse(
        _queries_browser._country_detail_payload(
            country_code,
            request.GET.get("level"),
            page=request.GET.get("page"),
            page_size=http._country_browser_page_size(request),
            include_children=request.GET.get("include_children") != "0",
        )
    )
