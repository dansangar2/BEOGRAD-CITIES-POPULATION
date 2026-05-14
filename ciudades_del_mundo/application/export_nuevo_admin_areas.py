"""Application service for exporting derived administrative areas."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ciudades_del_mundo.domain.nuevo_admin_export import (
    CellValue,
    NuevoAdminAreaSummary,
    NuevoAdminExportData,
    Sheet,
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
    rank_area_country: dict[str, int] = {}
    rank_pop_country: dict[str, int] = {}

    for level, level_areas in areas_by_level.items():
        total_area_by_level[level] = sum(
            (area.area_km2 for area in level_areas if area.area_km2 is not None),
            Decimal("0"),
        )
        total_pop_by_level[level] = sum(
            int(area.pop_latest) for area in level_areas if area.pop_latest is not None
        )

        for idx, area in enumerate(
            sorted(level_areas, key=lambda item: item.area_km2 or Decimal("0"), reverse=True),
            start=1,
        ):
            rank_area_country[area.id] = idx

        for idx, area in enumerate(
            sorted(level_areas, key=lambda item: item.pop_latest or 0, reverse=True),
            start=1,
        ):
            rank_pop_country[area.id] = idx

    rank_area_parent: dict[str, int] = {}
    rank_pop_parent: dict[str, int] = {}
    for siblings in children_by_parent.values():
        for idx, area in enumerate(
            sorted(siblings, key=lambda item: item.area_km2 or Decimal("0"), reverse=True),
            start=1,
        ):
            rank_area_parent[area.id] = idx

        for idx, area in enumerate(
            sorted(siblings, key=lambda item: item.pop_latest or 0, reverse=True),
            start=1,
        ):
            rank_pop_parent[area.id] = idx

    levels_sorted = tuple(sorted({area.level for area in areas}))
    header, columns_by_level = _build_header(levels_sorted, root_level)
    paths = _build_paths(root, children_by_parent, max_level=max_level)
    parent_by_id = {area.id: area for area in areas}
    parent_by_id[root.id] = root

    first_level = root_level + 1
    root_children = [area for area in areas if area.level == first_level]
    country_area_total = sum(
        (area.area_km2 for area in root_children if area.area_km2 is not None),
        Decimal("0"),
    )
    country_pop_total = sum(
        int(area.pop_latest) for area in root_children if area.pop_latest is not None
    )

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
            ]
            previous_country_written = True
        else:
            row = [None, None, None]

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
                    root_level=root_level,
                    parent=parent_by_id.get(area.parent_id),
                    total_area_by_level=total_area_by_level,
                    total_pop_by_level=total_pop_by_level,
                    rank_area_country=rank_area_country,
                    rank_area_parent=rank_area_parent,
                    rank_pop_country=rank_pop_country,
                    rank_pop_parent=rank_pop_parent,
                    children_by_parent=children_by_parent,
                    seats_direct=seats_direct,
                    seats_total=seats_total,
                    rep_level=rep_level,
                )
            )

        rows.append(tuple(row))

    workbook = Workbook(
        sheets=(
            Sheet(
                name="NuevoAdminArea",
                rows=tuple(rows),
                freeze_panes="A2",
                auto_filter=True,
            ),
        ),
        properties={"title": f"NuevoAdminArea {root.name}"},
    )
    return workbook, max(len(rows) - 1, 0), levels_sorted


def _build_header(
    levels_sorted: tuple[int, ...],
    root_level: int,
) -> tuple[list[str], dict[int, list[str]]]:
    header = ["pais_nombre", "pais_area_total_km2", "pais_poblacion_total"]
    columns_by_level: dict[int, list[str]] = {}

    for level in levels_sorted:
        rel = level - root_level
        prefix = f"L{rel}"
        columns = [
            f"{prefix}_tipo",
            f"{prefix}_nombre",
            f"{prefix}_area_km2",
            f"{prefix}_pct_area_pais",
        ]
        if rel > 1:
            columns.append(f"{prefix}_pct_area_superior")
        columns.extend(
            [
                f"{prefix}_rank_area_pais",
                f"{prefix}_rank_area_superior",
                f"{prefix}_poblacion",
                f"{prefix}_pct_poblacion_pais",
            ]
        )
        if rel > 1:
            columns.append(f"{prefix}_pct_poblacion_superior")
        columns.extend(
            [
                f"{prefix}_rank_poblacion_pais",
                f"{prefix}_rank_poblacion_superior",
                f"{prefix}_num_subdivisiones_hijas",
                f"{prefix}_capitales_nombres",
                f"{prefix}_capitales_poblacion",
                f"{prefix}_capitales_pct_poblacion_subdivision",
                f"{prefix}_ciudad_mas_poblada_nombre",
                f"{prefix}_ciudad_mas_poblada_poblacion",
                f"{prefix}_ciudad_mas_poblada_pct_poblacion_subdivision",
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


def _build_level_block(
    *,
    area: NuevoAdminAreaSummary,
    root_level: int,
    parent: NuevoAdminAreaSummary | None,
    total_area_by_level: dict[int, Decimal],
    total_pop_by_level: dict[int, int],
    rank_area_country: dict[str, int],
    rank_area_parent: dict[str, int],
    rank_pop_country: dict[str, int],
    rank_pop_parent: dict[str, int],
    children_by_parent: dict[str | None, list[NuevoAdminAreaSummary]],
    seats_direct: dict[str, int | None],
    seats_total: dict[str, int],
    rep_level: int | None,
) -> list[CellValue]:
    rel = area.level - root_level
    area_value = area.area_km2
    pop_value = area.pop_latest

    block: list[CellValue] = [
        area.entity_type,
        area.name,
        _decimal_to_float(area_value),
        _percentage(area_value, total_area_by_level.get(area.level)),
    ]
    if rel > 1:
        block.append(_percentage(area_value, parent.area_km2 if parent else None))

    block.extend(
        [
            rank_area_country.get(area.id),
            rank_area_parent.get(area.id),
            pop_value,
            _percentage(pop_value, total_pop_by_level.get(area.level)),
        ]
    )
    if rel > 1:
        block.append(_percentage(pop_value, parent.pop_latest if parent else None))

    direct_children = children_by_parent.get(area.id, [])
    children_count = len(direct_children) if direct_children else area.source_units_count
    capitals_pop = sum(city.pop_latest or 0 for city in area.capitals)
    most_city_pop = area.most_populated_city.pop_latest if area.most_populated_city else None

    if rep_level is not None and area.level == rep_level:
        seats = seats_direct.get(area.id) or 0
    else:
        seats = seats_total.get(area.id, 0)

    block.extend(
        [
            rank_pop_country.get(area.id),
            rank_pop_parent.get(area.id),
            children_count or None,
            " | ".join(sorted(city.name for city in area.capitals)) or None,
            capitals_pop or None,
            _percentage(capitals_pop, pop_value),
            area.most_populated_city.name if area.most_populated_city else None,
            most_city_pop,
            _percentage(most_city_pop, pop_value),
            seats or None,
        ]
    )
    return block


def _percentage(value, total) -> float | None:
    if value is None or total in (None, 0):
        return None
    return round(float(value) / float(total) * 100.0, 2)


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)
