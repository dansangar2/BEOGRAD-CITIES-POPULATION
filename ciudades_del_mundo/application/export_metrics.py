"""Framework-free calculations and hierarchy rollups for derived exports."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from ciudades_del_mundo.domain.nuevo_admin_export import CellMerge, Sheet, Table


def _new_country_materialized_legal_count(data) -> int | None:
    source_unit_ids = set(data.root.source_unit_ids)
    for area in data.areas:
        source_unit_ids.update(area.source_unit_ids)
    if source_unit_ids:
        return len(source_unit_ids)
    if data.root.source_units_count:
        return data.root.source_units_count
    child_total = sum(area.source_units_count for area in data.areas if area.parent_id == data.root.id)
    return child_total or None



def _new_country_apply_legal_source_rollups(rows: list[dict]) -> None:
    if not rows:
        return
    children_by_parent = _new_country_export_children_by_parent(rows)
    cache: dict[str, set[str]] = {}

    def resolve(row: dict) -> set[str]:
        row_key = str(row.get("tree_id") or "")
        if row_key in cache:
            return set(cache[row_key])
        source_ids = {str(value) for value in row.get("_legal_source_unit_ids") or () if str(value)}
        for child in children_by_parent.get(row_key, []):
            source_ids.update(resolve(child))
        cache[row_key] = source_ids
        row["_legal_source_unit_ids"] = tuple(sorted(source_ids))
        row["legal_subdivision_count"] = len(source_ids) or None
        return set(source_ids)

    for row in rows:
        resolve(row)



def _new_country_export_children_by_parent(rows: list[dict]) -> dict[str, list[dict]]:
    if not rows:
        return {}
    country_row = next((row for row in rows if row.get("row_kind") == "country"), rows[0])
    country_key = str(country_row.get("tree_id") or "")
    known_ids = {str(row.get("tree_id") or "") for row in rows if str(row.get("tree_id") or "")}
    children_by_parent: dict[str, list[dict]] = {}
    for row in rows:
        if row is country_row:
            continue
        row_key = str(row.get("tree_id") or "")
        if not row_key:
            continue
        parent_key = str(row.get("tree_parent") or "")
        if not parent_key or parent_key not in known_ids:
            parent_key = country_key
        children_by_parent.setdefault(parent_key, []).append(row)
    return children_by_parent



def _new_country_chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]



def _new_country_export_fallback_legal_count(rows: list[dict]) -> int | None:
    source_unit_ids: set[str] = set()
    for row in rows:
        source_unit_ids.update(str(value) for value in row.get("_legal_source_unit_ids") or () if str(value))
    if source_unit_ids:
        return len(source_unit_ids)
    total = sum(int(row.get("legal_subdivision_count") or 0) for row in rows)
    return total or None



def _new_country_export_legal_context() -> dict:
    return {
        "area_units": {},
        "derived_units": {},
        "derived_source_ids": {},
        "group_entry": {},
        "group_entries": {},
    }



def _new_country_export_path_block(row: dict) -> tuple[object, ...]:
    return (
        row["name"],
        row["population"],
        row["population_pct_total"],
        row.get("population_rank"),
        row["area_km2"],
        row["area_pct_total"],
        row.get("area_rank"),
        row["density"],
        row["capitals"],
        row["capital_population"],
        row["capital_pct_entity"],
        row.get("capital_rank"),
        row["most_populated_city"],
        row["most_populated_population"],
        row["most_populated_pct_entity"],
        row.get("most_populated_rank"),
        row["legal_subdivision_count"],
    )



def _new_country_detail_export_sheet(
    name: str,
    rows: tuple[tuple[object, ...], ...],
    *,
    table_name: str,
    merged_cells: tuple[CellMerge, ...] = (),
) -> Sheet:
    tables: tuple[Table, ...] = ()
    if len(rows) > 1 and not merged_cells:
        column_count = max((len(row) for row in rows), default=1)
        ref = f"A1:{_new_country_excel_column_name(column_count)}{len(rows)}"
        tables = (Table(name=table_name, ref=ref, columns=tuple(str(value or "") for value in rows[0])),)
    return Sheet(
        name=str(name or "Hoja")[:31],
        rows=rows,
        freeze_panes="A2",
        auto_filter=not bool(merged_cells),
        tables=tables,
        merged_cells=merged_cells,
        center_cells=True,
        auto_column_widths=True,
    )



def _new_country_export_parent_label(row: dict, rows_by_tree_id: dict[str, dict]) -> str:
    parent = rows_by_tree_id.get(str(row.get("tree_parent") or ""))
    if not parent:
        return ""
    code = str(parent.get("display_code") or "").strip()
    name = str(parent.get("entity_display_name") or "").strip()
    if code and name:
        return f"{code} - {name}"
    return code or name



def _new_country_export_decimal(value) -> Decimal | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    text = text.replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None



def _new_country_export_percentage(value, total) -> float | None:
    value = _new_country_export_decimal(value)
    total = _new_country_export_decimal(total)
    if value is None or total in (None, 0):
        return None
    return round(float(value / total * Decimal("100")), 2)



def _new_country_excel_column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"



def _safe_excel_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    filename = re.sub(r"_+", "_", filename).strip("._") or "new_country_export.xlsx"
    if not filename.lower().endswith(".xlsx"):
        filename = f"{filename}.xlsx"
    return filename

