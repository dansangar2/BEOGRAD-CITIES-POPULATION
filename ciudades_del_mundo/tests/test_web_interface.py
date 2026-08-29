import ast
import json
import os
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
import zipfile

from django.core.management import call_command
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from django.utils import translation

from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    DerivedSubdivision,
    NuevoAdminArea,
    ScrapingConfig,
    SubdivisionGroup,
    VisualAsset,
    VisualAssetTranslation,
)
from ciudades_del_mundo.services.derived_config_seeds import (
    bundled_derived_subdivision_paths,
    bundled_subdivision_group_paths,
    export_derived_subdivisions_to_toml,
    import_derived_subdivision_path_records,
    import_subdivision_group_seed,
    import_subdivision_group_path,
    import_subdivision_group_path_records,
    render_derived_country_selection_toml,
)
from ciudades_del_mundo.services.derived_subdivision_builder import (
    build_derived_subdivisions_for_country,
)
from ciudades_del_mundo.services.scraping_configs import (
    export_city_merges_to_toml,
    sync_city_merges_from_toml,
    upsert_scraping_config,
)
from ciudades_del_mundo.web.log_retention import cleanup_old_logs
from ciudades_del_mundo.web.task_progress import task_progress_path
from ciudades_del_mundo.web.tasks import ManagedTask, TaskManager, task_manager
from ciudades_del_mundo.web.views import (
    _admin_area_detail_payload,
    _area_capital_display_names,
    _area_related_places,
    _eligible_config_slugs_for_bulk,
    _latest_bulk_config_progress,
    _inferred_generated_page_level,
    _group_metric_text,
    _new_country_detail_export_rows,
    _new_country_detail_export_sheet_rows,
    _render_config_from_manual_post,
    _render_recipe_from_form,
    _subdivision_groups_for_country,
    _can_clear_config_row,
    _can_scrape_config_status,
    _validate_config_text,
    _validate_recipe_text,
)


def _config_action_form_tag(html: str, action: str) -> str:
    match = re.search(r'<form[^>]*data-config-action-form="' + re.escape(action) + r'"[^>]*>', html)
    return match.group(0) if match else ""


class _Relation:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Object:
    def __init__(self, **values):
        self.__dict__.update(values)


class _RecordingTaskManager(TaskManager):
    def __init__(self, *args, **kwargs):
        self.started_workers = []
        super().__init__(*args, **kwargs)

    def _start_worker(self, task_id: str) -> None:
        self.started_workers.append(task_id)


class DerivedSectionWebTests(TestCase):
    def test_group_metric_text_uses_plain_comma_decimal_numbers(self):
        self.assertEqual(_group_metric_text(1000), "1000")
        self.assertEqual(_group_metric_text(Decimal("34.50")), "34,5")
        self.assertEqual(_group_metric_text(Decimal("12.345")), "12,35")

    def test_new_country_configuration_flow_persists_toml(self):
        response = self.client.post(
            "/new-countries/new/",
            {
                "slug": "testland",
                "name": "Testland",
                "source_country_code": "aa",
                "description": "Demo",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/new-countries/aa/testland/")
        country = DerivedCountry.objects.get(slug="testland")
        self.assertEqual(country.source_country_code, "aa")

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "republic",
                "name": "Republic",
                "source_country_code": "aa",
                "content": 'kind = "derived_country_config"\n[selection]\ninclude_codes = []\n',
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="republic")
        self.assertEqual(config.derived_country_code, "testland")
        self.assertIn('kind = "derived_country_config"', config.content)
        legacy_response = self.client.get("/new-countries/testland/")
        self.assertEqual(legacy_response.status_code, 302)
        self.assertEqual(legacy_response["Location"], "/new-countries/aa/testland/")
        detail_response = self.client.get("/new-countries/aa/testland/")
        self.assertEqual(detail_response.status_code, 200)
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn("data-new-country-data-form", detail_html)
        self.assertIn("new-country-data-form-grid", detail_html)
        self.assertIn("new-country-subentities-table-wrap", detail_html)
        self.assertIn("data-new-country-config-tree", detail_html)
        self.assertIn('data-tree-url="/api/new-country-containers/testland/config-tree/"', detail_html)
        self.assertIn("data-new-country-config-tree-level", detail_html)
        self.assertNotIn('data-row-href="/new-countries/aa/testland/configs/republic/"', detail_html)
        self.assertIn("Terreno", detail_html)
        self.assertNotIn("km^2", detail_html)
        self.assertIn("Poblacion", detail_html)
        self.assertNotIn("Filas creadas", detail_html)
        self.assertNotIn('data-config-action="build"', detail_html)
        self.assertNotIn('data-config-action="export-excel"', detail_html)
        self.assertNotIn(">Configuracion<", detail_html)
        self.assertNotIn(">Ver<", detail_html)
        self.assertNotIn("data-new-country-data-modal", detail_html)
        self.assertNotIn("data-open-new-country-data-modal", detail_html)
        self.assertNotIn(">Editar<", detail_html)
        self.assertIn('action="/new-countries/aa/testland/"', detail_html)
        self.assertIn('value="TESTLAND"', detail_html)
        self.assertIn('class="new-country-data-description"', detail_html)
        self.assertIn('class="description-textarea" name="description" rows="2"', detail_html)
        tree_response = self.client.get("/api/new-country-containers/testland/config-tree/")
        self.assertEqual(tree_response.status_code, 200)
        tree_rows = tree_response.json()["rows"]
        self.assertEqual(len(tree_rows), 1)
        self.assertEqual(tree_rows[0]["entity_display_name"], "Republic")
        self.assertEqual(tree_rows[0]["edit_url"], "/new-countries/aa/testland/configs/republic/")
        self.assertEqual(tree_rows[0]["clone_url"], "/new-countries/aa/testland/configs/republic/clone/")
        self.assertEqual(tree_rows[0]["delete_url"], "/new-countries/aa/testland/configs/republic/delete/")
        response = self.client.post(
            "/new-countries/aa/testland/",
            {"name": "Testland Editado", "description": "Descripcion editada"},
        )
        self.assertEqual(response.status_code, 302)
        country.refresh_from_db()
        self.assertEqual(country.name, "Testland Editado")
        self.assertEqual(country.description, "Descripcion editada")
        self.assertEqual(self.client.get("/new-countries/bb/testland/").status_code, 404)
        self.assertEqual(self.client.get("/new-countries/testland/configs/republic/view/").status_code, 404)
        self.assertEqual(self.client.get("/new-countries/aa/testland/configs/republic/view/").status_code, 404)
        clone_response = self.client.post("/new-countries/aa/testland/configs/republic/clone/")
        self.assertEqual(clone_response.status_code, 302)
        self.assertEqual(clone_response["Location"], "/new-countries/aa/testland/configs/republic_copia/")
        clone = DerivedCountryConfig.objects.get(country=country, slug="republic_copia")
        self.assertEqual(clone.name, "Republic Copia")
        self.assertIn('slug = "republic_copia"', clone.content)
        self.assertIn('code = "REPUBLIC-COPIA"', clone.content)
        delete_response = self.client.post(
            "/new-countries/aa/testland/configs/republic/delete/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])
        self.assertFalse(DerivedCountryConfig.objects.filter(country=country, slug="republic").exists())

    def test_new_country_detail_export_excel_downloads_tree_and_writes_log(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="AA", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_1", country_code="aa", code="1", name="Region", level=1, parent=root)
        capital = AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="CITY",
            name="Capital City",
            level=2,
            parent=region,
            pop_latest=100,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="region",
            name="Region",
            source_country_code="aa",
            derived_country_code="testland",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'slug = "region"',
                    'source_country_code = "aa"',
                    'derived_country_code = "testland"',
                    'name = "Region"',
                    "",
                    "[[entities]]",
                    'name = "Region"',
                    'code = "REG"',
                    'entity_type = "Provincia"',
                    "level = 1",
                    'capitals = ["aa_city"]',
                    "area_km2 = 50",
                    "pop_latest = 1000",
                    "density = 20",
                    "",
                ]
            ),
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="child",
            name="Child",
            source_country_code="aa",
            derived_country_code="testland",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'slug = "child"',
                    'source_country_code = "aa"',
                    'derived_country_code = "testland"',
                    'name = "Child"',
                    "",
                    "[[entities]]",
                    'name = "Child"',
                    'code = "CHI"',
                    'entity_type = "Distrito"',
                    'parent_config_slug = "region"',
                    "level = 2",
                    "area_km2 = 10",
                    "pop_latest = 250",
                    "density = 25",
                    "",
                ]
            ),
        )
        built_root = NuevoAdminArea.objects.create(
            id="testland",
            country_code="testland",
            code="TESTLAND",
            name="Testland",
            level=0,
            entity_type="Country",
            area_km2=Decimal("50"),
            pop_latest=1000,
            density=Decimal("20"),
        )
        built_region = NuevoAdminArea.objects.create(
            id="testland-REG",
            country_code="testland",
            code="REG",
            name="Region",
            level=1,
            entity_type="Provincia",
            parent=built_root,
            area_km2=Decimal("50"),
            pop_latest=1000,
            density=Decimal("20"),
        )
        built_child = NuevoAdminArea.objects.create(
            id="testland-REG-CHI",
            country_code="testland",
            code="REG-CHI",
            name="Child",
            level=2,
            entity_type="Distrito",
            parent=built_region,
            area_km2=Decimal("10"),
            pop_latest=250,
            density=Decimal("25"),
        )
        built_region.capitals.add(capital)
        built_region.municipios_originales.add(capital)
        built_child.municipios_originales.add(capital)

        with TemporaryDirectory() as tmpdir, self.settings(BASE_DIR=Path(tmpdir)):
            response = self.client.get("/new-countries/aa/testland/export-excel/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response["Content-Type"],
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.assertIn('filename="testland_', response["Content-Disposition"])
            self.assertTrue(response.content.startswith(b"PK"))
            log_path = Path(response["X-Export-Log-Path"])
            self.assertTrue(log_path.exists())
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"country_slug": "testland"', log_text)
            self.assertIn('"rows": 3', log_text)
            self.assertIn('"legal_subdivision_count": 1', log_text)

            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                hierarchy_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                styles_xml = archive.read("xl/styles.xml").decode("utf-8")
                self.assertNotIn("xl/worksheets/sheet2.xml", archive.namelist())
            self.assertIn("Jerarquia", workbook_xml)
            self.assertIn("Num municipios", hierarchy_xml)
            self.assertIn("NV1 Nombre", hierarchy_xml)
            self.assertIn("NV2 Nombre", hierarchy_xml)
            self.assertIn("Ranking población", hierarchy_xml)
            self.assertIn("Población capital", hierarchy_xml)
            self.assertIn("Ciudad más poblada", hierarchy_xml)
            self.assertIn("<cols>", hierarchy_xml)
            self.assertIn('horizontal="center"', styles_xml)
            self.assertIn("Capital City", hierarchy_xml)
            self.assertIn("Testland", hierarchy_xml)
            self.assertIn("Region", hierarchy_xml)
            self.assertIn("Child", hierarchy_xml)
            self.assertLess(hierarchy_xml.find("Testland"), hierarchy_xml.find("Region"))
            self.assertLess(hierarchy_xml.find("Region"), hierarchy_xml.find("Child"))

    def test_new_country_detail_export_fallback_orders_country_then_grouped_levels(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        rows = [
            {
                "level": 1,
                "display_code": "Z",
                "entity_display_name": "Zeta",
                "entity_type": "Provincia",
                "entity_area_value": Decimal("30"),
                "entity_population_value": 300,
                "entity_density_value": Decimal("10"),
                "tree_id": "config:testland:zeta",
                "tree_parent": "",
                "row_kind": "config",
                "sort_kind": 0,
                "is_config_row": True,
            },
            {
                "level": 2,
                "display_code": "Z-1",
                "entity_display_name": "Zeta Child",
                "entity_type": "Distrito",
                "entity_area_value": Decimal("10"),
                "entity_population_value": 100,
                "entity_density_value": Decimal("10"),
                "tree_id": "config:testland:zeta-child",
                "tree_parent": "config:testland:zeta",
                "row_kind": "config",
                "sort_kind": 0,
                "is_config_row": True,
            },
            {
                "level": 1,
                "display_code": "A",
                "entity_display_name": "Alava",
                "entity_type": "Provincia",
                "entity_area_value": Decimal("20"),
                "entity_population_value": 200,
                "entity_density_value": Decimal("10"),
                "tree_id": "config:testland:alava",
                "tree_parent": "",
                "row_kind": "config",
                "sort_kind": 0,
                "is_config_row": True,
            },
            {
                "level": 2,
                "display_code": "A-1",
                "entity_display_name": "Alava Child",
                "entity_type": "Distrito",
                "entity_area_value": Decimal("5"),
                "entity_population_value": 50,
                "entity_density_value": Decimal("10"),
                "tree_id": "config:testland:alava-child",
                "tree_parent": "config:testland:alava",
                "row_kind": "config",
                "sort_kind": 0,
                "is_config_row": True,
            },
        ]

        export_rows, summary = _new_country_detail_export_rows(country, rows)

        self.assertEqual(
            [row["name"] for row in export_rows],
            ["Testland", "Alava", "Zeta", "Alava Child", "Zeta Child"],
        )
        sheet_rows = _new_country_detail_export_sheet_rows(export_rows)
        nv1_name_column = sheet_rows[0].index("NV1 Nombre")
        nv2_name_column = sheet_rows[0].index("NV2 Nombre")
        self.assertEqual(
            [(row[nv1_name_column], row[nv2_name_column]) for row in sheet_rows[1:]],
            [("Alava", "Alava Child"), ("Zeta", "Zeta Child")],
        )
        self.assertEqual(export_rows[0]["area_pct_total"], 100.0)
        self.assertEqual(summary["levels"], (1, 2))

    def test_new_country_detail_export_fallback_counts_legal_source_units(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="AA", name="Aaland", level=0)
        province = AdminArea.objects.create(
            id="aa_province",
            country_code="aa",
            code="P",
            name="Province",
            level=1,
            parent=root,
        )
        AdminArea.objects.create(
            id="aa_municipality",
            country_code="aa",
            code="M",
            name="Municipality",
            level=2,
            parent=province,
        )

        export_rows, summary = _new_country_detail_export_rows(
            country,
            [
                {
                    "level": 1,
                    "display_code": "P",
                    "entity_display_name": "Province",
                    "entity_type": "Provincia",
                    "entity_area_value": Decimal("10"),
                    "entity_population_value": 100,
                    "entity_density_value": Decimal("10"),
                    "tree_id": "config:testland:province:source:aa_province",
                    "tree_parent": "",
                    "row_kind": "assigned-subdivision",
                    "sort_kind": 1,
                    "is_config_row": False,
                }
            ],
        )

        legal_counts = {row["name"]: row["legal_subdivision_count"] for row in export_rows}
        self.assertEqual(legal_counts["Testland"], 1)
        self.assertEqual(legal_counts["Province"], 1)
        self.assertEqual(summary["legal_subdivision_count"], 1)

    def test_new_country_detail_export_uses_source_legal_level_zero_as_unit(self):
        country = DerivedCountry.objects.create(slug="sevilla", name="Reino de Sevilla", source_country_code="spain")
        spain_root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        spain_province = AdminArea.objects.create(
            id="spain_sevilla",
            country_code="spain",
            code="41",
            name="Sevilla",
            level=2,
            parent=spain_root,
        )
        spain_municipality = AdminArea.objects.create(
            id="spain_sevilla_city",
            country_code="spain",
            code="41091",
            name="Sevilla",
            level=3,
            parent=spain_province,
        )
        gibraltar_root = AdminArea.objects.create(
            id="gibraltar_gibraltar",
            country_code="gibraltar",
            code="gibraltar",
            name="Gibraltar",
            level=0,
        )
        gibraltar_child = AdminArea.objects.create(
            id="gibraltar_001",
            country_code="gibraltar",
            code="001",
            name="Enumeration Area",
            level=1,
            parent=gibraltar_root,
        )
        ScrapingConfig.objects.update_or_create(
            slug="gibraltar",
            defaults={
                "country_code": "gibraltar",
                "name": "Gibraltar",
                "content": "LEGAL_SUBDIVISION = 0\n",
                "content_hash": "test",
            },
        )

        export_rows, summary = _new_country_detail_export_rows(
            country,
            [
                {
                    "level": 1,
                    "display_code": "SEV",
                    "entity_display_name": "Sevilla",
                    "entity_type": "Provincia",
                    "entity_area_value": Decimal("10"),
                    "entity_population_value": 100,
                    "entity_density_value": Decimal("10"),
                    "tree_id": "config:sevilla:sevilla:source:spain_sevilla",
                    "tree_parent": "",
                    "row_kind": "assigned-subdivision",
                    "sort_kind": 1,
                    "is_config_row": False,
                },
                {
                    "level": 1,
                    "display_code": "GIB",
                    "entity_display_name": "Gibraltar",
                    "entity_type": "Territorio",
                    "entity_area_value": Decimal("5"),
                    "entity_population_value": 50,
                    "entity_density_value": Decimal("10"),
                    "tree_id": "config:sevilla:gibraltar:source:gibraltar_001",
                    "tree_parent": "",
                    "row_kind": "assigned-subdivision",
                    "sort_kind": 1,
                    "is_config_row": False,
                },
            ],
        )

        rows_by_name = {row["name"]: row for row in export_rows}
        self.assertEqual(set(rows_by_name["Sevilla"]["_legal_source_unit_ids"]), {spain_municipality.id})
        self.assertEqual(set(rows_by_name["Gibraltar"]["_legal_source_unit_ids"]), {gibraltar_root.id})
        self.assertNotIn(gibraltar_child.id, rows_by_name["Gibraltar"]["_legal_source_unit_ids"])
        self.assertEqual(summary["legal_subdivision_count"], 2)

    def test_new_country_detail_normalizes_materialized_created_subdivision_legal_units(self):
        country = DerivedCountry.objects.create(slug="sevilla", name="Reino de Sevilla", source_country_code="spain")
        gibraltar_root = AdminArea.objects.create(
            id="gibraltar_gibraltar",
            country_code="gibraltar",
            code="gibraltar",
            name="Gibraltar",
            level=0,
        )
        gibraltar_child = AdminArea.objects.create(
            id="gibraltar_001",
            country_code="gibraltar",
            code="001",
            name="Enumeration Area",
            level=1,
            parent=gibraltar_root,
        )
        ScrapingConfig.objects.update_or_create(
            slug="gibraltar",
            defaults={
                "country_code": "gibraltar",
                "name": "Gibraltar",
                "content": "LEGAL_SUBDIVISION = 0\n",
                "content_hash": "test",
            },
        )
        DerivedSubdivision.objects.create(
            slug="spain_sevilla_b_c_gb",
            internal_name="SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            source_country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            entity_type="Reino",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SEVILLA_B_C_GB"',
                    'source_country_code = "spain"',
                    'name = "Sevilla (Borbones con Gibraltar)"',
                    'code = "ESP-SEVILLA_B_C_GB"',
                    'parent_code = "ESP"',
                    'entity_type = "Reino"',
                    "level = 1",
                    "",
                    "[[include]]",
                    'country_code = "gibraltar"',
                    "level = 0",
                    'ids = ["gibraltar_gibraltar"]',
                ]
            ),
        )
        materialized = NuevoAdminArea.objects.create(
            id="spain-ESP-SEVILLA_B_C_GB",
            country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            level=1,
            entity_type="Reino",
        )
        materialized.municipios_originales.add(gibraltar_child)

        export_rows, summary = _new_country_detail_export_rows(
            country,
            [
                {
                    "level": 1,
                    "display_code": "SEV",
                    "entity_display_name": "Sevilla (Borbones con Gibraltar)",
                    "entity_type": "Reino",
                    "entity_area_value": Decimal("5"),
                    "entity_population_value": 50,
                    "entity_density_value": Decimal("10"),
                    "tree_id": "config:sevilla:sevilla:derived:spain:SEVILLA_B_C_GB",
                    "tree_parent": "",
                    "row_kind": "assigned-subdivision",
                    "sort_kind": 1,
                    "is_config_row": False,
                },
            ],
        )

        row = next(item for item in export_rows if item["name"] == "Sevilla (Borbones con Gibraltar)")
        self.assertEqual(set(row["_legal_source_unit_ids"]), {gibraltar_root.id})
        self.assertNotIn(gibraltar_child.id, row["_legal_source_unit_ids"])
        self.assertEqual(summary["legal_subdivision_count"], 1)

    def test_new_country_detail_displays_created_subdivision_legal_units_from_stale_materialization(self):
        country = DerivedCountry.objects.create(slug="sevilla", name="Reino de Sevilla", source_country_code="spain")
        AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        gibraltar_root = AdminArea.objects.create(
            id="gibraltar_gibraltar",
            country_code="gibraltar",
            code="gibraltar",
            name="Gibraltar",
            level=0,
            area_km2=Decimal("6.55"),
            pop_latest=38196,
        )
        gibraltar_child = AdminArea.objects.create(
            id="gibraltar_001",
            country_code="gibraltar",
            code="001",
            name="EA 1",
            level=1,
            parent=gibraltar_root,
            area_km2=Decimal("2.00"),
            pop_latest=100,
        )
        ScrapingConfig.objects.update_or_create(
            slug="gibraltar",
            defaults={
                "country_code": "gibraltar",
                "name": "Gibraltar",
                "content": "LEGAL_SUBDIVISION = 0\n",
                "content_hash": "test",
            },
        )
        DerivedSubdivision.objects.create(
            slug="spain_sevilla_b_c_gb",
            internal_name="SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            source_country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            entity_type="Reino",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SEVILLA_B_C_GB"',
                    'source_country_code = "spain"',
                    'name = "Sevilla (Borbones con Gibraltar)"',
                    'code = "ESP-SEVILLA_B_C_GB"',
                    'entity_type = "Reino"',
                    "level = 1",
                    "",
                    "[[include]]",
                    'country_code = "gibraltar"',
                    "level = 0",
                    'ids = ["gibraltar_gibraltar"]',
                ]
            ),
        )
        materialized = NuevoAdminArea.objects.create(
            id="spain-ESP-SEVILLA_B_C_GB",
            country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            level=1,
            entity_type="Reino",
        )
        materialized.municipios_originales.add(gibraltar_child)
        content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="reino_de_sevilla",
            name="Reino de Sevilla",
            source_country_code="spain",
            derived_country_code="sevilla",
            selected_ids=[],
            selected_derived_subdivisions=[{"country_code": "spain", "key": "SEVILLA_B_C_GB"}],
            entity_name="Reino de Sevilla",
            entity_code="SEV",
            entity_type="Reino",
            use_subdivisions=True,
            entity_level=1,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="reino_de_sevilla",
            name="Reino de Sevilla",
            source_country_code="spain",
            derived_country_code="sevilla",
            content=content,
        )

        response = self.client.get("/new-countries/spain/sevilla/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Gibraltar", html)
        self.assertNotIn("EA 1", html)
        self.assertIn('data-tree-id="config:sevilla:reino_de_sevilla:derived:spain:SEVILLA_B_C_GB:source:gibraltar_gibraltar"', html)

    def test_new_country_config_delete_reparents_children_without_deleting_configs(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_1", country_code="aa", code="1", name="Region", level=1, parent=root)
        city = AdminArea.objects.create(id="aa_city", country_code="aa", code="11", name="Capital City", level=2, parent=region)
        parent_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="parent",
            name="Parent",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Parent",
            entity_code="parent",
            entity_type="Reino",
            entity_level=1,
            capitals=[],
        )
        middle_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="middle",
            name="Middle",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[city.id],
            entity_name="Middle",
            entity_code="middle",
            entity_type="Provincia",
            parent_config_slug="parent",
            entity_level=2,
            capitals=[],
        )
        child_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="child",
            name="Child",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[city.id],
            entity_name="Child",
            entity_code="child",
            entity_type="Municipio",
            parent_config_slug="middle",
            entity_level=3,
            capitals=[],
        )
        child_content = child_content.replace('code = "CHILD"', 'code = "PARENT-MIDDLE-CHILD"')
        DerivedCountryConfig.objects.create(
            country=country,
            slug="parent",
            name="Parent",
            source_country_code="aa",
            derived_country_code="testland",
            content=parent_content,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="middle",
            name="Middle",
            source_country_code="aa",
            derived_country_code="testland",
            content=middle_content,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="child",
            name="Child",
            source_country_code="aa",
            derived_country_code="testland",
            content=child_content,
        )

        detail_response = self.client.get("/new-countries/aa/testland/")
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn('action="/new-countries/aa/testland/configs/middle/delete/"', detail_html)
        self.assertNotIn("Reasigna o elimina primero sus hijos", detail_html)

        delete_response = self.client.post(
            "/new-countries/aa/testland/configs/middle/delete/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(delete_response.status_code, 200)
        payload = delete_response.json()
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["reparented_slugs"], ["child"])
        self.assertFalse(DerivedCountryConfig.objects.filter(country=country, slug="middle").exists())
        child = DerivedCountryConfig.objects.get(country=country, slug="child")
        self.assertIn('parent_config_slug = "parent"', child.content)
        self.assertIn("level = 2", child.content)
        self.assertIn('code = "CHILD"', child.content)
        self.assertNotIn('code = "PARENT-MIDDLE-CHILD"', child.content)
        detail_response = self.client.get("/new-countries/aa/testland/")
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn("PARENT-CHILD", detail_html)
        self.assertNotIn("PARENT-MIDDLE-CHILD", detail_html)

    def test_new_country_create_uses_contextual_source_country(self):
        AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)

        response = self.client.get("/new-countries/aa/new/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Aaland", html)
        self.assertIn("Codigo", html)
        self.assertNotIn("Pais fuente", html)
        self.assertNotIn(">Slug<", html)
        self.assertIn('class="code-input"', html)
        self.assertIn("data-uppercase-code-input", html)
        self.assertIn("new-country-data-form-grid", html)
        self.assertIn('class="new-country-data-row"', html)
        self.assertIn('class="new-country-data-description"', html)
        self.assertIn('class="description-textarea" name="description" rows="2"', html)

        response = self.client.post(
            "/new-countries/aa/new/",
            {
                "slug": "testland",
                "name": "Testland",
                "description": "Demo",
            },
        )

        self.assertEqual(response.status_code, 302)
        country = DerivedCountry.objects.get(slug="testland")
        self.assertEqual(country.source_country_code, "aa")

    def test_new_country_invalid_code_uses_codigo_message(self):
        AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)

        response = self.client.post(
            "/new-countries/aa/new/",
            {
                "slug": "Imperio Español",
                "name": "Imperio Español",
                "description": "Demo",
            },
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("El codigo debe empezar por una letra", html)
        self.assertNotIn("slug de receta", html)

    def test_new_country_visual_selection_generates_sql_toml(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(
            id="aa",
            country_code="aa",
            code="aa",
            name="Source Country",
            level=0,
        )
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
        )
        city = AdminArea.objects.create(
            id="aa_11",
            country_code="aa",
            code="11",
            name="City",
            level=2,
            parent=region,
        )

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "visual",
                "name": "Visual",
                "source_country_code": "aa",
                "selection_json": json.dumps({"selected_ids": [region.id, city.id]}),
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="visual")
        self.assertIn('source_country_code = "aa"', config.content)
        self.assertIn('derived_country_code = "testland"', config.content)
        self.assertIn('include_ids = ["aa_1"]', config.content)
        self.assertIn('subtract_ids = ["aa_11"]', config.content)
        self.assertIn('operation = "add"', config.content)
        self.assertIn('operation = "subtract"', config.content)
        self.assertIn("[[entities]]", config.content)
        self.assertIn('mode = "final"', config.content)
        self.assertIn("[[entities.include]]", config.content)
        self.assertIn("[[entities.subtract]]", config.content)

    def test_new_country_config_create_hides_source_country_selector(self):
        DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)

        response = self.client.get("/new-countries/aa/testland/configs/new/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('type="hidden" name="source_country_code" value="aa"', html)
        self.assertIn("Aaland", html)
        self.assertIn("Codigo", html)
        self.assertIn("group-entry-main-box derived-create-main-box", html)
        self.assertIn("derived-tree-panel derived-create-source-panel", html)
        self.assertIn("Usar subdivisiones", html)
        self.assertIn('name="parent_config_slug"', html)
        self.assertIn('type="hidden" name="parent_config_slug"', html)
        self.assertNotIn('name="entity_level"', html)
        self.assertNotIn("data-entity-level-field", html)
        self.assertIn("derived-capital-picker", html)
        self.assertIn("data-new-country-capital-select", html)
        self.assertIn("Buscar capital", html)
        self.assertIn("Sin capitales", html)
        self.assertNotIn("capitals_text", html)
        self.assertNotIn("Separadas por coma", html)
        self.assertIn("Subdivisiones creadas", html)
        self.assertIn("Entidades creadas", html)
        self.assertIn("/new-countries/aa/testland/configs/new/created-entities/", html)
        self.assertIn("/new-countries/aa/testland/configs/new/source-areas/", html)
        self.assertIn("<th>Nivel</th>", html)
        self.assertNotIn("Subdivisiones reales", html)
        self.assertNotIn("data-source-tree-panel", html)
        self.assertNotIn("data-derived-tree", html)
        self.assertIn('id="new-country-derived-subdivisions-table"', html)
        self.assertIn('id="new-country-source-areas-table"', html)
        self.assertIn('type="search" name="q"', html)
        self.assertIn('data-new-country-derived-subdivision-country-filter', html)
        self.assertIn('data-new-country-source-area-country-filter', html)
        self.assertIn('data-new-country-source-area-level-filter', html)
        self.assertIn('data-select2-search="always"', html)
        self.assertIn('data-placeholder="Buscar pais"', html)
        self.assertIn("Buscar subdivisión", html)
        self.assertIn("data-new-country-derived-subdivision-pagination", html)
        self.assertIn("data-new-country-source-area-pagination", html)
        self.assertIn('data-initial-page-size="20"', html)
        self.assertIn('class="selection-cell"', html)
        derived_table_html = html.split('id="new-country-derived-subdivisions-table"', 1)[1].split(
            'id="new-country-source-areas-table"',
            1,
        )[0]
        self.assertNotIn("<th>Codigo</th>", derived_table_html)
        self.assertNotIn("<th>Bandera</th>", derived_table_html)
        self.assertNotIn("<th>Escudo</th>", derived_table_html)
        self.assertIn("<th>País</th>", html)
        self.assertIn("<th>Terreno</th>", html)
        self.assertIn("<th>Población</th>", html)
        self.assertIn("<th>Densidad</th>", html)
        self.assertIn("Subdivisiones fuente", html)
        self.assertNotIn('class="panel derived-create-form"', html)
        self.assertNotIn("Pais fuente", html)
        self.assertNotIn("Codigo derivado", html)
        self.assertNotIn("Activa", html)
        self.assertNotIn(">Slug<", html)

    def test_new_country_detail_shows_assigned_source_subdivision_rows(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
            entity_type="Region",
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="republic",
            name="Republic",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=["aa_1"],
            entity_name="Republic",
            entity_code="republic",
            entity_type="Region",
            entity_level=1,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="republic",
            name="Republic",
            source_country_code="aa",
            derived_country_code="testland",
            content=content,
        )

        response = self.client.get("/new-countries/aa/testland/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('<code class="new-country-assigned-code">1</code>', html)
        self.assertIn('<td class="new-country-tree-level">NV 2</td>', html)
        self.assertNotIn("Padre: aa", html)
        self.assertNotIn("<small>L1</small>", html)
        self.assertIn('data-tree-parent="config:testland:republic"', html)
        self.assertIn('class="new-country-config-tree-row is-child is-assigned-subdivision"', html)

    def test_new_country_config_create_supports_parent_and_derived_subdivision_children(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        city = AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="11",
            name="Capital City",
            level=2,
            parent=region,
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="parent",
            name="Parent",
            source_country_code="aa",
            derived_country_code="testland",
            content='\n'.join(
                [
                    'kind = "derived_country_config"',
                    'derived_country_code = "testland"',
                    "[[entities]]",
                    'name = "Parent"',
                    'code = "PARENT"',
                    "level = 1",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_child",
            internal_name="CHILD",
            name="Child",
            source_country_code="aa",
            code="AA-CHILD",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CHILD"',
                    'source_country_code = "aa"',
                    'name = "Child"',
                    'code = "AA-CHILD"',
                    'entity_type = "Provincia"',
                    'flag_url = "https://example.test/flag.svg"',
                    'coat_url = "https://example.test/coat.svg"',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 1",
                    'ids = ["aa_1"]',
                ]
            ),
        )

        response = self.client.get("/new-countries/aa/testland/configs/new/")
        html = response.content.decode("utf-8")
        self.assertNotIn("Parent (Nivel 1)", html)
        self.assertIn("/new-countries/aa/testland/configs/new/created-subdivisions/", html)
        self.assertIn("/new-countries/aa/testland/configs/new/created-entities/", html)
        self.assertNotIn("<code>AA-CHILD</code>", html)
        self.assertNotIn('src="https://example.test/flag.svg"', html)
        self.assertNotIn('src="https://example.test/coat.svg"', html)

        options_response = self.client.get("/new-countries/aa/testland/configs/new/created-subdivisions/")
        self.assertEqual(options_response.status_code, 200)
        option = options_response.json()["results"][0]
        self.assertEqual(option["name"], "Child")
        self.assertEqual(option["type_text"], "Provincia")
        self.assertNotIn("(Nivel", option["type_text"])
        self.assertEqual(option["value"], "derived-subdivision::aa::CHILD")
        self.assertEqual(option["area_text"], "25")
        self.assertEqual(option["population_text"], "1000")
        self.assertEqual(option["density_text"], "40")

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "child",
                "name": "Child Config",
                "source_country_code": "aa",
                "parent_config_slug": "parent",
                "entity_type": "Region",
                "capitals": [city.id],
                "selection_json": json.dumps({"selected_ids": [region.id]}),
            },
        )
        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="child")
        self.assertIn('derived_country_code = "testland"', config.content)
        self.assertIn('parent_config_slug = "parent"', config.content)
        self.assertIn("level = 2", config.content)
        self.assertIn('capitals = ["aa_city"]', config.content)

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "from_subdivision",
                "name": "From Subdivision",
                "source_country_code": "aa",
                "entity_type": "Region",
                "use_subdivisions": "1",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [],
                        "use_subdivisions": True,
                        "selected_subdivision_keys": ["CHILD"],
                    }
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="from_subdivision")
        self.assertIn('mode = "intermediate"', config.content)
        self.assertIn("level = 1", config.content)
        self.assertIn("use_selected_entities_as_children = true", config.content)
        self.assertRegex(
            config.content,
            r'\[\[entities\.include\]\]\ncountry_code = "aa"\nlevel = 2\nderived_subdivisions = \["CHILD"\]',
        )
        self.assertIn('derived_subdivisions = ["CHILD"]', config.content)
        self.assertIn("area_km2 = 25", config.content)
        self.assertIn("pop_latest = 1000", config.content)
        self.assertIn("density = 40", config.content)

        with patch(
            "ciudades_del_mundo.web.views._new_country_config_preview_metric_values",
            side_effect=AssertionError("created-entities must use stored metrics"),
        ):
            entities_response = self.client.get(
                "/new-countries/aa/testland/configs/new/created-entities/?q=From"
            )
        self.assertEqual(entities_response.status_code, 200)
        entity_option = entities_response.json()["results"][0]
        self.assertEqual(entity_option["slug"], "from_subdivision")
        self.assertEqual(entity_option["name"], "From Subdivision")
        self.assertEqual(entity_option["type_text"], "Region")
        self.assertEqual(entity_option["area_text"], "25")
        self.assertEqual(entity_option["population_text"], "1000")
        self.assertEqual(entity_option["density_text"], "40")

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "corona",
                "name": "Corona",
                "source_country_code": "aa",
                "entity_type": "Corona",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [],
                        "use_subdivisions": False,
                        "selected_child_config_slugs": ["from_subdivision"],
                    }
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        parent = DerivedCountryConfig.objects.get(country=country, slug="corona")
        self.assertIn('mode = "intermediate"', parent.content)
        self.assertIn('parent_config_slug = ""', parent.content)
        self.assertIn("level = 1", parent.content)
        self.assertIn("area_km2 = 25", parent.content)
        self.assertIn("pop_latest = 1000", parent.content)
        self.assertIn("density = 40", parent.content)
        config.refresh_from_db()
        self.assertIn('parent_config_slug = "corona"', config.content)
        self.assertIn("level = 2", config.content)

        with patch(
            "ciudades_del_mundo.web.views._new_country_config_preview_metric_values",
            side_effect=AssertionError("created-entities must use stored metrics"),
        ):
            parent_entities_response = self.client.get(
                "/new-countries/aa/testland/configs/new/created-entities/?q=Corona"
            )
        self.assertEqual(parent_entities_response.status_code, 200)
        parent_option = parent_entities_response.json()["results"][0]
        self.assertEqual(parent_option["slug"], "corona")
        self.assertEqual(parent_option["area_text"], "25")
        self.assertEqual(parent_option["population_text"], "1000")
        self.assertEqual(parent_option["density_text"], "40")

        detail_response = self.client.get("/new-countries/aa/testland/")
        self.assertEqual(detail_response.status_code, 200)
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn("<code>CORONA</code>", detail_html)
        self.assertIn("<code>CORONA-FROM_SUBDIVISION</code>", detail_html)
        self.assertNotIn("<code>TESTLAND-CORONA-FROM_SUBDIVISION</code>", detail_html)
        self.assertIn('data-tree-id="config:testland:corona"', detail_html)
        self.assertIn('data-tree-parent="config:testland:corona"', detail_html)
        self.assertIn('data-tree-depth="1"', detail_html)
        self.assertIn('<code class="new-country-assigned-code">CHILD</code>', detail_html)
        self.assertNotIn("derived-subdivision::aa::CHILD", detail_html)
        self.assertIn('data-tree-parent="config:testland:from_subdivision"', detail_html)
        self.assertIn('data-tree-depth="2"', detail_html)

        edit_child_response = self.client.get("/new-countries/aa/testland/configs/from_subdivision/")
        self.assertEqual(edit_child_response.status_code, 200)
        edit_child_html = edit_child_response.content.decode("utf-8")
        self.assertIn('name="slug" value="FROM_SUBDIVISION"', edit_child_html)
        self.assertNotIn('value="CORONA-FROM_SUBDIVISION"', edit_child_html)

    def test_new_country_config_create_reparents_same_code_child_without_duplicate_error(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Aragon",
            level=1,
            parent=root,
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        existing_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="ara",
            name="Reino de Aragon",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Reino de Aragon",
            entity_code="ara",
            entity_type="Reino",
            entity_level=1,
        )
        child = DerivedCountryConfig.objects.create(
            country=country,
            slug="ara",
            name="Reino de Aragon",
            source_country_code="aa",
            derived_country_code="testland",
            content=existing_content,
        )

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "ara",
                "name": "Corona de Aragon",
                "source_country_code": "aa",
                "entity_type": "Corona",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [],
                        "use_subdivisions": False,
                        "selected_child_config_slugs": ["ara"],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        parent = DerivedCountryConfig.objects.get(country=country, slug="ara")
        self.assertEqual(parent.name, "Corona de Aragon")
        self.assertIn('code = "ARA"', parent.content)
        child.refresh_from_db()
        self.assertEqual(child.slug, "ara_ara")
        self.assertIn('slug = "ara_ara"', child.content)
        self.assertIn('code = "ARA"', child.content)
        self.assertIn('parent_config_slug = "ara"', child.content)
        self.assertIn("level = 2", child.content)

        detail_response = self.client.get("/new-countries/aa/testland/")
        self.assertEqual(detail_response.status_code, 200)
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn("<code>ARA</code>", detail_html)
        self.assertIn("<code>ARA-ARA</code>", detail_html)

        edit_child_response = self.client.get("/new-countries/aa/testland/configs/ara_ara/")
        self.assertEqual(edit_child_response.status_code, 200)
        edit_child_html = edit_child_response.content.decode("utf-8")
        self.assertIn('name="slug" value="ARA"', edit_child_html)
        self.assertNotIn('name="slug" value="ara_ara"', edit_child_html)

    def test_new_country_config_edit_reparents_same_code_child_without_duplicate_error(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Alava",
            level=1,
            parent=root,
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        child_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="ala",
            name="Alava",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Alava",
            entity_code="ala",
            entity_type="Provincia",
            entity_level=1,
        )
        child = DerivedCountryConfig.objects.create(
            country=country,
            slug="ala",
            name="Alava",
            source_country_code="aa",
            derived_country_code="testland",
            content=child_content,
        )
        editable_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="draft",
            name="Borrador",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Borrador",
            entity_code="draft",
            entity_type="Region",
            entity_level=1,
        )
        editable = DerivedCountryConfig.objects.create(
            country=country,
            slug="draft",
            name="Borrador",
            source_country_code="aa",
            derived_country_code="testland",
            content=editable_content,
        )

        response = self.client.post(
            "/new-countries/aa/testland/configs/draft/",
            {
                "slug": "ala",
                "name": "Corona de Alava",
                "source_country_code": "aa",
                "entity_type": "Corona",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [],
                        "use_subdivisions": False,
                        "selected_child_config_slugs": ["ala"],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        editable.refresh_from_db()
        self.assertEqual(editable.slug, "ala")
        self.assertEqual(editable.name, "Corona de Alava")
        child.refresh_from_db()
        self.assertEqual(child.slug, "ala_ala")
        self.assertIn('parent_config_slug = "ala"', child.content)
        self.assertIn("level = 2", child.content)
        self.assertFalse(DerivedCountryConfig.objects.filter(country=country, slug="draft").exists())

    def test_new_country_config_created_subdivision_selector_supports_other_countries(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        AdminArea.objects.create(id="aa", country_code="aa", code="AA", name="Aaland", level=0)
        foreign_root = AdminArea.objects.create(id="bb", country_code="bb", code="BB", name="Betaland", level=0)
        foreign_region = AdminArea.objects.create(
            id="bb_1",
            country_code="bb",
            code="1",
            name="Foreign Region",
            level=1,
            parent=foreign_root,
            area_km2=Decimal("50.00"),
            pop_latest=2000,
        )
        foreign_city = AdminArea.objects.create(
            id="bb_city",
            country_code="bb",
            code="11",
            name="Foreign Capital",
            level=2,
            parent=foreign_region,
        )
        AdminArea.objects.create(
            id="bb_locality",
            country_code="bb",
            code="111",
            name="Scoped Locality",
            level=3,
            parent=foreign_city,
        )
        other_foreign_region = AdminArea.objects.create(
            id="bb_2",
            country_code="bb",
            code="2",
            name="Other Foreign Region",
            level=1,
            parent=foreign_root,
        )
        foreign_outside = AdminArea.objects.create(
            id="bb_outside",
            country_code="bb",
            code="21",
            name="Foreign Outside",
            level=2,
            parent=other_foreign_region,
        )
        DerivedSubdivision.objects.create(
            slug="bb_foreign",
            internal_name="FOREIGN",
            name="Foreign Child (Legacy)",
            source_country_code="bb",
            code="BB-FOREIGN",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "FOREIGN"',
                    'source_country_code = "bb"',
                    'name = "Foreign Child (Legacy)"',
                    'code = "BB-FOREIGN"',
                    'entity_type = "Provincia"',
                    'capitals = ["bb_city"]',
                    "",
                    "[[include]]",
                    'country_code = "bb"',
                    "level = 1",
                    'ids = ["bb_1"]',
                ]
            ),
        )

        response = self.client.get("/new-countries/aa/testland/configs/new/")
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/new-countries/aa/testland/configs/new/created-subdivisions/", html)
        self.assertIn('value="bb"', html)

        options_response = self.client.get("/new-countries/aa/testland/configs/new/created-subdivisions/?q=Foreign")
        self.assertEqual(options_response.status_code, 200)
        option = options_response.json()["results"][0]
        self.assertEqual(option["name"], "Foreign Child")
        self.assertEqual(option["selection_name"], "Foreign Child (Legacy)")
        self.assertEqual(option["label"], "Foreign Child - BB-FOREIGN")
        self.assertNotIn("(Legacy)", option["name"])
        self.assertIn("Foreign Child (Legacy)", option["search_text"])
        self.assertEqual(option["value"], "derived-subdivision::bb::FOREIGN")
        self.assertEqual(option["country_code"], "bb")

        filtered_response = self.client.get(
            "/new-countries/aa/testland/configs/new/created-subdivisions/?country=bb&q=Foreign"
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(len(filtered_response.json()["results"]), 1)
        empty_filtered_response = self.client.get(
            "/new-countries/aa/testland/configs/new/created-subdivisions/?country=aa&q=Foreign"
        )
        self.assertEqual(empty_filtered_response.status_code, 200)
        self.assertEqual(empty_filtered_response.json()["results"], [])

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "from_foreign",
                "name": "From Foreign",
                "source_country_code": "aa",
                "use_subdivisions": "1",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [],
                        "use_subdivisions": True,
                        "selected_subdivision_keys": ["derived-subdivision::bb::FOREIGN"],
                        "selected_derived_subdivisions": [{"country_code": "bb", "key": "FOREIGN"}],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="from_foreign")
        self.assertIn('country_code = "bb"', config.content)
        self.assertRegex(
            config.content,
            r'\[\[entities\.include\]\]\ncountry_code = "bb"\nlevel = 2\nderived_subdivisions = \["FOREIGN"\]',
        )
        self.assertIn('derived_subdivisions = ["FOREIGN"]', config.content)

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/capital-options/",
            data=json.dumps(
                {
                    "use_subdivisions": True,
                    "selected_derived_subdivisions": [{"country_code": "bb", "key": "FOREIGN"}],
                    "query": "Fo",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(foreign_city.id, values)
        self.assertNotIn(foreign_outside.id, values)

    def test_new_country_config_created_subdivision_selector_searches_sql_content(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="spain")
        AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        portugal = AdminArea.objects.create(id="portugal", country_code="portugal", code="PT", name="Portugal", level=0)
        AdminArea.objects.create(
            id="portugal_11",
            country_code="portugal",
            code="11",
            name="Lisboa",
            level=1,
            parent=portugal,
        )
        DerivedSubdivision.objects.create(
            slug="portugal_reduced",
            internal_name="REDUCED",
            name="Reduced Province",
            source_country_code="portugal",
            code="POR-RED",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "REDUCED"',
                    'source_country_code = "portugal"',
                    'name = "Reduced Province"',
                    'code = "POR-RED"',
                    'entity_type = "Provincia"',
                    "",
                    "[[include]]",
                    'country_code = "portugal"',
                    "level = 1",
                    'names = ["Lisboa"]',
                ]
            ),
        )

        response = self.client.get(
            "/new-countries/spain/testland/configs/new/created-subdivisions/?country=portugal&q=Lisboa"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        option = payload["results"][0]
        self.assertEqual(option["value"], "derived-subdivision::portugal::REDUCED")
        self.assertEqual(option["name"], "Reduced Province")
        self.assertIn("Lisboa", option["search_text"])

    def test_new_country_detail_uses_include_level_for_assigned_created_subdivisions(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="AA", name="Aaland", level=0)
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
            area_km2=Decimal("25.00"),
            pop_latest=1000,
        )
        DerivedSubdivision.objects.create(
            slug="aa_alava_b",
            internal_name="ALAVA_B",
            name="Alava B (Legacy)",
            source_country_code="aa",
            code="ALAVA_B",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ALAVA_B"',
                    'source_country_code = "aa"',
                    'name = "Alava B (Legacy)"',
                    'code = "ALAVA_B"',
                    'entity_type = "Provincia"',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 1",
                    'ids = ["aa_1"]',
                ]
            ),
        )
        parent_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="corona",
            name="Corona",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Corona",
            entity_code="CORONA",
            entity_type="Corona",
            entity_level=1,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="corona",
            name="Corona",
            source_country_code="aa",
            derived_country_code="testland",
            content=parent_content,
        )
        child_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="reino",
            name="Reino",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[],
            selected_derived_subdivisions=[{"country_code": "aa", "key": "ALAVA_B"}],
            entity_name="Reino",
            entity_code="REINO",
            entity_type="Reino",
            use_subdivisions=True,
            parent_config_slug="corona",
            entity_level=2,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="reino",
            name="Reino",
            source_country_code="aa",
            derived_country_code="testland",
            content=child_content,
        )

        child = DerivedCountryConfig.objects.get(country=country, slug="reino")
        self.assertRegex(
            child.content,
            r'\[\[entities\.include\]\]\ncountry_code = "aa"\nlevel = 3\nderived_subdivisions = \["ALAVA_B"\]',
        )
        detail_response = self.client.get("/new-countries/aa/testland/")
        detail_html = detail_response.content.decode("utf-8")
        self.assertIn('<code class="new-country-assigned-code">ALAVA_B</code>', detail_html)
        self.assertIn('<td class="new-country-tree-level">NV 3</td>', detail_html)
        self.assertIn("Alava B", detail_html)
        self.assertNotIn("Alava B (Legacy)", detail_html)

    def test_new_country_config_edit_uses_visual_create_form(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_1", country_code="aa", code="1", name="Region", level=1, parent=root)
        city = AdminArea.objects.create(id="aa_city", country_code="aa", code="11", name="Capital City", level=2, parent=region)
        DerivedSubdivision.objects.create(
            slug="aa_child",
            internal_name="CHILD",
            name="Child",
            source_country_code="aa",
            code="AA-CHILD",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CHILD"',
                    'source_country_code = "aa"',
                    'name = "Child"',
                    'code = "AA-CHILD"',
                    'entity_type = "Provincia"',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 1",
                    'ids = ["aa_1"]',
                ]
            ),
        )
        content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="from_subdivision",
            name="From Subdivision",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[],
            selected_derived_subdivisions=["CHILD"],
            entity_name="From Subdivision",
            entity_code="from_subdivision",
            entity_type="Region",
            use_subdivisions=True,
            entity_level=1,
            capitals=[city.id],
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="from_subdivision",
            name="From Subdivision",
            source_country_code="aa",
            derived_country_code="testland",
            content=content,
        )

        response = self.client.get("/new-countries/aa/testland/configs/from_subdivision/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("group-entry-main-box derived-create-main-box", html)
        self.assertIn("derived-tree-panel derived-create-source-panel", html)
        self.assertIn("data-new-country-capital-select", html)
        self.assertIn("Guardar", html)
        self.assertNotIn("Vista", html)
        self.assertNotIn("/view/", html)
        self.assertIn('name="slug" value="FROM_SUBDIVISION"', html)
        self.assertIn("aa_city", html)
        self.assertIn("derived-subdivision::aa::CHILD", html)
        self.assertNotIn("<label>\n      TOML", html)
        self.assertNotIn("new_country_config_form.html", html)

    def test_new_country_config_edit_saves_visual_form_and_renames_parent_references(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_1", country_code="aa", code="1", name="Region", level=1, parent=root)
        city = AdminArea.objects.create(id="aa_city", country_code="aa", code="11", name="Capital City", level=2, parent=region)
        parent_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="parent",
            name="Parent",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[region.id],
            entity_name="Parent",
            entity_code="parent",
            entity_type="Region",
            entity_level=1,
            capitals=[],
        )
        child_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="child",
            name="Child",
            source_country_code="aa",
            derived_country_code="testland",
            selected_ids=[city.id],
            entity_name="Child",
            entity_code="child",
            entity_type="Municipio",
            parent_config_slug="parent",
            entity_level=2,
            capitals=[],
        )
        parent = DerivedCountryConfig.objects.create(
            country=country,
            slug="parent",
            name="Parent",
            source_country_code="aa",
            derived_country_code="testland",
            content=parent_content,
        )
        child = DerivedCountryConfig.objects.create(
            country=country,
            slug="child",
            name="Child",
            source_country_code="aa",
            derived_country_code="testland",
            content=child_content,
        )

        response = self.client.post(
            "/new-countries/aa/testland/configs/parent/",
            {
                "slug": "parent_renamed",
                "name": "Parent Renamed",
                "source_country_code": "aa",
                "parent_config_slug": "",
                "entity_type": "Region",
                "capitals": [city.id],
                "selection_json": json.dumps(
                    {
                        "selected_ids": [region.id],
                        "use_subdivisions": False,
                        "selected_subdivision_keys": [],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/new-countries/aa/testland/configs/parent_renamed/")
        self.assertFalse(DerivedCountryConfig.objects.filter(country=country, slug="parent").exists())
        renamed = DerivedCountryConfig.objects.get(country=country, slug="parent_renamed")
        self.assertEqual(renamed.name, "Parent Renamed")
        self.assertIn('slug = "parent_renamed"', renamed.content)
        self.assertIn('name = "Parent Renamed"', renamed.content)
        self.assertIn('capitals = ["aa_city"]', renamed.content)
        child.refresh_from_db()
        self.assertIn('parent_config_slug = "parent_renamed"', child.content)
        self.assertNotIn('parent_config_slug = "parent"', child.content)

    def test_new_country_config_capital_options_filter_created_subdivisions(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(id="aa", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_1", country_code="aa", code="1", name="Region", level=1, parent=root)
        other_region = AdminArea.objects.create(
            id="aa_2",
            country_code="aa",
            code="2",
            name="Other Region",
            level=1,
            parent=root,
        )
        capital = AdminArea.objects.create(
            id="aa_capital",
            country_code="aa",
            code="11",
            name="Capital City",
            level=2,
            parent=region,
        )
        outside = AdminArea.objects.create(
            id="aa_outside",
            country_code="aa",
            code="21",
            name="Capital Outside",
            level=2,
            parent=other_region,
        )
        DerivedSubdivision.objects.create(
            slug="aa_child",
            internal_name="CHILD",
            name="Child",
            source_country_code="aa",
            code="AA-CHILD",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CHILD"',
                    'source_country_code = "aa"',
                    'name = "Child"',
                    'code = "AA-CHILD"',
                    'entity_type = "Provincia"',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 1",
                    'ids = ["aa_1"]',
                ]
            ),
        )

        response = self.client.post(
            "/new-countries/aa/testland/configs/new/capital-options/",
            data=json.dumps({"selected_subdivision_keys": ["CHILD"], "query": "Ca"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(capital.id, values)
        self.assertNotIn(outside.id, values)

        empty_response = self.client.post(
            "/new-countries/aa/testland/configs/new/capital-options/",
            data=json.dumps({"use_subdivisions": True, "selected_subdivision_keys": [], "query": "Ca"}),
            content_type="application/json",
        )

        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(empty_response.json()["capital_options"], [])

        selected_response = self.client.post(
            "/new-countries/aa/testland/configs/new/capital-options/",
            data=json.dumps({"use_subdivisions": True, "selected_subdivision_keys": ["CHILD"], "capitals": [capital.id]}),
            content_type="application/json",
        )

        self.assertEqual(selected_response.status_code, 200)
        self.assertEqual(selected_response.json()["capital"], capital.id)
        self.assertEqual(selected_response.json()["capitals"], [capital.id])

    def test_new_country_config_capital_options_filter_selected_created_child_entities(self):
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="EspaÃ±a perfecta", source_country_code="spain")
        AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="EspaÃ±a", level=0)
        portugal = AdminArea.objects.create(id="portugal", country_code="portugal", code="PT", name="Portugal", level=0)
        district = AdminArea.objects.create(
            id="portugal_11",
            country_code="portugal",
            code="11",
            name="Lisboa",
            level=1,
            parent=portugal,
        )
        lisboa_municipality = AdminArea.objects.create(
            id="portugal_1106",
            country_code="portugal",
            code="1106",
            name="Lisboa",
            level=2,
            parent=district,
        )
        lisboa = AdminArea.objects.create(
            id="portugal_110601",
            country_code="portugal",
            code="110601",
            name="Lisboa",
            level=3,
            parent=lisboa_municipality,
        )
        porto_municipality = AdminArea.objects.create(
            id="portugal_1312",
            country_code="portugal",
            code="1312",
            name="Porto",
            level=2,
            parent=portugal,
        )
        porto = AdminArea.objects.create(
            id="portugal_131201",
            country_code="portugal",
            code="131201",
            name="Porto",
            level=3,
            parent=porto_municipality,
        )
        portugal_content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="portugal",
            name="Portugal",
            source_country_code="portugal",
            derived_country_code=country.slug,
            selected_ids=[portugal.id],
            entity_name="Portugal",
            entity_code="PORTUGAL",
            entity_type="Pais",
            parent_config_slug="iberia",
            entity_level=2,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="iberia",
            name="Iberia",
            source_country_code="spain",
            derived_country_code=country.slug,
            content="\n".join(
                [
                    "schema_version = 1",
                    'kind = "derived_country_config"',
                    'country = "esp_perfecta"',
                    'slug = "iberia"',
                    'name = "Iberia"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    'parent_config_slug = ""',
                    "",
                    "[[entities]]",
                    'mode = "intermediate"',
                    'name = "Iberia"',
                    'code = "IBERIA"',
                    'entity_type = "Corona"',
                    "level = 1",
                    'parent_config_slug = ""',
                    "capitals = []",
                ]
            ),
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="portugal",
            name="Portugal",
            source_country_code="portugal",
            derived_country_code=country.slug,
            content=portugal_content,
        )

        empty_response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/new/capital-options/",
            data=json.dumps({"query": "Lisboa"}),
            content_type="application/json",
        )
        self.assertEqual(empty_response.status_code, 200)
        self.assertNotIn(lisboa.id, [row["value"] for row in empty_response.json()["capital_options"]])

        response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/new/capital-options/",
            data=json.dumps({"selected_child_config_slugs": ["portugal"], "query": "Lisboa"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(lisboa.id, values)
        self.assertNotIn(porto.id, values)

        selected_response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/new/capital-options/",
            data=json.dumps({"selected_child_config_slugs": ["portugal"], "capitals": [lisboa.id]}),
            content_type="application/json",
        )
        self.assertEqual(selected_response.status_code, 200)
        self.assertEqual(selected_response.json()["capital"], lisboa.id)
        self.assertEqual(selected_response.json()["capitals"], [lisboa.id])

        edit_response = self.client.get("/new-countries/spain/esp_perfecta/configs/iberia/")
        self.assertEqual(edit_response.status_code, 200)
        self.assertIn(
            'data-new-country-capital-options-url="/new-countries/spain/esp_perfecta/configs/iberia/capital-options/"',
            edit_response.content.decode("utf-8"),
        )
        inferred_response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/iberia/capital-options/",
            data=json.dumps({"query": "Lisboa"}),
            content_type="application/json",
        )
        self.assertEqual(inferred_response.status_code, 200)
        self.assertIn(lisboa.id, [row["value"] for row in inferred_response.json()["capital_options"]])

    def test_new_country_config_capital_options_infer_current_created_subdivisions(self):
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="Espana perfecta", source_country_code="spain")
        AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        portugal = AdminArea.objects.create(id="portugal", country_code="portugal", code="PT", name="Portugal", level=0)
        district = AdminArea.objects.create(
            id="portugal_11",
            country_code="portugal",
            code="11",
            name="Lisboa",
            level=1,
            parent=portugal,
        )
        lisboa = AdminArea.objects.create(
            id="portugal_1106",
            country_code="portugal",
            code="1106",
            name="Lisboa",
            level=2,
            parent=district,
        )
        porto = AdminArea.objects.create(
            id="portugal_1312",
            country_code="portugal",
            code="1312",
            name="Porto",
            level=2,
            parent=portugal,
        )
        DerivedSubdivision.objects.create(
            slug="portugal_estremadura",
            internal_name="ESTREMADURA",
            name="Extremadura",
            source_country_code="portugal",
            code="POR-EXT",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ESTREMADURA"',
                    'source_country_code = "portugal"',
                    'name = "Extremadura"',
                    'code = "POR-EXT"',
                    'entity_type = "Provincia"',
                    'capitals = ["portugal_1106"]',
                    "",
                    "[[include]]",
                    'country_code = "portugal"',
                    "level = 1",
                    'ids = ["portugal_11"]',
                ]
            ),
        )
        content = render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug="por_por",
            name="Portugal",
            source_country_code="spain",
            derived_country_code=country.slug,
            selected_ids=[],
            selected_derived_subdivisions=[{"country_code": "portugal", "key": "ESTREMADURA", "level": 3}],
            entity_name="Portugal",
            entity_code="POR",
            entity_type="Reino",
            use_subdivisions=True,
            parent_config_slug="por",
            entity_level=2,
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="por_por",
            name="Portugal",
            source_country_code="spain",
            derived_country_code=country.slug,
            content=content,
        )

        response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/por_por/capital-options/",
            data=json.dumps({"query": "Lisboa"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(lisboa.id, values)
        self.assertNotIn(porto.id, values)

        empty_visual_payload_response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/por_por/capital-options/",
            data=json.dumps(
                {
                    "use_subdivisions": True,
                    "selected_ids": [],
                    "selected_derived_subdivisions": [],
                    "query": "Lisboa",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(empty_visual_payload_response.status_code, 200)
        self.assertIn(lisboa.id, [row["value"] for row in empty_visual_payload_response.json()["capital_options"]])

        cleared_response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/por_por/capital-options/",
            data=json.dumps(
                {
                    "use_subdivisions": True,
                    "selected_ids": [],
                    "selected_derived_subdivisions": [],
                    "selection_touched": True,
                    "query": "Lisboa",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(cleared_response.status_code, 200)
        self.assertEqual(cleared_response.json()["capital_options"], [])

    def test_new_country_config_capital_options_match_display_alias_inside_selected_subdivision(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="spain")
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="España", level=0)
        gipuzkoa = AdminArea.objects.create(
            id="spain_20",
            country_code="spain",
            code="20",
            name="Gipuzkoa",
            level=2,
            parent=root,
        )
        madrid = AdminArea.objects.create(
            id="spain_28",
            country_code="spain",
            code="28",
            name="Madrid",
            level=2,
            parent=root,
        )
        donostia = AdminArea.objects.create(
            id="spain_20069",
            country_code="spain",
            code="20069",
            name="Donostia",
            level=3,
            parent=gipuzkoa,
        )
        outside = AdminArea.objects.create(
            id="spain_28134",
            country_code="spain",
            code="28134",
            name="San Sebastián de los Reyes",
            level=3,
            parent=madrid,
        )
        DerivedSubdivision.objects.create(
            slug="spain_gipuzkoa",
            internal_name="GIPUZKOA",
            name="Guipúzcoa",
            source_country_code="spain",
            code="GIP",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "GIPUZKOA"',
                    'source_country_code = "spain"',
                    'name = "Guipúzcoa"',
                    'code = "GIP"',
                    'entity_type = "Provincia"',
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_20"]',
                ]
            ),
        )

        with translation.override("es"):
            response = self.client.post(
                "/new-countries/spain/testland/configs/new/capital-options/",
                data=json.dumps(
                    {
                        "use_subdivisions": True,
                        "selected_subdivision_keys": ["GIPUZKOA"],
                        "query": "San Sebastián",
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(donostia.id, values)
        self.assertNotIn(outside.id, values)

    def test_new_country_sql_config_builds_nuevo_admin_tree(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(
            id="aa",
            country_code="aa",
            code="AA",
            name="Source Country",
            level=0,
        )
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
        )
        AdminArea.objects.create(
            id="aa_11",
            country_code="aa",
            code="11",
            name="City",
            level=2,
            parent=region,
            entity_type="Municipality",
            area_km2=Decimal("12.5"),
            pop_latest=321,
        )

        self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "visual",
                "name": "Visual",
                "source_country_code": "aa",
                "entity_type": "Region",
                "selection_json": json.dumps({"selected_ids": [region.id]}),
            },
        )
        self.client.post(
            "/new-countries/aa/testland/configs/new/",
            {
                "slug": "district",
                "name": "District",
                "source_country_code": "aa",
                "parent_config_slug": "visual",
                "entity_type": "Municipality",
                "selection_json": json.dumps({"selected_ids": ["aa_11"]}),
            },
        )

        call_command("build_new_subdivisions", "--country-id", "testland")

        root_area = NuevoAdminArea.objects.get(id="testland")
        self.assertEqual(root_area.name, "Testland")
        entity = root_area.children.get(name="Visual")
        self.assertEqual(entity.level, 1)
        self.assertEqual(entity.name, "Visual")
        self.assertEqual(entity.code, "VISUAL")
        self.assertEqual(entity.entity_type, "Region")
        self.assertEqual(entity.pop_latest, 321)
        self.assertTrue(entity.children.filter(name="City").exists())
        district = entity.children.get(name="District")
        self.assertEqual(district.level, 2)
        self.assertEqual(district.code, "VISUAL-DISTRICT")
        self.assertEqual(district.entity_type, "Municipality")
        self.assertEqual(district.parent_id, entity.id)

    def test_new_country_source_children_endpoint_returns_direct_children(self):
        root = AdminArea.objects.create(
            id="bb",
            country_code="bb",
            code="bb",
            name="Source Country",
            level=0,
        )
        region = AdminArea.objects.create(
            id="bb_1",
            country_code="bb",
            code="1",
            name="Region",
            level=1,
            parent=root,
        )
        AdminArea.objects.create(
            id="bb_11",
            country_code="bb",
            code="11",
            name="City",
            level=2,
            parent=region,
        )

        response = self.client.get("/new-countries/source-children/?country_code=bb")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["children"]], ["bb_1"])
        self.assertTrue(payload["children"][0]["has_children"])

    def test_new_country_source_area_options_require_country_and_level_filters(self):
        DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="spain")
        root = AdminArea.objects.create(
            id="portugal_portugal",
            country_code="portugal",
            code="PT",
            name="Portugal",
            level=0,
        )
        lisboa = AdminArea.objects.create(
            id="portugal_11",
            country_code="portugal",
            code="11",
            name="Lisboa",
            level=1,
            parent=root,
            entity_type="District",
        )
        lisboa_municipality = AdminArea.objects.create(
            id="portugal_1711106",
            country_code="portugal",
            code="1711106",
            name="Lisboa",
            level=2,
            parent=lisboa,
            entity_type="Municipality",
        )
        amadora = AdminArea.objects.create(
            id="portugal_1711115",
            country_code="portugal",
            code="1711115",
            name="Amadora",
            level=2,
            parent=lisboa,
            entity_type="Municipality",
        )

        response = self.client.get("/new-countries/spain/testland/configs/new/source-areas/?country=portugal")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["page_size"], 20)
        self.assertEqual({row["value"] for row in payload["levels"]}, {"1", "2"})

        response = self.client.get(
            "/new-countries/spain/testland/configs/new/source-areas/?country=portugal&level=1&q=Lisboa"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["results"]], [lisboa.id])
        self.assertEqual(payload["results"][0]["country_code"], "portugal")
        self.assertEqual(payload["results"][0]["level"], 1)

        AdminArea.objects.bulk_create(
            [
                AdminArea(
                    id=f"portugal_17112{index:03d}",
                    country_code="portugal",
                    code=f"17112{index:03d}",
                    name=f"Area {index:03d}",
                    level=2,
                    parent=lisboa,
                    entity_type="Municipality",
                )
                for index in range(105)
            ]
        )
        response = self.client.get(
            "/new-countries/spain/testland/configs/new/source-areas/?country=portugal&level=2&page_size=500"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 100)
        self.assertEqual(payload["page_size"], 100)
        self.assertEqual(payload["total"], 107)
        self.assertEqual(payload["num_pages"], 2)

        response = self.client.get(
            "/new-countries/spain/testland/configs/new/source-areas/?country=portugal&level=2&q=Lisboa"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["results"][:2]], [lisboa_municipality.id, amadora.id])

    def test_new_country_config_use_subdivisions_accepts_sql_source_ids_from_other_country(self):
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="Espana Perfecta", source_country_code="spain")
        root = AdminArea.objects.create(
            id="portugal_portugal",
            country_code="portugal",
            code="PT",
            name="Portugal",
            level=0,
        )
        lisboa = AdminArea.objects.create(
            id="portugal_11",
            country_code="portugal",
            code="11",
            name="Lisboa",
            level=1,
            parent=root,
            entity_type="District",
            area_km2=Decimal("2761.00"),
            pop_latest=2295000,
        )

        response = self.client.post(
            "/new-countries/spain/esp_perfecta/configs/new/",
            {
                "slug": "por_por",
                "name": "Portugal",
                "source_country_code": "spain",
                "entity_type": "Reino",
                "use_subdivisions": "1",
                "selection_json": json.dumps(
                    {
                        "selected_ids": [lisboa.id],
                        "use_subdivisions": True,
                        "selected_derived_subdivisions": [],
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="por_por")
        self.assertIn('source_country_code = "spain"', config.content)
        self.assertIn("use_selected_entities_as_children = true", config.content)
        self.assertIn('country_code = "portugal"', config.content)
        self.assertRegex(
            config.content,
            r'\[\[entities\.include\]\]\ncountry_code = "portugal"\nlevel = 1\nids = \["portugal_11"\]',
        )

    def test_new_country_source_children_respects_legal_level_and_unified_cities(self):
        gibraltar = AdminArea.objects.create(
            id="gibraltar_gibraltar",
            country_code="gibraltar",
            code="gibraltar",
            name="Gibraltar",
            level=0,
        )
        ScrapingConfig.objects.update_or_create(
            slug="gibraltar",
            defaults={
                "country_code": "gibraltar",
                "name": "Gibraltar",
                "content": "LEGAL_SUBDIVISION = 0\n",
                "content_hash": "test",
            },
        )
        AdminArea.objects.create(
            id="gibraltar_001",
            country_code="gibraltar",
            code="001",
            name="Enumeration Area",
            level=1,
            parent=gibraltar,
        )
        response = self.client.get("/new-countries/source-children/?country_code=gibraltar")
        self.assertEqual(response.status_code, 200)
        gibraltar_payload = response.json()
        self.assertEqual([row["id"] for row in gibraltar_payload["children"]], [gibraltar.id])
        self.assertFalse(gibraltar_payload["children"][0]["has_children"])

        root = AdminArea.objects.create(id="mc_root", country_code="mc", code="mc", name="Merge Country", level=0)
        parent = AdminArea.objects.create(id="mc_parent", country_code="mc", code="p", name="Parent", level=1, parent=root)
        normal = AdminArea.objects.create(id="mc_normal", country_code="mc", code="n", name="Normal", level=2, parent=parent)
        unified = AdminArea.objects.create(
            id="mc_unified",
            country_code="mc",
            code="u",
            name="Unified City",
            level=2,
            parent=parent,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        AdminArea.objects.create(
            id="mc_source",
            country_code="mc",
            code="s",
            name="Source Commune",
            level=2,
            parent=parent,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )

        response = self.client.get(f"/new-countries/source-children/?country_code=mc&parent_id={parent.id}")

        self.assertEqual(response.status_code, 200)
        child_ids = [row["id"] for row in response.json()["children"]]
        self.assertEqual(child_ids, [normal.id, unified.id])

    def test_new_country_import_toml_queues_one_task_per_seed(self):
        with patch("ciudades_del_mundo.web.views.bundled_new_country_config_paths") as paths:
            paths.return_value = [Path("one.toml"), Path("two.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post("/new-countries/import-toml/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(start.call_args_list[0].kwargs["args"], ["sync_derived_configs", "new-countries", "one", "--force"])
        self.assertEqual(start.call_args_list[1].kwargs["args"], ["sync_derived_configs", "new-countries", "two", "--force"])

    def test_new_country_import_toml_ajax_returns_task_popup_payload(self):
        with patch("ciudades_del_mundo.web.views.bundled_new_country_config_paths") as paths:
            paths.return_value = [Path("one.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued-new-country", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post(
                    "/new-countries/import-toml/",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "queued-new-country")
        self.assertEqual(payload["status_url"], "/tasks/queued-new-country/status/")
        self.assertEqual(start.call_args.kwargs["args"], ["sync_derived_configs", "new-countries", "one", "--force"])

    def test_new_country_list_uses_container_cards_and_compact_actions(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0, pop_latest=1000)
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        DerivedCountryConfig.objects.create(
            country=country,
            slug="visual",
            name="Visual",
            source_country_code="aa",
            derived_country_code="visual_land",
            content='kind = "derived_country_config"\nsource_country_code = "aa"\nderived_country_code = "visual_land"\n',
        )
        NuevoAdminArea.objects.create(
            id="visual_land",
            country_code="visual_land",
            code="visual_land",
            name="Visual Land",
            level=0,
            pop_latest=900,
            area_km2=Decimal("12.5"),
        )

        response = self.client.get("/new-countries/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("data-group-country-card", html)
        self.assertIn("data-new-country-detail-card", html)
        self.assertIn("data-stats-country-detail", html)
        self.assertIn("new-country-panel-content", html)
        self.assertIn("/api/new-country-containers/testland/", html)
        self.assertIn(">XLS<", html)
        self.assertIn(">CFG<", html)
        self.assertNotIn("Buscar pais nuevo", html)
        self.assertNotIn('data-config-action="build"', html)
        self.assertNotIn("/task/export-excel/", html)
        self.assertNotIn("Crear pais nuevo", html)

        summary_response = self.client.get("/api/new-country-containers/")

        self.assertEqual(summary_response.status_code, 200)
        summary = summary_response.json()
        self.assertEqual(len(summary["countries"]), 1)
        row = summary["countries"][0]
        self.assertEqual(row["label"], "Visual Land")
        self.assertEqual(row["population"], 900)
        self.assertEqual(row["area_km2"], 12.5)
        self.assertEqual(row["detail_url"], "/api/new-country-containers/testland/")
        self.assertEqual(row["edit_url"], "/new-countries/aa/testland/")
        self.assertEqual(row["export_excel_url"], "/new-countries/aa/testland/export-excel/")
        self.assertNotIn("country", row)
        self.assertNotIn("root", row)

    def test_new_country_list_groups_config_rows_by_container_and_shows_config_metrics(self):
        AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="ESP_PERFECTA", source_country_code="spain")
        for slug, name, code, population, area in (
            ("castilla", "Castilla", "CAS", 100, "10.50"),
            ("portugal", "Portugal", "POR", 200, "20.50"),
        ):
            DerivedCountryConfig.objects.create(
                country=country,
                slug=slug,
                name=name,
                source_country_code="spain",
                derived_country_code="esp_perfecta",
                content="\n".join(
                    [
                        'kind = "derived_country_config"',
                        'source_country_code = "spain"',
                        'derived_country_code = "esp_perfecta"',
                        "",
                        "[[entities]]",
                        f'name = "{name}"',
                        f'code = "{code}"',
                        'entity_type = "Region"',
                        "level = 1",
                        f"pop_latest = {population}",
                        f"area_km2 = {area}",
                    ]
                ),
            )

        response = self.client.get("/api/new-country-containers/")

        self.assertEqual(response.status_code, 200)
        summary = response.json()
        self.assertEqual(len(summary["countries"]), 1)
        row = summary["countries"][0]
        self.assertEqual(row["label"], "ESP_PERFECTA")
        self.assertEqual(row["code"], "esp_perfecta")
        self.assertEqual(row["population"], 300)
        self.assertEqual(row["area_km2"], 31.0)
        self.assertEqual(row["detail_url"], "/api/new-country-containers/esp_perfecta/")
        self.assertEqual(row["edit_url"], "/new-countries/spain/esp_perfecta/")
        self.assertEqual(row["export_excel_url"], "/new-countries/spain/esp_perfecta/export-excel/")

        api_response = self.client.get("/api/new-country-containers/esp_perfecta/")

        self.assertEqual(api_response.status_code, 200)
        payload = api_response.json()
        self.assertEqual(payload["country"]["name"], "ESP_PERFECTA")
        self.assertEqual(payload["country"]["population"], 300)
        self.assertEqual(payload["country"]["area_km2"], 31.0)
        self.assertEqual(payload["country"]["subdivision_count"], 2)
        self.assertEqual(len(payload["table"]["rows"]), 2)
        self.assertEqual(len(payload["first_order"]["cards"]), 2)
        self.assertIn("population_chart", payload["first_order"])

    def test_new_country_unbuilt_config_detail_drills_into_child_configs(self):
        AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        province = AdminArea.objects.create(
            id="sp_prov",
            country_code="spain",
            code="PROV",
            name="Province",
            entity_type="Province",
            level=2,
            parent_id="spain",
            pop_latest=30,
            area_km2=Decimal("3.00"),
        )
        AdminArea.objects.create(
            id="sp_madrid",
            country_code="spain",
            code="MAD",
            name="Madrid",
            entity_type="Municipality",
            level=1,
            parent_id="spain",
            pop_latest=80,
            area_km2=Decimal("8.00"),
        )
        AdminArea.objects.create(
            id="sp_muni",
            country_code="spain",
            code="MUNI",
            name="Municipality",
            entity_type="Municipality",
            level=3,
            parent=province,
            pop_latest=12,
            area_km2=Decimal("1.20"),
        )
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="ESP_PERFECTA", source_country_code="spain")
        parent = DerivedCountryConfig.objects.create(
            country=country,
            slug="castilla",
            name="Castilla",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    "",
                    "[[entities]]",
                    'name = "Castilla"',
                    'code = "CAS"',
                    'entity_type = "Corona"',
                    "level = 1",
                    'capitals = ["sp_madrid"]',
                    "pop_latest = 100",
                    "area_km2 = 10",
                ]
            ),
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="leon",
            name="Leon",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    'parent_config_slug = "castilla"',
                    "",
                    "[selection]",
                    'include_ids = ["sp_prov"]',
                    "",
                    "[[entities]]",
                    'name = "Leon"',
                    'code = "LEO"',
                    'entity_type = "Region"',
                    "level = 2",
                    "pop_latest = 40",
                    "area_km2 = 4",
                ]
            ),
        )

        response = self.client.get("/api/new-country-containers/esp_perfecta/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["country"]["capital"], ["Madrid"])
        card = payload["first_order"]["cards"][0]
        self.assertEqual(card["name"], "Castilla")
        self.assertEqual(card["official_name"], "Corona Castilla")
        self.assertEqual(card["capital"], ["Madrid"])
        self.assertEqual(card["child_count"], 1)
        self.assertEqual(card["subdivision_count"], 1)
        self.assertEqual(
            card["detail_url"],
            "/api/new-country-containers/esp_perfecta/config-nodes/config:esp_perfecta:castilla/",
        )
        tree_response = self.client.get("/api/new-country-containers/esp_perfecta/config-tree/")
        self.assertEqual(tree_response.status_code, 200)
        tree_rows = tree_response.json()["rows"]
        self.assertEqual([row["entity_display_name"] for row in tree_rows], ["Castilla"])
        self.assertNotIn("Leon", response.content.decode("utf-8"))
        child_tree_response = self.client.get(
            "/api/new-country-containers/esp_perfecta/config-tree/?parent=config%3Aesp_perfecta%3Acastilla"
        )
        self.assertEqual(child_tree_response.status_code, 200)
        self.assertEqual(
            [row["entity_display_name"] for row in child_tree_response.json()["rows"]],
            ["Leon"],
        )
        level_tree_response = self.client.get("/api/new-country-containers/esp_perfecta/config-tree/?level=2")
        self.assertEqual(level_tree_response.status_code, 200)
        self.assertEqual(level_tree_response.json()["rows"][0]["entity_display_name"], "Leon")
        level_response = self.client.get("/api/new-country-containers/esp_perfecta/?level=2")
        self.assertEqual(level_response.status_code, 200)
        level_rows = level_response.json()["table"]["rows"]
        self.assertEqual(level_rows[0]["name"], "Leon")
        self.assertEqual(
            level_rows[0]["detail_url"],
            "/api/new-country-containers/esp_perfecta/config-nodes/config:esp_perfecta:leon/",
        )

        child_response = self.client.get(card["detail_url"])

        self.assertEqual(child_response.status_code, 200)
        child_payload = child_response.json()
        self.assertEqual(child_payload["area"]["name"], "Castilla")
        self.assertEqual(child_payload["area"]["official_name"], "Corona Castilla")
        self.assertEqual(child_payload["area"]["capital"], ["Madrid"])
        self.assertEqual(child_payload["area"]["subdivision_count"], 1)
        self.assertEqual(len(child_payload["children"]), 1)
        self.assertEqual(child_payload["children"][0]["name"], "Leon")
        self.assertEqual(
            child_payload["children"][0]["detail_url"],
            "/api/new-country-containers/esp_perfecta/config-nodes/config:esp_perfecta:leon/",
        )

        grandchild_response = self.client.get(child_payload["children"][0]["detail_url"])

        self.assertEqual(grandchild_response.status_code, 200)
        grandchild_payload = grandchild_response.json()
        self.assertEqual(grandchild_payload["area"]["name"], "Leon")
        self.assertEqual(len(grandchild_payload["children"]), 1)
        self.assertEqual(grandchild_payload["children"][0]["name"], "Province")
        self.assertEqual(grandchild_payload["children"][0]["child_count"], 1)
        self.assertEqual(grandchild_payload["children"][0]["detail_url"], "/api/admin-areas/sp_prov/")

        source_response = self.client.get(grandchild_payload["children"][0]["detail_url"])

        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(source_response.json()["children"][0]["id"], "sp_muni")

    def test_country_detail_level_selection_updates_first_order_payload(self):
        AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            entity_type="Country",
            level=0,
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        north = AdminArea.objects.create(
            id="sp_north",
            country_code="spain",
            code="N",
            name="North",
            entity_type="Autonomous Community",
            level=1,
            parent_id="spain",
            pop_latest=600,
            area_km2=Decimal("60.00"),
        )
        south = AdminArea.objects.create(
            id="sp_south",
            country_code="spain",
            code="S",
            name="South",
            entity_type="Autonomous Community",
            level=1,
            parent_id="spain",
            pop_latest=400,
            area_km2=Decimal("40.00"),
        )
        province_one = AdminArea.objects.create(
            id="sp_prov_one",
            country_code="spain",
            code="P1",
            name="Province One",
            entity_type="Province",
            level=2,
            parent=north,
            pop_latest=350,
            area_km2=Decimal("35.00"),
        )
        province_two = AdminArea.objects.create(
            id="sp_prov_two",
            country_code="spain",
            code="P2",
            name="Province Two",
            entity_type="Province",
            level=2,
            parent=north,
            pop_latest=250,
            area_km2=Decimal("25.00"),
        )
        AdminArea.objects.create(
            id="sp_prov_three",
            country_code="spain",
            code="P3",
            name="Province Three",
            entity_type="Province",
            level=2,
            parent=south,
            pop_latest=400,
            area_km2=Decimal("40.00"),
        )
        AdminArea.objects.create(
            id="sp_muni",
            country_code="spain",
            code="M",
            name="Municipality",
            entity_type="Municipality",
            level=3,
            parent=province_one,
            pop_latest=50,
            area_km2=Decimal("5.00"),
        )

        default_response = self.client.get("/api/countries/spain/")
        level_response = self.client.get("/api/countries/spain/?level=2")

        self.assertEqual(default_response.status_code, 200)
        default_payload = default_response.json()
        self.assertEqual(
            [row["name"] for row in default_payload["first_order"]["cards"]],
            ["North", "South"],
        )
        self.assertEqual(level_response.status_code, 200)
        level_payload = level_response.json()
        self.assertEqual(level_payload["selected_level"], 2)
        self.assertEqual(
            [row["name"] for row in level_payload["first_order"]["cards"]],
            ["Province One", "Province Three", "Province Two"],
        )
        self.assertEqual(len(level_payload["table"]["rows"]), 3)
        province_card = level_payload["first_order"]["cards"][0]
        self.assertEqual(province_card["population"], 350)
        self.assertEqual(province_card["area_km2"], 35.0)
        self.assertEqual(province_card["detail_url"], "/api/admin-areas/sp_prov_one/")

    def test_new_country_unbuilt_derived_subdivision_rows_expose_source_municipalities(self):
        AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        province = AdminArea.objects.create(
            id="sp_prov",
            country_code="spain",
            code="PROV",
            name="Province",
            entity_type="Province",
            level=2,
            parent_id="spain",
            pop_latest=30,
            area_km2=Decimal("3.00"),
        )
        AdminArea.objects.create(
            id="sp_muni",
            country_code="spain",
            code="MUNI",
            name="Municipality",
            entity_type="Municipality",
            level=3,
            parent=province,
            pop_latest=12,
            area_km2=Decimal("1.20"),
        )
        DerivedSubdivision.objects.create(
            slug="spain_created_region",
            internal_name="CREATED_REGION",
            name="Created Region",
            source_country_code="spain",
            entity_type="Region",
            code="REG",
            content="\n".join(
                [
                    'schema_version = 1',
                    'kind = "derived_subdivision"',
                    'internal_name = "CREATED_REGION"',
                    'source_country_code = "spain"',
                    'name = "Created Region"',
                    'code = "REG"',
                    'entity_type = "Region"',
                    "level = 1",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["sp_prov"]',
                ]
            ),
        )
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="ESP_PERFECTA", source_country_code="spain")
        parent = DerivedCountryConfig.objects.create(
            country=country,
            slug="castilla",
            name="Castilla",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    "",
                    "[[entities]]",
                    'name = "Castilla"',
                    'code = "CAS"',
                    'entity_type = "Crown"',
                    "level = 1",
                ]
            ),
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="leon",
            name="Leon",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    'parent_config_slug = "castilla"',
                    "",
                    "[[entities]]",
                    'name = "Leon"',
                    'code = "LEO"',
                    'entity_type = "Region"',
                    "level = 2",
                    "",
                    "[[entities.include]]",
                    'country_code = "spain"',
                    "level = 1",
                    'derived_subdivisions = ["CREATED_REGION"]',
                ]
            ),
        )

        leon_url = "/api/new-country-containers/esp_perfecta/config-nodes/config:esp_perfecta:leon/"
        response = self.client.get(leon_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["children"][0]["name"], "Created Region")
        self.assertEqual(payload["children"][0]["child_count"], 1)
        created_region_url = payload["children"][0]["detail_url"]
        self.assertEqual(
            created_region_url,
            "/api/new-country-containers/esp_perfecta/config-nodes/config:esp_perfecta:leon:derived:spain:CREATED_REGION/",
        )

        created_response = self.client.get(created_region_url)

        self.assertEqual(created_response.status_code, 200)
        created_payload = created_response.json()
        self.assertEqual(len(created_payload["children"]), 1)
        self.assertEqual(created_payload["children"][0]["name"], "Municipio")
        self.assertEqual(created_payload["children"][0]["level"], 4)
        self.assertIn(":source:sp_muni", created_payload["children"][0]["id"])

        level_three_response = self.client.get("/api/new-country-containers/esp_perfecta/?level=3")

        self.assertEqual(level_three_response.status_code, 200)
        level_three_payload = level_three_response.json()
        self.assertEqual(level_three_payload["table"]["rows"][0]["name"], "Created Region")
        self.assertEqual(level_three_payload["table"]["rows"][0]["population"], 12)
        self.assertEqual(level_three_payload["table"]["rows"][0]["area_km2"], 1.2)
        self.assertEqual(level_three_payload["first_order"]["cards"][0]["name"], "Created Region")
        self.assertEqual(level_three_payload["first_order"]["cards"][0]["population"], 12)
        self.assertEqual(level_three_payload["first_order"]["cards"][0]["area_km2"], 1.2)

        level_response = self.client.get("/api/new-country-containers/esp_perfecta/?level=4")

        self.assertEqual(level_response.status_code, 200)
        self.assertEqual(level_response.json()["table"]["rows"][0]["name"], "Municipio")

    def test_new_country_unbuilt_detail_paginates_large_assigned_source_level(self):
        AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        province = AdminArea.objects.create(
            id="sp_prov",
            country_code="spain",
            code="PROV",
            name="Province",
            entity_type="Province",
            level=2,
            parent_id="spain",
            pop_latest=300,
            area_km2=Decimal("30.00"),
        )
        AdminArea.objects.bulk_create(
            [
                AdminArea(
                    id=f"sp_muni_{index:04d}",
                    country_code="spain",
                    code=f"M{index:04d}",
                    name=f"Municipality {index:04d}",
                    entity_type="Municipality",
                    level=3,
                    parent=province,
                    pop_latest=1,
                    area_km2=Decimal("1.00"),
                )
                for index in range(1001)
            ]
        )
        DerivedSubdivision.objects.create(
            slug="spain_created_region",
            internal_name="CREATED_REGION",
            name="Created Region",
            source_country_code="spain",
            entity_type="Region",
            code="REG",
            content="\n".join(
                [
                    'schema_version = 1',
                    'kind = "derived_subdivision"',
                    'internal_name = "CREATED_REGION"',
                    'source_country_code = "spain"',
                    'name = "Created Region"',
                    'code = "REG"',
                    'entity_type = "Region"',
                    "level = 1",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["sp_prov"]',
                ]
            ),
        )
        country = DerivedCountry.objects.create(slug="esp_perfecta", name="ESP_PERFECTA", source_country_code="spain")
        DerivedCountryConfig.objects.create(
            country=country,
            slug="castilla",
            name="Castilla",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    "",
                    "[[entities]]",
                    'name = "Castilla"',
                    'code = "CAS"',
                    'entity_type = "Crown"',
                    "level = 1",
                ]
            ),
        )
        DerivedCountryConfig.objects.create(
            country=country,
            slug="leon",
            name="Leon",
            source_country_code="spain",
            derived_country_code="esp_perfecta",
            content="\n".join(
                [
                    'kind = "derived_country_config"',
                    'source_country_code = "spain"',
                    'derived_country_code = "esp_perfecta"',
                    'parent_config_slug = "castilla"',
                    "",
                    "[[entities]]",
                    'name = "Leon"',
                    'code = "LEO"',
                    'entity_type = "Region"',
                    "level = 2",
                    'capitals = ["sp_muni_0000"]',
                    "",
                    "[[entities.include]]",
                    'country_code = "spain"',
                    "level = 1",
                    'derived_subdivisions = ["CREATED_REGION"]',
                ]
            ),
        )

        response = self.client.get("/api/new-country-containers/esp_perfecta/")
        tree_response = self.client.get("/api/new-country-containers/esp_perfecta/config-tree/?level=4")
        level_response = self.client.get("/api/new-country-containers/esp_perfecta/?level=4")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["value"] for row in payload["levels"]], [1, 2, 3, 4])
        self.assertEqual(payload["country"]["capital"], ["Municipality 0000"])
        self.assertEqual(payload["country"]["subdivision_count"], 1001)
        self.assertEqual(level_response.status_code, 200)
        level_payload = level_response.json()
        self.assertEqual(level_payload["selected_level"], 4)
        self.assertEqual(len(level_payload["table"]["rows"]), 20)
        self.assertEqual(level_payload["table"]["rows"][0]["name"], "Municipality 0000")
        self.assertEqual(level_payload["table"]["pagination"]["page_size"], 20)
        self.assertEqual(level_payload["table"]["pagination"]["total"], 1001)
        self.assertEqual(tree_response.status_code, 200)
        tree_payload = tree_response.json()
        self.assertEqual(len(tree_payload["rows"]), 20)
        self.assertEqual(tree_payload["rows"][0]["entity_display_name"], "Municipality 0000")
        self.assertEqual(tree_payload["pagination"]["total"], 1001)

    def test_new_country_detail_api_returns_country_browser_payload(self):
        root = NuevoAdminArea.objects.create(
            id="imperio",
            country_code="imperio",
            code="imperio",
            name="Imperio",
            level=0,
            entity_type="Country",
            pop_latest=1000,
            area_km2=Decimal("100.00"),
        )
        region = NuevoAdminArea.objects.create(
            id="imperio-region",
            country_code="imperio",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            pop_latest=600,
            area_km2=Decimal("60.00"),
        )
        NuevoAdminArea.objects.create(
            id="imperio-city",
            country_code="imperio",
            code="city",
            name="City",
            level=2,
            entity_type="City",
            parent=region,
            pop_latest=300,
            area_km2=Decimal("10.00"),
        )

        response = self.client.get("/api/new-countries/imperio/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["country"]["name"], "Imperio")
        self.assertEqual(payload["country"]["population"], 1000)
        self.assertEqual(payload["table"]["rows"][0]["detail_url"], "/api/new-areas/imperio-region/")
        self.assertEqual(payload["first_order"]["cards"][0]["name"], "Region")
        self.assertIn("population_chart", payload["first_order"])

        level_response = self.client.get("/api/new-countries/imperio/?level=2")
        self.assertEqual(level_response.status_code, 200)
        level_payload = level_response.json()
        self.assertEqual(level_payload["selected_level"], 2)
        self.assertEqual(level_payload["first_order"]["cards"][0]["name"], "Ciudad")
        self.assertEqual(level_payload["first_order"]["cards"][0]["population"], 300)
        self.assertEqual(level_payload["first_order"]["cards"][0]["area_km2"], 10.0)

        area_response = self.client.get("/api/new-areas/imperio-region/")
        self.assertEqual(area_response.status_code, 200)
        area_payload = area_response.json()
        self.assertEqual(area_payload["area"]["name"], "Region")
        self.assertEqual(area_payload["children"][0]["name"], "Ciudad")

    def test_recipe_task_ajax_returns_task_popup_payload(self):
        with TemporaryDirectory() as tmpdir:
            recipe_root = Path(tmpdir)
            (recipe_root / "demo.py").write_text("ROOT_NAME = 'Demo'\nDIVISIONES = []\n", encoding="utf-8")
            with patch("ciudades_del_mundo.web.views.NEW_RECIPES_ROOT", recipe_root):
                with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                    start.return_value = _Object(id="queued-recipe", status=ManagedTask.Status.QUEUED, is_active=True)
                    response = self.client.post(
                        "/recipes/demo/task/build/",
                        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                        HTTP_ACCEPT="application/json",
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "queued-recipe")
        self.assertEqual(payload["status_url"], "/tasks/queued-recipe/status/")
        self.assertEqual(payload["detail_url"], "/tasks/queued-recipe/")
        self.assertEqual(start.call_args.kwargs["args"], ["build_new_subdivisions", "--country-id", "demo"])

    def test_group_flow_persists_toml(self):
        response = self.client.post(
            "/groups/groups/aa/new/",
            {
                "internal_name": "HISTORIC_GROUP",
                "name": "Historic group",
                "blocks_json": json.dumps([{"country_code": "aa", "names": ["One", "Two"]}]),
            },
        )

        self.assertEqual(response.status_code, 302)
        group = SubdivisionGroup.objects.get(slug="aa_historic_group")
        self.assertEqual(group.source_country_code, "aa")
        self.assertIn('HISTORIC_GROUP = ["One", "Two"]', group.content)
        self.assertIn("[[country_groups]]", group.content)
        self.assertIn('include_names = ["One", "Two"]', group.content)
        self.assertNotIn('kind = "subdivision_group"', group.content)
        self.assertNotIn('slug = "aa_historic_group"', group.content)
        self.assertNotIn('name = ', group.content)
        self.assertEqual(self.client.get("/groups/").status_code, 200)
        self.assertEqual(self.client.get("/groups/groups/aa/historic_group/").status_code, 200)

    def test_group_form_rejects_duplicate_internal_name_for_same_country(self):
        SubdivisionGroup.objects.create(
            slug="aa_existing_group",
            name="EXISTING_GROUP",
            source_country_code="aa",
            content='source_country_code = "aa"\n\nEXISTING_GROUP = []\n',
        )

        response = self.client.post(
            "/groups/groups/aa/new/",
            {
                "internal_name": "EXISTING_GROUP",
                "blocks_json": json.dumps([{"country_code": "aa", "names": ["One"]}]),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe un grupo")
        group = SubdivisionGroup.objects.get(slug="aa_existing_group")
        self.assertNotIn("One", group.content)

    def test_group_list_country_cards_open_country_panels_without_query_links(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="aa", name="Aaland", level=0, pop_latest=1000)
        AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="1",
            name="Imported Region",
            level=1,
            entity_type="Region",
            area_km2=12.5,
            pop_latest=123,
        )
        AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="11",
            name="Aaland City",
            level=2,
            entity_type="City",
            pop_latest=321,
        )
        AdminArea.objects.create(id="bb_root", country_code="bb", code="bb", name="Bebia", level=0, pop_latest=2000)
        derived_country = DerivedCountry.objects.create(
            slug="aa_custom_country",
            name="AA Custom Country",
            source_country_code="aa",
        )
        DerivedCountryConfig.objects.create(
            country=derived_country,
            slug="main",
            name="Main",
            source_country_code="aa",
            derived_country_code="aa_custom",
        )
        NuevoAdminArea.objects.create(
            id="aa_custom_root",
            country_code="aa_custom",
            code="root",
            name="AA Custom",
            level=0,
        )
        NuevoAdminArea.objects.create(
            id="aa_custom_region",
            country_code="aa_custom",
            code="created-region",
            name="Created Region",
            level=1,
            entity_type="Created Region Type",
            area_km2=34.5,
            pop_latest=456,
        )
        NuevoAdminArea.objects.create(
            id="aa-NEW_PROVINCE",
            country_code="aa",
            code="NEW_PROVINCE",
            name="New Province",
            level=1,
            entity_type="Province",
            area_km2=45.5,
            pop_latest=789,
        )
        SubdivisionGroup.objects.create(
            slug="aa_group",
            name="Aaland group",
            source_country_code="aa",
            content="\n".join(
                [
                    'kind = "subdivision_group"',
                    'source_country_code = "aa"',
                    "",
                    "[legacy]",
                    'python_source = "ALTA_CERDANA = [\\"One\\", \\"Two\\"]\\nPROVINCIA = {\\"name\\": \\"Provincia\\", \\"childs\\": []}"',
                    "",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_new_province",
            internal_name="NEW_PROVINCE",
            name="New Province",
            source_country_code="aa",
            entity_type="Province",
            content='kind = "derived_subdivision"\ninternal_name = "NEW_PROVINCE"\nsource_country_code = "aa"\n',
        )
        with TemporaryDirectory() as tmpdir:
            seed = Path(tmpdir) / "aa_seed.toml"
            seed.write_text(
                "\n".join(
                    [
                        "schema_version = 1",
                        'kind = "subdivision_group"',
                        'slug = "aa_seed"',
                        'name = "AA Seed"',
                        'source_country_code = "aa"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[seed]),
                patch(
                    "ciudades_del_mundo.web.views.bundled_derived_subdivision_paths",
                    return_value=[Path("subdivisions/aa.toml")],
                ),
            ):
                response = self.client.get("/groups/")

                self.assertEqual(response.status_code, 200)
                html = response.content.decode("utf-8")
                self.assertIn('class="stats-country-grid"', html)
                self.assertIn('class="stats-country-card group-country-card"', html)
                self.assertIn("data-group-country-card", html)
                self.assertIn('data-group-detail-target="groups-country-detail-1"', html)
                self.assertIn('data-group-detail-url="/groups/countries/aa/data/"', html)
                self.assertIn(">Aaland<", html)
                self.assertIn(">Bebia<", html)
                self.assertNotIn("?country=", html)
                self.assertIn('id="groups-country-detail-1"', html)
                self.assertIn("data-group-country-detail", html)
                self.assertIn("1 semillas TOML disponibles", html)
                self.assertIn('href="/groups/groups/aa/new/"', html)
                self.assertIn("data-group-panel-loading", html)
                self.assertIn("data-group-country-panel", html)
                self.assertIn("group-country-detail-grid", html)
                self.assertIn("Agrupaciones", html)
                self.assertIn("Nuevas divisiones", html)
                self.assertNotIn("Divisiones por nivel", html)
                self.assertNotIn(">BBDD<", html)
                self.assertNotIn("Sumar nivel", html)
                self.assertIn('data-local-table="1"', html)
                self.assertIn("data-client-pagination", html)
                self.assertNotIn("data-client-page-select", html)
                self.assertIn("group-table-pagination-foot", html)
                self.assertIn('data-initial-page-size="25"', html)
                self.assertNotIn('data-client-page="previous"', html)
                self.assertNotIn('data-client-page="next"', html)
                self.assertIn('type="search" name="q"', html)
                self.assertIn('name="page_size"', html)
                self.assertIn('name="level"', html)
                self.assertIn('<option value="25" selected>25</option>', html)
                self.assertIn('<option value="100">100</option>', html)
                self.assertIn("<th>Grupo</th>", html)
                self.assertIn("<th>Grupos</th>", html)
                self.assertNotIn('id="groups-cities-table-1"', html)
                self.assertNotIn('action="/groups/cities/aa/import-toml/"', html)
                self.assertNotIn('action="/groups/cities/aa/export-toml/"', html)
                self.assertNotIn('href="/groups/cities/aa/ciudades/"', html)
                self.assertIn('aria-label="Importar TOML"', html)
                self.assertIn('aria-label="Exportar TOML"', html)
                self.assertNotIn("Aaland City", html)
                self.assertNotIn('data-row-href="/groups/cities/aa/11/"', html)
                self.assertIn("Nombre de la entidad", html)
                self.assertIn("<th>Tipo</th>", html)
                self.assertNotIn("Tipo (Nivel)", html)
                self.assertIn("Terreno", html)
                self.assertIn("Población", html)
                self.assertNotIn('data-level="1"', html)
                self.assertNotIn("Created Region", html)
                self.assertNotIn("Created Region Type", html)
                self.assertNotIn(">34,5</td>", html)
                self.assertNotIn(">456</td>", html)
                self.assertNotIn('data-row-href="/groups/subdivisions/aa/new_province/"', html)
                self.assertNotIn("Imported Region", html)
                self.assertNotIn("<th>TOML</th>", html)
                self.assertNotIn("<th>Municipios</th>", html)
                self.assertNotIn("<th>Nombre interno</th>", html)
                self.assertNotIn("<th>Nombre</th>", html)
                self.assertNotIn("<th>Slug</th>", html)
                self.assertNotIn('data-row-href="/groups/groups/aa/alta_cerdana/"', html)
                self.assertIn('action="/groups/aa/export-toml/"', html)
                self.assertIn('action="/groups/import-toml/" data-config-task-form', html)
                self.assertIn('action="/subdivisions/import-toml/"', html)
                self.assertIn('name="queue_task" value="1"', html)
                self.assertIn('data-config-action="import-toml"', html)
                self.assertIn('action="/subdivisions/aa/export-toml/"', html)
                self.assertNotIn('action="/subdivisions/aa/build/"', html)
                self.assertIn('href="/groups/subdivisions/aa/new/"', html)
                self.assertIn('href="/groups/groups/aa/new/"', html)
                self.assertIn("<th>Acciones</th>", html)
                self.assertNotIn('action="/groups/subdivisions/aa/new_province/clone/"', html)
                self.assertNotIn('action="/groups/subdivisions/aa/new_province/delete/"', html)
                self.assertNotIn(">Editar<", html)
                self.assertNotIn("ALTA_CERDANA", html)
                self.assertNotIn('class="numeric group-municipality-count">2</td>', html)
                self.assertNotIn("PROVINCIA", html)
                self.assertNotIn("Pais fuente", html)

                detail_response = self.client.get("/groups/countries/aa/data/")
                self.assertEqual(detail_response.status_code, 200)
                payload = detail_response.json()
                self.assertTrue(payload["ok"])
                self.assertEqual([row["internal_name"] for row in payload["groups"]], ["ALTA_CERDANA"])
                self.assertEqual(payload["groups"][0]["municipality_count"], 2)
                self.assertEqual(len(payload["division_rows"]), 1)
                division = payload["division_rows"][0]
                self.assertEqual(division["name"], "New Province")
                self.assertEqual(division["type_text"], "Province")
                self.assertEqual(division["level"], 1)
                self.assertEqual(division["href"], "/groups/subdivisions/aa/new_province/")
                self.assertEqual(division["clone_url"], "/groups/subdivisions/aa/new_province/clone/")
                self.assertEqual(division["delete_url"], "/groups/subdivisions/aa/new_province/delete/")
                self.assertEqual(payload["level_rows"][0]["level"], 1)
                self.assertEqual(payload["level_rows"][0]["count"], 1)
                serialized_payload = json.dumps(payload)
                self.assertNotIn("Created Region", serialized_payload)
                self.assertNotIn("Imported Region", serialized_payload)
                self.assertNotIn("Aaland City", serialized_payload)

    def test_group_list_links_stale_materialized_subdivision_by_unique_name(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0, pop_latest=1000)
        NuevoAdminArea.objects.create(
            id="aa-ARA",
            country_code="aa",
            code="ARA",
            name="Aragon",
            level=1,
            entity_type="Reino",
        )
        DerivedSubdivision.objects.create(
            slug="aa_aragon",
            internal_name="ARAGON",
            name="Aragon",
            source_country_code="aa",
            entity_type="Reino",
            code="AA-ARAGON",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ARAGON"',
                    'source_country_code = "aa"',
                    'name = "Aragon"',
                    'code = "AA-ARAGON"',
                    'entity_type = "Reino"',
                    "level = 2",
                ]
            ),
        )

        with (
            patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[]),
            patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths", return_value=[]),
        ):
            response = self.client.get("/groups/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn("Aragon", html)
        self.assertNotIn('data-level="2"', html)
        detail_response = self.client.get("/groups/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        rows = detail_response.json()["division_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Aragon")
        self.assertEqual(rows[0]["level"], 2)
        self.assertEqual(rows[0]["type_text"], "Reino")
        self.assertEqual(rows[0]["href"], "/groups/subdivisions/aa/aragon/")
        self.assertNotIn("Reino (Nivel 1)", html)
        self.assertNotIn("Reino (Nivel 2)", html)
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/aragon/"', html)

    def test_group_list_shows_pending_sql_subdivision_before_build(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="AA",
            name="Aaland",
            level=0,
            pop_latest=1000,
        )
        AdminArea.objects.create(
            id="aa_sic_source",
            country_code="aa",
            code="SIC-SOURCE",
            name="Sicilia fuente",
            level=2,
            parent=root,
            entity_type="Provincia",
            area_km2=Decimal("25.00"),
            pop_latest=1200,
        )
        DerivedSubdivision.objects.create(
            slug="aa_sic",
            internal_name="SIC",
            name="Sicilia",
            source_country_code="aa",
            entity_type="Provincia",
            code="AA-SIC",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SIC"',
                    'source_country_code = "aa"',
                    'name = "Sicilia"',
                    'code = "AA-SIC"',
                    'entity_type = "Provincia"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 2",
                    'ids = ["aa_sic_source"]',
                ]
            ),
        )

        with (
            patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[]),
            patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths", return_value=[]),
        ):
            response = self.client.get("/groups/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn("Sicilia", html)
        self.assertNotIn("Provincia (Nivel 2)", html)
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/sic/"', html)
        self.assertIn("<th>Terreno</th>", html)
        self.assertIn("<th>Poblaci", html)
        self.assertNotIn('class="numeric">25</td>', html)
        self.assertNotIn('class="numeric">1200</td>', html)
        self.assertIn("<th>Acciones</th>", html)
        self.assertNotIn('action="/groups/subdivisions/aa/sic/clone/"', html)
        self.assertNotIn('action="/groups/subdivisions/aa/sic/delete/"', html)
        detail_response = self.client.get("/groups/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        rows = detail_response.json()["division_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Sicilia")
        self.assertEqual(rows[0]["type_text"], "Provincia")
        self.assertEqual(rows[0]["href"], "/groups/subdivisions/aa/sic/")
        self.assertEqual(rows[0]["area_text"], "25")
        self.assertEqual(rows[0]["population_text"], "1200")
        self.assertEqual(rows[0]["clone_url"], "/groups/subdivisions/aa/sic/clone/")
        self.assertEqual(rows[0]["delete_url"], "/groups/subdivisions/aa/sic/delete/")
        self.assertIn("aa_sic SIC Sicilia Provincia AA-SIC Sicilia Provincia 2 25 1200", rows[0]["search_text"])

    def test_group_list_uses_sql_preview_metrics_when_materialized_subdivision_is_stale(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="AA",
            name="Aaland",
            level=0,
            pop_latest=1000,
        )
        AdminArea.objects.create(
            id="aa_source",
            country_code="aa",
            code="SOURCE",
            name="Updated Source",
            level=2,
            parent=root,
            entity_type="Provincia",
            area_km2=Decimal("25.00"),
            pop_latest=1200,
        )
        materialized = NuevoAdminArea.objects.create(
            id="aa-AA-SIC",
            country_code="aa",
            code="AA-SIC",
            name="Sicilia",
            level=2,
            entity_type="Provincia",
            area_km2=Decimal("1.00"),
            pop_latest=1,
        )
        record = DerivedSubdivision.objects.create(
            slug="aa_sic",
            internal_name="SIC",
            name="Sicilia",
            source_country_code="aa",
            entity_type="Provincia",
            code="AA-SIC",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SIC"',
                    'source_country_code = "aa"',
                    'name = "Sicilia"',
                    'code = "AA-SIC"',
                    'entity_type = "Provincia"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 2",
                    'ids = ["aa_source"]',
                ]
            ),
        )
        older = timezone.now() - timedelta(days=1)
        NuevoAdminArea.objects.filter(pk=materialized.pk).update(updated_at=older)
        DerivedSubdivision.objects.filter(pk=record.pk).update(updated_at=timezone.now())

        with (
            patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[]),
            patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths", return_value=[]),
        ):
            response = self.client.get("/groups/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/sic/"', html)
        detail_response = self.client.get("/groups/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        rows = detail_response.json()["division_rows"]
        self.assertEqual(rows[0]["href"], "/groups/subdivisions/aa/sic/")
        self.assertEqual(rows[0]["area_text"], "25")
        self.assertEqual(rows[0]["population_text"], "1200")
        self.assertNotIn('class="numeric">1</td>', html)

    def test_group_division_delete_removes_sql_definition_and_materialized_rows(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0, pop_latest=1000)
        country = NuevoAdminArea.objects.create(
            id="aa-AA",
            country_code="aa",
            code="AA",
            name="Aaland",
            level=0,
        )
        target = NuevoAdminArea.objects.create(
            id="aa-AA-SIC",
            country_code="aa",
            code="AA-SIC",
            name="Sicilia",
            level=2,
            entity_type="Provincia",
            parent=country,
        )
        NuevoAdminArea.objects.create(
            id="aa-AA-SIC-CHILD",
            country_code="aa",
            code="AA-SIC-CHILD",
            name="Sicilia child",
            level=3,
            entity_type="Municipio",
            parent=target,
        )
        DerivedSubdivision.objects.create(
            slug="aa_sic",
            internal_name="SIC",
            name="Sicilia",
            source_country_code="aa",
            entity_type="Provincia",
            code="AA-SIC",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SIC"',
                    'source_country_code = "aa"',
                    'name = "Sicilia"',
                    'code = "AA-SIC"',
                    'parent_code = "AA"',
                    'entity_type = "Provincia"',
                    "level = 2",
                ]
            ),
        )

        response = self.client.post("/groups/subdivisions/aa/sic/delete/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/groups/")
        self.assertFalse(DerivedSubdivision.objects.filter(slug="aa_sic").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(id="aa-AA-SIC").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(id="aa-AA-SIC-CHILD").exists())
        self.assertTrue(NuevoAdminArea.objects.filter(id="aa-AA").exists())

    def test_group_division_delete_ajax_returns_json_without_redirect(self):
        country = NuevoAdminArea.objects.create(
            id="aa-AA",
            country_code="aa",
            code="AA",
            name="Aaland",
            level=0,
        )
        NuevoAdminArea.objects.create(
            id="aa-AA-SIC",
            country_code="aa",
            code="AA-SIC",
            name="Sicilia",
            level=2,
            entity_type="Provincia",
            parent=country,
        )
        DerivedSubdivision.objects.create(
            slug="aa_sic",
            internal_name="SIC",
            name="Sicilia",
            source_country_code="aa",
            entity_type="Provincia",
            code="AA-SIC",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SIC"',
                    'source_country_code = "aa"',
                    'name = "Sicilia"',
                    'code = "AA-SIC"',
                    'parent_code = "AA"',
                    'entity_type = "Provincia"',
                    "level = 2",
                ]
            ),
        )

        response = self.client.post(
            "/groups/subdivisions/aa/sic/delete/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["country_code"], "aa")
        self.assertEqual(payload["subdivision_slug"], "sic")
        self.assertEqual(payload["record_slug"], "aa_sic")
        self.assertEqual(payload["redirect_url"], "/groups/")
        self.assertFalse(DerivedSubdivision.objects.filter(slug="aa_sic").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(id="aa-AA-SIC").exists())
        self.assertTrue(NuevoAdminArea.objects.filter(id="aa-AA").exists())

    def test_group_list_links_duplicate_name_subdivisions_by_prefixed_internal_code(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0, pop_latest=1000)
        NuevoAdminArea.objects.create(
            id="aa-AA-CATALUNHA_A",
            country_code="aa",
            code="AA-CATALUNHA_A",
            name="Catalunya",
            level=1,
            entity_type="Principado",
        )
        NuevoAdminArea.objects.create(
            id="aa-AA-CATALUNHA_B",
            country_code="aa",
            code="AA-CATALUNHA_B",
            name="Catalunya",
            level=1,
            entity_type="Principado",
        )
        DerivedSubdivision.objects.create(
            slug="aa_catalunha_a",
            internal_name="CATALUNHA_A",
            name="Catalunya",
            source_country_code="aa",
            entity_type="Principado",
            code="AA-CATALUNHA_A",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CATALUNHA_A"',
                    'source_country_code = "aa"',
                    'name = "Catalunya"',
                    'code = "AA-CATALUNHA_A"',
                    'entity_type = "Principado"',
                    "level = 1",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_catalunha_b",
            internal_name="CATALUNHA_B",
            name="Catalunya",
            source_country_code="aa",
            entity_type="Principado",
            code="CAT",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CATALUNHA_B"',
                    'source_country_code = "aa"',
                    'name = "Catalunya"',
                    'code = "CAT"',
                    'entity_type = "Principado"',
                    "level = 1",
                ]
            ),
        )

        with (
            patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[]),
            patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths", return_value=[]),
        ):
            response = self.client.get("/groups/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/catalunha_a/"', html)
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/catalunha_b/"', html)
        detail_response = self.client.get("/groups/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        hrefs = {row["href"] for row in detail_response.json()["division_rows"]}
        self.assertIn("/groups/subdivisions/aa/catalunha_a/", hrefs)
        self.assertIn("/groups/subdivisions/aa/catalunha_b/", hrefs)

    def test_group_return_state_refreshes_stale_list_after_editor_changes(self):
        script = Path("ciudades_del_mundo/static/ciudades_del_mundo/app.js").read_text(encoding="utf-8")
        css = Path("ciudades_del_mundo/static/ciudades_del_mundo/app.css").read_text(encoding="utf-8")
        base_template = Path("ciudades_del_mundo/templates/ciudades_del_mundo/base.html").read_text(encoding="utf-8")
        derived_subdivision_form_template = Path(
            "ciudades_del_mundo/templates/ciudades_del_mundo/derived_subdivision_form.html"
        ).read_text(encoding="utf-8")
        new_country_config_template = Path(
            "ciudades_del_mundo/templates/ciudades_del_mundo/new_country_config_create.html"
        ).read_text(encoding="utf-8")

        self.assertIn("function reloadGroupListIfReturnNeedsFreshData", script)
        self.assertIn("state.refreshList = false;", script)
        self.assertIn("window.location.reload();", script)
        self.assertIn("markGroupReturnLinkNeedsFreshData(data.redirect_url || \"\");", script)
        self.assertIn("previousState.refreshList === true", script)
        self.assertIn("function loadGroupCountryDetailPanel", script)
        self.assertIn("fetchJson(url)", script)
        self.assertIn("renderGroupCountryDetailPayload(panel, data || {});", script)
        self.assertIn("target._groupDetailPromise.then(function ()", script)
        self.assertIn("stats-country-detail-grid--new-country-container", script)
        self.assertIn("renderStatsLevelControls(", script)
        self.assertIn("useNewCountryContainerLayout ? target : grid", script)
        self.assertNotIn("stats-country-detail-side-stack", script)
        self.assertIn("window.CiudadesSelect2 = {", script)
        self.assertIn("refresh: refreshSelect2Element", script)
        self.assertIn("matcher: select2NormalizedMatcher", script)
        self.assertIn('text.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "")', script)
        self.assertIn(".new-country-subentities-table-wrap {\n  margin-top: 30px;\n  overflow-x: hidden;", css)
        self.assertIn(".new-country-config-tree-table {\n  min-width: 0;", css)
        self.assertIn("code.new-country-assigned-code", css)
        self.assertIn(".new-country-panel-content {\n  align-items: start;\n  display: grid;\n  gap: 18px;\n  grid-template-columns: 1fr;", css)
        self.assertIn(".new-country-container-detail .stats-country-detail-grid--new-country-container", css)
        self.assertIn(".new-country-container-detail > .stats-country-level-controls", css)
        self.assertNotIn(".stats-country-detail-side-stack", css)
        self.assertIn("grid-template-columns: minmax(280px, 0.9fr) minmax(420px, 1.1fr);", css)
        self.assertIn(
            "grid-template-columns: minmax(240px, 1fr) minmax(190px, 230px) minmax(82px, 104px) minmax(220px, 30%);",
            css,
        )
        self.assertIn(".new-country-derived-subdivision-controls {\n  display: contents;", css)
        self.assertIn(".new-country-derived-subdivision-controls .select2-container", css)
        self.assertIn(".derived-subdivision-option-table {\n  overflow-x: auto;", css)
        self.assertIn(".derived-subdivision-option-table .group-table-toolbar.new-country-derived-subdivision-toolbar", css)
        self.assertIn("min-width: 920px;", css)
        self.assertIn(".derived-subdivision-option-table .group-division-table-wrap", css)
        self.assertIn(
            ".select2-container--default .select2-selection--multiple .select2-selection__choice",
            css,
        )
        self.assertIn(".derived-source-modal-kind-filter.is-checked", css)
        self.assertIn(".config-city-modal {\n  align-items: center;", css)
        self.assertIn("z-index: 1100;", css)
        self.assertIn("var visibleName = option.selection_name || option.name || \"\";", new_country_config_template)
        self.assertIn("window.CiudadesSelect2.refresh(derivedSubdivisionCountryFilter);", new_country_config_template)
        self.assertIn('createdEntityRows.querySelectorAll("[data-created-entity-option]:checked")', new_country_config_template)
        self.assertIn("selected_child_config_slugs: selectedChildConfigSlugs(),", new_country_config_template)
        self.assertNotIn('forgetCreatedEntity(checkbox.value);\n      refreshCapitalOptions("");', new_country_config_template)
        self.assertIn('nav.hidden = nav.classList.contains("group-table-pagination-foot") && total <= pageSize;', new_country_config_template)
        self.assertIn("width: 144px;", css)
        self.assertIn("min-width: 144px;", css)
        self.assertIn("emptyCell.colSpan = 3;", derived_subdivision_form_template)
        self.assertIn(".group-division-clone-button", css)
        self.assertIn("function formatDecimalNoGrouping", script)
        self.assertIn("replace(/[^A-Z0-9_-]+/g", script)
        self.assertNotIn("Intl.NumberFormat", script)
        self.assertIn("20260822-new-country-sql-picker", base_template)

    def test_task_popup_accepts_all_web_task_start_urls(self):
        script = Path("ciudades_del_mundo/static/ciudades_del_mundo/app.js").read_text(encoding="utf-8")

        self.assertIn("/\\/configs\\/import-toml\\/?$/", script)
        self.assertIn("/\\/configs\\/all\\/task\\/[^/]+\\/?$/", script)
        self.assertIn("/\\/recipes\\/[^/]+\\/task\\/[^/]+\\/?$/", script)
        self.assertIn("/\\/new-countries\\/[^/]+\\/[^/]+\\/configs\\/[^/]+\\/task\\/[^/]+\\/?$/", script)
        self.assertIn("/\\/new-countries\\/import-toml\\/?$/", script)
        self.assertIn("/\\/groups\\/import-toml\\/?$/", script)
        self.assertIn("/\\/subdivisions\\/import-toml\\/?$/", script)
        self.assertIn("/\\/subdivisions\\/[^/]+\\/build\\/?$/", script)

    def test_derived_subdivision_list_uses_country_cards_and_groups(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="aa", name="Aaland", level=0, pop_latest=1000)
        SubdivisionGroup.objects.create(
            slug="aa_albacete_a_cuenca",
            name="ALBACETE_A_CUENCA",
            source_country_code="aa",
            content='\n'.join(
                [
                    'source_country_code = "aa"',
                    "",
                    'ALBACETE_A_CUENCA = ["One", "Two"]',
                    "",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_murcia",
            internal_name="MURCIA",
            name="Murcia",
            source_country_code="aa",
            entity_type="Reino",
            code="MUR",
            content='\n'.join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "MURCIA"',
                    'source_country_code = "aa"',
                    'name = "Murcia"',
                    'code = "MUR"',
                    'entity_type = "Reino"',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 2",
                    'names = ["Albacete"]',
                    'groups = []',
                    "",
                    "[[subtract]]",
                    'country_code = "aa"',
                    "level = 3",
                    "names = []",
                    'groups = ["ALBACETE_A_CUENCA"]',
                    "",
                ]
            ),
        )

        with patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths", return_value=[]):
            response = self.client.get("/subdivisions/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('action="/subdivisions/import-toml/" data-config-task-form', html)
        self.assertIn('name="queue_task" value="1"', html)
        self.assertIn('action="/subdivisions/aa/build/" data-config-task-form', html)
        self.assertIn('data-config-action="popular"', html)
        self.assertNotIn('action="/groups/subdivisions/aa/murcia/clone/"', html)
        self.assertNotIn("<code>MUR</code>", html)
        detail_response = self.client.get("/subdivisions/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        self.assertEqual(detail_payload["subdivisions"][0]["display_code"], "MUR")
        self.assertEqual(detail_payload["subdivisions"][0]["name"], "Murcia")
        self.assertEqual(detail_payload["subdivisions"][0]["entity_type"], "Reino")
        self.assertEqual(detail_payload["subdivisions"][0]["clone_url"], "/groups/subdivisions/aa/murcia/clone/")
        self.assertEqual(detail_payload["groups"][0]["internal_name"], "ALBACETE_A_CUENCA")
        clone_response = self.client.post("/groups/subdivisions/aa/murcia/clone/")
        self.assertEqual(clone_response.status_code, 302)
        self.assertEqual(clone_response["Location"], "/groups/subdivisions/aa/murcia_copia/")
        clone = DerivedSubdivision.objects.get(slug="aa_murcia_copia")
        self.assertEqual(clone.internal_name, "MURCIA_COPIA")
        self.assertEqual(clone.name, "Murcia Copia")
        self.assertEqual(clone.code, "MUR-COPIA")
        self.assertIn('internal_name = "MURCIA_COPIA"', clone.content)
        self.assertIn('code = "MUR-COPIA"', clone.content)
        form_response = self.client.get("/groups/subdivisions/aa/murcia/")
        self.assertEqual(form_response.status_code, 200)
        form_html = form_response.content.decode("utf-8")
        self.assertIn("group-entry-form derived-subdivision-entry-form", form_html)
        self.assertNotIn("AÃ±adir", form_html)
        self.assertIn("is-loading", form_html)
        self.assertIn("group-entry-main-box", form_html)
        self.assertIn('name="flag_url"', form_html)
        self.assertIn('name="coat_url"', form_html)
        self.assertIn('type="url"', form_html)
        self.assertIn('name="use_selected_entities_as_children"', form_html)
        child_mode_markup = form_html[
            form_html.index('name="use_selected_entities_as_children"') - 80 : form_html.index("data-derived-child-mode") + 40
        ]
        self.assertIn('type="hidden"', child_mode_markup)
        self.assertIn('value="0"', child_mode_markup)
        self.assertIn("data-derived-child-mode", child_mode_markup)
        self.assertNotIn("Usar entidades marcadas como hijos", form_html)
        self.assertNotIn("derived-child-mode-field", form_html)
        self.assertNotIn("derived-child-mode-switch", form_html)
        self.assertNotIn("derived-child-mode-slider", form_html)
        self.assertIn("derived-identity-assets-row", form_html)
        self.assertNotIn("check-inline derived-child-mode-field", form_html)
        self.assertIn("derived-subdivision-source-panels", form_html)
        self.assertIn("derived-source-country-control-panel", form_html)
        self.assertIn("data-derived-source-control", form_html)
        self.assertNotIn("data-derived-operation-target", form_html)
        self.assertIn('data-source-url="/groups/source-data/"', form_html)
        self.assertIn('data-refresh-on-success="1"', form_html)
        self.assertIn('data-derived-source-panel data-derived-operation="include"', form_html)
        self.assertNotIn('data-derived-source-panel data-derived-operation="subtract"', form_html)
        self.assertIn("data-derived-include-ids-json", form_html)
        self.assertIn("data-derived-subtract-ids-json", form_html)
        self.assertIn("data-derived-source-dirty", form_html)
        self.assertIn("data-group-country-block-template", form_html)
        self.assertIn("data-derived-source-item-table", form_html)
        self.assertIn("data-derived-source-item-modal", form_html)
        self.assertIn("data-save-derived-source-item-modal", form_html)
        self.assertIn("data-close-derived-source-item-modal", form_html)
        source_markup = form_html[
            form_html.index("derived-source-country-control-panel") : form_html.index(
                "<template data-group-country-block-template"
            )
        ]
        self.assertEqual(source_markup.count("data-derived-country-add-select"), 1)
        self.assertEqual(source_markup.count("data-derived-add-country"), 1)
        self.assertEqual(source_markup.count("data-derived-source-panel"), 1)
        self.assertIn(">Añadir<", source_markup)
        self.assertLess(form_html.index("<th>Incluidos</th>"), form_html.index("<th>Excluidos</th>"))
        self.assertLess(form_html.index("<th>Excluidos</th>"), form_html.index("<th>Acciones</th>"))
        self.assertIn("<th>Incluidos</th>", form_html)
        self.assertIn("<th>Excluidos</th>", form_html)
        self.assertNotIn("<th>Nivel</th>", form_html)
        self.assertNotIn("<th>Restar</th>", form_html)
        self.assertIn("derived-source-included-col", form_html)
        self.assertIn("derived-source-excluded-col", form_html)
        self.assertNotIn("derived-source-level-col", form_html)
        self.assertIn("derived-source-actions-col", form_html)
        self.assertIn("function renderDerivedSourcePanels", form_html)
        self.assertIn("function loadCountryData", form_html)
        self.assertIn("function renderLevelRows", form_html)
        self.assertIn("function renderRootHierarchyRow", form_html)
        self.assertIn("group-hierarchy-static-value", form_html)
        self.assertIn("function updateHierarchyFrom", form_html)
        self.assertIn("function addSelectedSourceItemFromRow", form_html)
        self.assertIn("function openSourceItemModal", form_html)
        self.assertIn("function openSourceExcludeModal", form_html)
        self.assertIn("function openSourceEditModal", form_html)
        self.assertIn("function selectedSourceItemGroups", form_html)
        source_scope_function = form_html[
            form_html.index("function sourceItemScopeKey") : form_html.index("function sourceItemMatchesEditScope")
        ]
        self.assertIn("sourceItemLevelValue(item)", source_scope_function)
        self.assertIn('String(item.parent_id || "")', source_scope_function)
        self.assertNotIn("sourceItemKind", source_scope_function)
        self.assertIn("function loadDescendants", form_html)
        self.assertIn("function sourceItemDescendantParentIds", form_html)
        self.assertIn("function sourceItemModalLevelGroups", form_html)
        self.assertIn("function renderSourceItemModalLevelGroups", form_html)
        self.assertIn("function sourceItemModalFilterText", form_html)
        self.assertIn("function sourceItemModalFilteredItems", form_html)
        self.assertIn("function sourceItemModalLevelGroupTitle", form_html)
        self.assertIn("function captureSourceItemModalScroll", form_html)
        self.assertIn("function restoreSourceItemModalScroll", form_html)
        self.assertIn("function sourceItemSelectedLabel", form_html)
        self.assertIn("function derivedSubdivisionKeyFromItem", form_html)
        self.assertIn("stripTrailingParentheticalLabel", form_html)
        self.assertIn("data-derived-source-modal-filter", form_html)
        self.assertIn("data-derived-source-modal-kind-filter", form_html)
        self.assertIn('value="area"', form_html)
        self.assertIn('value="group"', form_html)
        self.assertIn('value="derived_subdivision"', form_html)
        self.assertIn("dataset.sourceKind", form_html)
        self.assertIn("kindFilters", form_html)
        self.assertIn("sourceItemFilterKind", form_html)
        self.assertIn('itemKind === "group" || itemKind === "derived_subdivision"', form_html)
        self.assertIn("is-derived-subdivision-source", form_html)
        self.assertIn("derived-source-subdivision-chip", form_html)
        self.assertIn("derived_subdivision: 2", form_html)
        self.assertIn("sourceRequestContext()", form_html)
        self.assertIn("current_subdivision_country", form_html)
        self.assertIn('hasOwnProperty.call(payload || {}, "include_items")', form_html)
        self.assertIn("derived-source-modal-levels", form_html)
        self.assertIn("derived-source-modal-level-block", form_html)
        self.assertIn("data-edit-derived-source-group", form_html)
        self.assertIn("data-exclude-derived-source-group", form_html)
        self.assertIn("data-remove-derived-source-group", form_html)
        self.assertIn('editButton.textContent = "Editar"', form_html)
        self.assertIn('excludeButton.textContent = "Excluir"', form_html)
        self.assertIn("excluded_items", form_html)
        self.assertIn("derived-source-included-chip", form_html)
        self.assertIn("derived-source-excluded-chip", form_html)
        self.assertIn("function sourceItemMatchesEditScope", form_html)
        self.assertIn("function renderExcludedItemsList", form_html)
        self.assertIn("function loadSourceItems", form_html)
        self.assertIn("loadSourceItems(countryCode, level, parentId)", form_html)
        self.assertIn('source_mode: "items"', form_html)
        self.assertIn('source_mode: "descendants"', form_html)
        self.assertNotIn("function renderExcludedChildrenList", form_html)
        self.assertIn("sourceItemBlocksFromExcludedItems", form_html)
        self.assertIn("function readBlocksFromPanel(panel, includeExcluded)", form_html)
        self.assertNotIn("function readBlocksFromPanel(panel) {", form_html)
        self.assertIn("loadChildren(block.dataset.countryCode", form_html)
        self.assertIn("data-derived-source-modal-available", form_html)
        self.assertIn("data-derived-source-modal-selected", form_html)
        self.assertIn("include_ids_json", form_html)
        self.assertIn("subtract_ids_json", form_html)
        self.assertNotIn("derived-subdivision-editor-block", form_html)
        self.assertNotIn("Grupos disponibles", form_html)
        self.assertNotIn("data-derived-group-action", form_html)
        self.assertIn('data-derived-subdivision-load-url="/groups/subdivisions/aa/murcia/data/"', form_html)
        self.assertIn("data-derived-loading", form_html)
        self.assertIn("Cargando datos...", form_html)
        self.assertIn('action="/subdivisions/aa/export-toml/"', form_html)
        self.assertIn('action="/subdivisions/import-toml/"', form_html)
        self.assertIn('name="level" value="1"', form_html)
        self.assertIn('type="hidden" name="level" value="1" data-derived-level', form_html)
        self.assertNotIn('type="number" required data-derived-level', form_html)
        self.assertIn("data-derived-capital-select", form_html)
        capital_select = form_html[form_html.index("data-derived-capital-select") : form_html.index("</select>", form_html.index("data-derived-capital-select"))]
        self.assertNotIn("data-select2", capital_select)
        self.assertIn("data-input-too-short-label", capital_select)
        self.assertNotIn("derived-code-suffix-field", form_html)
        self.assertNotIn("Codigo propio", form_html)
        self.assertNotIn("Seccion padre", form_html)
        self.assertIn("derived-capital-picker", form_html)
        self.assertIn("derived-capital-search-column", form_html)
        self.assertIn("derived-capital-selected-column", form_html)
        self.assertIn("data-derived-capital-badges", form_html)
        self.assertIn("data-derived-capital-hidden-inputs", form_html)
        self.assertNotIn("refreshCapitalOptions", form_html)
        self.assertIn('name="parent_code"', form_html)
        self.assertIn("data-derived-parent-code", form_html)
        self.assertNotIn("data-derived-parent-select", form_html)
        self.assertIn('name="code" value=""', form_html)
        self.assertNotIn("Codigo calculado", form_html)
        self.assertNotIn("Pais fuente", form_html)

        data_response = self.client.get("/groups/subdivisions/aa/murcia/data/")
        self.assertEqual(data_response.status_code, 200)
        payload = data_response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["form"]["internal_name"], "MURCIA")
        self.assertEqual(payload["form"]["code_suffix"], "MUR")
        self.assertEqual(payload["form"]["code"], "AA-MUR")
        self.assertEqual(payload["form"]["parent_code"], "AA")
        self.assertNotIn("group_countries", payload)
        self.assertIn("include_items", payload)
        self.assertIn("subtract_items", payload)
        html = response.content.decode("utf-8")
        self.assertIn("Subdivisiones creadas", html)
        self.assertIn("Grupos disponibles", html)
        self.assertNotIn("MURCIA", html)
        self.assertNotIn("Murcia", html)
        self.assertNotIn("Reino", html)
        self.assertNotIn('data-row-href="/groups/subdivisions/aa/murcia/"', html)
        self.assertIn('action="/subdivisions/aa/export-toml/"', html)
        self.assertIn('href="/groups/subdivisions/aa/new/"', html)
        self.assertNotIn("ALBACETE_A_CUENCA", html)
        self.assertNotIn('class="numeric">1</td>', html)

    def test_derived_subdivision_list_ignores_countries_without_created_subdivisions(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0, pop_latest=1000)
        AdminArea.objects.create(id="bb_root", country_code="bb", code="BB", name="Betaland", level=0, pop_latest=900)
        SubdivisionGroup.objects.create(
            slug="bb_group",
            name="BB_GROUP",
            source_country_code="bb",
            content="\n".join(
                [
                    'source_country_code = "bb"',
                    "",
                    'BB_GROUP = ["One"]',
                    "",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_murcia",
            internal_name="MURCIA",
            name="Murcia",
            source_country_code="aa",
            entity_type="Reino",
            code="MUR",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "MURCIA"',
                    'source_country_code = "aa"',
                    'name = "Murcia"',
                    'code = "MUR"',
                    'entity_type = "Reino"',
                ]
            ),
        )

        with patch(
            "ciudades_del_mundo.web.views.bundled_derived_subdivision_paths",
            return_value=[Path("subdivision_groups/subdivisions/bb.toml")],
        ):
            response = self.client.get("/subdivisions/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertEqual(html.count("data-group-country-card"), 1)
        self.assertIn("Aaland", html)
        self.assertIn('data-group-detail-url="/subdivisions/countries/aa/data/"', html)
        self.assertNotIn("Murcia", html)
        self.assertIn('action="/subdivisions/aa/build/"', html)
        self.assertNotIn("Betaland", html)
        self.assertNotIn("BB_GROUP", html)
        self.assertNotIn('action="/subdivisions/bb/build/"', html)

        detail_response = self.client.get("/subdivisions/countries/aa/data/")
        self.assertEqual(detail_response.status_code, 200)
        payload = detail_response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["subdivisions"][0]["name"], "Murcia")
        self.assertEqual(payload["subdivisions"][0]["entity_type"], "Reino")

    def test_derived_subdivision_edit_finds_sql_record_by_internal_name(self):
        AdminArea.objects.create(id="spain_root", country_code="spain", code="spain", name="Espana", level=0)
        DerivedSubdivision.objects.create(
            slug="legacy_aragon",
            internal_name="ARAGON",
            name="Aragon",
            source_country_code="spain",
            entity_type="Reino",
            code="ARA",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ARAGON"',
                    'source_country_code = "spain"',
                    'name = "Aragon"',
                    'code = "ARA"',
                    'entity_type = "Reino"',
                    "level = 1",
                    'flag_url = "https://example.test/aragon-flag.svg"',
                    'coat_url = "https://example.test/aragon-coat.svg"',
                    "",
                ]
            ),
        )

        response = self.client.get("/groups/subdivisions/spain/aragon/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("ARAGON", html)
        self.assertIn('action="/subdivisions/spain/export-toml/"', html)
        self.assertIn('action="/subdivisions/import-toml/"', html)
        self.assertIn('data-derived-subdivision-load-url="/groups/subdivisions/spain/aragon/data/"', html)
        self.assertIn('data-group-return-link', html)
        self.assertIn('data-group-return-country="spain"', html)
        self.assertIn('data-group-return-row-href="/groups/subdivisions/spain/aragon/"', html)
        self.assertIn('data-group-return-table="divisions"', html)
        self.assertIn('data-group-return-refresh="0"', html)
        self.assertIn('name="code" value=""', html)
        self.assertNotIn("Codigo calculado", html)

        data_response = self.client.get("/groups/subdivisions/spain/aragon/data/")
        self.assertEqual(data_response.status_code, 200)
        payload = data_response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["form"]["internal_name"], "ARAGON")
        self.assertEqual(payload["form"]["code"], "ESP-ARA")
        self.assertEqual(payload["form"]["code_suffix"], "ARA")
        self.assertEqual(payload["form"]["flag_url"], "https://example.test/aragon-flag.svg")
        self.assertEqual(payload["form"]["coat_url"], "https://example.test/aragon-coat.svg")

        saved_response = self.client.get("/groups/subdivisions/spain/aragon/?saved=1")
        self.assertEqual(saved_response.status_code, 200)
        saved_html = saved_response.content.decode("utf-8")
        self.assertIn('data-group-return-refresh="1"', saved_html)

    def test_derived_subdivision_form_saves_url_country_parent_level_and_code(self):
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        capital = AdminArea.objects.create(
            id="aa_capital",
            country_code="aa",
            code="CAP",
            name="Capital City",
            level=1,
            parent=root,
        )
        second_capital = AdminArea.objects.create(
            id="aa_capital_2",
            country_code="aa",
            code="CAP2",
            name="Second Capital",
            level=1,
            parent=root,
        )

        response = self.client.post(
            "/groups/subdivisions/aa/new/",
            {
                "internal_name": "ARAGON",
                "name": "Aragon",
                "source_country_code": "bb",
                "entity_type": "Reino",
                "level": "1",
                "capitals": [capital.id, second_capital.id],
                "flag_url": "https://example.test/flag.svg",
                "coat_url": "https://example.test/coat.svg",
                "description": "historical",
                "content": "\n".join(
                    [
                        "[[include]]",
                        'country_code = "aa"',
                        "level = 2",
                        "names = []",
                        'groups = ["ALBACETE_A_CUENCA"]',
                        "",
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/groups/subdivisions/aa/aragon/?saved=1")
        record = DerivedSubdivision.objects.get(slug="aa_aragon")
        self.assertEqual(record.source_country_code, "aa")
        self.assertEqual(record.code, "AA-ARAGON")
        self.assertEqual(record.description, "historical")
        self.assertIn('source_country_code = "aa"', record.content)
        self.assertIn('code = "AA-ARAGON"', record.content)
        self.assertIn('parent_code = "AA"', record.content)
        self.assertIn("level = 1", record.content)
        self.assertIn('capitals = ["aa_capital", "aa_capital_2"]', record.content)
        self.assertIn('flag_url = "https://example.test/flag.svg"', record.content)
        self.assertIn('coat_url = "https://example.test/coat.svg"', record.content)
        self.assertIn('groups = ["ALBACETE_A_CUENCA"]', record.content)

    def test_derived_subdivision_form_ajax_save_returns_payload_without_redirect(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)

        response = self.client.post(
            "/groups/subdivisions/aa/new/",
            {
                "internal_name": "ARAGON",
                "name": "Aragon",
                "entity_type": "Reino",
                "level": "2",
                "description": "",
                "content": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["redirect_url"], "/groups/subdivisions/aa/aragon/")
        self.assertEqual(payload["data_url"], "/groups/subdivisions/aa/aragon/data/")
        self.assertEqual(payload["capital_options_url"], "/groups/subdivisions/aa/aragon/capital-options/")
        self.assertEqual(payload["country_code"], "aa")
        self.assertEqual(payload["subdivision_slug"], "aragon")
        self.assertEqual(payload["form"]["internal_name"], "ARAGON")
        self.assertEqual(payload["form"]["level"], "2")
        self.assertNotIn("include_items", payload)
        self.assertNotIn("subtract_items", payload)
        self.assertEqual(DerivedSubdivision.objects.get(slug="aa_aragon").code, "AA-ARAGON")

    def test_derived_subdivision_edit_keeps_existing_record_when_internal_code_changes(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        record = DerivedSubdivision.objects.create(
            slug="aa_old_region",
            internal_name="OLD_REGION",
            name="Old Region",
            source_country_code="aa",
            entity_type="Region",
            code="AA-OLD",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "OLD_REGION"',
                    'source_country_code = "aa"',
                    'name = "Old Region"',
                    'code = "AA-OLD"',
                    'parent_code = "AA"',
                    'entity_type = "Region"',
                    "level = 1",
                    "",
                ]
            ),
        )

        response = self.client.post(
            "/groups/subdivisions/aa/old_region/",
            {
                "internal_name": "NEW_REGION",
                "name": "New Region",
                "entity_type": "Region",
                "level": "1",
                "code_suffix": "NEW",
                "description": "",
                "content": record.content,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(DerivedSubdivision.objects.count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.slug, "aa_old_region")
        self.assertEqual(record.internal_name, "NEW_REGION")
        self.assertEqual(record.code, "AA-NEW")
        self.assertIn('internal_name = "NEW_REGION"', record.content)
        self.assertIn('code = "AA-NEW"', record.content)
        self.assertEqual(response["Location"], "/groups/subdivisions/aa/old_region/?saved=1")

    def test_derived_subdivision_form_saves_visual_source_items_as_db_ids(self):
        root = AdminArea.objects.create(id="av_root", country_code="av", code="AV", name="Visual Land", level=0)
        province = AdminArea.objects.create(
            id="av_province",
            country_code="av",
            code="1",
            name="Province",
            level=1,
            parent=root,
        )
        city = AdminArea.objects.create(
            id="av_city",
            country_code="av",
            code="101",
            name="City",
            level=2,
            parent=province,
        )
        excluded = AdminArea.objects.create(
            id="av_excluded",
            country_code="av",
            code="102",
            name="Excluded",
            level=2,
            parent=province,
        )

        response = self.client.post(
            "/groups/subdivisions/av/new/",
            {
                "internal_name": "VISUAL_SOURCE",
                "name": "Visual Source",
                "entity_type": "Region",
                "level": "1",
                "description": "",
                "content": "\n".join(
                    [
                        'kind = "derived_subdivision"',
                        "",
                        "[[include]]",
                        'country_code = "av"',
                        "level = 2",
                        'groups = ["OLD_GROUP"]',
                        "",
                    ]
                ),
                "derived_source_dirty": "1",
                "include_ids_json": json.dumps(
                    [
                        {
                            "country_code": "av",
                            "items": [{"id": city.id, "name": "City", "level": 2}],
                        }
                    ]
                ),
                "subtract_ids_json": json.dumps(
                    [
                        {
                            "country_code": "av",
                            "items": [{"id": excluded.id, "name": "Excluded", "level": 2}],
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        record = DerivedSubdivision.objects.get(slug="av_visual_source")
        self.assertIn("[[include]]", record.content)
        self.assertIn('ids = ["av_city"]', record.content)
        self.assertIn("[[subtract]]", record.content)
        self.assertIn('ids = ["av_excluded"]', record.content)
        self.assertNotIn("OLD_GROUP", record.content)

    def test_derived_subdivision_form_saves_visual_external_country_root_item(self):
        AdminArea.objects.create(id="av_root", country_code="av", code="AV", name="Visual Land", level=0)
        external_root = AdminArea.objects.create(
            id="bb_root",
            country_code="bb",
            code="BB",
            name="Bland",
            level=0,
        )
        AdminArea.objects.create(
            id="bb_unit",
            country_code="bb",
            code="001",
            name="Unit",
            level=1,
            parent=external_root,
        )

        response = self.client.post(
            "/groups/subdivisions/av/new/",
            {
                "internal_name": "EXTERNAL_ROOT",
                "name": "External Root",
                "entity_type": "Region",
                "level": "1",
                "description": "",
                "content": "",
                "derived_source_dirty": "1",
                "include_ids_json": json.dumps(
                    [
                        {
                            "country_code": "bb",
                            "items": [
                                {
                                    "id": external_root.id,
                                    "country_code": "bb",
                                    "name": "Bland",
                                    "level": 0,
                                }
                            ],
                        }
                    ]
                ),
                "subtract_ids_json": "[]",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = DerivedSubdivision.objects.get(slug="av_external_root")
        self.assertIn('country_code = "bb"', record.content)
        self.assertIn("level = 0", record.content)
        self.assertIn('ids = ["bb_root"]', record.content)
        data_response = self.client.get("/groups/subdivisions/av/external_root/data/")
        self.assertEqual(data_response.status_code, 200)
        external_block = next(block for block in data_response.json()["include_items"] if block["country_code"] == "bb")
        self.assertEqual(external_block["items"][0]["id"], "bb_root")
        self.assertEqual(external_block["items"][0]["level"], 0)

    def test_derived_subdivision_form_saves_visual_source_items_as_derived_subdivision_refs(self):
        AdminArea.objects.create(id="av_root", country_code="av", code="AV", name="Visual Land", level=0)
        DerivedSubdivision.objects.create(
            slug="av_principado",
            internal_name="PRINCIPADO",
            name="Principado",
            source_country_code="av",
            entity_type="Principado",
            code="PRI",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "PRINCIPADO"',
                    'source_country_code = "av"',
                    'name = "Principado"',
                    'code = "PRI"',
                    'entity_type = "Principado"',
                    "level = 2",
                ]
            ),
        )

        response = self.client.post(
            "/groups/subdivisions/av/new/",
            {
                "internal_name": "CORONA",
                "name": "Corona",
                "entity_type": "Corona",
                "level": "1",
                "use_selected_entities_as_children": "1",
                "description": "",
                "content": "",
                "derived_source_dirty": "1",
                "include_ids_json": json.dumps(
                    [
                        {
                            "country_code": "av",
                            "items": [
                                {
                                    "id": "derived-subdivision::av::PRINCIPADO",
                                    "source_kind": "derived_subdivision",
                                    "derived_subdivision_key": "PRINCIPADO",
                                    "country_code": "av",
                                    "level": 2,
                                }
                            ],
                        }
                    ]
                ),
                "subtract_ids_json": "[]",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = DerivedSubdivision.objects.get(slug="av_corona")
        self.assertIn("use_selected_entities_as_children = true", record.content)
        self.assertIn("[[include]]", record.content)
        self.assertIn('derived_subdivisions = ["PRINCIPADO"]', record.content)
        self.assertNotIn("derived-subdivision::av::PRINCIPADO", record.content)

    def test_derived_subdivision_source_data_exposes_reusable_groups_as_badges(self):
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_region", country_code="aa", code="1", name="Region", level=1, parent=root)
        province = AdminArea.objects.create(
            id="aa_albacete",
            country_code="aa",
            code="11",
            name="Albacete",
            level=2,
            parent=region,
        )
        AdminArea.objects.create(id="aa_villatoya", country_code="aa", code="1101", name="Villatoya", level=3, parent=province)
        AdminArea.objects.create(id="aa_alborea", country_code="aa", code="1102", name="Alborea", level=3, parent=province)
        SubdivisionGroup.objects.create(
            slug="aa_albacete_a_cuenca",
            name="ALBACETE_A_CUENCA",
            source_country_code="aa",
            content='\n'.join(
                [
                    'source_country_code = "aa"',
                    '',
                    'ALBACETE_A_CUENCA = ["Villatoya", "Alborea"]',
                ]
            ),
        )

        response = self.client.get(
            "/groups/source-data/",
            {"country_code": "aa", "parent_id": province.id, "include_groups": "1"},
        )

        self.assertEqual(response.status_code, 200)
        children = response.json()["children"]
        group_item = next(item for item in children if item.get("source_kind") == "group")
        self.assertEqual(group_item["id"], "group::aa::ALBACETE_A_CUENCA")
        self.assertEqual(group_item["level"], 3)
        self.assertEqual(group_item["parent_id"], province.id)
        self.assertEqual(group_item["group_key"], "ALBACETE_A_CUENCA")
        self.assertIn("Villatoya", group_item["member_text"])
        self.assertIn("Alborea", group_item["member_text"])

    def test_derived_subdivision_source_data_exposes_created_lower_subdivisions_as_badges(self):
        root = AdminArea.objects.create(id="ds_root", country_code="ds", code="DS", name="Derived Land", level=0)
        region = AdminArea.objects.create(id="ds_region", country_code="ds", code="1", name="Region", level=1, parent=root)
        province = AdminArea.objects.create(
            id="ds_province",
            country_code="ds",
            code="11",
            name="Province",
            level=2,
            parent=region,
            entity_type="Province",
        )
        municipality = AdminArea.objects.create(
            id="ds_municipality",
            country_code="ds",
            code="1101",
            name="Municipality",
            level=3,
            parent=province,
            entity_type="Municipality",
        )
        DerivedSubdivision.objects.create(
            slug="ds_principado",
            internal_name="PRINCIPADO",
            name="Principado",
            source_country_code="ds",
            entity_type="Principado",
            code="PRI",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "PRINCIPADO"',
                    'source_country_code = "ds"',
                    'name = "Principado"',
                    'code = "PRI"',
                    'entity_type = "Principado"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "ds"',
                    "level = 2",
                    'ids = ["ds_province"]',
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="ds_corona",
            internal_name="CORONA",
            name="Corona",
            source_country_code="ds",
            entity_type="Corona",
            code="COR",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CORONA"',
                    'source_country_code = "ds"',
                    'name = "Corona"',
                    'code = "COR"',
                    'entity_type = "Corona"',
                    "level = 1",
                ]
            ),
        )

        response = self.client.get(
            "/groups/source-data/",
            {
                "country_code": "ds",
                "parent_id": region.id,
                "include_derived_subdivisions": "1",
                "current_level": "1",
                "current_subdivision": "CORONA",
            },
        )

        self.assertEqual(response.status_code, 200)
        children = response.json()["children"]
        subdivision_item = next(item for item in children if item.get("source_kind") == "derived_subdivision")
        self.assertEqual(subdivision_item["id"], "derived-subdivision::ds::PRINCIPADO")
        self.assertEqual(subdivision_item["level"], 2)
        self.assertEqual(subdivision_item["derived_subdivision_key"], "PRINCIPADO")
        self.assertEqual(subdivision_item["member_names"], [])
        self.assertIn(municipality.id, subdivision_item["member_ids"])
        self.assertIn("Municipality", subdivision_item["member_text"])

    def test_derived_subdivision_source_data_only_excludes_current_key_in_current_country(self):
        root = AdminArea.objects.create(id="it_root", country_code="it", code="IT", name="Italy", level=0)
        region = AdminArea.objects.create(id="it_region", country_code="it", code="1", name="Region", level=1, parent=root)
        province = AdminArea.objects.create(
            id="it_province",
            country_code="it",
            code="11",
            name="Province",
            level=2,
            parent=region,
        )
        municipality = AdminArea.objects.create(
            id="it_municipality",
            country_code="it",
            code="1101",
            name="Municipality",
            level=3,
            parent=province,
        )
        DerivedSubdivision.objects.create(
            slug="it_corona",
            internal_name="CORONA",
            name="Corona italiana",
            source_country_code="it",
            entity_type="Principado",
            code="COR",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CORONA"',
                    'source_country_code = "it"',
                    'name = "Corona italiana"',
                    'code = "COR"',
                    'entity_type = "Principado"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "it"',
                    "level = 2",
                    'ids = ["it_province"]',
                ]
            ),
        )

        response = self.client.get(
            "/groups/source-data/",
            {
                "country_code": "it",
                "parent_id": region.id,
                "include_derived_subdivisions": "1",
                "current_level": "1",
                "current_subdivision": "CORONA",
                "current_subdivision_country": "ds",
            },
        )

        self.assertEqual(response.status_code, 200)
        children = response.json()["children"]
        subdivision_item = next(item for item in children if item.get("source_kind") == "derived_subdivision")
        self.assertEqual(subdivision_item["id"], "derived-subdivision::it::CORONA")
        self.assertIn(municipality.id, subdivision_item["member_ids"])

        response = self.client.get(
            "/groups/source-data/",
            {
                "country_code": "it",
                "parent_id": region.id,
                "include_derived_subdivisions": "1",
                "current_level": "1",
                "current_subdivision": "CORONA",
                "current_subdivision_country": "it",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(item.get("source_kind") == "derived_subdivision" for item in response.json()["children"]))

    def test_derived_subdivision_source_data_direct_groups_match_child_level(self):
        root = AdminArea.objects.create(id="gr_root", country_code="gr", code="GR", name="Group Land", level=0)
        region = AdminArea.objects.create(
            id="gr_region",
            country_code="gr",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        other_region = AdminArea.objects.create(
            id="gr_other_region",
            country_code="gr",
            code="2",
            name="Other Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gr_province",
            country_code="gr",
            code="11",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        AdminArea.objects.create(
            id="gr_municipality_one",
            country_code="gr",
            code="111",
            name="Municipality One",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        AdminArea.objects.create(
            id="gr_municipality_two",
            country_code="gr",
            code="112",
            name="Municipality Two",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        SubdivisionGroup.objects.create(
            slug="gr_region_pair",
            name="REGION_PAIR",
            source_country_code="gr",
            content='source_country_code = "gr"\n\nREGION_PAIR = ["Region", "Other Region"]\n',
        )
        SubdivisionGroup.objects.create(
            slug="gr_municipality_pair",
            name="MUNICIPALITY_PAIR",
            source_country_code="gr",
            content='source_country_code = "gr"\n\nMUNICIPALITY_PAIR = ["Municipality One", "Municipality Two"]\n',
        )

        response = self.client.get("/groups/source-data/", {"country_code": "gr", "parent_id": root.id, "include_groups": "1"})

        self.assertEqual(response.status_code, 200)
        root_group_ids = {row["id"] for row in response.json()["children"] if row.get("source_kind") == "group"}
        self.assertIn("group::gr::REGION_PAIR", root_group_ids)
        self.assertNotIn("group::gr::MUNICIPALITY_PAIR", root_group_ids)

        response = self.client.get("/groups/source-data/", {"country_code": "gr", "parent_id": province.id, "include_groups": "1"})

        self.assertEqual(response.status_code, 200)
        province_group_ids = {row["id"] for row in response.json()["children"] if row.get("source_kind") == "group"}
        self.assertNotIn("group::gr::REGION_PAIR", province_group_ids)
        self.assertIn("group::gr::MUNICIPALITY_PAIR", province_group_ids)

    def test_derived_subdivision_form_keeps_reusable_groups_in_visual_sources(self):
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_region", country_code="aa", code="1", name="Region", level=1, parent=root)
        province = AdminArea.objects.create(
            id="aa_albacete",
            country_code="aa",
            code="11",
            name="Albacete",
            level=2,
            parent=region,
        )
        AdminArea.objects.create(id="aa_villatoya", country_code="aa", code="1101", name="Villatoya", level=3, parent=province)
        AdminArea.objects.create(id="aa_alborea", country_code="aa", code="1102", name="Alborea", level=3, parent=province)
        SubdivisionGroup.objects.create(
            slug="aa_albacete_a_cuenca",
            name="ALBACETE_A_CUENCA",
            source_country_code="aa",
            content='\n'.join(
                [
                    'source_country_code = "aa"',
                    '',
                    'ALBACETE_A_CUENCA = ["Villatoya", "Alborea"]',
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_reino_de_murcia",
            internal_name="REINO_DE_MURCIA",
            name="Reino de Murcia",
            source_country_code="aa",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "REINO_DE_MURCIA"',
                    'source_country_code = "aa"',
                    'name = "Reino de Murcia"',
                    'code = "MUR"',
                    'parent_code = "AA"',
                    'entity_type = "Provincia"',
                    'level = 1',
                    '',
                    '[[include]]',
                    'country_code = "aa"',
                    'level = 3',
                    'groups = ["ALBACETE_A_CUENCA"]',
                    '',
                ]
            ),
        )

        response = self.client.get("/groups/subdivisions/aa/reino_de_murcia/data/")

        self.assertEqual(response.status_code, 200)
        include_items = response.json()["include_items"][0]["items"]
        self.assertEqual(len(include_items), 1)
        self.assertEqual(include_items[0]["source_kind"], "group")
        self.assertEqual(include_items[0]["group_key"], "ALBACETE_A_CUENCA")
        self.assertIn("Villatoya", include_items[0]["member_text"])

    def test_derived_subdivision_form_scopes_subtract_group_homonyms_to_included_parent(self):
        AdminArea.objects.create(id="spain_root", country_code="spain", code="ESP", name="Spain", level=0)
        france_root = AdminArea.objects.create(id="france_root", country_code="france", code="FR", name="France", level=0)
        france_region = AdminArea.objects.create(
            id="france_region",
            country_code="france",
            code="76",
            name="Occitanie",
            level=1,
            parent=france_root,
        )
        pyrenees = AdminArea.objects.create(
            id="france_66",
            country_code="france",
            code="66",
            name="Pyrenees-Orientales",
            level=2,
            parent=france_region,
        )
        prades = AdminArea.objects.create(
            id="france_663",
            country_code="france",
            code="663",
            name="Prades",
            level=3,
            parent=pyrenees,
        )
        perpignan = AdminArea.objects.create(
            id="france_662",
            country_code="france",
            code="662",
            name="Perpignan",
            level=3,
            parent=pyrenees,
        )
        other_department = AdminArea.objects.create(
            id="france_09",
            country_code="france",
            code="09",
            name="Ariege",
            level=2,
            parent=france_region,
        )
        foix = AdminArea.objects.create(
            id="france_092",
            country_code="france",
            code="092",
            name="Foix",
            level=3,
            parent=other_department,
        )
        belesta_inside = AdminArea.objects.create(
            id="france_66019",
            country_code="france",
            code="66019",
            name="Belesta",
            level=4,
            parent=prades,
        )
        other_inside = AdminArea.objects.create(
            id="france_66020",
            country_code="france",
            code="66020",
            name="Other",
            level=4,
            parent=perpignan,
        )
        belesta_outside = AdminArea.objects.create(
            id="france_09047",
            country_code="france",
            code="09047",
            name="Belesta",
            level=4,
            parent=foix,
        )
        SubdivisionGroup.objects.create(
            slug="france_fenolleda",
            name="FENOLLEDA",
            source_country_code="france",
            content='source_country_code = "france"\n\nFENOLLEDA = ["Belesta", "Other"]\n',
        )
        DerivedSubdivision.objects.create(
            slug="spain_catalunha_a",
            internal_name="CATALUNHA_A",
            name="Catalunya",
            source_country_code="spain",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CATALUNHA_A"',
                    'source_country_code = "spain"',
                    'name = "Catalunya"',
                    'code = "CAT"',
                    'level = 1',
                    "",
                    "[[include]]",
                    'country_code = "france"',
                    "level = 2",
                    'names = ["Pyrenees-Orientales"]',
                    "",
                    "[[subtract]]",
                    'country_code = "france"',
                    "level = 4",
                    'groups = ["FENOLLEDA"]',
                    "",
                ]
            ),
        )

        response = self.client.get("/groups/subdivisions/spain/catalunha_a/data/")

        self.assertEqual(response.status_code, 200)
        france_subtract = next(block for block in response.json()["subtract_items"] if block["country_code"] == "france")
        group_item = france_subtract["items"][0]
        self.assertEqual(group_item["parent_id"], pyrenees.id)
        self.assertIn(belesta_inside.id, group_item["member_ids"])
        self.assertIn(other_inside.id, group_item["member_ids"])
        self.assertNotIn(belesta_outside.id, group_item["member_ids"])

    def test_derived_subdivision_form_saves_reusable_groups_as_group_refs(self):
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        region = AdminArea.objects.create(id="aa_region", country_code="aa", code="1", name="Region", level=1, parent=root)
        province = AdminArea.objects.create(
            id="aa_albacete",
            country_code="aa",
            code="11",
            name="Albacete",
            level=2,
            parent=region,
        )

        response = self.client.post(
            "/groups/subdivisions/aa/new/",
            {
                "internal_name": "REINO_DE_MURCIA",
                "name": "Reino de Murcia",
                "entity_type": "Provincia",
                "level": "1",
                "description": "",
                "content": "",
                "derived_source_dirty": "1",
                "include_ids_json": json.dumps(
                    [
                        {
                            "country_code": "aa",
                            "items": [{"id": province.id, "name": "Albacete", "level": 2}],
                        }
                    ]
                ),
                "subtract_ids_json": json.dumps(
                    [
                        {
                            "country_code": "aa",
                            "items": [
                                {
                                    "id": "group::aa::ALBACETE_A_CUENCA",
                                    "source_kind": "group",
                                    "group_key": "ALBACETE_A_CUENCA",
                                    "country_code": "aa",
                                    "level": 3,
                                    "parent_id": province.id,
                                }
                            ],
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        record = DerivedSubdivision.objects.get(slug="aa_reino_de_murcia")
        self.assertIn("[[include]]", record.content)
        self.assertIn('ids = ["aa_albacete"]', record.content)
        self.assertIn("[[subtract]]", record.content)
        self.assertIn('groups = ["ALBACETE_A_CUENCA"]', record.content)
        self.assertNotIn("group::aa::ALBACETE_A_CUENCA", record.content)

    def test_derived_subdivision_form_allows_no_capitals(self):
        AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)

        response = self.client.post(
            "/groups/subdivisions/aa/new/",
            {
                "internal_name": "SIN_CAPITAL",
                "name": "Sin capital",
                "entity_type": "Provincia",
                "level": "1",
                "description": "",
                "content": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        record = DerivedSubdivision.objects.get(slug="aa_sin_capital")
        self.assertIn("capitals = []", record.content)

    def test_derived_subdivision_form_data_capital_options_skip_merge_sources(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province = AdminArea.objects.create(
            id="spain_province",
            country_code="spain",
            code="02",
            name="Albacete",
            level=2,
            parent=root,
        )
        normal = AdminArea.objects.create(
            id="spain_normal",
            country_code="spain",
            code="02001",
            name="Municipio Normal",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        unified = AdminArea.objects.create(
            id="spain_unified",
            country_code="spain",
            code="02099",
            name="Municipio Unificado",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        contains_only = AdminArea.objects.create(
            id="spain_contains_only",
            country_code="spain",
            code="02098",
            name="Villa Municipio",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        zafra = AdminArea.objects.create(
            id="spain_zafra",
            country_code="spain",
            code="02097",
            name="Zafra",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        source = AdminArea.objects.create(
            id="spain_source",
            country_code="spain",
            code="02002",
            name="Municipio Fuente",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )
        other_province = AdminArea.objects.create(
            id="spain_other_province",
            country_code="spain",
            code="50",
            name="Zaragoza",
            level=2,
            parent=root,
        )
        outside = AdminArea.objects.create(
            id="spain_outside",
            country_code="spain",
            code="50001",
            name="Zaragoza",
            level=3,
            parent=other_province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        DerivedSubdivision.objects.create(
            slug="spain_test",
            internal_name="TEST",
            name="Test",
            source_country_code="spain",
            code="TEST",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "TEST"',
                    'source_country_code = "spain"',
                    'name = "Test"',
                    'code = "TEST"',
                    'entity_type = "Provincia"',
                    "level = 2",
                    'capitals = ["spain_unified"]',
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'names = ["Albacete"]',
                    "",
                ]
            ),
        )

        response = self.client.get("/groups/subdivisions/spain/test/data/")

        self.assertEqual(response.status_code, 200)
        options = response.json()["capital_options"]
        values = [row["value"] for row in options]
        self.assertNotIn(normal.id, values)
        self.assertEqual(values, [unified.id])
        self.assertNotIn(source.id, values)
        self.assertNotIn(outside.id, values)
        self.assertEqual({row["label"] for row in options}, {"Municipio Unificado"})
        self.assertEqual(response.json()["form"]["capital"], unified.id)
        self.assertEqual(response.json()["form"]["capitals"], [unified.id])

        search_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": response.json()["form"]["content"], "query": "Mu"}),
            content_type="application/json",
        )
        self.assertEqual(search_response.status_code, 200)
        search_options = search_response.json()["capital_options"]
        search_values = [row["value"] for row in search_options]
        self.assertIn(normal.id, search_values)
        self.assertIn(unified.id, search_values)
        self.assertNotIn(contains_only.id, search_values)
        self.assertNotIn(source.id, search_values)
        self.assertNotIn(outside.id, search_values)
        self.assertEqual({row["label"] for row in search_options}, {"Municipio Normal", "Municipio Unificado"})

        za_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": response.json()["form"]["content"], "query": "Za"}),
            content_type="application/json",
        )
        self.assertEqual(za_response.status_code, 200)
        za_values = [row["value"] for row in za_response.json()["capital_options"]]
        self.assertEqual(za_values, [zafra.id])
        self.assertNotIn(outside.id, za_values)

    def test_derived_subdivision_capital_options_follow_current_toml_selection(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province = AdminArea.objects.create(
            id="spain_province",
            country_code="spain",
            code="02",
            name="Albacete",
            level=2,
            parent=root,
        )
        normal = AdminArea.objects.create(
            id="spain_normal",
            country_code="spain",
            code="02001",
            name="Municipio Normal",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        unified = AdminArea.objects.create(
            id="spain_unified",
            country_code="spain",
            code="02099",
            name="Municipio Unificado",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        source = AdminArea.objects.create(
            id="spain_source",
            country_code="spain",
            code="02002",
            name="Municipio Fuente",
            level=3,
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )
        other_province = AdminArea.objects.create(
            id="spain_other_province",
            country_code="spain",
            code="50",
            name="Zaragoza",
            level=2,
            parent=root,
        )
        outside = AdminArea.objects.create(
            id="spain_outside",
            country_code="spain",
            code="50001",
            name="Zaragoza",
            level=3,
            parent=other_province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        content = "\n".join(
            [
                'kind = "derived_subdivision"',
                'internal_name = "TEST"',
                'source_country_code = "spain"',
                'name = "Test"',
                'code = "TEST"',
                'entity_type = "Provincia"',
                "level = 2",
                "",
                "[[include]]",
                'country_code = "spain"',
                "level = 2",
                'names = ["Albacete"]',
                "",
                "[[subtract]]",
                'country_code = "spain"',
                "level = 3",
                'ids = ["spain_normal"]',
                "",
            ]
        )

        response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": content, "capitals": [normal.id, unified.id]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        values = [row["value"] for row in payload["capital_options"]]
        self.assertEqual(values, [normal.id, unified.id])
        self.assertEqual(payload["capital"], normal.id)
        self.assertEqual(payload["capitals"], [normal.id, unified.id])

        filtered_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": content, "capitals": [normal.id, unified.id], "query": "Mu"}),
            content_type="application/json",
        )
        self.assertEqual(filtered_response.status_code, 200)
        filtered_payload = filtered_response.json()
        filtered_values = [row["value"] for row in filtered_payload["capital_options"]]
        self.assertNotIn(normal.id, filtered_values)
        self.assertIn(unified.id, filtered_values)
        self.assertEqual(filtered_payload["capital"], unified.id)
        self.assertEqual(filtered_payload["capitals"], [unified.id])

        fast_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": "[", "capitals": [unified.id]}),
            content_type="application/json",
        )
        self.assertEqual(fast_response.status_code, 200)
        self.assertEqual([row["value"] for row in fast_response.json()["capital_options"]], [unified.id])

        long_invalid_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": "[", "query": "Mu"}),
            content_type="application/json",
        )
        self.assertEqual(long_invalid_response.status_code, 400)

        values = filtered_values
        self.assertNotIn(source.id, values)
        self.assertNotIn(outside.id, values)

        short_response = self.client.post(
            "/groups/subdivisions/spain/test/capital-options/",
            data=json.dumps({"content": content, "query": "M"}),
            content_type="application/json",
        )
        self.assertEqual(short_response.status_code, 200)
        self.assertEqual(short_response.json()["capital_options"], [])

    def test_derived_subdivision_capital_options_match_display_alias_inside_scope(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="España", level=0)
        gipuzkoa = AdminArea.objects.create(
            id="spain_20",
            country_code="spain",
            code="20",
            name="Gipuzkoa",
            level=2,
            parent=root,
        )
        madrid = AdminArea.objects.create(
            id="spain_28",
            country_code="spain",
            code="28",
            name="Madrid",
            level=2,
            parent=root,
        )
        donostia = AdminArea.objects.create(
            id="spain_20069",
            country_code="spain",
            code="20069",
            name="Donostia",
            level=3,
            parent=gipuzkoa,
        )
        outside = AdminArea.objects.create(
            id="spain_28134",
            country_code="spain",
            code="28134",
            name="San Sebastián de los Reyes",
            level=3,
            parent=madrid,
        )
        content = "\n".join(
            [
                'kind = "derived_subdivision"',
                'internal_name = "GIPUZKOA"',
                'source_country_code = "spain"',
                'name = "Guipúzcoa"',
                'code = "GIP"',
                'entity_type = "Provincia"',
                "level = 2",
                "",
                "[[include]]",
                'country_code = "spain"',
                "level = 2",
                'ids = ["spain_20"]',
                "",
            ]
        )

        with translation.override("es"):
            response = self.client.post(
                "/groups/subdivisions/spain/test/capital-options/",
                data=json.dumps({"content": content, "query": "San Sebastián"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        values = [row["value"] for row in response.json()["capital_options"]]
        self.assertIn(donostia.id, values)
        self.assertNotIn(outside.id, values)

    def test_group_new_prefills_source_country_from_section_link(self):
        response = self.client.get("/groups/groups/aa/new/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('name="country_code" value="aa"', html)
        self.assertNotIn("AÃ±adir", html)
        self.assertIn("data-group-entry-form", html)
        self.assertIn('data-source-url="/groups/source-data/"', html)
        self.assertIn("data-group-return-link", html)
        self.assertIn('data-group-return-country="aa"', html)
        self.assertIn('data-group-return-table="groups"', html)
        self.assertIn('data-group-return-refresh="0"', html)
        self.assertIn("data-group-add-country", html)
        self.assertIn(">Añadir<", html)
        self.assertIn("blocksContainer.appendChild(fragment);", html)
        self.assertIn("block.scrollIntoView", html)
        self.assertIn("group-search-select-input", html)
        self.assertIn("group-search-select-trigger", html)
        self.assertIn("function openSearchSelectDropdown", html)
        self.assertIn("normalizeSearchText", html)
        self.assertIn("toLowerCase()", html)
        self.assertIn("data-group-hierarchy-controls", html)
        self.assertIn("data-group-hierarchy-row", html)
        self.assertIn("data-group-country-data-loading", html)
        self.assertNotIn("function hydrateBlockData", html)
        self.assertNotIn("postSourceData", html)
        self.assertNotIn("Promise.all([loadCountryData", html)
        self.assertIn("function renderLevelRows", html)
        self.assertIn("function renderRootHierarchyRow", html)
        self.assertIn("group-hierarchy-static-value", html)
        self.assertIn("function updateHierarchyFrom", html)
        self.assertIn("function addSelectedSectionFromRow", html)
        self.assertIn("function removeDescendantSections", html)
        self.assertIn("hasSelectedAncestor", html)
        self.assertIn("ancestor_ids", html)
        self.assertIn("area_label", html)
        self.assertIn("section_parent_id", html)
        self.assertNotIn('<option value="">Selecciona un pais</option>', html)
        self.assertNotIn("data-group-select-search", html)
        self.assertNotIn("data-select2-search", html)
        self.assertIn("data-group-country-block-template", html)
        self.assertIn("data-group-section-select", html)
        self.assertNotIn("data-group-level-select", html)
        self.assertNotIn("firstLevel", html)
        self.assertIn("data-group-section-template", html)
        self.assertIn("data-group-section-table", html)
        self.assertIn("data-group-section-modal", html)
        self.assertIn("data-save-group-section-modal", html)
        self.assertIn("data-close-group-section-modal", html)
        self.assertIn("function ensureSectionChildrenLoaded", html)
        self.assertIn("function openSectionModal", html)
        self.assertIn("function renderSectionTable", html)
        self.assertIn("bindSelectValueChange", html)
        self.assertIn("group-existing-keys-data", html)
        self.assertIn("data-group-duplicate-warning", html)
        self.assertIn('data-group-duplicate-warning aria-hidden="true"', html)
        self.assertNotIn("data-group-duplicate-warning hidden", html)
        self.assertIn("updateDuplicateState", html)
        self.assertGreaterEqual(html.count(">Guardar<"), 2)
        self.assertIn("NUEVO GRUPO", html)
        self.assertIn("data-group-internal-code", html)
        self.assertIn('pattern="[A-Za-z][A-Za-z0-9_]*"', html)
        self.assertNotIn('name="name"', html)
        self.assertNotIn("<p class=\"eyebrow\">Agrupaciones</p>", html)
        self.assertIn("Disponibles", html)

    def test_group_entry_form_generates_country_name_blocks_toml(self):
        response = self.client.post(
            "/groups/groups/aa/new/",
            {
                "internal_name": "ALTA_CERDANA",
                "blocks_json": json.dumps(
                    [
                        {
                            "country_code": "aa",
                            "names": ["Angoustrine-Villeneuve-des-Escaldes"],
                            "sections": [
                                {
                                    "area_id": "aa_soria",
                                    "area_name": "Soria",
                                    "level": 2,
                                    "selected": [{"id": "aa_city", "name": "City"}],
                                }
                            ],
                        },
                        {"country_code": "bb", "names": []},
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/groups/groups/aa/alta_cerdana/?saved=1")
        group = SubdivisionGroup.objects.get(slug="aa_alta_cerdana")
        self.assertEqual(group.name, "ALTA_CERDANA")
        self.assertIn('source_country_code = "aa"', group.content)
        self.assertIn('ALTA_CERDANA = ["Angoustrine-Villeneuve-des-Escaldes", "City"]', group.content)
        self.assertIn('include_names = ["Angoustrine-Villeneuve-des-Escaldes", "City"]', group.content)
        self.assertIn("[[country_groups]]", group.content)
        self.assertIn("[[country_groups.sections]]", group.content)
        self.assertIn('area_id = "aa_soria"', group.content)
        self.assertIn('selected_names = ["City"]', group.content)
        self.assertIn('country_code = "bb"', group.content)
        self.assertNotIn('kind = "subdivision_group"', group.content)
        self.assertNotIn('entry_slug = ', group.content)

    def test_group_entry_form_renames_group_and_derived_subdivision_references(self):
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="AA", name="Aaland", level=0)
        province = AdminArea.objects.create(
            id="aa_alicante",
            country_code="aa",
            code="03",
            name="Alicante",
            level=2,
            parent=root,
        )
        villena = AdminArea.objects.create(
            id="aa_villena",
            country_code="aa",
            code="03140",
            name="Villena",
            level=3,
            parent=province,
        )
        old_group = SubdivisionGroup.objects.create(
            slug="aa_valencia_a_murcia",
            name="VALENCIA_A_MURCIA",
            source_country_code="aa",
            content='source_country_code = "aa"\n\nVALENCIA_A_MURCIA = ["Villena"]\n',
        )
        legacy_bundle = SubdivisionGroup.objects.create(
            slug="aa_legacy_groups",
            name="AA Legacy Groups",
            source_country_code="aa",
            content="\n".join(
                [
                    'kind = "subdivision_group"',
                    'slug = "aa_legacy_groups"',
                    'name = "AA Legacy Groups"',
                    'source_country_code = "aa"',
                    "",
                    "[legacy]",
                    'python_source = "PRIOR = [\\"Other\\"]\\nVALENCIA_A_MURCIA = [\\"Villena\\"]\\nMURCIA_REF = VALENCIA_A_MURCIA"',
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="aa_reino_de_valencia",
            internal_name="REINO_DE_VALENCIA",
            name="Reino de Valencia",
            source_country_code="aa",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "REINO_DE_VALENCIA"',
                    'source_country_code = "aa"',
                    'name = "Reino de Valencia"',
                    'code = "AA-VALENCIA"',
                    'entity_type = "Reino"',
                    'level = 1',
                    "",
                    "[[include]]",
                    'country_code = "aa"',
                    "level = 3",
                    'groups = ["VALENCIA_A_MURCIA"]',
                    "",
                    "[[subtract]]",
                    'country_code = "aa"',
                    "level = 3",
                    'groups = ["VALENCIA_A_MURCIA"]',
                    "",
                    "[[capital_groups]]",
                    'country_code = "aa"',
                    "level = 3",
                    'group = "VALENCIA_A_MURCIA"',
                    "",
                    "[[children]]",
                    'name = "Valencia interior"',
                    'code = "AA-VALENCIA-INT"',
                    'entity_type = "Provincia"',
                    "level = 2",
                    "",
                    "[[children.include]]",
                    'country_code = "aa"',
                    "level = 3",
                    'groups = ["VALENCIA_A_MURCIA"]',
                    "",
                    "[[children.capital_groups]]",
                    'country_code = "aa"',
                    "level = 3",
                    'group = "VALENCIA_A_MURCIA"',
                    "",
                ]
            ),
        )

        response = self.client.post(
            "/groups/groups/aa/valencia_a_murcia/",
            {
                "internal_name": "ALICANTE_A_MURCIA",
                "blocks_json": json.dumps(
                    [
                        {
                            "country_code": "aa",
                            "names": [],
                            "sections": [
                                {
                                    "area_id": province.id,
                                    "area_name": province.name,
                                    "level": province.level,
                                    "selected": [{"id": villena.id, "name": villena.name}],
                                }
                            ],
                        }
                    ]
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/groups/groups/aa/alicante_a_murcia/?saved=1")
        self.assertFalse(SubdivisionGroup.objects.filter(slug=old_group.slug).exists())
        group = SubdivisionGroup.objects.get(slug="aa_alicante_a_murcia")
        self.assertEqual(group.name, "ALICANTE_A_MURCIA")
        self.assertIn('ALICANTE_A_MURCIA = ["Villena"]', group.content)
        legacy_bundle.refresh_from_db()
        self.assertNotIn("VALENCIA_A_MURCIA", legacy_bundle.content)
        self.assertIn("ALICANTE_A_MURCIA", legacy_bundle.content)
        self.assertIn("MURCIA_REF = ALICANTE_A_MURCIA", legacy_bundle.content)
        subdivision = DerivedSubdivision.objects.get(slug="aa_reino_de_valencia")
        self.assertNotIn("VALENCIA_A_MURCIA", subdivision.content)
        self.assertIn('groups = ["ALICANTE_A_MURCIA"]', subdivision.content)
        self.assertIn('group = "ALICANTE_A_MURCIA"', subdivision.content)
        list_response = self.client.get("/groups/")
        self.assertEqual(list_response.status_code, 200)
        list_html = list_response.content.decode("utf-8")
        self.assertNotIn("VALENCIA_A_MURCIA", list_html)
        self.assertNotIn("ALICANTE_A_MURCIA", list_html)
        list_detail_response = self.client.get("/groups/countries/aa/data/")
        self.assertEqual(list_detail_response.status_code, 200)
        group_names = {row["internal_name"] for row in list_detail_response.json()["groups"]}
        self.assertNotIn("VALENCIA_A_MURCIA", group_names)
        self.assertIn("ALICANTE_A_MURCIA", group_names)

    def test_group_source_data_returns_levels_sections_and_children(self):
        root = AdminArea.objects.create(id="gg_root", country_code="gg", code="gg", name="Group Land", level=0)
        region = AdminArea.objects.create(
            id="gg_region",
            country_code="gg",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gg_province",
            country_code="gg",
            code="11",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        other_region = AdminArea.objects.create(
            id="gg_other_region",
            country_code="gg",
            code="2",
            name="Other Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        other_province = AdminArea.objects.create(
            id="gg_other_province",
            country_code="gg",
            code="21",
            name="Other Province",
            level=2,
            entity_type="Province",
            parent=other_region,
        )
        city = AdminArea.objects.create(
            id="gg_city",
            country_code="gg",
            code="111",
            name="City",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        AdminArea.objects.create(
            id="gg_other_city",
            country_code="gg",
            code="211",
            name="Other City",
            level=3,
            entity_type="Municipality",
            parent=other_province,
        )
        localities = [
            AdminArea(
                id=f"gg_locality_{index}",
                country_code="gg",
                code=f"111{index:03d}",
                name=f"Locality {index}",
                level=4,
                entity_type="Locality",
                parent=city,
            )
            for index in range(120)
        ]
        AdminArea.objects.bulk_create(localities)
        AdminArea.objects.create(
            id="gg_residual_level_5",
            country_code="gg",
            code="1110001",
            name="Residual Level 5",
            level=5,
            entity_type="Locality",
            parent_id="gg_locality_0",
        )

        response = self.client.get(
            "/groups/source-data/?country_code=gg&level=2&section_parent_id=gg_region&parent_id=gg_province"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["root"]["id"], root.id)
        self.assertEqual(payload["root"]["children_count"], 2)
        self.assertEqual([row["value"] for row in payload["levels"]], ["1", "2"])
        self.assertEqual([row["id"] for row in payload["sections"]], ["gg_province"])
        self.assertEqual(payload["sections"][0]["label"], "Province (Province)")
        self.assertEqual(payload["sections"][0]["parent_id"], "gg_region")
        self.assertEqual(payload["sections"][0]["children_count"], 1)
        self.assertEqual(payload["children"][0]["id"], "gg_city")
        self.assertEqual(payload["children"][0]["label"], "Ciudad (Municipio)")

    def test_group_source_data_items_mode_uses_group_levels(self):
        root = AdminArea.objects.create(id="gi_root", country_code="gi", code="gi", name="Items Land", level=0)
        region = AdminArea.objects.create(
            id="gi_region",
            country_code="gi",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gi_province",
            country_code="gi",
            code="11",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        AdminArea.objects.create(
            id="gi_leaf",
            country_code="gi",
            code="111",
            name="Leaf City",
            level=3,
            entity_type="Municipality",
            parent=province,
        )

        response = self.client.get("/groups/source-data/?country_code=gi&source_mode=items")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["root"]["id"], root.id)
        self.assertEqual([row["value"] for row in payload["levels"]], ["1", "2"])

        response = self.client.get(
            "/groups/source-data/?country_code=gi&source_mode=items&level=3&section_parent_id=gi_province"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["sections"]], ["gi_leaf"])
        self.assertEqual(payload["sections"][0]["label"], "Leaf City (Municipio)")

    def test_group_source_data_children_ignores_deeper_direct_rows_when_parent_has_shallow_layer(self):
        root = AdminArea.objects.create(id="gl_root", country_code="gl", code="gl", name="Layer Land", level=0)
        region = AdminArea.objects.create(
            id="gl_region",
            country_code="gl",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        stray = AdminArea.objects.create(
            id="gl_stray_municipality",
            country_code="gl",
            code="1001",
            name="Stray Municipality",
            level=4,
            entity_type="Municipality",
            parent=root,
        )

        response = self.client.get("/groups/source-data/", {"country_code": "gl", "parent_id": root.id})

        self.assertEqual(response.status_code, 200)
        child_ids = [row["id"] for row in response.json()["children"]]
        self.assertIn(region.id, child_ids)
        self.assertNotIn(stray.id, child_ids)

    def test_group_source_data_descendants_mode_returns_all_lower_levels(self):
        root = AdminArea.objects.create(id="gd_root", country_code="gd", code="gd", name="Desc Land", level=0)
        region = AdminArea.objects.create(
            id="gd_region",
            country_code="gd",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gd_province",
            country_code="gd",
            code="11",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        municipality = AdminArea.objects.create(
            id="gd_municipality",
            country_code="gd",
            code="111",
            name="Municipality",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        locality = AdminArea.objects.create(
            id="gd_locality",
            country_code="gd",
            code="1111",
            name="Locality",
            level=4,
            entity_type="Locality",
            parent=municipality,
        )
        other_region = AdminArea.objects.create(
            id="gd_other_region",
            country_code="gd",
            code="2",
            name="Other Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        AdminArea.objects.create(
            id="gd_other_province",
            country_code="gd",
            code="21",
            name="Other Province",
            level=2,
            entity_type="Province",
            parent=other_region,
        )

        response = self.client.get(
            "/groups/source-data/?country_code=gd&source_mode=descendants&descendant_parent_ids=gd_region"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["children"]], [province.id, municipality.id, locality.id])
        municipality_payload = next(row for row in payload["children"] if row["id"] == municipality.id)
        locality_payload = next(row for row in payload["children"] if row["id"] == locality.id)
        self.assertEqual(municipality_payload["ancestor_ids"], [province.id, region.id])
        self.assertEqual(locality_payload["ancestor_ids"], [municipality.id, province.id, region.id])

    def test_group_source_data_descendants_mode_includes_groups_from_descendant_levels(self):
        root = AdminArea.objects.create(id="gh_root", country_code="gh", code="gh", name="Group Desc Land", level=0)
        region = AdminArea.objects.create(
            id="gh_region",
            country_code="gh",
            code="1",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gh_province",
            country_code="gh",
            code="11",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        municipality = AdminArea.objects.create(
            id="gh_municipality",
            country_code="gh",
            code="111",
            name="Municipality",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        AdminArea.objects.create(
            id="gh_locality",
            country_code="gh",
            code="1111",
            name="Locality",
            level=4,
            entity_type="Locality",
            parent=municipality,
        )
        other_region = AdminArea.objects.create(
            id="gh_other_region",
            country_code="gh",
            code="2",
            name="Other Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        AdminArea.objects.create(
            id="gh_other_province",
            country_code="gh",
            code="21",
            name="Other Province",
            level=2,
            entity_type="Province",
            parent=other_region,
        )
        for key, names in {
            "PROVINCE_GROUP": ["Province"],
            "MUNICIPALITY_GROUP": ["Municipality"],
            "LOCALITY_GROUP": ["Locality"],
            "OTHER_GROUP": ["Other Province"],
        }.items():
            SubdivisionGroup.objects.create(
                slug=f"gh_{key.lower()}",
                name=key,
                source_country_code="gh",
                content=f'source_country_code = "gh"\n\n{key} = {json.dumps(names)}\n',
            )

        response = self.client.get(
            "/groups/source-data/",
            {"country_code": "gh", "source_mode": "descendants", "descendant_parent_ids": region.id, "include_groups": "1"},
        )

        self.assertEqual(response.status_code, 200)
        group_ids = {row["id"] for row in response.json()["children"] if row.get("source_kind") == "group"}
        self.assertIn("group::gh::PROVINCE_GROUP", group_ids)
        self.assertIn("group::gh::MUNICIPALITY_GROUP", group_ids)
        self.assertIn("group::gh::LOCALITY_GROUP", group_ids)
        self.assertNotIn("group::gh::OTHER_GROUP", group_ids)

    def test_group_source_data_children_skip_merge_sources(self):
        root = AdminArea.objects.create(id="gm_root", country_code="gm", code="gm", name="Merge Land", level=0)
        province = AdminArea.objects.create(
            id="gm_province",
            country_code="gm",
            code="1",
            name="Province",
            level=2,
            entity_type="Province",
            parent=root,
        )
        normal = AdminArea.objects.create(
            id="gm_normal",
            country_code="gm",
            code="101",
            name="Normal Municipality",
            level=3,
            entity_type="Municipality",
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.NONE,
        )
        unified = AdminArea.objects.create(
            id="gm_unified",
            country_code="gm",
            code="199",
            name="Unified City",
            level=3,
            entity_type="City",
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        source = AdminArea.objects.create(
            id="gm_source",
            country_code="gm",
            code="102",
            name="Merge Source",
            level=3,
            entity_type="Municipality",
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )

        response = self.client.get("/groups/source-data/?country_code=gm&parent_id=gm_province")

        self.assertEqual(response.status_code, 200)
        child_ids = [row["id"] for row in response.json()["children"]]
        self.assertIn(normal.id, child_ids)
        self.assertIn(unified.id, child_ids)
        self.assertNotIn(source.id, child_ids)

    def test_group_source_data_children_keep_direct_unified_city_on_mixed_levels(self):
        root = AdminArea.objects.create(id="gmx_root", country_code="gmx", code="gmx", name="Merge Land", level=0)
        region = AdminArea.objects.create(
            id="gmx_region",
            country_code="gmx",
            code="r",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="gmx_province",
            country_code="gmx",
            code="p",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        city = AdminArea.objects.create(
            id="gmx_city",
            country_code="gmx",
            code="c",
            name="Unified City",
            level=3,
            entity_type="City",
            parent=region,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )

        response = self.client.get("/groups/source-data/?country_code=gmx&parent_id=gmx_region")

        self.assertEqual(response.status_code, 200)
        child_ids = [row["id"] for row in response.json()["children"]]
        self.assertIn(province.id, child_ids)
        self.assertIn(city.id, child_ids)

    def test_group_entry_form_hydrates_legacy_names_from_sql(self):
        root = AdminArea.objects.create(id="spain_root", country_code="spain", code="spain", name="Spain", level=0)
        region = AdminArea.objects.create(
            id="spain_cm",
            country_code="spain",
            code="cm",
            name="Castilla-La Mancha",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="spain_albacete",
            country_code="spain",
            code="02",
            name="Albacete",
            level=2,
            entity_type="Province",
            parent=region,
        )
        villatoya = AdminArea.objects.create(
            id="spain_villatoya",
            country_code="spain",
            code="02080",
            name="Villatoya",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        alborea = AdminArea.objects.create(
            id="spain_alborea",
            country_code="spain",
            code="02005",
            name="Alborea",
            level=3,
            entity_type="Municipality",
            parent=province,
        )
        AdminArea.objects.create(
            id="spain_villatoya_seat",
            country_code="spain",
            code="020820001",
            name="Villatoya",
            level=4,
            entity_type="Municipality seat",
            parent=villatoya,
        )
        AdminArea.objects.create(
            id="spain_alborea_seat",
            country_code="spain",
            code="020050001",
            name="Alborea",
            level=4,
            entity_type="Municipality seat",
            parent=alborea,
        )
        SubdivisionGroup.objects.create(
            slug="spanish_old_provinces",
            name="Spanish old provinces",
            source_country_code="spain",
            content="\n".join(
                [
                    'kind = "subdivision_group"',
                    'source_country_code = "spain"',
                    "",
                    "[legacy]",
                    'python_source = "ALBACETE_A_CUENCA = [\\"Villatoya\\", \\"Alborea\\"]"',
                ]
            ),
        )

        response = self.client.get("/groups/groups/spain/albacete_a_cuenca/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('data-group-return-link', html)
        self.assertIn('data-group-return-country="spain"', html)
        self.assertIn('data-group-return-row-href="/groups/groups/spain/albacete_a_cuenca/"', html)
        self.assertIn('data-group-return-table="groups"', html)
        self.assertIn('data-group-return-refresh="0"', html)
        match = re.search(r'<script id="group-blocks-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        blocks = json.loads(match.group(1))
        self.assertEqual(blocks[0]["names"], [])
        self.assertEqual(blocks[0]["sections"][0]["area_id"], province.id)
        selected = blocks[0]["sections"][0]["selected"]
        self.assertEqual({item["id"] for item in selected}, {villatoya.id, alborea.id})
        self.assertEqual({item["name"] for item in selected}, {"Villatoya", "Alborea"})

        saved_response = self.client.get("/groups/groups/spain/albacete_a_cuenca/?saved=1")
        self.assertEqual(saved_response.status_code, 200)
        saved_html = saved_response.content.decode("utf-8")
        self.assertIn('data-group-return-refresh="1"', saved_html)

        response = self.client.post(
            "/groups/source-data/",
            data=json.dumps({"country_code": "spain", "names": ["Villatoya", "Alborea"], "sections": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["names"], [])
        self.assertEqual(payload["sections"][0]["area_id"], province.id)
        selected = payload["sections"][0]["selected"]
        self.assertEqual({item["id"] for item in selected}, {villatoya.id, alborea.id})
        self.assertEqual({item["name"] for item in selected}, {"Villatoya", "Alborea"})

    def test_legacy_group_country_inference_uses_source_python_before_embedded_keys(self):
        SubdivisionGroup.objects.create(
            slug="portugal_provinces",
            name="Portugal Provinces",
            source_country_code="",
            content="\n".join(
                [
                    'kind = "subdivision_group"',
                    'slug = "portugal_provinces"',
                    'name = "Portugal Provinces"',
                    'source_python = "ciudades_del_mundo/historical_divisions/portugal_provinces.py"',
                    'source_country_code = ""',
                    "",
                    "[legacy]",
                    'python_source = "PORTUGUESE = [\\"Arouca\\"]\\nOLIVENZA = {\\"restar\\": {\\"spain\\": [\\"Olivenza\\"]}}"',
                ]
            ),
        )
        SubdivisionGroup.objects.create(
            slug="spanish_old_provinces",
            name="Spanish Old Provinces",
            source_country_code="",
            content="\n".join(
                [
                    'kind = "subdivision_group"',
                    'slug = "spanish_old_provinces"',
                    'name = "Spanish Old Provinces"',
                    'source_python = "ciudades_del_mundo/historical_divisions/spanish_old_provinces.py"',
                    'source_country_code = ""',
                    "",
                    "[legacy]",
                    'python_source = "SPANISH = [\\"Alborea\\"]"',
                ]
            ),
        )

        self.assertEqual([group.slug for group in _subdivision_groups_for_country("portugal")], ["portugal_provinces"])
        self.assertEqual([group.slug for group in _subdivision_groups_for_country("spain")], ["spanish_old_provinces"])

    def test_group_edit_exports_and_imports_country_toml_seed(self):
        group = SubdivisionGroup.objects.create(
            slug="aa_export_group",
            name="Export group",
            source_country_code="aa",
            content="\n".join(
                [
                    'source_country_code = "aa"',
                    "",
                    "EXPORT_GROUP = []",
                    "",
                ]
            ),
        )
        SubdivisionGroup.objects.create(
            slug="aa_other_group",
            name="Other group",
            source_country_code="aa",
            content='source_country_code = "aa"\n\nOTHER_GROUP = ["Two"]\n',
        )
        SubdivisionGroup.objects.create(
            slug="bb_export_group",
            name="BB group",
            source_country_code="bb",
            content='source_country_code = "bb"\n\nBB_GROUP = ["Three"]\n',
        )

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch(
                "ciudades_del_mundo.services.derived_config_seeds.SUBDIVISION_GROUP_SEED_ROOT",
                root,
            ):
                response = self.client.post(
                    "/groups/aa/export-toml/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

                self.assertEqual(response.status_code, 200)
                seed = root / "groups" / "aa.toml"
                self.assertTrue(seed.is_file())
                seed_text = seed.read_text(encoding="utf-8")
                self.assertNotIn('name = "Export group"', seed_text)
                self.assertNotIn('kind = "subdivision_group"', seed_text)
                self.assertIn('source_country_code = "aa"', seed_text)
                self.assertIn("EXPORT_GROUP = []", seed_text)
                self.assertIn('OTHER_GROUP = [\n    "Two",\n]', seed_text)
                self.assertNotIn("BB_GROUP", seed_text)

                seed.write_text(
                    "\n".join(
                        [
                            'source_country_code = "aa"',
                            "",
                            'EXPORT_GROUP = ["One"]',
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                response = self.client.post(
                    "/groups/aa_export_group/import-toml/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        self.assertEqual(group.name, "EXPORT_GROUP")
        self.assertEqual(group.source_country_code, "aa")
        self.assertIn('source_country_code = "aa"', group.content)
        self.assertIn('EXPORT_GROUP = ["One"]', group.content)

    def test_group_seed_paths_use_country_bundle_and_legacy_assignment_import(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            groups_root = root / "groups"
            subdivisions_root = root / "subdivisions"
            groups_root.mkdir()
            subdivisions_root.mkdir()
            group_seed = groups_root / "france.toml"
            group_seed.write_text(
                'source_country_code = "france"\n\nALTA_CERDANA = ["One", "Two"]\nBAJA_CERDANA = ["Three"]\n',
                encoding="utf-8",
            )
            (subdivisions_root / "ignored_subdivision.toml").write_text(
                'kind = "subdivision_group"\nslug = "ignored_subdivision"\nname = "Ignored"\n',
                encoding="utf-8",
            )

            with patch(
                "ciudades_del_mundo.services.derived_config_seeds.SUBDIVISION_GROUP_SEED_ROOT",
                root,
            ):
                paths = bundled_subdivision_group_paths()
                self.assertEqual([path.name for path in paths], ["france.toml"])
                records = import_subdivision_group_path_records(paths[0], force=True)
                imported = import_subdivision_group_seed("france", force=True)

        self.assertEqual(imported.slug, "france_alta_cerdana")
        self.assertEqual([record.slug for record in records], ["france_alta_cerdana", "france_baja_cerdana"])
        self.assertEqual(imported.name, "ALTA_CERDANA")
        self.assertEqual(imported.source_country_code, "france")
        self.assertEqual(imported.content, 'source_country_code = "france"\n\nALTA_CERDANA = ["One", "Two"]\n')

    def test_group_import_toml_queues_one_task_per_seed(self):
        with patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths") as paths:
            paths.return_value = [Path("group_one.toml"), Path("group_two.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post("/groups/import-toml/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(start.call_args_list[0].kwargs["args"], ["sync_derived_configs", "groups", "group_one", "--force"])
        self.assertEqual(start.call_args_list[1].kwargs["args"], ["sync_derived_configs", "groups", "group_two", "--force"])

    def test_group_import_toml_ajax_returns_task_popup_payload(self):
        with patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths") as paths:
            paths.return_value = [Path("group_one.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post(
                    "/groups/import-toml/",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "queued")
        self.assertEqual(payload["status_url"], "/tasks/queued/status/")
        self.assertEqual(payload["detail_url"], "/tasks/queued/")
        self.assertEqual(start.call_count, 1)

    def test_group_entry_import_toml_ajax_imports_refreshes_and_redirects_to_modern_editor(self):
        group = SubdivisionGroup.objects.create(
            slug="aa_alpha",
            name="ALPHA",
            source_country_code="aa",
            content='source_country_code = "aa"\n\nALPHA = ["One"]\n',
        )
        with (
            patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths", return_value=[Path("groups/aa.toml")]),
            patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
            patch("ciudades_del_mundo.web.views.import_subdivision_group_path_records", return_value=[group]) as importer,
        ):
            response = self.client.post(
                "/groups/aa_alpha/import-toml/",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["refresh"])
        self.assertEqual(payload["redirect_url"], "/groups/groups/aa/alpha/")
        self.assertEqual(importer.call_args.args[0], Path("groups/aa.toml"))

    def test_derived_subdivision_importer_converts_legacy_dicts(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            root.mkdir(parents=True, exist_ok=True)
            seed = root / "spain.toml"
            seed.write_text(
                "\n".join(
                    [
                        'source_country_code = "spain"',
                        "",
                        "[legacy]",
                        "python_source = '''",
                        'ALBACETE_A_CUENCA = ["Villatoya", "Alborea"]',
                        'MURCIA = {',
                        '    "name": "Murcia",',
                        '    "code": "MUR",',
                        '    "entity_type": "Reino",',
                        '    "capitals": ["Murcia"],',
                        '    "flag_url": "https://example.test/murcia-flag.svg",',
                        '    "coat_url": "https://example.test/murcia-coat.svg",',
                        '    "spec": {',
                        '        2: {"spain": ["Murcia", "Albacete"]},',
                        '        "restar": {3: {"spain": ALBACETE_A_CUENCA}},',
                        "    },",
                        "}",
                        'ORAN = {',
                        '    "name": "Oran",',
                        '    "code": "ORA",',
                        '    "entity_type": "Region",',
                        '    "spec": {',
                        '        2: {"algeria": ["Oran"]},',
                        "    },",
                        "}",
                        "'''",
                    ]
                ),
                encoding="utf-8",
            )
            DerivedSubdivision.objects.create(
                slug="spain_oran",
                internal_name="ORAN",
                name="Oran",
                source_country_code="spain",
                content='kind = "derived_subdivision"\ninternal_name = "ORAN"\nsource_country_code = "spain"\n',
            )

            with patch(
                "ciudades_del_mundo.services.derived_config_seeds.DERIVED_SUBDIVISION_SEED_ROOT",
                root,
            ):
                paths = bundled_derived_subdivision_paths(["spain"])
                records = import_derived_subdivision_path_records(paths[0], force=True)

        self.assertEqual([record.slug for record in records], ["spain_murcia", "algeria_oran"])
        record_by_slug = {record.slug: record for record in records}
        record = record_by_slug["spain_murcia"]
        self.assertEqual(record.internal_name, "MURCIA")
        self.assertEqual(record.name, "Murcia")
        self.assertEqual(record.entity_type, "Reino")
        self.assertIn("[[include]]", record.content)
        self.assertIn('flag_url = "https://example.test/murcia-flag.svg"', record.content)
        self.assertIn('coat_url = "https://example.test/murcia-coat.svg"', record.content)
        self.assertIn('names = ["Murcia", "Albacete"]', record.content)
        self.assertIn("[[subtract]]", record.content)
        self.assertIn('groups = ["ALBACETE_A_CUENCA"]', record.content)
        oran = record_by_slug["algeria_oran"]
        self.assertEqual(oran.source_country_code, "algeria")
        self.assertEqual(oran.internal_name, "ORAN")
        self.assertIn('source_country_code = "algeria"', oran.content)
        self.assertIn('country_code = "algeria"', oran.content)
        self.assertFalse(DerivedSubdivision.objects.filter(slug="spain_oran").exists())

    def test_derived_subdivision_export_toml_writes_one_country_file(self):
        DerivedSubdivision.objects.create(
            slug="aa_new_province",
            internal_name="NEW_PROVINCE",
            name="New Province",
            source_country_code="aa",
            code="NP",
            entity_type="Province",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "NEW_PROVINCE"',
                    'source_country_code = "aa"',
                    'name = "New Province"',
                    'code = "NP"',
                    'entity_type = "Province"',
                    "",
                ]
            ),
        )

        with TemporaryDirectory() as tmpdir:
            exported = export_derived_subdivisions_to_toml(
                force=True,
                country_codes=["aa"],
                output_dir=Path(tmpdir),
            )
            exported_path = Path(tmpdir) / "aa.toml"
            content = exported_path.read_text(encoding="utf-8")

        self.assertEqual(exported, 1)
        self.assertIn('kind = "derived_subdivision_bundle"', content)
        self.assertIn("[[subdivisions]]", content)
        self.assertIn('internal_name = "NEW_PROVINCE"', content)
        self.assertIn('content = "kind = \\"derived_subdivision\\"', content)

    def test_derived_subdivision_import_toml_queues_country_seed_tasks(self):
        with patch("ciudades_del_mundo.web.views.bundled_derived_subdivision_paths") as paths:
            paths.return_value = [
                Path("subdivisions/spain.toml"),
                Path("subdivisions/france.toml"),
            ]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post("/subdivisions/import-toml/", {"country_code": "spain"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(
            start.call_args_list[0].kwargs["args"],
            ["sync_derived_configs", "subdivisions", "spain", "--force"],
        )

    def test_derived_subdivision_import_toml_ajax_imports_and_refreshes(self):
        with (
            patch(
                "ciudades_del_mundo.web.views.bundled_derived_subdivision_paths",
                return_value=[Path("subdivisions/spain.toml"), Path("subdivisions/france.toml")],
            ),
            patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
            patch("ciudades_del_mundo.web.views.import_derived_subdivision_path_records") as importer,
        ):
            importer.return_value = [_Object(slug="spain_murcia")]
            response = self.client.post(
                "/subdivisions/import-toml/",
                {"country_code": "spain"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["refresh"])
        self.assertEqual(importer.call_count, 1)
        self.assertEqual(importer.call_args.args[0], Path("subdivisions/spain.toml"))

    def test_derived_subdivision_import_toml_ajax_queue_task_returns_task_payload(self):
        with patch(
            "ciudades_del_mundo.web.views.bundled_derived_subdivision_paths",
            return_value=[Path("subdivisions/spain.toml"), Path("subdivisions/france.toml")],
        ):
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post(
                    "/subdivisions/import-toml/",
                    {"country_code": "spain", "queue_task": "1"},
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                    HTTP_ACCEPT="application/json",
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "queued")
        self.assertEqual(payload["status_url"], "/tasks/queued/status/")
        self.assertEqual(start.call_count, 1)
        self.assertEqual(
            start.call_args.kwargs["args"],
            ["sync_derived_configs", "subdivisions", "spain", "--force"],
        )

    def test_derived_subdivision_build_button_queues_country_build(self):
        DerivedSubdivision.objects.create(
            slug="spain_murcia",
            internal_name="MURCIA",
            name="Murcia",
            source_country_code="spain",
            content='kind = "derived_subdivision"\ninternal_name = "MURCIA"\nsource_country_code = "spain"\n',
        )

        with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
            start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
            response = self.client.post("/subdivisions/spain/build/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            start.call_args.kwargs["args"],
            ["build_derived_subdivisions", "spain", "--force"],
        )

    def test_derived_subdivision_build_ajax_returns_task_popup_payload(self):
        DerivedSubdivision.objects.create(
            slug="spain_murcia",
            internal_name="MURCIA",
            name="Murcia",
            source_country_code="spain",
            content='kind = "derived_subdivision"\ninternal_name = "MURCIA"\nsource_country_code = "spain"\n',
        )

        with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
            start.return_value = _Object(id="queued-build", status=ManagedTask.Status.QUEUED, is_active=True)
            response = self.client.post(
                "/subdivisions/spain/build/",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], "queued-build")
        self.assertEqual(payload["status_url"], "/tasks/queued-build/status/")
        self.assertEqual(payload["detail_url"], "/tasks/queued-build/")

    def test_derived_subdivision_builder_materializes_groups_subtractions_and_capitals(self):
        root = AdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
            area_km2=Decimal("300.00"),
            pop_latest=3000,
        )
        province = AdminArea.objects.create(
            id="spain_albacete",
            country_code="spain",
            code="02",
            name="Albacete",
            level=2,
            parent=root,
            area_km2=Decimal("300.00"),
            pop_latest=3000,
        )
        mun_a = AdminArea.objects.create(
            id="spain_mun_a",
            country_code="spain",
            code="02001",
            name="Municipio A",
            level=3,
            parent=province,
            area_km2=Decimal("120.00"),
            pop_latest=1200,
        )
        mun_b = AdminArea.objects.create(
            id="spain_mun_b",
            country_code="spain",
            code="02002",
            name="Municipio B",
            level=3,
            parent=province,
            area_km2=Decimal("180.00"),
            pop_latest=1800,
        )
        mun_merged = AdminArea.objects.create(
            id="spain_mun_merged",
            country_code="spain",
            code="02099",
            name="Municipio Unificado",
            level=3,
            parent=province,
            area_km2=Decimal("70.00"),
            pop_latest=700,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        mun_merge_source = AdminArea.objects.create(
            id="spain_mun_merge_source",
            country_code="spain",
            code="02003",
            name="Municipio Fuente",
            level=3,
            parent=province,
            area_km2=Decimal("30.00"),
            pop_latest=300,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )
        SubdivisionGroup.objects.create(
            slug="spain_albacete_a_cuenca",
            name="ALBACETE_A_CUENCA",
            source_country_code="spain",
            content='source_country_code = "spain"\n\nALBACETE_A_CUENCA = ["Municipio B"]\n',
        )
        SubdivisionGroup.objects.create(
            slug="spain_capital_compuesta",
            name="CAPITAL_COMPUESTA",
            source_country_code="spain",
            content='source_country_code = "spain"\n\nCAPITAL_COMPUESTA = ["Municipio Unificado"]\n',
        )
        DerivedSubdivision.objects.create(
            slug="spain_murcia",
            internal_name="MURCIA",
            name="Murcia",
            source_country_code="spain",
            code="MUR",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "MURCIA"',
                    'source_country_code = "spain"',
                    'name = "Murcia"',
                    'code = "MUR"',
                    'entity_type = "Provincia"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'names = ["Albacete"]',
                    "groups = []",
                    "",
                    "[[subtract]]",
                    'country_code = "spain"',
                    "level = 3",
                    "names = []",
                    'groups = ["ALBACETE_A_CUENCA"]',
                    "",
                    "[[capital_groups]]",
                    'country_code = "spain"',
                    "level = 3",
                    'group = "CAPITAL_COMPUESTA"',
                    'capital_name = "Capital compuesta"',
                    "",
                ]
            ),
        )

        result = build_derived_subdivisions_for_country("spain", force=True)

        self.assertEqual(result.built, 1)
        area = NuevoAdminArea.objects.get(id="spain-ESP-MUR")
        self.assertEqual(area.code, "ESP-MUR")
        self.assertEqual(area.level, 2)
        self.assertEqual(area.entity_type, "Provincia")
        self.assertEqual(area.area_km2, Decimal("190.00"))
        self.assertEqual(area.pop_latest, 1900)
        self.assertEqual(set(area.municipios_originales.all()), {mun_a, mun_merged})
        self.assertNotIn(mun_b, area.municipios_originales.all())
        self.assertNotIn(mun_merge_source, area.municipios_originales.all())
        self.assertEqual(list(area.capitals.all()), [mun_merged])
        self.assertEqual(area.capital_names_by_language["es"][mun_merged.id], "Capital compuesta")

    def test_derived_subdivision_builder_folds_single_self_child_wrapper(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province = AdminArea.objects.create(
            id="spain_province",
            country_code="spain",
            code="02",
            name="Provincia",
            level=2,
            parent=root,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        municipality = AdminArea.objects.create(
            id="spain_mun_a",
            country_code="spain",
            code="02001",
            name="Municipio A",
            level=3,
            parent=province,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        DerivedSubdivision.objects.create(
            slug="spain_provincia",
            internal_name="PROVINCIA",
            name="Provincia Nueva",
            source_country_code="spain",
            code="PRO",
            entity_type="Provincia",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "PROVINCIA"',
                    'source_country_code = "spain"',
                    'name = "Provincia Nueva"',
                    'code = "PRO"',
                    'entity_type = "Provincia"',
                    "level = 1",
                    "",
                    "[[children]]",
                    'name = "Provincia Nueva"',
                    'code = "PN"',
                    'entity_type = "Provincia"',
                    "level = 1",
                    "",
                    "[[children.include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_province"]',
                ]
            ),
        )

        result = build_derived_subdivisions_for_country("spain", force=True)

        self.assertEqual(result.built, 1)
        area = NuevoAdminArea.objects.get(id="spain-ESP-PRO")
        self.assertEqual(area.parent_id, "spain")
        self.assertEqual(area.level, 1)
        self.assertEqual(set(area.municipios_originales.all()), {municipality})
        self.assertFalse(NuevoAdminArea.objects.filter(id="spain-ESP-PN").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(id="spain-ESP-PRO-PN").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(id="spain-ESP-PRO-PRO").exists())

    def test_derived_subdivision_builder_folds_self_child_wrapper_and_keeps_other_children(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province_a = AdminArea.objects.create(
            id="spain_province_a",
            country_code="spain",
            code="02",
            name="Provincia A",
            level=2,
            parent=root,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        province_b = AdminArea.objects.create(
            id="spain_province_b",
            country_code="spain",
            code="03",
            name="Provincia B",
            level=2,
            parent=root,
            area_km2=Decimal("70.00"),
            pop_latest=700,
        )
        municipality_a = AdminArea.objects.create(
            id="spain_mun_a",
            country_code="spain",
            code="02001",
            name="Municipio A",
            level=3,
            parent=province_a,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        municipality_b = AdminArea.objects.create(
            id="spain_mun_b",
            country_code="spain",
            code="03001",
            name="Municipio B",
            level=3,
            parent=province_b,
            area_km2=Decimal("70.00"),
            pop_latest=700,
        )
        DerivedSubdivision.objects.create(
            slug="spain_estado",
            internal_name="ESTADO",
            name="Estado",
            source_country_code="spain",
            code="EST",
            entity_type="Estado",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ESTADO"',
                    'source_country_code = "spain"',
                    'name = "Estado"',
                    'code = "EST"',
                    'entity_type = "Estado"',
                    "level = 1",
                    "",
                    "[[children]]",
                    'name = "Estado"',
                    'code = "EST"',
                    'entity_type = "Estado"',
                    "level = 1",
                    "",
                    "[[children.include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_province_a"]',
                    "",
                    "[[children]]",
                    'name = "Dependencia"',
                    'code = "DEP"',
                    'entity_type = "Dependencia"',
                    "level = 1",
                    "",
                    "[[children.include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_province_b"]',
                ]
            ),
        )

        result = build_derived_subdivisions_for_country("spain", force=True)

        self.assertEqual(result.built, 2)
        area = NuevoAdminArea.objects.get(id="spain-ESP-EST")
        self.assertEqual(area.area_km2, Decimal("100.00"))
        self.assertEqual(area.pop_latest, 1000)
        self.assertEqual(set(area.municipios_originales.all()), {municipality_a, municipality_b})
        self.assertEqual(set(area.children.values_list("name", flat=True)), {"Dependencia"})
        self.assertFalse(NuevoAdminArea.objects.filter(id="spain-ESP-EST-EST").exists())

    def test_derived_subdivision_builder_uses_parent_code_hierarchy(self):
        AdminArea.objects.create(id="spain", country_code="spain", code="spain", name="Espana", level=0)
        DerivedSubdivision.objects.create(
            slug="spain_corona_de_aragon",
            internal_name="CORONA_DE_ARAGON",
            name="Corona de Aragon",
            source_country_code="spain",
            code="ESP-ARA",
            entity_type="Corona",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CORONA_DE_ARAGON"',
                    'source_country_code = "spain"',
                    'name = "Corona de Aragon"',
                    'code = "ESP-ARA"',
                    'parent_code = "ESP"',
                    'entity_type = "Corona"',
                    "level = 1",
                    "",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="spain_aragon",
            internal_name="ARAGON",
            name="Reino de Aragon",
            source_country_code="spain",
            code="ESP-ARA-ARA",
            entity_type="Reino",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "ARAGON"',
                    'source_country_code = "spain"',
                    'name = "Reino de Aragon"',
                    'code = "ESP-ARA-ARA"',
                    'parent_code = "ESP-ARA"',
                    'entity_type = "Reino"',
                    "level = 2",
                    "",
                ]
            ),
        )

        result = build_derived_subdivisions_for_country("spain", force=True)

        self.assertEqual(result.built, 2)
        self.assertEqual(NuevoAdminArea.objects.get(id="spain").code, "ESP")
        corona = NuevoAdminArea.objects.get(id="spain-ESP-ARA")
        aragon = NuevoAdminArea.objects.get(id="spain-ESP-ARA-ARA")
        self.assertEqual(corona.parent_id, "spain")
        self.assertEqual(corona.level, 1)
        self.assertEqual(aragon.parent_id, corona.id)
        self.assertEqual(aragon.level, 2)

    def test_derived_subdivision_builder_generates_source_unit_children_when_unchecked(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province = AdminArea.objects.create(
            id="spain_province",
            country_code="spain",
            code="02",
            name="Provincia",
            level=2,
            parent=root,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        mun_a = AdminArea.objects.create(
            id="spain_mun_a",
            country_code="spain",
            code="02001",
            name="Municipio A",
            level=3,
            parent=province,
            area_km2=Decimal("10.00"),
            pop_latest=100,
        )
        mun_b = AdminArea.objects.create(
            id="spain_mun_b",
            country_code="spain",
            code="02002",
            name="Municipio B",
            level=3,
            parent=province,
            area_km2=Decimal("20.00"),
            pop_latest=200,
        )
        DerivedSubdivision.objects.create(
            slug="spain_corona",
            internal_name="CORONA",
            name="Corona",
            source_country_code="spain",
            code="COR",
            entity_type="Corona",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CORONA"',
                    'source_country_code = "spain"',
                    'name = "Corona"',
                    'code = "COR"',
                    'parent_code = "ESP"',
                    'entity_type = "Corona"',
                    "level = 1",
                    "use_selected_entities_as_children = false",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_province"]',
                ]
            ),
        )

        build_derived_subdivisions_for_country("spain", force=True)

        corona = NuevoAdminArea.objects.get(id="spain-ESP-COR")
        child_names = set(corona.children.values_list("name", flat=True))
        self.assertEqual(child_names, {"Municipio A", "Municipio B"})
        self.assertEqual(corona.children.get(name="Municipio A").level, 2)
        self.assertEqual(corona.children.get(name="Municipio B").level, 2)
        self.assertEqual(set(corona.municipios_originales.all()), {mun_a, mun_b})

    def test_derived_subdivision_builder_can_use_created_subdivisions_as_children(self):
        root = AdminArea.objects.create(id="spain", country_code="spain", code="ESP", name="Espana", level=0)
        province = AdminArea.objects.create(
            id="spain_province",
            country_code="spain",
            code="02",
            name="Provincia",
            level=2,
            parent=root,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        municipality = AdminArea.objects.create(
            id="spain_mun_a",
            country_code="spain",
            code="02001",
            name="Municipio A",
            level=3,
            parent=province,
            area_km2=Decimal("30.00"),
            pop_latest=300,
        )
        DerivedSubdivision.objects.create(
            slug="spain_principado",
            internal_name="PRINCIPADO",
            name="Principado",
            source_country_code="spain",
            code="PRI",
            entity_type="Principado",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "PRINCIPADO"',
                    'source_country_code = "spain"',
                    'name = "Principado"',
                    'code = "PRI"',
                    'parent_code = "ESP"',
                    'entity_type = "Principado"',
                    "level = 2",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_province"]',
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="spain_corona",
            internal_name="CORONA",
            name="Corona",
            source_country_code="spain",
            code="COR",
            entity_type="Corona",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "CORONA"',
                    'source_country_code = "spain"',
                    'name = "Corona"',
                    'code = "COR"',
                    'parent_code = "ESP"',
                    'entity_type = "Corona"',
                    "level = 1",
                    "use_selected_entities_as_children = true",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 3",
                    'derived_subdivisions = ["PRINCIPADO"]',
                ]
            ),
        )

        build_derived_subdivisions_for_country("spain", force=True)

        corona = NuevoAdminArea.objects.get(id="spain-ESP-COR")
        principado = NuevoAdminArea.objects.get(id="spain-ESP-COR-PRI")
        self.assertEqual(principado.parent_id, corona.id)
        self.assertEqual(principado.level, 3)
        self.assertEqual(set(principado.municipios_originales.all()), {municipality})
        municipal_child = principado.children.get(name="Municipio A")
        self.assertEqual(municipal_child.level, 4)

    def test_group_import_toml_filters_seed_tasks_by_country(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            aa_seed = tmp_path / "aa_group.toml"
            aa_seed.write_text(
                'kind = "subdivision_group"\nslug = "aa_group"\nname = "AA"\nsource_country_code = "aa"\n',
                encoding="utf-8",
            )
            inferred_seed = tmp_path / "legacy_aa_group.toml"
            inferred_seed.write_text(
                "\n".join(
                    [
                        'kind = "subdivision_group"',
                        'slug = "legacy_aa_group"',
                        'name = "Legacy AA"',
                        'source_country_code = ""',
                        "",
                        "[legacy]",
                        'python_source = "DIVISIONS = [{\\"spec\\": {1: {\\"aa\\": [\\"A\\"]}}}]"',
                    ]
                ),
                encoding="utf-8",
            )
            bb_seed = tmp_path / "bb_group.toml"
            bb_seed.write_text(
                'kind = "subdivision_group"\nslug = "bb_group"\nname = "BB"\nsource_country_code = "bb"\n',
                encoding="utf-8",
            )
            with patch(
                "ciudades_del_mundo.web.views.bundled_subdivision_group_paths",
                return_value=[aa_seed, inferred_seed, bb_seed],
            ):
                with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                    start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                    response = self.client.post("/groups/import-toml/", {"country_code": "aa"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 2)
        queued_slugs = [call.kwargs["args"][2] for call in start.call_args_list]
        self.assertEqual(queued_slugs, ["aa_group", "legacy_aa_group"])


class WebInterfaceHelperTests(TestCase):
    def test_generated_page_level_inference_keeps_source_and_route_independent(self):
        self.assertEqual(_inferred_generated_page_level("morocco", "admin", "benimellalkhenifra/admin"), 1)
        self.assertEqual(_inferred_generated_page_level("morocco", "admin", "admin"), 0)
        self.assertEqual(_inferred_generated_page_level("gibraltar", "cities", "admin"), 0)
        self.assertEqual(_inferred_generated_page_level("france", "cities", "cities/guyane"), 1)

    def test_area_map_helpers_include_capitals_and_major_city(self):
        capital = _Object(id="capital-1", name="Capital source")
        area = _Object(
            name="Region",
            country_code="testland",
            level=1,
            parent=None,
            capitals=_Relation([capital]),
            capital_names_by_language={"es": {"capital-1": "Capital traducida"}},
            most_populate_city=_Object(name="Big City"),
        )

        self.assertEqual(_area_capital_display_names(area, "es"), ["Capital traducida"])
        places = _area_related_places(area, "es")
        self.assertIn(
            {"kind": "Capital registrada", "name": "Capital traducida", "query": "Capital traducida, Region"},
            places,
        )
        self.assertIn(
            {"kind": "Ciudad mayor registrada", "name": "Big City", "query": "Big City, Region"},
            places,
        )


    def test_admin_area_detail_groups_children_by_level(self):
        root = AdminArea.objects.create(
            id="test_root",
            country_code="testcountry",
            code="testcountry",
            name="Test Country",
            level=0,
            pop_latest=1000,
        )
        province = AdminArea.objects.create(
            id="test_province",
            country_code="testcountry",
            code="province",
            name="Province",
            entity_type="Province",
            level=2,
            parent=root,
            pop_latest=500,
        )
        AdminArea.objects.create(
            id="test_commune",
            country_code="testcountry",
            code="commune",
            name="Commune",
            entity_type="Commune",
            level=3,
            parent=province,
            pop_latest=300,
        )
        AdminArea.objects.create(
            id="test_place",
            country_code="testcountry",
            code="place",
            name="Urban Place",
            entity_type="Urban Place",
            level=4,
            parent=province,
            pop_latest=200,
        )

        payload = _admin_area_detail_payload(province)

        self.assertEqual([group["level"] for group in payload["child_groups"]], [3, 4])
        self.assertEqual([row["name"] for row in payload["child_groups"][0]["children"]], ["Commune"])
        self.assertEqual([row["name"] for row in payload["child_groups"][1]["children"]], ["Urban Place"])

    def test_validate_config_text_accepts_minimal_toml(self):
        _validate_config_text(
            "testland",
            """
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""",
        )

    def test_manual_config_save_prefers_visible_level_fields_over_stale_pages_json(self):
        post = QueryDict("", mutable=True)
        post.update(
            {
                "manual_country_code": "netherlands",
                "manual_legal_subdivision": "2",
                "pages_json": (
                    '[{"source": "admin", "path": ["admin"], '
                    '"include": {"infosection": true, "major_subdivision": true, "minor_subdivision": true}}, '
                    '{"source": "cities", "path": ["drenthe"], "parent_level": 3, '
                    '"include": {"infosection": false, "major_subdivision": false, "cities": true}}]'
                ),
            }
        )
        post.setlist("page_source", ["admin", "cities"])
        post.setlist("page_enabled", ["true", "true"])
        post.setlist("page_force_highest_level", ["", "3"])
        post.setlist("page_parent_level", ["", "2"])
        post.setlist("page_include_infosection", ["true", "true"])
        post.setlist("page_include_major_subdivision", ["true", "false"])
        post.setlist("page_include_minor_subdivision", ["true", "false"])
        post.setlist("page_include_cities", ["false", "true"])

        content = _render_config_from_manual_post("netherlands", post, current_content="")

        self.assertIn('path = ["drenthe"]', content)
        self.assertIn("force_highest_level = 3", content)
        self.assertIn("parent_level = 2", content)
        self.assertIn("include = { cities = true, infosection = true, major_subdivision = false }", content)

    def test_config_edit_autosave_post_persists_visible_manual_page_changes(self):
        ScrapingConfig.objects.update_or_create(
            slug="netherlands",
            defaults={
                "country_code": "netherlands",
                "name": "Netherlands",
                "content": """
LEGAL_SUBDIVISION = 2
scrape_schema_version = 2

[[pages]]
source = "admin"
path = ["admin"]
include = { infosection = true, major_subdivision = true, minor_subdivision = true }

[[pages]]
source = "cities"
path = ["drenthe"]
parent_level = 3
include = { cities = true, infosection = false, major_subdivision = false }
""".strip()
                + "\n",
            },
        )
        pages_json = json.dumps(
            [
                {
                    "source": "admin",
                    "path": ["admin"],
                    "include": {
                        "infosection": True,
                        "major_subdivision": True,
                        "minor_subdivision": True,
                    },
                },
                {
                    "source": "cities",
                    "path": ["drenthe"],
                    "parent_level": 3,
                    "include": {
                        "cities": True,
                        "infosection": False,
                        "major_subdivision": False,
                    },
                },
            ]
        )

        response = self.client.post(
            "/configs/netherlands/",
            data={
                "editor_mode": "manual",
                "manual_country_code": "netherlands",
                "manual_legal_subdivision": "2",
                "pages_json": pages_json,
                "page_source": ["admin", "cities"],
                "page_enabled": ["true", "false"],
                "page_force_highest_level": ["", "3"],
                "page_parent_level": ["", "2"],
                "page_include_infosection": ["true", "true"],
                "page_include_major_subdivision": ["true", "false"],
                "page_include_minor_subdivision": ["true", "false"],
                "page_include_cities": ["false", "true"],
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        content = ScrapingConfig.objects.get(slug="netherlands").content
        self.assertIn('path = ["drenthe"]', content)
        self.assertIn("enabled = false", content)
        self.assertIn("force_highest_level = 3", content)
        self.assertIn("parent_level = 2", content)
        self.assertIn("include = { cities = true, infosection = true, major_subdivision = false }", content)

    def test_config_edit_keeps_citypopulation_config_as_main_editor(self):
        upsert_scraping_config(
            "spain",
            """
scrape_schema_version = 2

[[pages]]
source = "admin"
path = ["admin"]
""".strip() + "\n",
        )

        response = self.client.get("/configs/spain/")

        self.assertEqual(response.status_code, 200)
        front = ScrapingConfig.objects.get(slug="spain")
        self.assertIn("[[pages]]", front.content)
        self.assertIn('source = "admin"', front.content)
        self.assertFalse(ScrapingConfig.objects.filter(slug="old-spain").exists())
        self.assertContains(response, "admin")

    def test_config_edit_renders_dynamic_cities_subsection(self):
        upsert_scraping_config(
            "aacity",
            """
scrape_schema_version = 2
country_code = "aa"

[[pages]]
source = "admin"
path = ["admin"]
""".strip()
            + "\n",
        )

        response = self.client.get("/configs/aacity/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("data-config-cities-section", html)
        self.assertNotIn('id="config-cities-table"', html)
        self.assertIn("data-config-city-builder", html)
        self.assertIn("data-open-config-city-picker", html)
        self.assertIn("data-config-city-parent-select", html)
        self.assertIn("data-config-city-table", html)
        self.assertIn("data-config-cities-json", html)
        self.assertIn("data-config-city-modal", html)
        self.assertIn("data-save-config-city-modal", html)
        self.assertIn("data-close-config-city-modal", html)
        self.assertIn("Nombre de la ciudad", html)
        self.assertIn("Sin nombre", html)
        self.assertIn("Nueva ciudad", html)
        self.assertIn("Editar ciudad", html)
        self.assertIn("Buscar por nombre", html)
        self.assertIn("Todos los tipos", html)
        self.assertIn("Sin tipo", html)
        self.assertIn('/configs/aacity/cities/import-toml/', html)
        self.assertIn('/configs/aacity/cities/export-toml/', html)
        self.assertLess(html.index("data-config-city-modal"), html.index("data-config-city-table"))
        parent_select = html[html.index("data-config-city-parent-select") : html.index("</select>", html.index("data-config-city-parent-select"))]
        self.assertNotIn("data-select2", parent_select)
        self.assertNotIn("data-client-page-select", html)
        self.assertNotIn("data-add-config-city-parent", html)
        self.assertIn("data-generated-config-form hidden", html)
        self.assertIn('class="section-title asset-corrections-title" hidden', html)

    def test_config_editor_data_does_not_send_legacy_city_rows(self):
        upsert_scraping_config(
            "aacity",
            """
scrape_schema_version = 2
country_code = "aa"

[[pages]]
source = "admin"
path = ["admin"]
""".strip()
            + "\n",
        )

        response = self.client.get("/configs/aacity/editor-data/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manual"]["config_cities"], [])

        AdminArea.objects.create(id="aa_root", country_code="aa", code="aa", name="Aaland", level=0)
        AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="1",
            name="Imported Region",
            level=1,
            entity_type="Region",
        )
        AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="11",
            name="Aaland City",
            level=2,
            entity_type="Municipality",
            pop_latest=321,
        )

        response = self.client.get("/configs/aacity/editor-data/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manual"]["config_cities"], [])

    def test_city_merge_toml_export_import_replaces_only_configured_cities(self):
        record = upsert_scraping_config(
            "aacity",
            """
LEGAL_SUBDIVISION = 3
scrape_schema_version = 2
country_code = "aa"

[[pages]]
source = "admin"
path = ["admin"]

[[cities]]
city = "Old City"
id = "OLD"
level = 3
type = "City"
from = { 2 = ["Old Province"] }
communes = ["OLD1"]
keep_communes = false
""".strip()
            + "\n",
        )

        with TemporaryDirectory() as tmpdir:
            exported = export_city_merges_to_toml(force=True, slugs=["aacity"], output_dir=tmpdir)
            path = Path(tmpdir) / "aacity.toml"

            self.assertEqual(exported, 1)
            content = path.read_text(encoding="utf-8")
            self.assertIn("[[cities]]", content)
            self.assertIn('city = "Old City"', content)
            self.assertNotIn("[[pages]]", content)

            path.write_text(
                """
[[cities]]
city = "New City"
id = "NEW"
level = 3
type = "City"
parent_type = "Prefecture"
from = { 2 = ["New Province"] }
communes = ["NEW1", "NEW2"]
keep_communes = false
""".strip()
                + "\n",
                encoding="utf-8",
            )
            imported = sync_city_merges_from_toml(force=True, slugs=["aacity"], input_dir=tmpdir)

        self.assertEqual(imported, 1)
        record.refresh_from_db()
        self.assertIn("[[pages]]", record.content)
        self.assertIn('source = "admin"', record.content)
        self.assertIn('city = "New City"', record.content)
        self.assertIn('parent_type = "Prefecture"', record.content)
        self.assertIn('communes = ["NEW1", "NEW2"]', record.content)
        self.assertNotIn('city = "Old City"', record.content)

    def test_config_editor_data_builds_city_creator_from_legal_parent_level(self):
        upsert_scraping_config(
            "aacity",
            """
LEGAL_SUBDIVISION = 3
scrape_schema_version = 2
country_code = "aa"

[[pages]]
source = "admin"
path = ["admin"]

[[cities]]
city = "Tanger"
id = "TNG"
level = 3
type = "City"
from = { 2 = ["Tanger-Assilah"] }
communes = ["C1"]
keep_communes = false
""".strip()
            + "\n",
        )
        root = AdminArea.objects.create(id="aa_root", country_code="aa", code="aa", name="Aaland", level=0)
        region = AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="R1",
            name="North",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="aa_parent",
            country_code="aa",
            code="TNG",
            name="Tanger-Assilah",
            level=2,
            entity_type="Province",
            parent=region,
        )
        AdminArea.objects.create(
            id="aa_commune_1",
            country_code="aa",
            code="C1",
            name="Tanger",
            level=3,
            entity_type="Commune",
            parent=province,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="aa_commune_2",
            country_code="aa",
            code="C2",
            name="Assilah",
            level=3,
            entity_type="Commune",
            parent=province,
            pop_latest=50,
        )

        response = self.client.get("/configs/aacity/editor-data/")

        self.assertEqual(response.status_code, 200)
        builder = response.json()["manual"]["city_builder"]
        self.assertTrue(builder["enabled"])
        self.assertEqual(builder["legal_level"], 3)
        self.assertEqual(builder["parent_level"], 2)
        self.assertEqual(builder["parent_options"][0]["id"], "aa_parent")
        self.assertIn("Tanger-Assilah", builder["parent_options"][0]["label"])
        self.assertEqual([row["code"] for row in builder["children_by_parent"]["aa_parent"]], ["C2", "C1"])
        self.assertEqual(builder["sections"][0]["parent_id"], "aa_parent")
        self.assertEqual(builder["sections"][0]["selected_ids"], ["aa_commune_1"])

    def test_manual_config_save_renders_configured_cities_from_city_builder_json(self):
        post = QueryDict("", mutable=True)
        post.update(
            {
                "manual_country_code": "aa",
                "manual_legal_subdivision": "3",
                "config_cities_loaded": "1",
                "pages_json": json.dumps(
                    [
                        {
                            "source": "admin",
                            "path": ["admin"],
                            "include": {
                                "infosection": True,
                                "major_subdivision": True,
                                "minor_subdivision": True,
                            },
                        }
                    ]
                ),
                "config_cities_json": json.dumps(
                    [
                        {
                            "parent_id": "aa_parent",
                            "parent_name": "Tanger-Assilah",
                            "parent_label": "Tanger-Assilah (Province)",
                            "parent_code": "TNG",
                            "parent_level": "2",
                            "city": "Tanger",
                            "code": "TNG",
                            "level": "3",
                            "type": "City",
                            "selected_ids": ["aa_commune_1", "aa_commune_2"],
                            "communes": ["C1", "C2"],
                            "keep_communes": False,
                        }
                    ]
                ),
            }
        )

        content = _render_config_from_manual_post(
            "aacity",
            post,
            current_content="""
[[cities]]
city = "Old"
id = "OLD"
level = 3
type = "City"
from = { 2 = ["Old"] }
""",
        )

        self.assertIn("[[cities]]", content)
        self.assertIn('city = "Tanger"', content)
        self.assertIn('id = "TNG"', content)
        self.assertIn('from = { 2 = ["Tanger-Assilah"] }', content)
        self.assertIn('communes = ["C1", "C2"]', content)
        self.assertIn("keep_communes = false", content)
        self.assertNotIn('city = "Old"', content)

    def test_manual_config_save_rejects_empty_unified_city_name(self):
        post = QueryDict("", mutable=True)
        post.update(
            {
                "manual_country_code": "aa",
                "manual_legal_subdivision": "3",
                "config_cities_loaded": "1",
                "pages_json": json.dumps(
                    [
                        {
                            "source": "admin",
                            "path": ["admin"],
                            "include": {
                                "infosection": True,
                                "major_subdivision": True,
                                "minor_subdivision": True,
                            },
                        }
                    ]
                ),
                "config_cities_json": json.dumps(
                    [
                        {
                            "parent_name": "Tanger-Assilah",
                            "parent_code": "TNG",
                            "parent_level": "2",
                            "city": "",
                            "code": "TNG",
                            "level": "3",
                            "type": "City",
                            "communes": ["C1"],
                        }
                    ]
                ),
            }
        )

        with self.assertRaisesMessage(ValueError, "El nombre de la ciudad unificada no puede estar vacío."):
            _render_config_from_manual_post("aacity", post, current_content="")

    def test_manual_config_save_preserves_configured_cities_until_city_builder_loads(self):
        post = QueryDict("", mutable=True)
        post.update(
            {
                "manual_country_code": "aa",
                "manual_legal_subdivision": "3",
                "pages_json": json.dumps(
                    [
                        {
                            "source": "admin",
                            "path": ["admin"],
                            "include": {
                                "infosection": True,
                                "major_subdivision": True,
                                "minor_subdivision": True,
                            },
                        }
                    ]
                ),
                "config_cities_json": "[]",
            }
        )

        content = _render_config_from_manual_post(
            "aacity",
            post,
            current_content="""
[[cities]]
city = "Existing"
id = "EX"
level = 3
type = "City"
from = { 2 = ["Existing Province"] }
communes = ["EX1"]
""",
        )

        self.assertIn('city = "Existing"', content)
        self.assertIn('communes = ["EX1"]', content)

    def test_recipe_form_renders_importable_python_with_numeric_dat_keys(self):
        content = _render_recipe_from_form(
            {
                "slug": "testland",
                "root_name": "Testland",
                "source_country": "spain",
                "municipal_level": "3",
                "representation_level": "2",
                "representation_total": "100",
                "representation_min": "1",
                "divisions_json": """
[
  {
    "name": "Provincia",
    "code": "PRO",
    "dat": {"2": ["Madrid"]}
  }
]
""",
            }
        )

        _validate_recipe_text(content, filename="testland.py")
        tree = ast.parse(content)
        divisions_node = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "DIVISIONS" for target in node.targets)
        )
        divisions = ast.literal_eval(divisions_node)
        self.assertEqual(divisions[0]["dat"], {2: ["Madrid"]})

    def test_web_translation_catalogs_are_loaded(self):
        with translation.override("en"):
            self.assertEqual(translation.gettext("Panel"), "Dashboard")
        with translation.override("fr"):
            self.assertEqual(translation.gettext("Panel"), "Tableau de bord")
        with translation.override("de"):
            self.assertEqual(translation.gettext("Panel"), "Übersicht")
        with translation.override("ru"):
            self.assertEqual(translation.gettext("Panel"), "Панель")
            self.assertEqual(translation.gettext("Buscar pais"), "\u041d\u0430\u0439\u0442\u0438 \u0441\u0442\u0440\u0430\u043d\u0443")

    def test_user_facing_text_sources_are_not_mojibake(self):
        repo_root = Path(__file__).resolve().parents[2]
        expected_by_path = {
            "ciudades_del_mundo/templates/ciudades_del_mundo/config_list.html": [
                "Configuraciones pa\u00edses",
                "Nueva configuraci\u00f3n",
                "En ejecuci\u00f3n",
                "Pa\u00eds o c\u00f3digo",
            ],
            "ciudades_del_mundo/management/commands/ensure_visual_assets.py": [
                "pa\u00edses",
                "configuraci\u00f3n",
                "L\u00edmite",
                "im\u00e1genes",
                "p\u00e1ginas",
            ],
            "ciudades_del_mundo/services/visual_assets.py": [
                "respuesta vac\u00eda al descargar la imagen",
            ],
            "locale/ru/LC_MESSAGES/django.po": [
                "\u041d\u0430\u0439\u0442\u0438 \u0441\u0442\u0440\u0430\u043d\u0443",
                "\u0421\u0442\u0440\u043e\u043a \u043d\u0430 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0435",
                "\u0421\u0442\u0438\u043b\u044c",
                "\u0421\u0432\u0435\u0442\u043b\u044b\u0439",
                "\u0422\u0451\u043c\u043d\u044b\u0439",
                "\u0420\u0435\u0442\u0440\u043e 80",
                "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435",
            ],
        }
        mojibake_markers = ("\u00c3", "\u00c2", "\ufffd")

        for relative_path, expected_strings in expected_by_path.items():
            with self.subTest(path=relative_path):
                text = (repo_root / relative_path).read_text(encoding="utf-8-sig")
                self.assertFalse(any(marker in text for marker in mojibake_markers))
                for expected in expected_strings:
                    self.assertIn(expected, text)

    def test_task_manager_reads_existing_db_task_history(self):
        with TemporaryDirectory() as tmpdir:
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            log_path = Path(tmpdir) / "task-1.log"
            task = ManagedTask.objects.create(
                id="task-1",
                key="validate:test",
                label="Validar test",
                args=["validate_subdivision_configs", "test"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=timezone.now(),
                started_at=timezone.now(),
                finished_at=timezone.now(),
                log_path=str(log_path),
            )
            with manager._lock:
                manager._append_output_locked(task, "ok\n")
                task.save(update_fields=["output", "log_path", "updated_at"])

            reloaded = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = reloaded.get("task-1")

        self.assertIsNotNone(task)
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(task.output_text, "ok\n")
        self.assertEqual(reloaded.output_text(task), "ok\n")
        self.assertEqual(reloaded.latest_for_key("validate:test").id, "task-1")

    def test_task_manager_reads_full_log_file_after_output_tail_is_trimmed(self):
        with TemporaryDirectory() as tmpdir:
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = ManagedTask.objects.create(
                id="task-log",
                key="validate:log",
                label="Validar log",
                args=["validate_subdivision_configs", "log"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=timezone.now(),
                started_at=timezone.now(),
                finished_at=timezone.now(),
                log_path=str(Path(tmpdir) / "task-log.log"),
            )
            with manager._lock:
                for index in range(260):
                    manager._append_output_locked(task, f"line {index}\n")
                task.save(update_fields=["output", "log_path", "updated_at"])

            reloaded = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = reloaded.get("task-log")
            full_output = reloaded.output_text(task)

        self.assertIsNotNone(task)
        self.assertLess(len(task.output), 260)
        self.assertIn("line 0\n", full_output)
        self.assertIn("line 259\n", full_output)

    def test_task_manager_groups_scrape_logs_by_country(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True

            task = manager.start(
                key="scrape:algeria",
                label="Popular Argelia",
                args=["scrape_subdivisions_with_assets", "algeria"],
            )

        self.assertEqual(Path(task.log_path).parts, (".web_task_logs", "algeria", f"{task.id}.log"))

    def test_cleanup_old_logs_removes_files_older_than_retention(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_log = root / ".web_task_logs" / "algeria" / "old.log"
            fresh_log = root / ".web_task_logs" / "algeria" / "fresh.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old", encoding="utf-8")
            fresh_log.write_text("fresh", encoding="utf-8")
            old_timestamp = time.time() - 91 * 24 * 60 * 60
            os.utime(old_log, (old_timestamp, old_timestamp))

            removed = cleanup_old_logs(base_dir=root, days=90)

            self.assertEqual(removed, 1)
            self.assertFalse(old_log.exists())
            self.assertTrue(fresh_log.exists())


    def test_task_manager_queues_tasks_past_running_limit(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True
            tasks = [
                manager.start(
                    key=f"validate:test-{index}",
                    label=f"Validar test {index}",
                    args=["validate_subdivision_configs", f"test-{index}"],
                )
                for index in range(5)
            ]

            statuses = list(
                ManagedTask.objects.filter(key__startswith="validate:test-")
                .order_by("created_at", "id")
                .values_list("status", flat=True)
            )
            self.assertEqual(statuses, ["running", "queued", "queued", "queued", "queued"])
            self.assertEqual(manager.started_workers, [tasks[0].id])

            first = ManagedTask.objects.get(id=tasks[0].id)
            first.status = ManagedTask.Status.SUCCEEDED
            first.finished_at = timezone.now()
            first.save(update_fields=["status", "finished_at", "updated_at"])
            with manager._lock:
                task_ids = manager._dispatch_queued_locked()
            manager._start_workers(task_ids)

            tasks[1].refresh_from_db()
            self.assertEqual(tasks[1].status, "running")
            self.assertEqual(manager.started_workers, [tasks[0].id, tasks[1].id])

    def test_task_manager_cancels_queued_task_without_starting_worker(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True
            first = manager.start(
                key="validate:first",
                label="Validar primero",
                args=["validate_subdivision_configs"],
            )
            second = manager.start(
                key="validate:second",
                label="Validar segundo",
                args=["validate_subdivision_configs"],
            )

            manager.cancel(second.id)
            first.refresh_from_db()
            second.refresh_from_db()

        self.assertEqual(first.status, "running")
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(manager.started_workers, [first.id])

    def test_can_clear_requires_rows_and_no_active_operation(self):
        for status in ["pending", "validated", "populated", "failed"]:
            with self.subTest(status=status):
                self.assertTrue(_can_clear_config_row({"rows": 3}, status))

        for status in ["validating", "populating", "clearing", "running", "queued"]:
            with self.subTest(status=status):
                self.assertFalse(_can_clear_config_row({"rows": 3}, status))

        self.assertFalse(_can_clear_config_row({"rows": 0}, "populated"))

    def test_can_scrape_matches_config_lifecycle(self):
        for status in ["pending", "failed", "validated"]:
            with self.subTest(status=status):
                self.assertTrue(_can_scrape_config_status(status))

        for status in ["populated", "validating", "populating", "clearing", "running", "queued"]:
            with self.subTest(status=status):
                self.assertFalse(_can_scrape_config_status(status))


class TaskManagerDatabaseTests(TestCase):
    def test_task_status_maps_finished_scrape_progress_to_populated(self):
        slug = "zzstatusdone"
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-done-status",
                key=f"scrape:{slug}",
                label="Popular status",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            response = self.client.get(f"/tasks/{task.id}/status/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["config_progress"][slug]["status"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_task_status_synthesizes_finished_scrape_progress_when_file_is_missing(self):
        slug = "zzstatusmissing"
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-done-missing",
                key=f"scrape:{slug}",
                label="Popular status missing",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            progress_path = task_progress_path(task.id)
            if progress_path.exists():
                progress_path.unlink()

            response = self.client.get(f"/tasks/{task.id}/status/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["config_progress"][slug]["status"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_bulk_config_summary_maps_finished_scrape_progress_to_populated(self):
        slug = "zzbulkdone"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Bulk Done Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-all-done-status",
                key="scrape:all",
                label="Popular todas",
                args=["validate_and_scrape_configs"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            bulk_task, status = _latest_bulk_config_progress(slug)
            response = self.client.get(f"/configs/{slug}/summary/")

            self.assertEqual(bulk_task, task)
            self.assertEqual(status, "populated")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["task_status"], "populated")
            self.assertEqual(response.json()["status_filter"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_output_since_text_reads_incremental_log_from_offset(self):
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "task.log"
            log_path.write_text("line 0\nline 1\n", encoding="utf-8", newline="")
            task = ManagedTask.objects.create(
                id="task-incremental-log",
                key="validate:incremental-log",
                label="Validar log incremental",
                args=["validate_subdivision_configs", "log"],
                status=ManagedTask.Status.RUNNING,
                created_at=timezone.now(),
                started_at=timezone.now(),
                log_path=str(log_path),
            )
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            initial_offset = manager.output_offset(task)

            with log_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write("line 2\n")

            delta, next_offset, reset = manager.output_since_text(task, initial_offset)

        self.assertFalse(reset)
        self.assertEqual(delta, "line 2\n")
        self.assertGreater(next_offset, initial_offset)


class ConfigSourceEntitiesViewTests(TestCase):
    def test_populated_config_only_shows_clear_when_rows_exist(self):
        slug = "zztestpopulatedonlyclear"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populated Only Clear Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Populatedland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-finished",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertFalse(_can_scrape_config_status("populated"))
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            clear_form = re.search(r'<form[^>]*data-config-action-form="clear"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertIsNotNone(clear_form)
            self.assertIn("hidden", scrape_form.group(0))
            self.assertNotIn("hidden", clear_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_disables_validate_button_while_validation_is_active(self):
        slug = "zztestvalidate"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validate Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-active",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/validate/", html)
            self.assertIn("config-status-validating config-status-loading", html)
            self.assertIn('class="config-status-dots" aria-hidden="true"', html)
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_links_failed_validation_to_task_log(self):
        slug = "zztestvalidatefailed"
        task_id = "validate-failed-log-link"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validate Failed Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id=task_id,
                key=f"validate-config:{slug}",
                label="Validar test fallido",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.FAILED,
                returncode=1,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f'href="/tasks/{task_id}/"', html)
            self.assertIn("config-status-failed", html)
            self.assertIn(">Fallo</a>", html)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_uses_loading_badge_for_active_population(self):
        slug = "zztestpopulateactive"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populate Active Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/scrape/", html)
            self.assertIn("config-status-populating config-status-loading", html)
            self.assertIn('<span class="config-status-label">Populando</span>', html)
            self.assertIn('class="config-status-dots" aria-hidden="true"', html)
            self.assertNotIn("\u2026Populando", html)
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_populating_after_prevalidation_hides_popular_button(self):
        slug = "zztestpopulateafterprevalidation"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populate After Prevalidation Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-after-prevalidation-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["validate_and_scrape_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn("config-status-populating config-status-loading", html)
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "clear"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_shows_stop_button_while_population_is_active(self):
        slug = "zzteststopbutton"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Stop Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-stop-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/stop/", html)
            self.assertIn('data-config-action-form="stop"', html)
            self.assertIn('data-config-action="stop"', html)
            self.assertIn(">Parar<", html)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_stop_config_task_returns_to_validated_state(self):
        slug = "zzteststopstate"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Stop State Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-before-stop",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            task = ManagedTask.objects.create(
                id="scrape-stop-state",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.post(
                f"/configs/{slug}/task/stop/",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "validated")
            task.refresh_from_db()
            self.assertEqual(task.status, ManagedTask.Status.CANCELLED)

            summary = self.client.get(f"/configs/{slug}/summary/").json()
            self.assertEqual(summary["task_status"], "validated")
            self.assertEqual(summary["status_filter"], "validated")
            self.assertTrue(summary["can_scrape"])
            self.assertFalse(summary["can_resume"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_cancelled_scrape_row_returns_to_validated_without_resume_button(self):
        slug = "zztestcancelledscrapevalidated"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Cancelled Scrape Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-cancelled",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.CANCELLED,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn("config-status-validated", html)
            self.assertNotIn(f"/configs/{slug}/task/resume/", html)
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertNotIn("hidden", scrape_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_pending_popular_action_validates_before_scraping(self):
        slug = "zztestpendingpopularvalidates"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Pending Popular Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="pending-popular-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"scrape:{slug}")
            self.assertEqual(kwargs["args"], ["validate_and_scrape_configs", slug, "--no-download-assets", "--page-workers=4"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_popular_missing_config_imports_matching_toml_seed_before_scraping(self):
        slug = "zztestmissingpopularseed"
        original_recovery_done = task_manager._recovery_done

        def sync_seed(**kwargs):
            upsert_scraping_config(
                slug,
                """
name = "Missing Popular Seed"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
                source_path=f"ciudades_del_mundo/subdivisions/{slug}.toml",
            )
            return 1

        try:
            task_manager._recovery_done = True
            with (
                patch(
                    "ciudades_del_mundo.web.views.bundled_toml_config_paths",
                    return_value=[Path(f"ciudades_del_mundo/subdivisions/{slug}.toml")],
                ) as paths,
                patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
                patch("ciudades_del_mundo.web.views.sync_scraping_configs_from_toml", side_effect=sync_seed) as sync,
                patch("ciudades_del_mundo.web.views.task_manager.start") as start,
            ):
                start.return_value = _Object(
                    id="missing-popular-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            paths.assert_called_once_with([slug])
            sync.assert_called_once_with(force=False, only_if_empty=False, slugs=[slug])
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"scrape:{slug}")
            self.assertEqual(kwargs["args"], ["validate_and_scrape_configs", slug, "--no-download-assets", "--page-workers=4"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_import_toml_starts_background_sync_task(self):
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="import-toml-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    "/configs/import-toml/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], "config-import:toml")
            self.assertEqual(kwargs["args"], ["sync_scraping_configs", "--force"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_single_config_import_toml_reads_only_matching_seed(self):
        slug = "zzimportone"
        with (
            patch("ciudades_del_mundo.web.views.bundled_toml_config_paths", return_value=[Path(f"subdivisions/{slug}.toml")]) as paths,
            patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
            patch("ciudades_del_mundo.web.views.sync_scraping_configs_from_toml", return_value=1) as sync,
        ):
            response = self.client.post(
                f"/configs/{slug}/import-toml/",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        paths.assert_called_once_with([slug])
        sync.assert_called_once_with(force=True, only_if_empty=False, slugs=[slug])
        self.assertTrue(response.json()["imported"])

    def test_bulk_popular_all_uses_country_workers(self):
        original_recovery_done = task_manager._recovery_done
        for slug in ("zztestbulkworkersa", "zztestbulkworkersb"):
            upsert_scraping_config(
                slug,
                """
name = "Bulk Workers Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
            )
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="bulk-workers-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    "/configs/all/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], "scrape:all")
            self.assertEqual(kwargs["args"][-3:], ["--no-download-assets", "--page-workers=4", "--country-workers=1"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_bulk_popular_bootstraps_toml_seeds_when_config_table_is_empty(self):
        slug = "zztestbulkbootstrapseed"
        original_recovery_done = task_manager._recovery_done
        ScrapingConfig.objects.all().delete()

        def bootstrap_seed(*, force=False):
            self.assertFalse(force)
            upsert_scraping_config(
                slug,
                """
name = "Bulk Bootstrap Seed"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
                source_path=f"ciudades_del_mundo/subdivisions/{slug}.toml",
            )
            return _Object(table_ready=True, expected_count=1, stored_count=1, missing_count=0, imported_count=1)

        try:
            task_manager._recovery_done = True
            with (
                patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
                patch("ciudades_del_mundo.web.views.ensure_initial_scraping_configs", side_effect=bootstrap_seed) as bootstrap,
                patch("ciudades_del_mundo.web.views.task_manager.start") as start,
            ):
                start.return_value = _Object(
                    id="bulk-bootstrap-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    "/configs/all/task/scrape-unpopulated/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            bootstrap.assert_called_once_with(force=False)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], "scrape:unpopulated")
            self.assertIn(slug, kwargs["args"])
            self.assertEqual(kwargs["args"][0], "validate_and_scrape_configs")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_validated_popular_action_scrapes_directly(self):
        slug = "zztestvalidatedpopulardirect"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validated Popular Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-direct-popular",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="validated-popular-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"scrape:{slug}")
            self.assertEqual(kwargs["args"], ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"])
        finally:
            task_manager._recovery_done = original_recovery_done


    def test_validate_command_clears_previous_config_error_on_success(self):
        slug = "zztestvalidateclearsfailure"
        upsert_scraping_config(
            slug,
            """
name = "Validate Clears Failure"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        ScrapingConfig.objects.filter(slug=slug).update(is_valid=False, validation_error="old failure")

        call_command("validate_subdivision_configs", slug, stdout=StringIO())

        record = ScrapingConfig.objects.get(slug=slug)
        self.assertTrue(record.is_valid)
        self.assertEqual(record.validation_error, "")
        summary = self.client.get(f"/configs/{slug}/summary/").json()
        self.assertEqual(summary["status_filter"], "validated")
        self.assertTrue(summary["can_scrape"])

    def test_config_table_shows_clear_button_when_rows_exist_and_no_operation_is_active(self):
        slug = "zztestclearbutton"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clearland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertIn('data-config-action-form="clear"', html)
            self.assertIn('data-config-action="clear"', html)
            self.assertNotRegex(html, r'data-config-action-form="clear"[^>]*hidden')
            self.assertIn(">Limpiar<", html)
            self.assertNotIn("status-icon", html)

            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-clear-button-active",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            response = self.client.get(f"/configs/table/?q={slug}")
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertRegex(html, r'data-config-action-form="clear"[^>]*hidden')
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_cancelled_clear_returns_to_previous_available_actions(self):
        slug = "zztestcancelledclearnotonly"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Cancelled Clear Not Only Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Cancelledland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="clear-cancelled-not-only",
                key=f"clear-config:{slug}",
                label="Limpiar test",
                args=["clear_config_data", slug],
                status=ManagedTask.Status.CANCELLED,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            clear_form = re.search(r'<form[^>]*data-config-action-form="clear"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertIsNotNone(clear_form)
            self.assertNotIn("hidden", scrape_form.group(0))
            self.assertNotIn("hidden", clear_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_hides_clear_button_while_clear_is_active(self):
        slug = "zztestclearbuttonactiveclear"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Active Clear Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Active Clearland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="clear-button-active-clear",
                key=f"clear-config:{slug}",
                label="Limpiar test",
                args=["clear_config_data", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertRegex(html, r'data-config-action-form="clear"[^>]*hidden')
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_starts_clear_command(self):
        slug = "zztestclearaction"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Action Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Action Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-action-populated",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="clear-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"clear-config:{slug}")
            self.assertEqual(kwargs["args"], ["clear_config_data_with_assets", slug])
            self.assertEqual(response.json()["label"], f"Limpiar: {slug}")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_rejects_active_operation_even_with_rows(self):
        slug = "zztestclearactive"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Active Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Active Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-active",
                key=f"scrape:{slug}",
                label="Popular active",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 400)
            start.assert_not_called()
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_action_passes_country_code_to_command(self):
        slug = "zztestclearcountryaction"
        country_code = "zzrealclearcountry"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            f"""
name = "Clear Action CountryCode Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Action CountryCode Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-country-populated",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="clear-country-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"clear-config:{slug}")
            self.assertEqual(kwargs["args"], ["clear_config_data_with_assets", country_code])
            self.assertEqual(response.json()["label"], f"Limpiar: {slug}")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_data_command_deletes_rows_and_returns_to_pending(self):
        slug = "zztestclearcommand"
        original_recovery_done = task_manager._recovery_done
        record = upsert_scraping_config(
            slug,
            """
name = "Clear Command Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        record.is_valid = False
        record.validation_error = "previous validation failure"
        record.save(update_fields=["is_valid", "validation_error"])
        root = AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Command Land",
            level=0,
        )
        AdminArea.objects.create(
            id=f"{slug}_child",
            country_code=slug,
            code="child",
            name="Child",
            level=1,
            parent=root,
        )
        try:
            task_manager._recovery_done = True
            call_command("clear_config_data", slug)

            self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 0)
            record.refresh_from_db()
            self.assertFalse(record.is_valid)
            self.assertEqual(record.validation_error, "")

            summary = self.client.get(f"/configs/{slug}/summary/").json()
            self.assertEqual(summary["rows"], 0)
            self.assertEqual(summary["task_status"], "pending")
            self.assertEqual(summary["status_filter"], "pending")
            self.assertTrue(summary["can_validate"])
            self.assertFalse(summary["can_clear"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_data_command_deletes_visual_assets_and_translations(self):
        slug = "zztestclearassetslug"
        country_code = "zzrealclearassetcountry"
        upsert_scraping_config(
            slug,
            f"""
name = "Clear Assets Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Assets Land",
            level=0,
        )
        child = AdminArea.objects.create(
            id=f"{country_code}_child",
            country_code=country_code,
            code="child",
            name="Child",
            level=1,
            parent=root,
        )
        now = timezone.now()
        country_asset = VisualAsset.objects.create(
            entity_type="country",
            entity_key=slug,
            entity_name="Clear Assets Land",
            country_code="",
            kind="flag",
            status="found",
            created_at=now,
            updated_at=now,
        )
        admin_asset = VisualAsset.objects.create(
            entity_type="admin_area",
            entity_key=child.id,
            entity_name="Child",
            country_code="",
            kind="coat",
            status="found",
            created_at=now,
            updated_at=now,
        )
        unrelated_asset = VisualAsset.objects.create(
            entity_type="country",
            entity_key="otherland",
            entity_name="Otherland",
            country_code="otherland",
            kind="flag",
            status="found",
            created_at=now,
            updated_at=now,
        )
        VisualAssetTranslation.objects.create(
            asset=country_asset,
            language="es",
            title="Bandera",
            created_at=now,
            updated_at=now,
        )
        VisualAssetTranslation.objects.create(
            asset=admin_asset,
            language="es",
            title="Escudo",
            created_at=now,
            updated_at=now,
        )

        call_command("clear_config_data", country_code)

        self.assertFalse(VisualAsset.objects.filter(id__in=[country_asset.id, admin_asset.id]).exists())
        self.assertEqual(VisualAssetTranslation.objects.count(), 0)
        self.assertTrue(VisualAsset.objects.filter(id=unrelated_asset.id).exists())

    def test_clear_config_data_command_deletes_local_visual_asset_files(self):
        slug = "zztestclearmedia"
        upsert_scraping_config(
            slug,
            """
name = "Clear Media Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Media Land",
            level=0,
        )
        with TemporaryDirectory() as tmpdir, self.settings(MEDIA_ROOT=Path(tmpdir)):
            local_path = Path("visual_assets") / "flag" / slug / f"{slug}_flag.svg"
            absolute_path = Path(tmpdir) / local_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_text("<svg></svg>", encoding="utf-8")
            now = timezone.now()
            VisualAsset.objects.create(
                entity_type="country",
                entity_key=slug,
                entity_name="Clear Media Land",
                country_code=slug,
                kind="flag",
                status="downloaded",
                local_path=str(local_path),
                local_exists=True,
                created_at=now,
                updated_at=now,
            )

            call_command("clear_config_data", slug)

            self.assertFalse(absolute_path.exists())
            self.assertEqual(VisualAsset.objects.filter(country_code=slug).count(), 0)

    def test_clear_config_data_command_accepts_country_code_not_only_slug(self):
        slug = "zztestclearcountryslug"
        country_code = "zzrealclearcommand"
        record = upsert_scraping_config(
            slug,
            f"""
name = "Clear Command CountryCode Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Command CountryCode Land",
            level=0,
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Different Slug Land",
            level=0,
        )

        call_command("clear_config_data", country_code)

        self.assertEqual(AdminArea.objects.filter(country_code=country_code).count(), 0)
        self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 1)
        record.refresh_from_db()
        self.assertFalse(record.is_valid)
        self.assertEqual(record.validation_error, "")

    def test_clear_config_data_command_uses_small_delete_batches(self):
        slug = "zztestclearbatch"
        upsert_scraping_config(
            slug,
            """
name = "Clear Batch Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Batch Land",
            level=0,
        )
        parent = root
        created = []
        for index in range(1, 8):
            area = AdminArea.objects.create(
                id=f"{slug}_child_{index}",
                country_code=slug,
                code=f"child-{index}",
                name=f"Child {index}",
                level=min(index, 5),
                parent=parent,
            )
            created.append(area)
            parent = area

        root.capitals.add(created[0], created[1])
        created[2].most_populate_city = created[-1]
        created[2].save(update_fields=["most_populate_city"])
        derived = NuevoAdminArea.objects.create(
            id=f"{slug}-derived",
            country_code=slug,
            code="derived",
            name="Derived",
            level=0,
            most_populate_city=created[-1],
        )
        derived.capitals.add(created[0])
        derived.municipios_originales.add(created[1], created[2])

        stdout = StringIO()
        with patch(
            "ciudades_del_mundo.management.commands.clear_config_data.CLEAR_DELETE_BATCH_SIZE",
            2,
        ):
            call_command("clear_config_data", slug, stdout=stdout)

        self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 0)
        derived.refresh_from_db()
        self.assertIsNone(derived.most_populate_city_id)
        self.assertEqual(derived.capitals.count(), 0)
        self.assertEqual(derived.municipios_originales.count(), 0)
        output = stdout.getvalue()
        self.assertIn(f"Limpiando: country_code={slug} filas=8 lote=2", output)
        self.assertIn("Limpiando: lote=1", output)
        self.assertIn("borradas=", output)

    def test_bulk_scrape_unpopulated_excludes_already_populated_configs(self):
        validated_slug = "zztestvalidatedonly"
        populated_slug = "zztestpopulatedskip"
        original_recovery_done = task_manager._recovery_done
        for slug, name in (
            (validated_slug, "Validated Only"),
            (populated_slug, "Populated Skip"),
        ):
            upsert_scraping_config(
                slug,
                f"""
name = "{name}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
            )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-unpopulated-only",
                key=f"validate-config:{validated_slug}",
                label="Validar test",
                args=["validate_subdivision_configs", validated_slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            ManagedTask.objects.create(
                id="scrape-populated-skip",
                key=f"scrape:{populated_slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", populated_slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            eligible = _eligible_config_slugs_for_bulk("scrape-unpopulated")

            self.assertIn(validated_slug, eligible)
            self.assertNotIn(populated_slug, eligible)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_source_entities_use_configured_country_code_for_levels_and_parents(self):
        slug = "zztestalias"
        upsert_scraping_config(
            slug,
            """
name = "Alias"
country_code = "realcountry"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id="real_root",
            country_code="realcountry",
            code="root",
            name="Real Country",
            level=0,
        )
        parent = AdminArea.objects.create(
            id="real_parent",
            country_code="realcountry",
            code="parent",
            name="Parent",
            level=1,
            entity_type="Region",
            parent=root,
        )
        AdminArea.objects.create(
            id="real_child",
            country_code="realcountry",
            code="child",
            name="Child",
            level=2,
            entity_type="Municipality",
            parent=parent,
        )

        response = self.client.get(f"/configs/{slug}/source-entities/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["entities"]], ["real_parent", "real_child"])
        self.assertEqual([row["value"] for row in payload["levels"]], ["1", "2"])
        self.assertEqual([row["label"] for row in payload["levels"]], ["Region (1)", "Municipio (1)"])
        self.assertEqual([row["value"] for row in payload["entity_types"]], ["Municipio", "Region"])
        self.assertIn("real_parent", {row["value"] for row in payload["parents"]})
        self.assertEqual(payload["parents_by_level"]["1"], [])
        self.assertIn("real_parent", {row["value"] for row in payload["parents_by_level"]["2"]})

        form_response = self.client.get(f"/configs/{slug}/")
        self.assertEqual(form_response.status_code, 200)
        form_html = form_response.content.decode("utf-8")
        level_select = form_html.split('data-transfer-filter="level"', 1)[1].split("</select>", 1)[0]
        self.assertNotIn('<option value="">Todos</option>', level_select)
        self.assertIn('<option value="1">Region (1)</option>', form_html)
        self.assertIn('<option value="2">Municipio (1)</option>', form_html)
        self.assertIn('"id": "real_parent"', form_html)
