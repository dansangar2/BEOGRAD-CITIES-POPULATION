"""Compose export workbooks from SQL selectors and application calculations."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from ciudades_del_mundo.application import export_metrics as _application_export_metrics

from decimal import Decimal
from django.db.models import Q
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from ciudades_del_mundo.domain.nuevo_admin_export import CellMerge, Workbook
from ciudades_del_mundo.application.export_nuevo_admin_areas import build_nuevo_admin_workbook
from ciudades_del_mundo.infrastructure.django.nuevo_admin_area_export_repository import DjangoNuevoAdminAreaExportRepository
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.forms import new_countries as _forms_new_countries
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web import view_support as _view_support


def _new_country_detail_materialized_export_workbook(
    country: DerivedCountry,
    root: NuevoAdminArea,
) -> tuple[Workbook, list[dict], dict]:
    data = DjangoNuevoAdminAreaExportRepository().get_export_data(root.id)
    hierarchy_workbook, _data_rows, levels = build_nuevo_admin_workbook(data)
    hierarchy_sheet = hierarchy_workbook.sheets[0]
    export_rows = [
        {
            "level": area.level,
            "row_kind": "materialized",
            "tree_id": area.id,
            "tree_parent": area.parent_id or "",
        }
        for area in (data.root, *data.areas)
    ]
    summary = {
        "total_area_km2": data.root.area_km2,
        "total_population": int(data.root.pop_latest) if data.root.pop_latest is not None else None,
        "legal_subdivision_count": _application_export_metrics._new_country_materialized_legal_count(data),
        "levels": tuple(levels),
        "log_pending_label": _("Se guardara al completar la descarga"),
    }
    return (
        Workbook(
            sheets=(hierarchy_sheet,),
            properties={"title": f"{country.name} - new country export"},
        ),
        export_rows,
        summary,
    )


def _new_country_detail_export_headers() -> tuple[str, ...]:
    return _new_country_export_path_headers(0)


def _new_country_detail_export_rows(country: DerivedCountry, rows: list[dict]) -> tuple[list[dict], dict]:
    total_area, total_population = _new_country_detail_export_totals(country, rows)
    rows_by_tree_id = {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}
    raw_export_rows: list[dict] = []
    legal_context = _application_export_metrics._new_country_export_legal_context()
    for row in rows:
        area_value = _application_export_metrics._new_country_export_decimal(row.get("entity_area_value"))
        if area_value is None:
            area_value = _application_export_metrics._new_country_export_decimal(row.get("entity_area_text"))
        population_value = _application_export_metrics._new_country_export_decimal(row.get("entity_population_value"))
        if population_value is None:
            population_value = _application_export_metrics._new_country_export_decimal(row.get("entity_population_text"))
        density_value = _application_export_metrics._new_country_export_decimal(row.get("entity_density_value"))
        if density_value is None:
            density_value = _application_export_metrics._new_country_export_decimal(row.get("entity_density_text"))
        if density_value is None:
            density_value = _application_export_metrics._new_country_export_decimal(_application_browser_metrics._density(population_value, area_value))
        legal_source_unit_ids = _new_country_export_legal_source_unit_ids(row, legal_context)
        capital_population = _new_country_export_capital_population(row)
        most_populated_city, most_populated_population = _new_country_export_most_populated_city(row)
        raw_export_rows.append(
            {
                "level": int(row.get("level") or 0),
                "depth": int(row.get("depth") or 0),
                "code": str(row.get("display_code") or ""),
                "name": str(row.get("entity_display_name") or ""),
                "type": str(row.get("entity_type") or ""),
                "parent": _application_export_metrics._new_country_export_parent_label(row, rows_by_tree_id),
                "area_km2": area_value,
                "area_pct_total": _application_export_metrics._new_country_export_percentage(area_value, total_area),
                "population": int(population_value) if population_value is not None else None,
                "population_pct_total": _application_export_metrics._new_country_export_percentage(population_value, total_population),
                "density": density_value,
                "legal_subdivision_count": len(legal_source_unit_ids) or None,
                "_legal_source_unit_ids": tuple(sorted(legal_source_unit_ids)),
                "capitals": _new_country_export_capitals(row),
                "capital_population": capital_population,
                "capital_pct_entity": None,
                "most_populated_city": most_populated_city,
                "most_populated_population": most_populated_population,
                "most_populated_pct_entity": None,
                "row_kind": str(row.get("row_kind") or ""),
                "tree_id": str(row.get("tree_id") or ""),
                "tree_parent": str(row.get("tree_parent") or ""),
                "sort_kind": int(row.get("sort_kind") or 0),
                "sort_index": int(row.get("sort_index") or 0),
            }
        )
        raw_export_rows[-1]["capital_pct_entity"] = _application_export_metrics._new_country_export_percentage(
            raw_export_rows[-1]["capital_population"],
            raw_export_rows[-1]["population"],
        )
        raw_export_rows[-1]["most_populated_pct_entity"] = _application_export_metrics._new_country_export_percentage(
            raw_export_rows[-1]["most_populated_population"],
            raw_export_rows[-1]["population"],
        )
    export_rows = _new_country_export_ordered_rows(
        country,
        raw_export_rows,
        total_area=total_area,
        total_population=total_population,
    )
    _new_country_finalize_export_rows(export_rows)
    levels = tuple(sorted({row["level"] for row in export_rows if row["level"] > 0}))
    summary = {
        "total_area_km2": total_area,
        "total_population": int(total_population) if total_population is not None else None,
        "legal_subdivision_count": _application_export_metrics._new_country_export_fallback_legal_count(export_rows),
        "levels": levels,
        "log_pending_label": _("Se guardara al completar la descarga"),
    }
    return export_rows, summary


def _new_country_export_ordered_rows(
    country: DerivedCountry,
    rows: list[dict],
    *,
    total_area: Decimal | None,
    total_population: Decimal | None,
) -> list[dict]:
    country_row = {
        "level": 0,
        "depth": 0,
        "code": country.slug,
        "name": country.name,
        "type": _("Pais nuevo"),
        "parent": "",
        "area_km2": total_area,
        "area_pct_total": 100.0 if total_area not in (None, 0) else None,
        "population": int(total_population) if total_population is not None else None,
        "population_pct_total": 100.0 if total_population not in (None, 0) else None,
        "density": _application_browser_metrics._density(total_population, total_area),
        "legal_subdivision_count": _application_export_metrics._new_country_export_fallback_legal_count(rows),
        "_legal_source_unit_ids": (),
        "capitals": "",
        "capital_population": None,
        "capital_pct_entity": None,
        "most_populated_city": "",
        "most_populated_population": None,
        "most_populated_pct_entity": None,
        "row_kind": "country",
        "tree_id": f"country:{country.slug}",
        "tree_parent": "",
        "sort_kind": -1,
        "sort_index": 0,
    }
    children_by_parent: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for row in rows:
        parent_key = str(row.get("tree_parent") or "")
        if parent_key:
            children_by_parent.setdefault(parent_key, []).append(row)
        else:
            roots.append(row)

    ordered = [country_row]
    seen = set()
    current_layer = []
    for row in sorted(roots, key=_new_country_export_sort_key):
        item = dict(row)
        item["depth"] = 1
        ordered.append(item)
        seen.add(str(item.get("tree_id") or ""))
        current_layer.append(item)

    while current_layer:
        next_layer = []
        for parent in current_layer:
            parent_key = str(parent.get("tree_id") or "")
            for child in sorted(children_by_parent.get(parent_key, []), key=_new_country_export_sort_key):
                child_key = str(child.get("tree_id") or "")
                if child_key in seen:
                    continue
                item = dict(child)
                item["depth"] = int(parent.get("depth") or 0) + 1
                ordered.append(item)
                seen.add(child_key)
                next_layer.append(item)
        current_layer = next_layer

    for row in sorted(rows, key=_new_country_export_sort_key):
        row_key = str(row.get("tree_id") or "")
        if row_key in seen:
            continue
        item = dict(row)
        item["depth"] = max(1, int(item.get("depth") or 0))
        ordered.append(item)
        seen.add(row_key)
    return ordered


def _new_country_finalize_export_rows(rows: list[dict]) -> None:
    _application_export_metrics._new_country_apply_legal_source_rollups(rows)
    _new_country_apply_most_populated_from_legal_units(rows)
    for row in rows:
        row["capital_pct_entity"] = _application_export_metrics._new_country_export_percentage(
            row.get("capital_population"),
            row.get("population"),
        )
        row["most_populated_pct_entity"] = _application_export_metrics._new_country_export_percentage(
            row.get("most_populated_population"),
            row.get("population"),
        )
    _new_country_apply_export_rankings(rows)


def _new_country_apply_most_populated_from_legal_units(rows: list[dict]) -> None:
    source_ids = {
        str(value)
        for row in rows
        for value in row.get("_legal_source_unit_ids") or ()
        if str(value)
    }
    if not source_ids:
        return
    ranked_source_rows: list[tuple[str, str, int]] = []
    for chunk in _application_export_metrics._new_country_chunks(sorted(source_ids), 900):
        ranked_source_rows.extend(
            (str(area_id), str(name or ""), int(population))
            for area_id, name, population in AdminArea.objects.filter(
                id__in=chunk,
                pop_latest__isnull=False,
            ).values_list("id", "name", "pop_latest")
            if population is not None
        )
    ranked_source_rows.sort(
        key=lambda item: (-item[2], _queries_groups._group_name_match_key(item[1]), item[0])
    )
    for row in rows:
        if row.get("most_populated_population") is not None and row.get("most_populated_city"):
            continue
        row_source_ids = {str(value) for value in row.get("_legal_source_unit_ids") or () if str(value)}
        for source_id, name, population in ranked_source_rows:
            if source_id not in row_source_ids:
                continue
            row["most_populated_city"] = name
            row["most_populated_population"] = population
            break


def _new_country_apply_export_rankings(rows: list[dict]) -> None:
    ranking_specs = (
        ("population", "population_rank"),
        ("area_km2", "area_rank"),
        ("capital_population", "capital_rank"),
        ("most_populated_population", "most_populated_rank"),
    )
    rows_by_level: dict[int, list[dict]] = {}
    for row in rows:
        rows_by_level.setdefault(int(row.get("level") or 0), []).append(row)
    for value_key, rank_key in ranking_specs:
        for level_rows in rows_by_level.values():
            ranked = sorted(
                (row for row in level_rows if _application_export_metrics._new_country_export_decimal(row.get(value_key)) is not None),
                key=lambda row: (
                    -float(_application_export_metrics._new_country_export_decimal(row.get(value_key)) or 0),
                    _queries_groups._group_name_match_key(str(row.get("name") or "")),
                    str(row.get("code") or ""),
                ),
            )
            for index, row in enumerate(ranked, start=1):
                row[rank_key] = index


def _new_country_export_sort_key(row: dict) -> tuple[int, int, str, str]:
    return (
        int(row.get("level") or 0),
        int(row.get("sort_kind") or 0),
        _queries_groups._group_name_match_key(str(row.get("name") or "")),
        str(row.get("code") or ""),
    )


def _new_country_detail_export_sheet_rows(rows: list[dict]) -> tuple[tuple[object, ...], ...]:
    sheet_rows, _merged_cells = _new_country_detail_export_sheet_rows_and_merges(rows)
    return sheet_rows


def _new_country_detail_export_sheet_rows_and_merges(
    rows: list[dict],
) -> tuple[tuple[tuple[object, ...], ...], tuple[CellMerge, ...]]:
    paths = _new_country_export_path_rows(rows)
    max_depth = max((len(path) - 1 for path in paths), default=0)
    block_count = max_depth + 1
    sheet_rows: list[list[object]] = [list(_new_country_export_path_headers(max_depth))]
    for path in paths:
        row: list[object] = []
        for item in path:
            row.extend(_application_export_metrics._new_country_export_path_block(item))
        row.extend([None] * (_view_support._NEW_COUNTRY_EXPORT_BLOCK_SIZE * (block_count - len(path))))
        sheet_rows.append(row)
    merged_cells = _new_country_merge_repeated_path_blocks(sheet_rows, paths, block_count)
    return tuple(tuple(row) for row in sheet_rows), tuple(merged_cells)


def _new_country_export_path_headers(max_depth: int) -> tuple[str, ...]:
    block_headers = (
        _("Nombre"),
        _("Poblacion"),
        "%",
        _("Ranking poblacion"),
        _("Terreno"),
        "%",
        _("Ranking terreno"),
        _("Densidad"),
        _("Capital"),
        _("Poblacion capital"),
        _("% capital"),
        _("Ranking capital"),
        _("Ciudad mas poblada"),
        _("Poblacion ciudad mas poblada"),
        _("% ciudad mas poblada"),
        _("Ranking ciudad mas poblada"),
        _("Num municipios"),
    )
    headers = list(block_headers)
    for depth in range(1, max_depth + 1):
        headers.extend(f"NV{depth} {header}" for header in block_headers)
    return tuple(headers)


def _new_country_export_path_rows(rows: list[dict]) -> tuple[tuple[dict, ...], ...]:
    if not rows:
        return ()

    country_row = next((row for row in rows if row.get("row_kind") == "country"), rows[0])
    country_key = str(country_row.get("tree_id") or "country")
    indexed_rows: list[dict] = []
    fallback_index = 0
    for row in rows:
        if row is country_row:
            continue
        item = dict(row)
        tree_id = str(item.get("tree_id") or "")
        if not tree_id:
            fallback_index += 1
            tree_id = f"row:{fallback_index}"
            item["tree_id"] = tree_id
        indexed_rows.append(item)

    known_ids = {country_key, *(str(row.get("tree_id") or "") for row in indexed_rows)}
    children_by_parent: dict[str, list[dict]] = {country_key: []}
    for row in indexed_rows:
        parent_key = str(row.get("tree_parent") or "")
        if parent_key and parent_key in known_ids:
            children_by_parent.setdefault(parent_key, []).append(row)
        else:
            children_by_parent.setdefault(country_key, []).append(row)

    paths: list[tuple[dict, ...]] = []
    seen = {country_key}

    def visit(row: dict, path: tuple[dict, ...]) -> None:
        row_key = str(row.get("tree_id") or "")
        if row_key:
            seen.add(row_key)
        children = [
            child
            for child in sorted(children_by_parent.get(row_key, []), key=_new_country_export_sort_key)
            if str(child.get("tree_id") or "") not in seen
        ]
        if not children:
            paths.append(path)
            return
        for child in children:
            visit(child, (*path, child))

    visit(country_row, (country_row,))
    for row in sorted(indexed_rows, key=_new_country_export_sort_key):
        row_key = str(row.get("tree_id") or "")
        if row_key in seen:
            continue
        visit(row, (country_row, row))
    return tuple(paths)


def _new_country_merge_repeated_path_blocks(
    rows: list[list[object]],
    paths: tuple[tuple[dict, ...], ...],
    block_count: int,
) -> list[CellMerge]:
    merged_cells: list[CellMerge] = []
    if len(paths) <= 1:
        return merged_cells
    for depth in range(block_count):
        start = 0
        while start < len(paths):
            if depth >= len(paths[start]):
                start += 1
                continue
            row_key = str(paths[start][depth].get("tree_id") or "")
            end = start + 1
            while (
                end < len(paths)
                and depth < len(paths[end])
                and str(paths[end][depth].get("tree_id") or "") == row_key
            ):
                end += 1
            if row_key and end - start > 1:
                first_row = start + 2
                last_row = end + 1
                first_column = depth * _view_support._NEW_COUNTRY_EXPORT_BLOCK_SIZE + 1
                for offset in range(_view_support._NEW_COUNTRY_EXPORT_BLOCK_SIZE):
                    column = first_column + offset
                    merged_cells.append(
                        CellMerge(
                            start_row=first_row,
                            start_column=column,
                            end_row=last_row,
                            end_column=column,
                        )
                    )
                    for row_index in range(start + 1, end):
                        rows[row_index + 1][column - 1] = None
            start = end
    return merged_cells


def _new_country_export_legal_source_unit_ids(row: dict, context: dict) -> set[str]:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, NuevoAdminArea):
        return set(entity_node.municipios_originales.values_list("id", flat=True))

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        return _new_country_legal_source_unit_ids_for_area_id(source_id, context=context)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            return _new_country_derived_subdivision_legal_source_unit_ids(country_code, record_key, context=context)

    config = row.get("config")
    if isinstance(config, DerivedCountryConfig):
        data = _queries_new_countries._new_country_config_toml(config)
        entity = _forms_new_countries._new_country_config_first_entity(data)
        source_code = _queries_new_countries._new_country_config_source_code(config)
        return _new_country_config_legal_source_unit_ids(data, entity, source_code, context=context)
    return set()


def _new_country_config_legal_source_unit_ids(
    data: dict,
    entity: dict,
    source_country_code: str,
    *,
    context: dict,
) -> set[str]:
    include_ids = _new_country_config_direct_legal_source_unit_ids(
        data,
        entity,
        source_country_code,
        "include",
        context=context,
    )
    include_ids.update(
        _new_country_config_derived_legal_source_unit_ids(
            entity,
            source_country_code,
            "include",
            context=context,
        )
    )
    subtract_ids = _new_country_config_direct_legal_source_unit_ids(
        data,
        entity,
        source_country_code,
        "subtract",
        context=context,
    )
    subtract_ids.update(
        _new_country_config_derived_legal_source_unit_ids(
            entity,
            source_country_code,
            "subtract",
            context=context,
        )
    )
    return include_ids - subtract_ids


def _new_country_config_direct_legal_source_unit_ids(
    data: dict,
    entity: dict,
    source_country_code: str,
    operation: str,
    *,
    context: dict,
) -> set[str]:
    source_country_code = query_parameters.normalize_country_key(source_country_code)
    ids: list[str] = []
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    selection_items = selection.get("items") if isinstance(selection, dict) else None
    if isinstance(selection_items, list):
        for item in selection_items:
            if not isinstance(item, dict):
                continue
            item_operation = str(item.get("operation") or "add").strip().lower()
            expected_operation = "add" if operation == "include" else "subtract"
            if item_operation != expected_operation:
                continue
            ids.extend(_forms_new_countries._new_country_config_text_values([item.get("id") or item.get("value")]))
    elif isinstance(selection, dict):
        key = "include_ids" if operation == "include" else "subtract_ids"
        ids.extend(_forms_new_countries._new_country_config_text_values(selection.get(key)))

    blocks = entity.get(operation) if isinstance(entity.get(operation), list) else []
    for block in blocks:
        if isinstance(block, dict):
            ids.extend(_forms_new_countries._new_country_config_text_values(block.get("ids")))

    result: set[str] = set()
    for area_id in ids:
        result.update(
            _new_country_legal_source_unit_ids_for_area_id(
                area_id,
                fallback_country_code=source_country_code,
                context=context,
            )
        )
    return result


def _new_country_config_derived_legal_source_unit_ids(
    entity: dict,
    source_country_code: str,
    operation: str,
    *,
    context: dict,
) -> set[str]:
    blocks = entity.get(operation) if isinstance(entity.get(operation), list) else []
    result: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or source_country_code)
        for key in _forms_new_countries._new_country_config_text_values(block.get("derived_subdivisions")):
            result.update(
                _new_country_derived_subdivision_legal_source_unit_ids(
                    block_country,
                    key,
                    context=context,
                )
            )
    return result


def _new_country_derived_subdivision_legal_source_unit_ids(
    country_code: str,
    record_key: str,
    *,
    context: dict,
) -> set[str]:
    country_code = query_parameters.normalize_country_key(country_code)
    record_key = str(record_key or "").strip()
    if not country_code or not record_key:
        return set()
    cache_key = (country_code, record_key.casefold())
    derived_cache: dict[tuple[str, str], set[str]] = context["derived_units"]
    if cache_key in derived_cache:
        return set(derived_cache[cache_key])
    record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(country_code, record_key)
    if record is None:
        return set()

    materialized_country, code_candidates = _queries_subdivisions._derived_subdivision_materialized_code_candidates(record)
    if materialized_country and code_candidates:
        materialized_rows = NuevoAdminArea.objects.filter(
            country_code__iexact=materialized_country,
            code__in=code_candidates,
        ).prefetch_related("municipios_originales")
        materialized_source_areas: list[AdminArea] = []
        for area in materialized_rows:
            materialized_source_areas.extend(area.municipios_originales.all())
        source_ids = _queries_new_countries._new_country_legal_source_unit_ids_for_source_areas(materialized_source_areas)
        if source_ids:
            derived_cache[cache_key] = set(source_ids)
            return source_ids

    source_ids = set(
        _queries_subdivision_sources._derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=context["derived_source_ids"],
            group_entry_cache=context["group_entry"],
            group_entries_cache=context["group_entries"],
        )
    )
    derived_cache[cache_key] = set(source_ids)
    return source_ids


def _new_country_legal_source_unit_ids_for_area_id(
    area_id: str,
    *,
    fallback_country_code: str = "",
    context: dict,
) -> set[str]:
    cache_key = (str(area_id or ""), query_parameters.normalize_country_key(fallback_country_code))
    area_cache: dict[tuple[str, str], set[str]] = context["area_units"]
    if cache_key in area_cache:
        return set(area_cache[cache_key])
    area = area_selectors.group_source_admin_areas().filter(id=area_id).only("id", "country_code", "level", "parent_id").first()
    if area is None and fallback_country_code:
        area = (
            area_selectors.group_source_admin_areas()
            .filter(country_code__iexact=fallback_country_code)
            .filter(Q(id=area_id) | Q(code__iexact=area_id))
            .only("id", "country_code", "level", "parent_id")
            .first()
        )
    if area is None:
        return set()
    source_ids = _new_country_legal_source_unit_ids_for_area(area)
    area_cache[cache_key] = set(source_ids)
    return source_ids


def _new_country_legal_source_unit_ids_for_area(area: AdminArea) -> set[str]:
    country_code = query_parameters.normalize_country_key(area.country_code)
    target_level = _queries_subdivision_capitals._derived_subdivision_capital_target_level(country_code)
    if target_level is None:
        target_level = int(area.level or 0)
    try:
        area_level = int(area.level or 0)
    except (TypeError, ValueError):
        area_level = 0
    if area_level > target_level:
        ancestor = _queries_subdivision_capitals._derived_subdivision_ancestor_at_level(area, target_level)
        return {str(ancestor.id)} if ancestor is not None else set()
    result: set[str] = set()
    seen: set[str] = set()
    stack = [area]
    while stack:
        current = stack.pop()
        current_id = str(current.id)
        if current_id in seen:
            continue
        seen.add(current_id)
        current_level = int(current.level or 0)
        if current_level == target_level:
            result.add(current_id)
            continue
        if current_level > target_level:
            continue
        children = list(
            area_selectors.group_source_admin_areas()
            .filter(parent_id=current.id)
            .only("id", "country_code", "level")
            .order_by("name", "id")
        )
        if children:
            stack.extend(children)
        elif current_level > 0:
            result.add(current_id)
    return result


def _new_country_detail_export_totals(
    country: DerivedCountry,
    rows: list[dict],
) -> tuple[Decimal | None, Decimal | None]:
    root = _new_country_detail_export_root(country, rows)
    if root is not None:
        area = _application_export_metrics._new_country_export_decimal(root.area_km2)
        population = _application_export_metrics._new_country_export_decimal(root.pop_latest)
        if area is not None or population is not None:
            return area, population

    top_rows = [
        row
        for row in rows
        if row.get("is_config_row") and not str(row.get("tree_parent") or "")
    ]
    if not top_rows:
        top_rows = [row for row in rows if row.get("is_config_row")]
    area_total = sum(
        (value for value in (_application_export_metrics._new_country_export_decimal(row.get("entity_area_value")) for row in top_rows) if value is not None),
        Decimal("0"),
    )
    population_total = sum(
        (
            value
            for value in (_application_export_metrics._new_country_export_decimal(row.get("entity_population_value")) for row in top_rows)
            if value is not None
        ),
        Decimal("0"),
    )
    return (area_total if area_total else None, population_total if population_total else None)


def _new_country_detail_export_root(country: DerivedCountry, rows: list[dict]) -> NuevoAdminArea | None:
    built_codes = []
    seen = set()
    for row in rows:
        config = row.get("config")
        if not isinstance(config, DerivedCountryConfig):
            continue
        code = _queries_new_countries._new_country_config_built_code(config)
        if code and code not in seen:
            seen.add(code)
            built_codes.append(code)
    for code in [country.slug, *built_codes]:
        root = (
            NuevoAdminArea.objects.filter(country_code=code, parent__isnull=True)
            .order_by("level", "id")
            .first()
        )
        if root is not None:
            return root
    return None


def _new_country_export_capitals(row: dict) -> str:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        names = _queries_browser._area_capital_display_names(entity_node, get_language())
        if names:
            return " | ".join(names)

    if row.get("is_config_row"):
        config = row.get("config")
        if isinstance(config, DerivedCountryConfig):
            data = _queries_new_countries._new_country_config_toml(config)
            entity = _forms_new_countries._new_country_config_first_entity(data)
            source_code = _queries_new_countries._new_country_config_source_code(config)
            return _new_country_export_capital_names_from_values(
                source_code,
                _queries_subdivision_capitals._derived_subdivision_capital_values(source_code, entity),
            )

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).first()
        if area is not None:
            names = _queries_browser._area_capital_display_names(area, get_language())
            if names:
                return " | ".join(names)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(country_code, record_key)
            if record is not None:
                data = _queries_subdivisions._derived_subdivision_record_toml(record)
                source_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
                return _new_country_export_capital_names_from_values(
                    source_code,
                    _queries_subdivision_capitals._derived_subdivision_capital_values(source_code, data),
                )
    return ""


def _new_country_export_capital_population(row: dict) -> int | None:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        return _new_country_export_capital_population_for_area(entity_node)

    if row.get("is_config_row"):
        config = row.get("config")
        if isinstance(config, DerivedCountryConfig):
            data = _queries_new_countries._new_country_config_toml(config)
            entity = _forms_new_countries._new_country_config_first_entity(data)
            source_code = _queries_new_countries._new_country_config_source_code(config)
            return _new_country_export_capital_population_from_values(
                _queries_subdivision_capitals._derived_subdivision_capital_values(source_code, entity)
            )

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).prefetch_related("capitals").first()
        if area is not None:
            return _new_country_export_capital_population_for_area(area)

    if ":derived:" in tree_id:
        derived_ref = tree_id.rsplit(":derived:", 1)[1]
        if ":" in derived_ref:
            country_code, record_key = derived_ref.split(":", 1)
            record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(country_code, record_key)
            if record is not None:
                data = _queries_subdivisions._derived_subdivision_record_toml(record)
                source_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
                return _new_country_export_capital_population_from_values(
                    _queries_subdivision_capitals._derived_subdivision_capital_values(source_code, data)
                )
    return None


def _new_country_export_capital_population_from_values(values: list[str]) -> int | None:
    clean_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not clean_values:
        return None
    populations = [
        area.pop_latest
        for area in AdminArea.objects.filter(id__in=clean_values)
        if area.pop_latest is not None
    ]
    return sum(populations) if populations else None


def _new_country_export_most_populated_city(row: dict) -> tuple[str, int | None]:
    entity_node = row.get("entity_node")
    if isinstance(entity_node, (AdminArea, NuevoAdminArea)):
        city = getattr(entity_node, "most_populate_city", None)
        if city is not None:
            return _queries_browser._area_display_name(city), city.pop_latest

    tree_id = str(row.get("tree_id") or "")
    if ":source:" in tree_id:
        source_id = tree_id.rsplit(":source:", 1)[1]
        area = AdminArea.objects.filter(id=source_id).select_related("most_populate_city").first()
        if area is not None and area.most_populate_city is not None:
            return _queries_browser._area_display_name(area.most_populate_city), area.most_populate_city.pop_latest
    return "", None


def _new_country_export_capital_names_from_values(country_code: str, values: list[str]) -> str:
    clean_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not clean_values:
        return ""
    areas = {
        area.id: area
        for area in AdminArea.objects.filter(id__in=clean_values).prefetch_related("capitals")
    }
    names = []
    seen = set()
    for value in clean_values:
        area = areas.get(value)
        name = _queries_browser._area_display_name(area) if area is not None else value
        marker = str(name or "").casefold()
        if marker and marker not in seen:
            seen.add(marker)
            names.append(name)
    return " | ".join(names)


def _new_country_export_capital_population_for_area(area) -> int | None:
    populations = [
        capital.pop_latest
        for capital in area.capitals.all()
        if capital.pop_latest is not None
    ]
    return sum(populations) if populations else None
