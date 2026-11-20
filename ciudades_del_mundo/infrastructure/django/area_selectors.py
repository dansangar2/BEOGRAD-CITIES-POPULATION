"""Reusable area selectors responsibilities."""
from __future__ import annotations
from django.db.models import Q

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.models import AdminArea, NuevoAdminArea


def apply_numeric_range(queryset, field: str, minimum, maximum):
    try:
        if minimum not in (None, ""):
            queryset = queryset.filter(**{f"{field}__gte": minimum})
        if maximum not in (None, ""):
            queryset = queryset.filter(**{f"{field}__lte": maximum})
    except (TypeError, ValueError):
        return queryset
    return queryset


def filtered_nuevo_areas(params, root: NuevoAdminArea):
    areas = (
        NuevoAdminArea.objects.filter(country_code=root.country_code)
        .select_related("parent", "most_populate_city", "depends_on")
        .prefetch_related("capitals")
        .order_by("level", "parent__name", "name")
    )
    q = (params.get("q") or "").strip()
    levels = query_parameters.clean_values(params.get("level", []))
    entity_types = query_parameters.clean_values(params.get("entity_type", []))
    statuses = query_parameters.clean_values(params.get("province_status", []))
    parents = query_parameters.clean_values(params.get("parent", []))
    has_representatives = params.get("has_representatives")

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
    areas = apply_numeric_range(areas, "pop_latest", params.get("min_pop"), params.get("max_pop"))
    areas = apply_numeric_range(areas, "area_km2", params.get("min_area"), params.get("max_area"))

    sort = params.get("sort") or "level_name"
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


def filtered_admin_areas(params):
    areas = visible_admin_areas().select_related("parent", "most_populate_city").order_by("country_code", "level", "name")
    q = (params.get("q") or "").strip()
    countries = query_parameters.clean_values(params.get("country", []))
    levels = query_parameters.clean_values(params.get("level", []))
    entity_types = query_parameters.clean_values(params.get("entity_type", []))
    merge_statuses = query_parameters.clean_values(params.get("city_merge_status", []))
    parent = (params.get("parent") or "").strip()

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
    areas = apply_numeric_range(areas, "pop_latest", params.get("min_pop"), params.get("max_pop"))
    areas = apply_numeric_range(areas, "area_km2", params.get("min_area"), params.get("max_area"))

    sort = params.get("sort") or "country_level_name"
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


def visible_admin_areas():
    return AdminArea.objects.exclude(city_merge_status=3)


def group_source_admin_areas():
    return visible_admin_areas().filter(
        Q(city_merge_status__in=(AdminArea.CityMergeStatus.NONE, AdminArea.CityMergeStatus.UNIFIED))
        | Q(city_merge_status=AdminArea.CityMergeStatus.SOURCE,
            parent__city_merge_status__in=(AdminArea.CityMergeStatus.UNIFIED, AdminArea.CityMergeStatus.SOURCE))
    )
