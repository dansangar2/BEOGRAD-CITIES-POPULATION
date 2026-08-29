import unittest
from decimal import Decimal

from ciudades_del_mundo.application.export_nuevo_admin_areas import build_nuevo_admin_workbook
from ciudades_del_mundo.domain.nuevo_admin_export import (
    CellMerge,
    NuevoAdminAreaSummary,
    NuevoAdminCitySummary,
    NuevoAdminExportData,
)


def _area(
    area_id: str,
    *,
    code: str,
    name: str,
    level: int,
    parent_id: str | None,
    area_km2: Decimal | None = None,
    density: Decimal | None = None,
    pop_latest: int | None = None,
    source_unit_ids: tuple[str, ...] = (),
    capitals: tuple[str | tuple[str, int | None], ...] = (),
    most_populated_city: str | tuple[str, int | None] | None = None,
) -> NuevoAdminAreaSummary:
    capital_summaries = []
    for index, capital in enumerate(capitals, start=1):
        if isinstance(capital, tuple):
            capital_name, capital_population = capital
        else:
            capital_name, capital_population = capital, None
        capital_summaries.append(
            NuevoAdminCitySummary(
                id=f"{area_id}-capital-{index}",
                name=capital_name,
                pop_latest=capital_population,
            )
        )
    if isinstance(most_populated_city, tuple):
        most_populated_summary = NuevoAdminCitySummary(
            id=f"{area_id}-largest",
            name=most_populated_city[0],
            pop_latest=most_populated_city[1],
        )
    elif most_populated_city:
        most_populated_summary = NuevoAdminCitySummary(id=f"{area_id}-largest", name=most_populated_city)
    else:
        most_populated_summary = None
    return NuevoAdminAreaSummary(
        id=area_id,
        country_code="test",
        code=code,
        name=name,
        level=level,
        parent_id=parent_id,
        entity_type="Subdivision",
        area_km2=area_km2,
        density=density,
        pop_latest=pop_latest,
        source_units_count=len(source_unit_ids),
        source_unit_ids=source_unit_ids,
        capitals=tuple(capital_summaries),
        most_populated_city=most_populated_summary,
    )


class NuevoAdminExcelExportTests(unittest.TestCase):
    def test_top_level_paths_are_alphabetical_by_name_not_code_and_country_cells_are_merged(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        zeta = _area("test-zeta", code="A", name="Zeta", level=1, parent_id="test")
        alava = _area("test-alava", code="Z", name="Alava", level=1, parent_id="test")

        workbook, data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(zeta, alava))
        )

        sheet = workbook.sheets[0]
        rows = sheet.rows
        nv1_name_column = rows[0].index("NV1 Nombre")
        self.assertEqual(
            rows[0][:17],
            (
                "Nombre",
                "Poblaci\u00f3n",
                "%",
                "Ranking poblaci\u00f3n",
                "Terreno",
                "%",
                "Ranking terreno",
                "Densidad",
                "Capital",
                "Poblaci\u00f3n capital",
                "% capital",
                "Ranking capital",
                "Ciudad m\u00e1s poblada",
                "Poblaci\u00f3n ciudad m\u00e1s poblada",
                "% ciudad m\u00e1s poblada",
                "Ranking ciudad m\u00e1s poblada",
                "Num municipios",
            ),
        )
        self.assertEqual(data_rows, 2)
        self.assertEqual([row[0] for row in rows[1:]], ["Test", None])
        self.assertEqual([row[nv1_name_column] for row in rows[1:]], ["Alava", "Zeta"])
        self.assertIn(CellMerge(start_row=2, start_column=1, end_row=3, end_column=1), sheet.merged_cells)
        self.assertTrue(sheet.center_cells)
        self.assertTrue(sheet.auto_column_widths)

    def test_child_paths_are_alphabetical_by_name_not_code_and_parent_cells_are_merged(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        parent = _area("test-parent", code="P", name="Parent", level=1, parent_id="test")
        bravo = _area("test-bravo", code="A", name="Bravo", level=2, parent_id="test-parent")
        alpha = _area("test-alpha", code="Z", name="Alpha", level=2, parent_id="test-parent")

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent, bravo, alpha))
        )

        rows = workbook.sheets[0].rows
        nv1_name_column = rows[0].index("NV1 Nombre")
        nv2_name_column = rows[0].index("NV2 Nombre")
        self.assertEqual([row[nv1_name_column] for row in rows[1:]], ["Parent", None])
        self.assertEqual([row[nv2_name_column] for row in rows[1:]], ["Alpha", "Bravo"])

    def test_rows_group_descendant_paths_under_each_parent(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        zeta = _area("test-zeta", code="Z", name="Zeta", level=1, parent_id="test")
        alava = _area("test-alava", code="A", name="Alava", level=1, parent_id="test")
        zeta_child = _area("test-zeta-child", code="Z1", name="Zeta Child", level=2, parent_id="test-zeta")
        alava_b = _area("test-alava-b", code="A2", name="Alava B", level=2, parent_id="test-alava")
        alava_a = _area("test-alava-a", code="A1", name="Alava A", level=2, parent_id="test-alava")

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(zeta, zeta_child, alava_b, alava_a, alava))
        )

        rows = workbook.sheets[0].rows
        nv1_name_column = rows[0].index("NV1 Nombre")
        nv2_name_column = rows[0].index("NV2 Nombre")
        self.assertEqual(
            [(row[nv1_name_column], row[nv2_name_column]) for row in rows[1:]],
            [("Alava", "Alava A"), (None, "Alava B"), ("Zeta", "Zeta Child")],
        )

    def test_path_blocks_include_requested_metrics_rankings_capital_and_largest_city(self):
        root = _area(
            "test",
            code="test",
            name="Test",
            level=0,
            parent_id=None,
            area_km2=Decimal("100"),
            density=Decimal("10"),
            pop_latest=1000,
            source_unit_ids=("mun-1", "mun-2"),
        )
        parent = _area(
            "test-parent",
            code="P",
            name="Parent",
            level=1,
            parent_id="test",
            area_km2=Decimal("25"),
            density=Decimal("10"),
            pop_latest=250,
            source_unit_ids=("mun-1", "mun-2"),
            capitals=(("Capital City", 50),),
            most_populated_city=("Big City", 100),
        )

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent,))
        )

        header, row = workbook.sheets[0].rows
        self.assertEqual(
            row[:17],
            ("Test", 1000, 100.0, 1, 100.0, 100.0, 1, 10.0, None, None, None, None, None, None, None, None, 2),
        )
        nv1 = header.index("NV1 Nombre")
        self.assertEqual(
            row[nv1 : nv1 + 17],
            ("Parent", 250, 25.0, 1, 25.0, 25.0, 1, 10.0, "Capital City", 50, 20.0, 1, "Big City", 100, 40.0, 1, 2),
        )

    def test_country_row_includes_legal_subdivision_count_from_descendants(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        parent = _area(
            "test-parent",
            code="P",
            name="Parent",
            level=1,
            parent_id="test",
            source_unit_ids=("mun-1", "mun-2"),
        )

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent,))
        )

        rows = workbook.sheets[0].rows
        self.assertEqual(rows[1][16], 2)
        self.assertEqual(rows[1][rows[0].index("NV1 Num municipios")], 2)

    def test_export_writes_plain_rows_without_structured_tables(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        parent = _area("test-parent", code="P", name="Parent", level=1, parent_id="test")
        child = _area("test-child", code="C", name="Child", level=2, parent_id="test-parent")

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent, child))
        )

        sheet = workbook.sheets[0]
        header_width = len(sheet.rows[0])
        self.assertEqual(len(sheet.tables), 0)
        self.assertTrue(all(len(row) == header_width for row in sheet.rows))
