"""Application service for exporting derived administrative areas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Protocol

from ciudades_del_mundo.domain.nuevo_admin_export import (
    CellValue,
    NuevoAdminAreaSummary,
    NuevoAdminExportData,
    Sheet,
    Table,
    Workbook,
)


class NuevoAdminAreaExportRepository(Protocol):
    def get_export_data(
        self,
        country_id: str,
        max_level: int | None = None,
    ) -> NuevoAdminExportData:
        ...


class WorkbookWriter(Protocol):
    def write(self, workbook: Workbook, path: Path) -> None:
        ...


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
    areas = list(data.areas)
    root_level = root.level or 0

    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]] = defaultdict(list)
    for area in areas:
        children_by_parent[area.parent_id].append(area)

    seats_direct = {
        area.id: area.representatives
        for area in areas
        if area.representatives is not None
    }
    first_rep = next((area for area in areas if area.representatives is not None), None)
    rep_level = first_rep.level if first_rep else None

    seats_total: dict[str, int] = {}
    for area in sorted(areas, key=lambda item: item.level or 0, reverse=True):
        total = seats_direct.get(area.id, 0) or 0
        for child in children_by_parent.get(area.id, []):
            total += seats_total.get(child.id, 0)
        seats_total[area.id] = total

    areas_by_level: dict[int, list[NuevoAdminAreaSummary]] = defaultdict(list)
    for area in areas:
        areas_by_level[area.level].append(area)

    total_area_by_level: dict[int, Decimal] = {}
    total_pop_by_level: dict[int, int] = {}

    for level, level_areas in areas_by_level.items():
        total_area_by_level[level] = sum(
            (area.area_km2 for area in level_areas if area.area_km2 is not None),
            Decimal("0"),
        )
        total_pop_by_level[level] = sum(
            int(area.pop_latest) for area in level_areas if area.pop_latest is not None
        )

    levels_sorted = tuple(sorted({area.level for area in areas}))
    header, columns_by_level = _build_header(levels_sorted, root_level)
    paths = _build_paths(root, children_by_parent, max_level=max_level)
    all_for_population = [root] + areas
    effective_population_indexes = _effective_population_indexes(all_for_population)
    weighted_populations = {
        area.id: _scaled_population(
            area.pop_latest,
            effective_population_indexes.get(area.id, Decimal("1")),
        )
        for area in all_for_population
    }
    territory_flags = _territory_flags(all_for_population)
    representation_groups = _build_representation_groups(
        areas=areas,
        rep_level=rep_level,
        weighted_populations=weighted_populations,
        territory_flags=territory_flags,
    )

    first_level = root_level + 1
    root_children = [area for area in areas if area.level == first_level]
    country_area_total = sum(
        (area.area_km2 for area in root_children if area.area_km2 is not None),
        Decimal("0"),
    )
    country_pop_total = sum(
        int(area.pop_latest) for area in root_children if area.pop_latest is not None
    )
    country_representation_pop_total = representation_groups["country_population"]

    rows: list[tuple[CellValue, ...]] = [tuple(header)]
    previous_country_written = False
    previous_area_by_level: dict[int, NuevoAdminAreaSummary] = {}

    for path in paths:
        path_by_level = {area.level: area for area in path}
        if not previous_country_written:
            row: list[CellValue] = [
                root.name,
                _decimal_to_float(country_area_total) if country_area_total else None,
                country_pop_total or None,
                country_representation_pop_total,
            ]
            previous_country_written = True
        else:
            row = [None, None, None, None]

        for level in levels_sorted:
            cols_for_level = columns_by_level[level]
            area = path_by_level.get(level)
            if not area:
                row.extend([None] * len(cols_for_level))
                continue

            previous_area = previous_area_by_level.get(level)
            if previous_area is not None and previous_area.id == area.id:
                row.extend([None] * len(cols_for_level))
                continue
            previous_area_by_level[level] = area

            row.extend(
                _build_level_block(
                    area=area,
                    total_area_by_level=total_area_by_level,
                    total_pop_by_level=total_pop_by_level,
                    children_by_parent=children_by_parent,
                    seats_direct=seats_direct,
                    seats_total=seats_total,
                    rep_level=rep_level,
                    effective_population_indexes=effective_population_indexes,
                    weighted_populations=weighted_populations,
                    representation_groups=representation_groups,
                )
            )

        rows.append(tuple(row))

    main_rows_count = len(rows)
    main_columns_count = len(header)
    secondary_tables = _build_secondary_tables(
        areas=areas,
        children_by_parent=children_by_parent,
        seats_total=seats_total,
    )
    sheet_rows, secondary_table_refs = _append_columns(
        rows,
        secondary_tables,
        gap_columns=2,
    )
    main_table_ref = f"A1:{_column_name(main_columns_count)}{main_rows_count}"
    tables = (
        Table(
            name="TablaPrincipal",
            ref=main_table_ref,
            columns=tuple(header),
        ),
        *secondary_table_refs,
    )

    workbook = Workbook(
        sheets=(
            Sheet(
                name="NuevoAdminArea",
                rows=tuple(sheet_rows),
                freeze_panes="A2",
                auto_filter=False,
                tables=tables,
            ),
        ),
        properties={"title": f"NuevoAdminArea {root.name}"},
    )
    return workbook, max(len(rows) - 1, 0), levels_sorted


def _build_header(
    levels_sorted: tuple[int, ...],
    root_level: int,
) -> tuple[list[str], dict[int, list[str]]]:
    header = [
        "pais_nombre",
        "pais_area_total_km2",
        "pais_poblacion_total",
        "pais_poblacion_computo_escanos",
    ]
    columns_by_level: dict[int, list[str]] = {}

    for level in levels_sorted:
        rel = level - root_level
        prefix = f"L{rel}"
        columns = [
            f"{prefix}_tipo",
            f"{prefix}_nombre",
            f"{prefix}_estado_provincia",
            f"{prefix}_depende_de",
            f"{prefix}_indice_poblacion",
            f"{prefix}_indice_poblacion_efectivo",
            f"{prefix}_area_km2",
            f"{prefix}_pct_area_pais",
        ]
        columns.extend(
            [
                f"{prefix}_poblacion",
                f"{prefix}_poblacion_ponderada",
                f"{prefix}_pct_poblacion_pais",
            ]
        )
        columns.extend(
            [
                f"{prefix}_num_subdivisiones_hijas",
                f"{prefix}_capitales_nombres",
                f"{prefix}_capitales_poblacion",
                f"{prefix}_ciudad_mas_poblada_nombre",
                f"{prefix}_ciudad_mas_poblada_poblacion",
                f"{prefix}_grupo_representacion",
                f"{prefix}_poblacion_computo_escanos",
                f"{prefix}_representantes_grupo",
                f"{prefix}_representantes",
            ]
        )
        columns_by_level[level] = columns
        header.extend(columns)

    return header, columns_by_level


def _build_paths(
    root: NuevoAdminAreaSummary,
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    max_level: int | None = None,
) -> list[list[NuevoAdminAreaSummary]]:
    paths: list[list[NuevoAdminAreaSummary]] = []

    def dfs(node: NuevoAdminAreaSummary, path: list[NuevoAdminAreaSummary]) -> None:
        new_path = path + [node]
        children = children_by_parent.get(node.id, [])
        if max_level is not None:
            children = [child for child in children if child.level <= max_level]

        if not children:
            paths.append(new_path)
            return

        for child in sorted(children, key=lambda item: (item.level or 0, item.code)):
            dfs(child, new_path)

    root_children = children_by_parent.get(root.id, [])
    if max_level is not None:
        root_children = [child for child in root_children if child.level <= max_level]

    for child in sorted(root_children, key=lambda item: (item.level or 0, item.code)):
        dfs(child, [])

    return paths


def _build_secondary_tables(
    *,
    areas: list[NuevoAdminAreaSummary],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    seats_total: dict[str, int],
) -> list[list[tuple[CellValue, ...]]]:
    tables: list[list[tuple[CellValue, ...]]] = []
    for parent in sorted(areas, key=lambda item: (item.level or 0, item.code)):
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
        for child in sorted(children, key=lambda item: (item.level or 0, item.code)):
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


def _decimal_to_float(value: Decimal | None, *, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
