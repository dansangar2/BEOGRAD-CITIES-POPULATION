"""Build the browser payloads for SQL derived configurations."""

from __future__ import annotations
from ciudades_del_mundo.ports.query_errors import DataNotFoundError

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from ciudades_del_mundo.application import export_metrics as _application_export_metrics

from collections import Counter
from django.db import OperationalError, ProgrammingError
from django.db.models import Count
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, DerivedSubdivision, NuevoAdminArea
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.forms import new_countries as _forms_new_countries
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import exports as _queries_exports
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web.queries import nuevo_areas as _queries_nuevo_areas
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web import view_support as _view_support


def _new_country_config_detail_payload(
    country: DerivedCountry,
    rows: list[dict],
    selected_level: int | str | None = None,
    *,
    available_levels: list[dict] | None = None,
    page: int | str | None = None,
    page_size: int = _view_support.COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
) -> dict:
    """Render an unbuilt DerivedCountry config tree as the country-browser payload."""
    tree_rows = [row for row in rows if row.get("is_config_row") or row.get("is_assigned_subdivision")]
    config_rows = [row for row in tree_rows if row.get("is_config_row")]
    source_code = _new_country_config_rows_source_code(country, config_rows)
    built_code = _new_country_config_rows_built_code(country, config_rows)
    area_total, population_total = _queries_exports._new_country_detail_export_totals(country, config_rows)
    area_total_value = _application_browser_metrics._number_or_none(area_total) or 0
    population_total_value = _new_country_json_number(population_total) or 0
    available_levels = available_levels or _new_country_config_available_levels(tree_rows, source_code)
    selected_level, selected_entity_types, selected_filter = _queries_browser._resolve_country_level_selection(
        selected_level,
        available_levels,
    )
    rows_by_tree_id = _new_country_config_rows_by_tree_id(tree_rows)
    children_by_parent = _new_country_config_children_by_parent(tree_rows)
    first_order_rows = _new_country_config_order_rows_for_selection(
        tree_rows,
        config_rows,
        selected_level,
        selected_entity_types,
    )
    page_obj = _queries_browser._country_browser_page(
        first_order_rows, page, page_size,
        paginate=bool(available_levels) and selected_level != available_levels[0]["value"],
    )
    first_order = list(page_obj.object_list)
    table_rows = [
        _new_country_config_browser_row(
            row,
            rows_by_tree_id,
            population_total_value,
            area_total_value,
            source_code,
            country_slug=country.slug,
            children_by_parent=children_by_parent,
        )
        for row in first_order
    ]
    visual_assets = {}
    country_entity_type = _("Pais nuevo")
    return {
        "country": {
            "id": country.slug,
            "code": built_code,
            "name": country.name,
            "official_name": _new_country_official_name(country_entity_type, country.name),
            "entity_type": country_entity_type,
            "level": 0,
            "parent": "",
            "population": population_total_value,
            "area_km2": area_total_value,
            "density": _application_browser_metrics._density(population_total_value, area_total_value),
            "capital": _new_country_config_country_capital_names(config_rows),
            "subdivision_count": _new_country_config_country_subdivision_count(config_rows),
            "type_summary": _new_country_config_type_summary(tree_rows, source_code),
            "wikidata_query": country.name,
            "wikidata_id": "",
            "map_url": "",
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": table_rows,
            "pagination": _queries_browser._pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _new_country_config_donut_payload(first_order, "entity_population_value", population_total_value),
            "area_chart": _new_country_config_donut_payload(first_order, "entity_area_value", area_total_value),
            "cards": _new_country_config_first_order_cards(
                first_order,
                rows_by_tree_id,
                population_total_value,
                area_total_value,
                source_code,
                country.slug,
                children_by_parent,
            ),
            "child_groups": _new_country_config_first_order_child_groups(
                first_order,
                rows_by_tree_id,
                population_total_value,
                area_total_value,
                source_code,
                country.slug,
                children_by_parent,
            ),
            "pagination": _queries_browser._pagination_payload(page_obj),
        },
    }


def _new_country_config_rows_source_code(country: DerivedCountry, rows: list[dict]) -> str:
    source_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_code:
        return source_code
    for row in rows:
        source_code = query_parameters.normalize_country_key(row.get("source_code"))
        if source_code:
            return source_code
    return ""


def _new_country_config_rows_built_code(country: DerivedCountry, rows: list[dict]) -> str:
    for row in rows:
        built_code = query_parameters.normalize_country_key(row.get("built_code"))
        if built_code:
            return built_code
    return query_parameters.normalize_country_key(country.slug)


def _new_country_json_number(value):
    value = _application_export_metrics._new_country_export_decimal(value)
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _new_country_config_rows_by_tree_id(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}


def _new_country_config_available_levels(rows: list[dict], source_code: str) -> list[dict]:
    grouped: dict[int, Counter[str]] = {}
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        entity_type = str(row.get("entity_type") or "").strip()
        grouped.setdefault(level, Counter())[entity_type] += 1
    return _new_country_config_available_levels_from_counts(grouped, source_code)


def _new_country_config_available_levels_for_configs(
    configs: list[DerivedCountryConfig],
) -> list[dict]:
    source_code = ""
    for config in configs:
        source_code = _queries_new_countries._new_country_config_source_code(config)
        if source_code:
            break
    return _new_country_config_available_levels_from_counts(
        _new_country_config_level_type_counts(configs),
        source_code,
    )


def _new_country_config_available_levels_from_counts(
    grouped: dict[int, Counter[str]],
    source_code: str,
) -> list[dict]:
    result = []
    for level, type_counts in sorted(grouped.items()):
        level_total = sum(type_counts.values())
        if level_total <= 0:
            continue
        entity_types = [
            _queries_browser._entity_type_label(entity_type, country_code=source_code) or _("Sin tipo")
            for entity_type in sorted(type_counts)
        ]
        entity_type_label = " / ".join(entity_types[:3])
        result.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": level},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return result


def _new_country_config_level_type_counts(
    configs: list[DerivedCountryConfig],
) -> dict[int, Counter[str]]:
    grouped: dict[int, Counter[str]] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}

    def add(level: int, entity_type: str, count: int = 1) -> None:
        try:
            clean_level = int(level or 0)
        except (TypeError, ValueError):
            return
        if clean_level <= 0 or count <= 0:
            return
        grouped.setdefault(clean_level, Counter())[str(entity_type or "").strip()] += int(count)

    for config in configs:
        data = _queries_new_countries._new_country_config_toml(config)
        entity = _forms_new_countries._new_country_config_first_entity(data)
        source_code = _queries_new_countries._new_country_config_source_code(config)
        config_level = _queries_new_countries._new_country_config_level(config)
        entity_type = str(entity.get("entity_type") or "").strip()
        add(config_level, entity_type)

        display_level = max(1, int(config_level or 1) + 1)
        source_ids = _forms_new_countries._new_country_config_selected_source_ids(data, entity)
        for direct_entity_type, total in _new_country_config_source_id_type_counts(source_ids).items():
            add(display_level, direct_entity_type, total)

        seen_derived: set[tuple[str, str]] = set()
        for selection in _forms_new_countries._new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code=source_code,
        ):
            record_country_code = query_parameters.normalize_country_key(selection.get("country_code") or source_code)
            record_key = str(selection.get("key") or "").strip()
            if not record_country_code or not record_key:
                continue
            lookup_key = (record_country_code, record_key.casefold())
            if lookup_key in seen_derived:
                continue
            seen_derived.add(lookup_key)
            record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(record_country_code, record_key)
            if record is None:
                continue
            record_data = _queries_subdivisions._derived_subdivision_record_toml(record)
            try:
                assigned_level = int(selection.get("level") or display_level)
            except (TypeError, ValueError):
                assigned_level = display_level
            assigned_level = max(display_level, max(1, min(9, assigned_level)))
            add(assigned_level, record_data.get("entity_type") or record.entity_type)
            source_count = _new_country_config_derived_source_row_count(
                record,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
            add(assigned_level + 1, "", source_count)
    return grouped


def _new_country_config_source_id_type_counts(source_ids: list[str]) -> Counter[str]:
    clean_ids = [str(value).strip() for value in source_ids if str(value or "").strip()]
    if not clean_ids:
        return Counter()
    try:
        return Counter(
            {
                str(row["entity_type"] or ""): int(row["total"] or 0)
                for row in area_selectors.group_source_admin_areas()
                .filter(id__in=clean_ids)
                .values("entity_type")
                .annotate(total=Count("id"))
            }
        )
    except (OperationalError, ProgrammingError, ValueError):
        return Counter({"": len(clean_ids)})


def _new_country_config_derived_source_row_count(
    record: DerivedSubdivision,
    *,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> int:
    return len(
        _queries_subdivision_sources._derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    )


def _new_country_config_type_summary(rows: list[dict], source_code: str) -> list[dict]:
    counts: Counter[tuple[int, str]] = Counter()
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        counts[(level, str(row.get("entity_type") or ""))] += 1
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "entity_type": _queries_browser._entity_type_label(entity_type, country_code=source_code) or _("Sin tipo"),
            "total": total,
        }
        for (level, entity_type), total in sorted(counts.items())
    ]


def _new_country_config_country_capital_names(config_rows: list[dict]) -> list[str]:
    first_order = _new_country_config_first_order_rows(config_rows)
    names = _new_country_config_unique_capital_names(first_order)
    if names:
        return names
    return _new_country_config_unique_capital_names(config_rows)


def _new_country_config_unique_capital_names(rows: list[dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for name in _new_country_config_row_capital_names(row):
            marker = name.casefold()
            if marker and marker not in seen:
                seen.add(marker)
                names.append(name)
    return names


def _new_country_config_country_subdivision_count(config_rows: list[dict]) -> int:
    context = _application_export_metrics._new_country_export_legal_context()
    source_unit_ids: set[str] = set()
    for row in config_rows:
        source_unit_ids.update(_queries_exports._new_country_export_legal_source_unit_ids(row, context))
    return len(source_unit_ids) or len(config_rows)


def _new_country_config_first_order_rows(rows: list[dict]) -> list[dict]:
    top_rows = [row for row in rows if not str(row.get("tree_parent") or "")]
    if top_rows:
        return sorted(top_rows, key=lambda row: str(row.get("entity_display_name") or "").casefold())
    levels = sorted(
        {
            int(row.get("level") or 0)
            for row in rows
            if str(row.get("level") or "").isdigit() and int(row.get("level") or 0) > 0
        }
    )
    if not levels:
        return []
    first_level = levels[0]
    return sorted(
        [row for row in rows if int(row.get("level") or 0) == first_level],
        key=lambda row: str(row.get("entity_display_name") or "").casefold(),
    )


def _new_country_config_order_rows_for_selection(
    tree_rows: list[dict],
    config_rows: list[dict],
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[dict]:
    if selected_level is None:
        return _new_country_config_first_order_rows(config_rows)
    rows = [
        row
        for row in tree_rows
        if int(row.get("level") or 0) == selected_level
        and (
            not selected_entity_types
            or str(row.get("entity_type") or "") in selected_entity_types
        )
    ]
    if not rows:
        return _new_country_config_first_order_rows(config_rows)
    return sorted(rows, key=lambda row: str(row.get("entity_display_name") or "").casefold())


def _new_country_config_browser_row(
    row: dict,
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    *,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> dict:
    tree_id = str(row.get("tree_id") or "")
    entity_node = row.get("entity_node")
    parent = rows_by_tree_id.get(str(row.get("tree_parent") or ""))
    parent_node = parent.get("entity_node") if parent else None
    population = _new_country_json_number(row.get("entity_population_value"))
    area_km2 = _application_browser_metrics._number_or_none(row.get("entity_area_value"))
    density = _application_browser_metrics._number_or_none(row.get("entity_density_value")) or _application_browser_metrics._density(population, area_km2)
    config_child_count = len((children_by_parent or {}).get(tree_id, []))
    dynamic_config_child_count = config_child_count or int(row.get("assigned_subdivision_child_count") or 0)
    if isinstance(entity_node, AdminArea):
        try:
            source_child_count = int(row.get("source_child_count"))
        except (TypeError, ValueError):
            source_child_count = _queries_browser._admin_area_browser_child_count(entity_node)
    else:
        source_child_count = 0
    new_area_child_count = _queries_nuevo_areas._nuevo_area_browser_child_count(entity_node) if isinstance(entity_node, NuevoAdminArea) else 0
    child_count = dynamic_config_child_count or source_child_count or new_area_child_count
    detail_url = ""
    if isinstance(entity_node, NuevoAdminArea) and new_area_child_count:
        detail_url = _queries_nuevo_areas._nuevo_area_detail_url(entity_node)
    elif isinstance(entity_node, AdminArea) and source_child_count:
        detail_url = _queries_browser._admin_area_detail_url(entity_node)
    if not detail_url and country_slug and (dynamic_config_child_count or row.get("has_assigned_subdivision_children")):
        detail_url = _queries_new_countries._new_country_config_node_detail_url(country_slug, tree_id)
    parent_detail_url = _queries_nuevo_areas._nuevo_area_detail_url(parent_node) if isinstance(parent_node, NuevoAdminArea) else ""
    if not parent_detail_url and country_slug and parent:
        parent_tree_id = str(parent.get("tree_id") or "")
        if (children_by_parent or {}).get(parent_tree_id):
            parent_detail_url = _queries_new_countries._new_country_config_node_detail_url(country_slug, parent_tree_id)
    entity_type_label = _queries_browser._entity_type_label(
        str(row.get("entity_type") or ""),
        country_code=query_parameters.normalize_country_key(
            getattr(entity_node, "country_code", "")
            or row.get("source_code")
            or source_code
        ),
    )
    name = str(row.get("entity_display_name") or row.get("display_name") or "")
    return {
        "id": tree_id,
        "code": str(row.get("display_code") or ""),
        "level": int(row.get("level") or 0),
        "detail_url": detail_url,
        "parent_id": str(parent.get("tree_id") or "") if parent else "",
        "parent_level": int(parent.get("level") or 0) if parent else None,
        "parent_detail_url": parent_detail_url,
        "name": name,
        "official_name": str(row.get("official_name") or "") or _new_country_official_name(entity_type_label, name),
        "area_km2": area_km2,
        "population": population,
        "density": density,
        "population_percent": _application_browser_metrics._ratio_percent(population, population_total),
        "area_percent": _application_browser_metrics._ratio_percent(area_km2, area_total),
        "parent": _new_country_config_parent_name(parent),
        "entity_type": entity_type_label,
        "capital": _new_country_config_row_capital_names(row),
        "subdivision_count": child_count,
        "annotations": "",
        "child_count": child_count,
    }


def _new_country_official_name(entity_type: str, base_name: str) -> str:
    entity_type = str(entity_type or "").strip()
    base_name = str(base_name or "").strip()
    if not entity_type:
        return base_name
    if not base_name:
        return entity_type
    entity_key = _queries_groups._group_name_match_key(entity_type)
    base_key = _queries_groups._group_name_match_key(base_name)
    if entity_key and (base_key == entity_key or base_key.startswith(f"{entity_key} ")):
        return base_name
    return f"{entity_type} {base_name}"


def _new_country_config_row_capital_names(row: dict) -> list[str]:
    text = _queries_exports._new_country_export_capitals(row)
    if not text:
        return []
    names = []
    seen = set()
    for raw_name in str(text).split(" | "):
        name = raw_name.strip()
        marker = name.casefold()
        if name and marker not in seen:
            seen.add(marker)
            names.append(name)
    return names


def _new_country_config_parent_name(parent: dict | None) -> str:
    if not parent:
        return ""
    return str(parent.get("entity_display_name") or parent.get("display_name") or "")


def _new_country_config_node_detail_payload(country: DerivedCountry, rows: list[dict], tree_id: str) -> dict:
    tree_rows = [row for row in rows if row.get("is_config_row") or row.get("is_assigned_subdivision")]
    rows_by_tree_id = _new_country_config_rows_by_tree_id(tree_rows)
    node = rows_by_tree_id.get(str(tree_id or ""))
    if not node:
        raise DataNotFoundError(_("No existe entidad de pais nuevo '%(area_id)s'.") % {"area_id": tree_id})
    children_by_parent = _new_country_config_children_by_parent(tree_rows)
    source_code = _new_country_config_rows_source_code(country, [row for row in tree_rows if row.get("is_config_row")])
    children = children_by_parent.get(str(node.get("tree_id") or ""), [])
    population = _new_country_json_number(node.get("entity_population_value"))
    area_km2 = _application_browser_metrics._number_or_none(node.get("entity_area_value"))
    return {
        "area": _new_country_config_browser_row(
            node,
            rows_by_tree_id,
            population,
            area_km2,
            source_code,
            country_slug=country.slug,
            children_by_parent=children_by_parent,
        ),
        "children": [
            _new_country_config_browser_row(
                child,
                rows_by_tree_id,
                population,
                area_km2,
                source_code,
                country_slug=country.slug,
                children_by_parent=children_by_parent,
            )
            for child in children
        ],
        "child_groups": _new_country_config_area_child_groups(
            country,
            children,
            rows_by_tree_id,
            children_by_parent,
            population,
            area_km2,
            source_code,
        ),
    }


def _new_country_config_area_child_groups(
    country: DerivedCountry,
    children: list[dict],
    rows_by_tree_id: dict[str, dict],
    children_by_parent: dict[str, list[dict]],
    population_total,
    area_total,
    source_code: str,
) -> list[dict]:
    if not children:
        return []
    grouped: dict[int, list[dict]] = {}
    for child in children:
        try:
            level = int(child.get("level") or 0)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(level, []).append(child)
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "children": [
                _new_country_config_browser_row(
                    child,
                    rows_by_tree_id,
                    population_total,
                    area_total,
                    source_code,
                    country_slug=country.slug,
                    children_by_parent=children_by_parent,
                )
                for child in rows_for_level
            ],
        }
        for level, rows_for_level in sorted(grouped.items())
    ]


def _new_country_config_donut_payload(rows: list[dict], value_key: str, total) -> dict:
    items = []
    for row in rows:
        value = _application_browser_metrics._number_or_none(row.get(value_key))
        if not value:
            continue
        items.append(
            {
                "key": str(row.get("tree_id") or row.get("display_code") or row.get("entity_display_name") or ""),
                "label": str(row.get("entity_display_name") or row.get("display_name") or ""),
                "value": value,
            }
        )
    return {
        "type": "donut",
        "total": _application_browser_metrics._number_or_none(total),
        "items": items,
    }


def _new_country_config_first_order_cards(
    first_order: list[dict],
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> list[dict]:
    children_by_parent = children_by_parent or _new_country_config_children_by_parent(rows_by_tree_id.values())
    cards = []
    for row in first_order:
        payload = _new_country_config_browser_row(
            row,
            rows_by_tree_id,
            population_total,
            area_total,
            source_code,
            country_slug=country_slug,
            children_by_parent=children_by_parent,
        )
        config_child_count = len(children_by_parent.get(str(row.get("tree_id") or ""), []))
        if config_child_count:
            payload["child_count"] = config_child_count
        payload["children"] = []
        cards.append(payload)
    return cards


def _new_country_config_children_by_parent(rows) -> dict[str, list[dict]]:
    children_by_parent: dict[str, list[dict]] = {}
    for row in rows:
        parent_id = str(row.get("tree_parent") or "")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(row)
    for children in children_by_parent.values():
        children.sort(key=lambda row: str(row.get("entity_display_name") or "").casefold())
    return children_by_parent


def _new_country_config_share_row(
    row: dict,
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    *,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> dict:
    payload = _new_country_config_browser_row(
        row,
        rows_by_tree_id,
        population_total,
        area_total,
        source_code,
        country_slug=country_slug,
        children_by_parent=children_by_parent,
    )
    return {
        "name": payload["name"],
        "entity_type": payload["entity_type"],
        "population": payload["population"],
        "area_km2": payload["area_km2"],
        "population_percent": payload["population_percent"],
        "area_percent": payload["area_percent"],
        "child_count": payload.get("child_count", 0),
        "detail_url": payload.get("detail_url", ""),
    }


def _new_country_config_first_order_child_groups(
    first_order: list[dict],
    rows_by_tree_id: dict[str, dict],
    population_total,
    area_total,
    source_code: str,
    country_slug: str = "",
    children_by_parent: dict[str, list[dict]] | None = None,
) -> list[dict]:
    children_by_parent = children_by_parent or _new_country_config_children_by_parent(rows_by_tree_id.values())
    grouped: dict[int, list[dict]] = {}
    for row in first_order:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(level, []).append(row)
    return [
        {
            "level": level,
            "label": _("Nivel %(level)s") % {"level": level},
            "children": [
                _new_country_config_browser_row(
                    row,
                    rows_by_tree_id,
                    population_total,
                    area_total,
                    source_code,
                    country_slug=country_slug,
                    children_by_parent=children_by_parent,
                )
                for row in rows_for_level
            ],
        }
        for level, rows_for_level in sorted(grouped.items())
    ]
