from __future__ import annotations

import html
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ciudades_del_mundo.domain.nuevo_admin_export import CellValue, Sheet, Workbook


class SimpleXlsxWriter:
    """Small XLSX writer for tabular exports, with no third-party dependency."""

    def write(self, workbook: Workbook, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _content_types(workbook))
            archive.writestr("_rels/.rels", _root_rels())
            archive.writestr("docProps/core.xml", _core_props(workbook))
            archive.writestr("docProps/app.xml", _app_props(workbook))
            archive.writestr("xl/workbook.xml", _workbook_xml(workbook))
            archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(workbook))
            archive.writestr("xl/styles.xml", _styles_xml())
            for idx, sheet in enumerate(workbook.sheets, start=1):
                archive.writestr(f"xl/worksheets/sheet{idx}.xml", _sheet_xml(sheet))


def _content_types(workbook: Workbook) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx, _sheet in enumerate(workbook.sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{sheet_overrides}</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(workbook: Workbook) -> str:
    sheets = "".join(
        f'<sheet name="{_xml(sheet.name[:31])}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, sheet in enumerate(workbook.sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _workbook_rels(workbook: Workbook) -> str:
    sheets = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{idx}.xml"/>'
        for idx, _sheet in enumerate(workbook.sheets, start=1)
    )
    style_id = len(workbook.sheets) + 1
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{sheets}"
        f'<Relationship Id="rId{style_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def _sheet_xml(sheet: Sheet) -> str:
    rows_xml = "".join(_row_xml(row, row_idx) for row_idx, row in enumerate(sheet.rows, start=1))
    max_row = len(sheet.rows)
    max_col = max((len(row) for row in sheet.rows), default=1)
    dimension = f"A1:{_column_name(max_col)}{max(max_row, 1)}"
    views = _sheet_views(sheet.freeze_panes)
    auto_filter = f'<autoFilter ref="{dimension}"/>' if sheet.auto_filter and max_row > 1 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        f"{views}"
        f"<sheetData>{rows_xml}</sheetData>"
        f"{auto_filter}"
        "</worksheet>"
    )


def _sheet_views(freeze_panes: str | None) -> str:
    if not freeze_panes:
        return ""
    match = re.fullmatch(r"([A-Z]+)([0-9]+)", freeze_panes.upper())
    if not match:
        return ""
    column, row = match.groups()
    x_split = _column_index(column) - 1
    y_split = int(row) - 1
    return (
        "<sheetViews><sheetView workbookViewId=\"0\">"
        f'<pane xSplit="{x_split}" ySplit="{y_split}" topLeftCell="{freeze_panes.upper()}" '
        'activePane="bottomRight" state="frozen"/>'
        "</sheetView></sheetViews>"
    )


def _row_xml(row: tuple[CellValue, ...], row_idx: int) -> str:
    cells = "".join(_cell_xml(value, row_idx, col_idx) for col_idx, value in enumerate(row, start=1))
    return f'<row r="{row_idx}">{cells}</row>'


def _cell_xml(value: CellValue, row_idx: int, col_idx: int) -> str:
    if value is None or value == "":
        return ""

    ref = f"{_column_name(col_idx)}{row_idx}"
    style = ' s="1"' if row_idx == 1 else ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style}><v>{int(value)}</v></c>'
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'

    return f'<c r="{ref}" t="inlineStr"{style}><is><t>{_xml(str(value))}</t></is></c>'


def _core_props(workbook: Workbook) -> str:
    title = ""
    if workbook.properties:
        title = str(workbook.properties.get("title") or "")
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_xml(title)}</dc:title>"
        "<dc:creator>ciudades_del_mundo</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_props(workbook: Workbook) -> str:
    sheet_count = len(workbook.sheets)
    sheet_names = "".join(f"<vt:lpstr>{_xml(sheet.name)}</vt:lpstr>" for sheet in workbook.sheets)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>ciudades_del_mundo</Application>"
        '<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>'
        f"<vt:variant><vt:i4>{sheet_count}</vt:i4></vt:variant></vt:vector></HeadingPairs>"
        f'<TitlesOfParts><vt:vector size="{sheet_count}" baseType="lpstr">{sheet_names}</vt:vector></TitlesOfParts>'
        "</Properties>"
    )


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _column_index(name: str) -> int:
    total = 0
    for char in name:
        total = total * 26 + ord(char) - 64
    return total


def _xml(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch in ("\t", "\n", "\r") or ord(ch) >= 32)
    return html.escape(cleaned, quote=True)
