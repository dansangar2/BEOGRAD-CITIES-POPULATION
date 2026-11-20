"""Select materialized NuevoAdminArea hierarchy and browser payloads."""

from __future__ import annotations
from ciudades_del_mundo.ports.query_errors import DataNotFoundError


from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import NuevoAdminArea
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.queries import assets as _queries_assets
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web import view_support as _view_support


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


def _nuevo_country_detail_payload(
    root: NuevoAdminArea,
    selected_level: int | str | None = None,
    *,
    page: int | str | None = None,
    page_size: int = _view_support.COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
) -> dict:
    country_code = root.country_code
    population_total = _nuevo_country_total(country_code, "pop_latest", root) or 0
    area_total = _application_browser_metrics._number_or_none(_nuevo_country_total(country_code, "area_km2", root)) or 0
    available_levels = _nuevo_country_available_levels(country_code, root)
    selected_level, selected_entity_types, selected_filter = _queries_browser._resolve_country_level_selection(
        selected_level,
        available_levels,
    )
    row_queryset = _nuevo_country_level_rows_queryset(
        country_code,
        root,
        selected_level,
        selected_entity_types,
    )
    page_obj = _queries_browser._country_browser_page(
        row_queryset, page, page_size,
        paginate=bool(available_levels) and selected_level != available_levels[0]["value"],
    )
    first_order = list(page_obj.object_list)
    visual_assets = _queries_new_countries._new_country_visual_assets_for_root(root, _queries_new_countries._new_country_source_code_for_built_code(country_code))

    return {
        "country": {
            "id": root.id,
            "code": country_code,
            "name": _queries_browser._area_display_name(root),
            "official_name": _queries_browser._area_display_name(root),
            "entity_type": _queries_browser._entity_type_label(root.entity_type, country_code=country_code),
            "level": root.level,
            "parent": "",
            "population": int(population_total or 0),
            "area_km2": area_total,
            "density": _application_browser_metrics._number_or_none(root.density) or _application_browser_metrics._density(population_total, area_total),
            "capital": _queries_browser._capital_names_for_area(root),
            "subdivision_count": max(0, NuevoAdminArea.objects.filter(country_code=country_code).count() - 1),
            "type_summary": _nuevo_country_type_summary(country_code, root),
            "wikidata_query": root.name,
            "wikidata_id": "",
            "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "derived", "area_id": root.id}),
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": _nuevo_country_table_rows_from_records(first_order, country_code, population_total, area_total),
            "pagination": _queries_browser._pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _queries_browser._donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _queries_browser._donut_payload(first_order, "area_km2", area_total),
            "cards": _nuevo_first_order_cards(country_code, root, first_order, population_total, area_total),
            "child_groups": _nuevo_country_first_order_child_groups(
                country_code,
                root,
                first_order,
                population_total,
                area_total,
            ),
            "pagination": _queries_browser._pagination_payload(page_obj),
        },
    }


def _nuevo_first_order_areas_for_selection(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[NuevoAdminArea]:
    return list(_nuevo_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types))


def _nuevo_country_level_rows_queryset(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
):
    if selected_level is None:
        return NuevoAdminArea.objects.none()
    rows = (
        NuevoAdminArea.objects.filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name", "code", "id")
    )
    if selected_entity_types:
        rows = rows.filter(entity_type__in=selected_entity_types)
    if root:
        rows = rows.exclude(id=root.id)
    return rows


def _nuevo_area_detail_payload(area: NuevoAdminArea) -> dict:
    children = _nuevo_area_browser_children(area)
    return {
        "area": _nuevo_area_identity_payload(area, children),
        "children": _nuevo_area_child_rows(children, area.pop_latest, area.area_km2),
        "child_groups": _nuevo_area_child_groups(area, children),
    }


def _nuevo_area_identity_payload(area: NuevoAdminArea, children: list[NuevoAdminArea] | None = None) -> dict:
    country_root = _queries_browser._area_country_root(area)
    visual_assets = _queries_new_countries._new_country_visual_assets_for_root(area, _queries_new_countries._new_country_source_code_for_built_code(area.country_code)) if area.level == 0 else _queries_assets._visual_assets_payload("admin_area", str(area.id))
    return {
        "id": area.id,
        "code": area.code,
        "country_code": area.country_code,
        "name": _queries_browser._area_display_name(area),
        "official_name": _queries_browser._area_display_name(area),
        "entity_type": _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "parent": _queries_browser._area_display_name(area.parent) if area.parent else "",
        "population": int(area.pop_latest or 0) if area.pop_latest is not None else None,
        "area_km2": _application_browser_metrics._number_or_none(area.area_km2),
        "density": _application_browser_metrics._number_or_none(area.density) or _application_browser_metrics._density(area.pop_latest, area.area_km2),
        "capital": _queries_browser._capital_names_for_area(area),
        "subdivision_count": len(children) if children is not None else _nuevo_area_browser_child_count(area),
        "type_summary": [],
        "wikidata_query": _queries_browser._area_wikidata_query(area, country_root),
        "wikidata_id": "",
        "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "derived", "area_id": area.id}),
        "detail_url": _nuevo_area_detail_url(area),
        "visual_assets": visual_assets,
        "flag_asset": visual_assets.get("flag", {}),
        "seal_asset": visual_assets.get("seal", {}),
        "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
    }


def _nuevo_area_child_rows(children: list[NuevoAdminArea], population_total, area_total) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": _queries_browser._area_display_name(child),
            "entity_type": _queries_browser._entity_type_label(child.entity_type, country_code=child.country_code),
            "level": child.level,
            "area_km2": _application_browser_metrics._number_or_none(child.area_km2),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "density": _application_browser_metrics._number_or_none(child.density) or _application_browser_metrics._density(child.pop_latest, child.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(child.pop_latest, population_total),
            "area_percent": _application_browser_metrics._ratio_percent(child.area_km2, area_total),
            "child_count": _nuevo_area_browser_child_count(child),
            "detail_url": _nuevo_area_detail_url(child),
        }
        for child in children
    ]


def _nuevo_area_browser_children(area: NuevoAdminArea) -> list[NuevoAdminArea]:
    return list(
        NuevoAdminArea.objects.filter(parent=area)
        .select_related("parent")
        .order_by("name")
    )


def _nuevo_area_browser_child_count(area: NuevoAdminArea) -> int:
    return NuevoAdminArea.objects.filter(parent=area).count()


def _nuevo_area_child_groups(area: NuevoAdminArea, children: list[NuevoAdminArea]) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[NuevoAdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _nuevo_area_child_group_label(area.country_code, level),
            "children": _nuevo_area_child_rows(rows, area.pop_latest, area.area_km2),
        }
        for level, rows in sorted(grouped.items())
    ]


def _nuevo_country_top_level_areas(country_code: str, root: NuevoAdminArea | None = None):
    if root:
        children = NuevoAdminArea.objects.filter(parent=root)
        first_child_level = children.values_list("level", flat=True).order_by("level").first()
        if first_child_level is not None:
            return children.filter(level=first_child_level).order_by("name")
        next_level = (
            NuevoAdminArea.objects.filter(country_code=country_code)
            .exclude(id=root.id)
            .values_list("level", flat=True)
            .order_by("level")
            .first()
        )
        if next_level is not None:
            return NuevoAdminArea.objects.filter(country_code=country_code, level=next_level).exclude(id=root.id).order_by("name")
        return NuevoAdminArea.objects.none()
    return NuevoAdminArea.objects.filter(country_code=country_code, level=0, parent__isnull=True).order_by("name")


def _nuevo_country_total(country_code: str, field: str, root: NuevoAdminArea | None):
    if root and getattr(root, field) is not None:
        return getattr(root, field)
    return _nuevo_country_top_level_areas(country_code, root).aggregate(total=Sum(field))["total"]


def _nuevo_country_available_levels(country_code: str, root: NuevoAdminArea | None) -> list[dict]:
    levels = list(
        NuevoAdminArea.objects.filter(country_code=country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    rows = []
    for level in levels:
        if root and level == root.level:
            continue
        level_rows = NuevoAdminArea.objects.filter(country_code=country_code, level=level)
        if root:
            level_rows = level_rows.exclude(id=root.id)
        type_stats = _nuevo_country_level_type_stats(level_rows)
        level_total = sum(count for count, _rows_with_children in type_stats.values())
        if level_total <= 0:
            continue
        entity_type_values = tuple(sorted(type_stats))
        entity_types = [_queries_browser._entity_type_label(item, country_code=country_code) or _("Sin tipo") for item in entity_type_values]
        entity_type_label = " / ".join(entity_types[:3])
        rows.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": _queries_browser._display_level(level, root)},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return rows


def _nuevo_country_level_type_stats(level_rows) -> dict[str, tuple[int, int]]:
    counts = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in level_rows.values("entity_type").annotate(count=Count("id")).order_by("entity_type")
    }
    rows_with_children = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in (
            level_rows.filter(children__isnull=False)
            .values("entity_type")
            .annotate(count=Count("id", distinct=True))
            .order_by("entity_type")
        )
    }
    return {entity_type: (count, rows_with_children.get(entity_type, 0)) for entity_type, count in counts.items()}


def _nuevo_country_table_rows(
    country_code: str,
    root: NuevoAdminArea | None,
    selected_level: int | None,
    population_total=0,
    area_total=0,
    selected_entity_types: tuple[str, ...] = (),
) -> list[dict]:
    rows = _nuevo_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types)
    return _nuevo_country_table_rows_from_records(rows, country_code, population_total, area_total)


def _nuevo_country_table_rows_from_records(
    rows,
    country_code: str,
    population_total=0,
    area_total=0,
) -> list[dict]:
    return [
        {
            "id": row.id,
            "level": row.level,
            "detail_url": _nuevo_area_detail_url(row),
            "parent_id": row.parent_id or "",
            "parent_level": row.parent.level if row.parent else None,
            "parent_detail_url": _nuevo_area_detail_url(row.parent) if row.parent else "",
            "name": _queries_browser._area_display_name(row),
            "area_km2": _application_browser_metrics._number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _application_browser_metrics._number_or_none(row.density) or _application_browser_metrics._density(row.pop_latest, row.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(row.pop_latest, population_total),
            "area_percent": _application_browser_metrics._ratio_percent(row.area_km2, area_total),
            "parent": _queries_browser._area_display_name(row.parent) if row.parent else "",
            "entity_type": _queries_browser._entity_type_label(row.entity_type, country_code=country_code),
            "annotations": "",
        }
        for row in rows
    ]


def _nuevo_country_type_summary(country_code: str, root: NuevoAdminArea | None) -> list[dict]:
    rows = NuevoAdminArea.objects.filter(country_code=country_code)
    if root:
        rows = rows.exclude(id=root.id)
    grouped = rows.values("level", "entity_type").annotate(total=Count("id")).order_by("level", "entity_type")
    return [
        {
            "level": item["level"],
            "label": _("Nivel %(level)s") % {"level": _queries_browser._display_level(item["level"], root)},
            "entity_type": _queries_browser._entity_type_label(item["entity_type"], country_code=country_code) or _("Sin tipo"),
            "total": item["total"],
        }
        for item in grouped
    ]


def _nuevo_country_first_order_child_groups(
    country_code: str,
    root: NuevoAdminArea | None,
    children: list[NuevoAdminArea],
    population_total,
    area_total,
) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[NuevoAdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _nuevo_area_child_group_label(country_code, level),
            "children": _nuevo_area_child_rows(rows, population_total, area_total),
        }
        for level, rows in sorted(grouped.items())
    ]


def _nuevo_area_child_group_label(country_code: str, level: int) -> str:
    types = _nuevo_level_entity_types(country_code, level)
    base = _("Nivel %(level)s") % {"level": level}
    if types:
        return f"{base} - {', '.join(types[:3])}"
    return base


def _nuevo_level_entity_types(country_code: str, level: int) -> list[str]:
    return [
        _queries_browser._entity_type_label(item, country_code=country_code) or _("Sin tipo")
        for item in (
            NuevoAdminArea.objects.filter(country_code=country_code, level=level)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        )
    ]


def _nuevo_first_order_cards(
    country_code: str,
    root: NuevoAdminArea | None,
    first_order: list[NuevoAdminArea],
    population_total,
    area_total,
) -> list[dict]:
    child_level = _queries_browser._second_order_level(first_order)
    child_count = (
        NuevoAdminArea.objects.filter(country_code=country_code, level=child_level).count()
        if child_level is not None
        else 0
    )
    include_children = child_level is not None and child_count < 150
    return [
        _nuevo_first_order_card(area, population_total, area_total, include_children=include_children)
        for area in first_order
    ]


def _nuevo_first_order_card(area: NuevoAdminArea, population_total, area_total, *, include_children: bool = False) -> dict:
    population = int(area.pop_latest or 0) if area.pop_latest is not None else 0
    area_km2 = _application_browser_metrics._number_or_none(area.area_km2) or 0
    return {
        "id": area.id,
        "name": _queries_browser._area_display_name(area),
        "entity_type": _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "population": population,
        "area_km2": area_km2,
        "density": _application_browser_metrics._number_or_none(area.density) or _application_browser_metrics._density(area.pop_latest, area.area_km2),
        "population_percent": _application_browser_metrics._ratio_percent(population, population_total),
        "area_percent": _application_browser_metrics._ratio_percent(area_km2, area_total),
        "child_count": _nuevo_area_browser_child_count(area),
        "detail_url": _nuevo_area_detail_url(area),
        "children": _nuevo_second_order_share_rows(area) if include_children else [],
    }


def _nuevo_second_order_share_rows(area: NuevoAdminArea) -> list[dict]:
    children = _nuevo_area_browser_children(area)
    return [
        {
            "name": _queries_browser._area_display_name(child),
            "entity_type": _queries_browser._entity_type_label(child.entity_type, country_code=child.country_code),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "area_km2": _application_browser_metrics._number_or_none(child.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(child.pop_latest, area.pop_latest),
            "area_percent": _application_browser_metrics._ratio_percent(child.area_km2, area.area_km2),
        }
        for child in children
    ]


def _nuevo_area_detail_url(area: NuevoAdminArea) -> str:
    return reverse("ciudades_del_mundo:api_new_area_detail", kwargs={"area_id": area.id})


def _nuevo_country_label_map() -> dict[str, str]:
    roots = NuevoAdminArea.objects.filter(level=0, parent__isnull=True).values_list("country_code", "name")
    return {country_code: _queries_browser._display_name(name, country_code) for country_code, name in roots}


def _nuevo_country_options() -> list[dict[str, str]]:
    codes = NuevoAdminArea.objects.values_list("country_code", flat=True).distinct().order_by("country_code")
    return _queries_browser._country_options(codes, _nuevo_country_label_map())


def get_nuevo_country_root(country_id: str) -> NuevoAdminArea:
    country_id = str(country_id or "").strip()
    if not country_id:
        raise DataNotFoundError(_("No existe NuevoAdminArea '%(area_id)s'.") % {"area_id": country_id})
    root = (
        NuevoAdminArea.objects.select_related("parent", "most_populate_city")
        .prefetch_related("capitals")
        .filter(id__iexact=country_id, parent__isnull=True)
        .order_by("name")
        .first()
    )
    if root is None:
        root = (
            NuevoAdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(country_code__iexact=country_id, parent__isnull=True)
            .order_by("name")
            .first()
        )
    if root is None:
        raise DataNotFoundError(_("No existe NuevoAdminArea '%(area_id)s'.") % {"area_id": country_id})
    return root
