# CSV and Excel exports

Detailed repository reference. Start with `AGENTS.md` and read only the relevant section.

## Export Pipeline

CSV command:

```powershell
py manage.py export_nuevoadmin_csv --country-id <derived_id>
```

Excel command:

```powershell
py manage.py export_nuevoadmin_excel --country-id <derived_id>
```

Important files:

- `ciudades_del_mundo/management/commands/export_nuevoadmin_csv.py`
- `ciudades_del_mundo/management/commands/export_nuevoadmin_excel.py`
- `ciudades_del_mundo/application/export_nuevo_admin_areas.py`
- `ciudades_del_mundo/infrastructure/django/nuevo_admin_area_export_repository.py`
- `ciudades_del_mundo/infrastructure/excel/simple_xlsx_writer.py`
- `ciudades_del_mundo/domain/nuevo_admin_export.py`

Excel details:

- `export_nuevoadmin_excel` refreshes derived most-populated city before export.
- Default output directory is `excels/`.
- `SimpleXlsxWriter` is a custom no-third-party XLSX writer using `zipfile`.
- Workbook rows are wide hierarchy paths. The first block describes the country
  with `Nombre`, `Poblacion`, `%`, `Ranking poblacion`, `Terreno`, `%`,
  `Ranking terreno`, `Densidad`, `Capital`, `Poblacion capital`, `% capital`,
  `Ranking capital`, `Ciudad mas poblada`, `Poblacion ciudad mas poblada`, `%
  ciudad mas poblada`, `Ranking ciudad mas poblada` and `Num municipios`;
  following blocks repeat those columns for `NV1`, `NV2` and deeper levels.
- Each exported row follows one branch. Repeated country/parent blocks across
  consecutive child paths are vertically merged cell-by-cell. Descendant paths
  are grouped under their parent before moving to the next sibling, with
  siblings sorted alphabetically by name, not code.
- `Num municipios` is calculated from accumulated `municipios_originales` source
  units and is rolled up from children to parents. The fallback web export uses
  those source units to resolve the most-populated city when needed. The workbook
  no longer exports technical columns, a `Resumen` sheet, structured Excel tables
  when merged cells are present, or secondary child-summary tables to the right.
- The main sheet name is `Jerarquia`.
- The hierarchy sheet uses `CellMerge` metadata rendered by `SimpleXlsxWriter`
  as `<mergeCells>` ranges, plus optional centered styles and inferred column
  widths.
