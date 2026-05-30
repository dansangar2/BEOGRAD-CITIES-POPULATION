import unittest

from ciudades_del_mundo.application.export_nuevo_admin_areas import build_nuevo_admin_workbook
from ciudades_del_mundo.domain.nuevo_admin_export import (
    NuevoAdminAreaSummary,
    NuevoAdminExportData,
)


def _area(
    area_id: str,
    *,
    code: str,
    name: str,
    level: int,
    parent_id: str | None,
    pop_latest: int | None = None,
) -> NuevoAdminAreaSummary:
    return NuevoAdminAreaSummary(
        id=area_id,
        country_code="test",
        code=code,
        name=name,
        level=level,
        parent_id=parent_id,
        entity_type="Subdivision",
        pop_latest=pop_latest,
    )


class NuevoAdminExcelExportTests(unittest.TestCase):
    def test_top_level_rows_are_alphabetical_by_name_not_code(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        zeta = _area("test-zeta", code="A", name="Zeta", level=1, parent_id="test")
        alava = _area("test-alava", code="Z", name="Alava", level=1, parent_id="test")

        workbook, data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(zeta, alava))
        )

        rows = workbook.sheets[0].rows
        l1_name_column = rows[0].index("L1_nombre")
        self.assertEqual(data_rows, 2)
        self.assertEqual(rows[1][l1_name_column], "Alava")
        self.assertEqual(rows[2][l1_name_column], "Zeta")

    def test_child_rows_are_alphabetical_by_name_not_code(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        parent = _area("test-parent", code="P", name="Parent", level=1, parent_id="test")
        bravo = _area("test-bravo", code="A", name="Bravo", level=2, parent_id="test-parent")
        alpha = _area("test-alpha", code="Z", name="Alpha", level=2, parent_id="test-parent")

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent, bravo, alpha))
        )

        rows = workbook.sheets[0].rows
        l2_name_column = rows[0].index("L2_nombre")
        self.assertEqual(rows[1][l2_name_column], "Alpha")
        self.assertEqual(rows[2][l2_name_column], "Bravo")

    def test_population_rank_is_countrywide_within_each_level(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        zeta = _area("test-zeta", code="A", name="Zeta", level=1, parent_id="test", pop_latest=20)
        alava = _area("test-alava", code="Z", name="Alava", level=1, parent_id="test", pop_latest=10)

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(zeta, alava))
        )

        rows = workbook.sheets[0].rows
        l1_name_column = rows[0].index("L1_nombre")
        l1_rank_column = rows[0].index("L1_ranking_poblacion_pais")
        ranks_by_name = {
            row[l1_name_column]: row[l1_rank_column]
            for row in rows[1:]
        }

        self.assertEqual(ranks_by_name, {"Alava": 2, "Zeta": 1})

    def test_export_only_writes_main_table_columns(self):
        root = _area("test", code="test", name="Test", level=0, parent_id=None)
        parent = _area("test-parent", code="P", name="Parent", level=1, parent_id="test")
        child = _area("test-child", code="C", name="Child", level=2, parent_id="test-parent")

        workbook, _data_rows, _levels = build_nuevo_admin_workbook(
            NuevoAdminExportData(root=root, areas=(parent, child))
        )

        sheet = workbook.sheets[0]
        header_width = len(sheet.rows[0])
        self.assertEqual(len(sheet.tables), 1)
        self.assertTrue(all(len(row) == header_width for row in sheet.rows))
