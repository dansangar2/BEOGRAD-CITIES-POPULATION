"""Application service for exporting derived administrative areas."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ciudades_del_mundo.domain.nuevo_admin_export import (
    CellMerge,
    CellValue,
    NuevoAdminExportData,
    NuevoAdminAreaSummary,
    Sheet,
    Table,
    Workbook,
)
from ciudades_del_mundo.ports import NuevoAdminAreaExportRepository, WorkbookWriter


@dataclass(frozen=True)
class NuevoAdminExcelExportResult:
    path: Path
    rows: int
    levels: tuple[int, ...]


class ExportNuevoAdminAreasToExcel:
    def __init__(
        self,
        repository: NuevoAdminAreaExportRepository,
        writer: WorkbookWriter,
    ):
        self.repository = repository
        self.writer = writer

    def run(
        self,
        *,
        country_id: str,
        output_path: Path,
        max_level: int | None = None,
    ) -> NuevoAdminExcelExportResult:
        data = self.repository.get_export_data(country_id, max_level=max_level)
        workbook, data_rows, levels = build_nuevo_admin_workbook(data, max_level=max_level)
        if data_rows:
            self.writer.write(workbook, output_path)
        return NuevoAdminExcelExportResult(path=output_path, rows=data_rows, levels=levels)


def build_nuevo_admin_workbook(
    data: NuevoAdminExportData,
    max_level: int | None = None,
) -> tuple[Workbook, int, tuple[int, ...]]:
    root = data.root
    areas = sorted(
        (area for area in data.areas if max_level is None or area.level <= max_level),
        key=_alphabetical_area_key,
    )
    root_level = root.level or 0

    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]] = defaultdict(list)
    for area in areas:
        children_by_parent[area.parent_id].append(area)

    legal_subdivision_counts = _legal_subdivision_counts(root, areas, children_by_parent)

    root_children = children_by_parent.get(root.id, [])
    country_area_total = _country_area_total(root, root_children)
    country_pop_total = _country_population_total(root, root_children)
    all_for_ranking = [root, *areas]
    population_rank_by_level = _rank_by_metric_by_level(
        all_for_ranking,
        lambda area: area.pop_latest,
    )
    area_rank_by_level = _rank_by_metric_by_level(
        all_for_ranking,
        lambda area: area.area_km2,
    )
    capital_rank_by_level = _rank_by_metric_by_level(
        all_for_ranking,
        lambda area: _city_population_total(area.capitals),
    )
    most_populated_rank_by_level = _rank_by_metric_by_level(
        all_for_ranking,
        lambda area: area.most_populated_city.pop_latest if area.most_populated_city else None,
    )

    hierarchy_paths = _hierarchy_leaf_paths(root, areas, children_by_parent)
    max_depth = max((len(path) - 1 for path in hierarchy_paths), default=0)
    block_count = max_depth + 1
    rows: list[list[CellValue]] = [list(_build_path_header(max_depth))]
    for path in hierarchy_paths:
        row: list[CellValue] = []
        for area in path:
            row.extend(
                _build_path_block(
                    area=area,
                    country_area_total=country_area_total,
                    country_pop_total=country_pop_total,
                    legal_subdivision_counts=legal_subdivision_counts,
                    population_rank_by_level=population_rank_by_level,
                    area_rank_by_level=area_rank_by_level,
                    capital_rank_by_level=capital_rank_by_level,
                    most_populated_rank_by_level=most_populated_rank_by_level,
                )
            )
        row.extend([None] * (_EXPORT_BLOCK_SIZE * (block_count - len(path))))
        rows.append(row)
    merged_cells = _merge_repeated_path_blocks(rows, hierarchy_paths, block_count)

    main_rows_count = len(rows)
    main_columns_count = len(rows[0])

    workbook = Workbook(
        sheets=(
            Sheet(
                name="Jerarquia",
                rows=tuple(tuple(row) for row in rows),
                freeze_panes="A2",
                auto_filter=False,
                tables=(),
                merged_cells=tuple(merged_cells),
                center_cells=True,
                auto_column_widths=True,
            ),
        ),
        properties={"title": f"NuevoAdminArea {root.name}"},
    )
    levels_sorted = tuple(sorted({area.level for area in (root, *areas)}))
    return workbook, max(len(rows) - 1, 0), tuple(level for level in levels_sorted if level > root_level)


_EXPORT_BLOCK_HEADERS = (
    "Nombre",
    "Población",
    "%",
    "Ranking población",
    "Terreno",
    "%",
    "Ranking terreno",
    "Densidad",
    "Capital",
    "Población capital",
    "% capital",
    "Ranking capital",
    "Ciudad más poblada",
    "Población ciudad más poblada",
    "% ciudad más poblada",
    "Ranking ciudad más poblada",
    "Num municipios",
)
_EXPORT_BLOCK_SIZE = len(_EXPORT_BLOCK_HEADERS)


def _build_path_header(max_depth: int) -> list[str]:
    headers = list(_EXPORT_BLOCK_HEADERS)
    for depth in range(1, max_depth + 1):
        headers.extend(f"NV{depth} {header}" for header in _EXPORT_BLOCK_HEADERS)
    return headers


def _hierarchy_leaf_paths(
    root: NuevoAdminAreaSummary,
    areas: list[NuevoAdminAreaSummary],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
) -> list[tuple[NuevoAdminAreaSummary, ...]]:
    paths: list[tuple[NuevoAdminAreaSummary, ...]] = []
    seen: set[str] = set()

    def visit(area: NuevoAdminAreaSummary, path: tuple[NuevoAdminAreaSummary, ...]) -> None:
        seen.add(area.id)
        children = [
            child
            for child in sorted(children_by_parent.get(area.id, []), key=_alphabetical_area_key)
            if child.id not in seen
        ]
        if not children:
            paths.append(path)
            return
        for child in children:
            visit(child, (*path, child))

    visit(root, (root,))
    for area in sorted(areas, key=_alphabetical_area_key):
        if area.id in seen:
            continue
        visit(area, (root, area))
    return paths


def _build_path_block(
    *,
    area: NuevoAdminAreaSummary,
    country_area_total: Decimal | None,
    country_pop_total: int | None,
    legal_subdivision_counts: dict[str, int],
    population_rank_by_level: dict[int, dict[str, int]],
    area_rank_by_level: dict[int, dict[str, int]],
    capital_rank_by_level: dict[int, dict[str, int]],
    most_populated_rank_by_level: dict[int, dict[str, int]],
) -> list[CellValue]:
    area_value = area.area_km2
    pop_value = area.pop_latest
    density_value = area.density
    if density_value is None:
        density_value = _density(pop_value, area_value)
    capitals = tuple(sorted(area.capitals, key=lambda city: _normalized_sort_text(city.name)))
    capital_population = _city_population_total(capitals)
    most_populated_city = area.most_populated_city
    most_populated_population = most_populated_city.pop_latest if most_populated_city else None
    return [
        area.name,
        pop_value,
        _percentage(pop_value, country_pop_total),
        population_rank_by_level.get(area.level, {}).get(area.id),
        _decimal_to_float(area_value),
        _percentage(area_value, country_area_total),
        area_rank_by_level.get(area.level, {}).get(area.id),
        _decimal_to_float(density_value, digits=4),
        " | ".join(city.name for city in capitals) or None,
        capital_population,
        _percentage(capital_population, pop_value),
        capital_rank_by_level.get(area.level, {}).get(area.id),
        most_populated_city.name if most_populated_city else None,
        most_populated_population,
        _percentage(most_populated_population, pop_value),
        most_populated_rank_by_level.get(area.level, {}).get(area.id),
        legal_subdivision_counts.get(area.id) or None,
    ]


def _city_population_total(cities) -> int | None:
    populations = [city.pop_latest for city in cities if city.pop_latest is not None]
    return sum(populations) if populations else None


def _rank_by_metric_by_level(
    areas: list[NuevoAdminAreaSummary],
    value_for,
) -> dict[int, dict[str, int]]:
    ranks_by_level: dict[int, dict[str, int]] = {}
    areas_by_level: dict[int, list[NuevoAdminAreaSummary]] = defaultdict(list)
    for area in areas:
        areas_by_level[area.level].append(area)
    for level, level_areas in areas_by_level.items():
        ranked = sorted(
            (area for area in level_areas if value_for(area) is not None),
            key=lambda area: (-float(value_for(area) or 0), _alphabetical_area_key(area)),
        )
        ranks_by_level[level] = {
            area.id: index
            for index, area in enumerate(ranked, start=1)
        }
    return ranks_by_level


def _merge_repeated_path_blocks(
    rows: list[list[CellValue]],
    paths: list[tuple[NuevoAdminAreaSummary, ...]],
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
            area_id = paths[start][depth].id
            end = start + 1
            while end < len(paths) and depth < len(paths[end]) and paths[end][depth].id == area_id:
                end += 1
            if end - start > 1:
                first_row = start + 2
                last_row = end + 1
                first_column = depth * _EXPORT_BLOCK_SIZE + 1
                for offset in range(_EXPORT_BLOCK_SIZE):
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


def _build_header() -> list[str]:
    return [
        "nivel",
        "profundidad",
        "codigo",
        "nombre",
        "tipo",
        "estado_provincia",
        "depende_de",
        "padre_codigo",
        "padre_nombre",
        "area_km2",
        "pct_area_pais",
        "poblacion",
        "ranking_poblacion_nivel",
        "poblacion_ponderada",
        "pct_poblacion_pais",
        "densidad",
        "subdivisiones_legales",
        "subdivisiones_hijas",
        "capitales_nombres",
        "capitales_poblacion",
        "ciudad_mas_poblada_nombre",
        "ciudad_mas_poblada_poblacion",
        "indice_poblacion",
        "indice_poblacion_efectivo",
        "grupo_representacion",
        "poblacion_computo_escanos",
        "representantes_grupo",
        "representantes",
    ]


def _hierarchy_ordered_rows(
    root: NuevoAdminAreaSummary,
    areas: list[NuevoAdminAreaSummary],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
) -> list[tuple[NuevoAdminAreaSummary, int]]:
    ordered: list[tuple[NuevoAdminAreaSummary, int]] = [(root, 0)]
    seen = {root.id}
    parent_layer: list[tuple[NuevoAdminAreaSummary, int]] = [(root, 0)]

    while parent_layer:
        next_layer: list[tuple[NuevoAdminAreaSummary, int]] = []
        for parent, depth in parent_layer:
            for child in sorted(children_by_parent.get(parent.id, []), key=_alphabetical_area_key):
                if child.id in seen:
                    continue
                seen.add(child.id)
                item = (child, depth + 1)
                ordered.append(item)
                next_layer.append(item)
        parent_layer = next_layer

    for area in sorted(areas, key=_alphabetical_area_key):
        if area.id in seen:
            continue
        seen.add(area.id)
        ordered.append((area, 0))

    return ordered


def _country_area_total(
    root: NuevoAdminAreaSummary,
    root_children: list[NuevoAdminAreaSummary],
) -> Decimal | None:
    if root.area_km2 is not None:
        return root.area_km2
    total = sum(
        (area.area_km2 for area in root_children if area.area_km2 is not None),
        Decimal("0"),
    )
    return total if total else None


def _country_population_total(
    root: NuevoAdminAreaSummary,
    root_children: list[NuevoAdminAreaSummary],
) -> int | None:
    if root.pop_latest is not None:
        return int(root.pop_latest)
    total = sum(int(area.pop_latest) for area in root_children if area.pop_latest is not None)
    return total or None


def _legal_subdivision_counts(
    root: NuevoAdminAreaSummary,
    areas: list[NuevoAdminAreaSummary],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
) -> dict[str, int]:
    by_id = {root.id: root, **{area.id: area for area in areas}}
    cache: dict[str, set[str]] = {}

    def resolve(area: NuevoAdminAreaSummary) -> set[str]:
        if area.id in cache:
            return cache[area.id]
        units = {str(unit_id) for unit_id in area.source_unit_ids if str(unit_id)}
        if not units and area.source_units_count:
            units = {f"{area.id}#source-unit-{index}" for index in range(area.source_units_count)}
        for child in children_by_parent.get(area.id, []):
            if child.id in by_id:
                units.update(resolve(child))
        cache[area.id] = units
        return units

    for area in by_id.values():
        resolve(area)
    return {area_id: len(unit_ids) for area_id, unit_ids in cache.items()}


def _build_area_row(
    *,
    area: NuevoAdminAreaSummary,
    depth: int,
    root_level: int,
    parent: NuevoAdminAreaSummary | None,
    country_area_total: Decimal | None,
    country_pop_total: int | None,
    country_representation_pop_total: int | None,
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    legal_subdivision_counts: dict[str, int],
    seats_direct: dict[str, int | None],
    seats_total: dict[str, int],
    rep_level: int | None,
    effective_population_indexes: dict[str, Decimal],
    weighted_populations: dict[str, int | None],
    representation_groups: dict[str, dict],
    population_rank_by_level: dict[int, dict[str, int]],
) -> list[CellValue]:
    area_value = area.area_km2
    pop_value = area.pop_latest
    density_value = area.density
    if density_value is None:
        density_value = _density(pop_value, area_value)

    direct_children = children_by_parent.get(area.id, [])
    capitals_pop = sum(city.pop_latest or 0 for city in area.capitals)
    most_city_pop = area.most_populated_city.pop_latest if area.most_populated_city else None

    if rep_level is not None and area.level == rep_level:
        seats = seats_direct.get(area.id) or 0
    else:
        seats = seats_total.get(area.id, 0)
    display_seats = seats if rep_level is not None and area.level == rep_level else seats or None

    group_owner_names = representation_groups["owner_names"]
    group_populations = representation_groups["populations"]
    group_seats = representation_groups["seats"]
    if rep_level is not None and area.level == rep_level:
        group_population = group_populations.get(area.id)
        group_seats_value = group_seats.get(area.id)
    elif area.level == root_level:
        group_population = country_representation_pop_total
        group_seats_value = None
    else:
        group_population = None
        group_seats_value = None

    return [
        area.level,
        depth,
        area.code,
        area.name,
        area.entity_type,
        _province_status_label(area.province_status),
        area.depends_on_name,
        parent.code if parent else None,
        parent.name if parent else None,
        _decimal_to_float(area_value),
        _percentage(area_value, country_area_total),
        pop_value,
        population_rank_by_level.get(area.level, {}).get(area.id),
        weighted_populations.get(area.id),
        _percentage(pop_value, country_pop_total),
        _decimal_to_float(density_value, digits=4),
        legal_subdivision_counts.get(area.id) or None,
        len(direct_children) or None,
        " | ".join(sorted(city.name for city in area.capitals)) or None,
        capitals_pop or None,
        area.most_populated_city.name if area.most_populated_city else None,
        most_city_pop,
        _decimal_to_float(area.population_index, digits=4),
        _decimal_to_float(effective_population_indexes.get(area.id), digits=4),
        group_owner_names.get(area.id),
        group_population,
        group_seats_value,
        display_seats,
    ]


def _build_secondary_tables(
    *,
    areas: list[NuevoAdminAreaSummary],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    seats_total: dict[str, int],
) -> list[list[tuple[CellValue, ...]]]:
    tables: list[list[tuple[CellValue, ...]]] = []
    for parent in sorted(areas, key=_alphabetical_area_key):
        children = children_by_parent.get(parent.id, [])
        if not children:
            continue

        table: list[tuple[CellValue, ...]] = [
            (parent.name, None, None, None, None, None, None),
            (
                "tipo",
                "nombre",
                "area_km2",
                "pct_area_subentidad",
                "poblacion",
                "pct_poblacion_subentidad",
                "representantes",
            ),
        ]
        for child in sorted(children, key=_alphabetical_area_key):
            table.append(
                (
                    child.entity_type,
                    child.name,
                    _decimal_to_float(child.area_km2),
                    _percentage(child.area_km2, parent.area_km2),
                    child.pop_latest,
                    _percentage(child.pop_latest, parent.pop_latest),
                    seats_total.get(child.id) or None,
                )
            )
        tables.append(table)

    return tables


def _alphabetical_area_key(area: NuevoAdminAreaSummary) -> tuple[int, str, str, str]:
    return (
        area.level or 0,
        _normalized_sort_text(area.name),
        _normalized_sort_text(area.code),
        area.id,
    )


def _normalized_sort_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(normalized.casefold().split())


def _append_columns(
    base_rows: list[tuple[CellValue, ...]],
    tables: list[list[tuple[CellValue, ...]]],
    *,
    gap_columns: int,
) -> tuple[list[tuple[CellValue, ...]], list[Table]]:
    rows = [list(row) for row in base_rows]
    if not tables:
        return [tuple(row) for row in rows], []

    table_refs: list[Table] = []
    for table_index, table in enumerate(tables, start=1):
        current_width = max((len(row) for row in rows), default=0)
        start_column = current_width + gap_columns + 1
        for row in rows:
            row.extend([None] * (current_width - len(row)))
            row.extend([None] * gap_columns)

        table_width = max((len(row) for row in table), default=0)
        required_rows = len(table)
        while len(rows) < required_rows:
            rows.append([None] * (current_width + gap_columns))

        for row_index, table_row in enumerate(table):
            rows[row_index].extend(table_row)
            rows[row_index].extend([None] * (table_width - len(table_row)))

        for row_index in range(required_rows, len(rows)):
            rows[row_index].extend([None] * table_width)

        if len(table) > 2 and table_width:
            end_column = start_column + table_width - 1
            table_refs.append(
                Table(
                    name=f"Subentidad_{table_index}",
                    ref=f"{_column_name(start_column)}2:{_column_name(end_column)}{len(table)}",
                    columns=_table_columns(table[1], table_width),
                )
            )

    return [tuple(row) for row in rows], table_refs


def _table_columns(row: tuple[CellValue, ...], width: int) -> tuple[str, ...]:
    columns: list[str] = []
    seen: dict[str, int] = {}
    for index in range(width):
        raw_value = row[index] if index < len(row) else None
        name = str(raw_value or f"columna_{index + 1}")
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count > 1:
            name = f"{name}_{count}"
        columns.append(name)
    return tuple(columns)


def _build_level_block(
    *,
    area: NuevoAdminAreaSummary,
    total_area_by_level: dict[int, Decimal],
    total_pop_by_level: dict[int, int],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    seats_direct: dict[str, int | None],
    seats_total: dict[str, int],
    rep_level: int | None,
    effective_population_indexes: dict[str, Decimal],
    weighted_populations: dict[str, int | None],
    representation_groups: dict[str, dict],
    population_rank_by_level: dict[int, dict[str, int]],
) -> list[CellValue]:
    area_value = area.area_km2
    pop_value = area.pop_latest

    block: list[CellValue] = [
        area.entity_type,
        area.name,
        _province_status_label(area.province_status),
        area.depends_on_name,
        _decimal_to_float(area.population_index, digits=4),
        _decimal_to_float(effective_population_indexes.get(area.id), digits=4),
        _decimal_to_float(area_value),
        _percentage(area_value, total_area_by_level.get(area.level)),
    ]

    block.extend(
        [
            pop_value,
            population_rank_by_level.get(area.level, {}).get(area.id),
            weighted_populations.get(area.id),
            _percentage(pop_value, total_pop_by_level.get(area.level)),
        ]
    )

    direct_children = children_by_parent.get(area.id, [])
    children_count = len(direct_children) if direct_children else area.source_units_count
    capitals_pop = sum(city.pop_latest or 0 for city in area.capitals)
    most_city_pop = area.most_populated_city.pop_latest if area.most_populated_city else None

    if rep_level is not None and area.level == rep_level:
        seats = seats_direct.get(area.id) or 0
    else:
        seats = seats_total.get(area.id, 0)
    display_seats = seats if rep_level is not None and area.level == rep_level else seats or None

    group_owner_names = representation_groups["owner_names"]
    group_populations = representation_groups["populations"]
    group_seats = representation_groups["seats"]
    group_population = group_populations.get(area.id)
    group_seats_value = group_seats.get(area.id)
    if rep_level is None or area.level != rep_level:
        group_population = None
        group_seats_value = None

    block.extend(
        [
            children_count or None,
            " | ".join(sorted(city.name for city in area.capitals)) or None,
            capitals_pop or None,
            area.most_populated_city.name if area.most_populated_city else None,
            most_city_pop,
            group_owner_names.get(area.id),
            group_population,
            group_seats_value,
            display_seats,
        ]
    )
    return block


def _rank_by_population_by_level(
    areas_by_level: dict[int, list[NuevoAdminAreaSummary]],
) -> dict[int, dict[str, int]]:
    ranks_by_level: dict[int, dict[str, int]] = {}

    for level, level_areas in areas_by_level.items():
        ranked = sorted(
            (area for area in level_areas if area.pop_latest is not None),
            key=lambda area: (-(area.pop_latest or 0), _alphabetical_area_key(area)),
        )
        ranks_by_level[level] = {
            area.id: index
            for index, area in enumerate(ranked, start=1)
        }

    return ranks_by_level


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _effective_population_indexes(
    areas: list[NuevoAdminAreaSummary],
) -> dict[str, Decimal]:
    areas_by_id = {area.id: area for area in areas}
    cache: dict[str, Decimal] = {}

    def resolve(area: NuevoAdminAreaSummary) -> Decimal:
        if area.id in cache:
            return cache[area.id]

        parent_factor = Decimal("1")
        if area.parent_id:
            parent = areas_by_id.get(area.parent_id)
            if parent is not None:
                parent_factor = resolve(parent)

        own_factor = area.population_index if area.population_index is not None else Decimal("1")
        cache[area.id] = parent_factor * Decimal(own_factor)
        return cache[area.id]

    for area in areas:
        resolve(area)

    return cache


def _territory_flags(areas: list[NuevoAdminAreaSummary]) -> dict[str, bool]:
    areas_by_id = {area.id: area for area in areas}
    cache: dict[str, bool] = {}

    def resolve(area: NuevoAdminAreaSummary) -> bool:
        if area.id in cache:
            return cache[area.id]

        is_territory = area.province_status == "territory"
        if not is_territory and area.parent_id:
            parent = areas_by_id.get(area.parent_id)
            if parent is not None:
                is_territory = resolve(parent)

        cache[area.id] = is_territory
        return is_territory

    for area in areas:
        resolve(area)

    return cache


def _build_representation_groups(
    *,
    areas: list[NuevoAdminAreaSummary],
    rep_level: int | None,
    weighted_populations: dict[str, int | None],
    territory_flags: dict[str, bool],
) -> dict[str, dict | int | None]:
    if rep_level is None:
        return {
            "owner_names": {},
            "populations": {},
            "seats": {},
            "country_population": None,
        }

    rep_areas = [area for area in areas if area.level == rep_level]
    rep_areas_by_id = {area.id: area for area in rep_areas}
    owner_by_area_id: dict[str, str | None] = {}
    population_by_owner_id: dict[str, int] = defaultdict(int)

    for area in rep_areas:
        if territory_flags.get(area.id, False):
            owner_by_area_id[area.id] = None
            continue

        owner_id = _representation_owner_id(area, rep_areas_by_id, territory_flags)
        owner_by_area_id[area.id] = owner_id
        population_by_owner_id[owner_id] += weighted_populations.get(area.id) or 0

    owner_names: dict[str, str | None] = {}
    group_populations: dict[str, int] = {}
    group_seats: dict[str, int] = {}

    for area in rep_areas:
        owner_id = owner_by_area_id.get(area.id)
        if owner_id is None:
            owner_names[area.id] = None
            group_populations[area.id] = 0
            group_seats[area.id] = 0
            continue

        owner = rep_areas_by_id[owner_id]
        owner_names[area.id] = owner.name
        group_populations[area.id] = population_by_owner_id.get(owner_id, 0)
        group_seats[area.id] = owner.representatives or 0

    return {
        "owner_names": owner_names,
        "populations": group_populations,
        "seats": group_seats,
        "country_population": sum(population_by_owner_id.values()),
    }


def _representation_owner_id(
    area: NuevoAdminAreaSummary,
    areas_by_id: dict[str, NuevoAdminAreaSummary],
    territory_flags: dict[str, bool],
) -> str:
    current = area
    seen: set[str] = set()

    while current.province_status == "dependency":
        if not current.depends_on_id:
            raise ValueError(
                f"La dependencia '{current.name}' debe indicar de que provincia depende."
            )
        if current.id in seen:
            raise ValueError(f"Dependencia circular detectada en '{area.name}'.")
        seen.add(current.id)

        owner = areas_by_id.get(current.depends_on_id)
        if owner is None:
            raise ValueError(
                f"La dependencia '{current.name}' apunta a '{current.depends_on_id}', "
                "que no existe en el nivel de representacion exportado."
            )
        if territory_flags.get(owner.id, False):
            raise ValueError(
                f"La dependencia '{current.name}' no puede depender del territorio '{owner.name}'."
            )
        current = owner

    return current.id


def _scaled_population(population: int | None, population_index: Decimal) -> int | None:
    if population is None:
        return None
    value = Decimal(max(population, 0)) * population_index
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _province_status_label(value: str | None) -> str:
    labels = {
        "normal": "Normal",
        "dependency": "Dependencia",
        "territory": "Territorio",
    }
    return labels.get(value or "normal", value or "Normal")


def _percentage(value, total) -> float | None:
    if value is None or total in (None, 0):
        return None
    return round(float(value) / float(total) * 100.0, 2)


def _density(population, area) -> Decimal | None:
    if population is None or area in (None, 0):
        return None
    try:
        return Decimal(population) / Decimal(area)
    except (ArithmeticError, ValueError):
        return None


def _decimal_to_float(value: Decimal | None, *, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
