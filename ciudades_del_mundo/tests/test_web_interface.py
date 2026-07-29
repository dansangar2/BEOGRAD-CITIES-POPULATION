import ast
import json
import os
from decimal import Decimal
from io import StringIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

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
    _render_config_from_manual_post,
    _render_recipe_from_form,
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
        country = DerivedCountry.objects.get(slug="testland")
        self.assertEqual(country.source_country_code, "aa")

        response = self.client.post(
            "/new-countries/testland/configs/new/",
            {
                "slug": "republic",
                "name": "Republic",
                "source_country_code": "aa",
                "derived_country_code": "testland_republic",
                "is_active": "1",
                "content": 'kind = "derived_country_config"\n[selection]\ninclude_codes = []\n',
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="republic")
        self.assertEqual(config.derived_country_code, "testland_republic")
        self.assertIn('kind = "derived_country_config"', config.content)
        self.assertEqual(self.client.get("/new-countries/testland/").status_code, 200)
        self.assertEqual(self.client.get("/new-countries/testland/configs/republic/view/").status_code, 200)

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
            "/new-countries/testland/configs/new/",
            {
                "slug": "visual",
                "name": "Visual",
                "source_country_code": "aa",
                "derived_country_code": "visual_land",
                "is_active": "1",
                "selection_json": json.dumps({"selected_ids": [region.id, city.id]}),
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="visual")
        self.assertIn('source_country_code = "aa"', config.content)
        self.assertIn('include_ids = ["aa_1"]', config.content)
        self.assertIn('subtract_ids = ["aa_11"]', config.content)
        self.assertIn('operation = "add"', config.content)
        self.assertIn('operation = "subtract"', config.content)

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
                self.assertIn("Tipo (Nivel)", html)
                self.assertIn("Terreno", html)
                self.assertIn("Población", html)
                self.assertIn('data-level="1"', html)
                self.assertIn("Created Region", html)
                self.assertIn("Created Region Type (Nivel 1)", html)
                self.assertIn(">34.5</td>", html)
                self.assertIn(">456</td>", html)
                self.assertIn('data-row-href="/groups/subdivisions/aa/new_province/"', html)
                self.assertNotIn("Imported Region", html)
                self.assertNotIn("<th>TOML</th>", html)
                self.assertNotIn("<th>Municipios</th>", html)
                self.assertNotIn("<th>Nombre interno</th>", html)
                self.assertNotIn("<th>Nombre</th>", html)
                self.assertNotIn("<th>Slug</th>", html)
                self.assertIn('data-row-href="/groups/groups/aa/alta_cerdana/"', html)
                self.assertIn('action="/groups/aa/export-toml/"', html)
                self.assertIn('action="/subdivisions/import-toml/"', html)
                self.assertIn('action="/subdivisions/aa/export-toml/"', html)
                self.assertNotIn('action="/subdivisions/aa/build/"', html)
                self.assertIn('href="/groups/subdivisions/aa/new/"', html)
                self.assertIn('href="/groups/groups/aa/new/"', html)
                self.assertNotIn("<th>Acciones</th>", html)
                self.assertNotIn(">Editar<", html)
                self.assertIn("ALTA_CERDANA", html)
                self.assertIn('class="numeric group-municipality-count">2</td>', html)
                self.assertNotIn("PROVINCIA", html)
                self.assertNotIn("Pais fuente", html)

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
        self.assertLess(form_html.index("<th>Excluidos</th>"), form_html.index("<th>Nivel</th>"))
        self.assertLess(form_html.index("<th>Nivel</th>"), form_html.index("<th>Acciones</th>"))
        self.assertIn("<th>Incluidos</th>", form_html)
        self.assertIn("<th>Excluidos</th>", form_html)
        self.assertNotIn("<th>Restar</th>", form_html)
        self.assertIn("derived-source-included-col", form_html)
        self.assertIn("derived-source-excluded-col", form_html)
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
        self.assertIn("function loadDescendants", form_html)
        self.assertIn("function sourceItemModalLevelGroups", form_html)
        self.assertIn("function renderSourceItemModalLevelGroups", form_html)
        self.assertIn("function sourceItemModalFilterText", form_html)
        self.assertIn("function sourceItemModalFilteredItems", form_html)
        self.assertIn("function sourceItemModalLevelGroupTitle", form_html)
        self.assertIn("data-derived-source-modal-filter", form_html)
        self.assertIn("data-derived-source-modal-groups-only", form_html)
        self.assertIn("dataset.sourceKind", form_html)
        self.assertIn("groupsOnly", form_html)
        self.assertIn('sourceItemKind(item) === "group" ? "group" : "area"', form_html)
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
        self.assertIn("MURCIA", html)
        self.assertIn("Murcia", html)
        self.assertIn("Reino", html)
        self.assertIn('data-row-href="/groups/subdivisions/aa/murcia/"', html)
        self.assertIn('action="/subdivisions/aa/export-toml/"', html)
        self.assertIn('href="/groups/subdivisions/aa/new/"', html)
        self.assertIn("ALBACETE_A_CUENCA", html)
        self.assertIn('class="numeric">1</td>', html)

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

    def test_group_new_prefills_source_country_from_section_link(self):
        response = self.client.get("/groups/groups/aa/new/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('name="country_code" value="aa"', html)
        self.assertNotIn("AÃ±adir", html)
        self.assertIn("data-group-entry-form", html)
        self.assertIn('data-source-url="/groups/source-data/"', html)
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
        group = SubdivisionGroup.objects.get(slug="aa_alta_cerdana")
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
        match = re.search(r'<script id="group-blocks-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        blocks = json.loads(match.group(1))
        self.assertEqual(blocks[0]["names"], [])
        self.assertEqual(blocks[0]["sections"][0]["area_id"], province.id)
        selected = blocks[0]["sections"][0]["selected"]
        self.assertEqual({item["id"] for item in selected}, {villatoya.id, alborea.id})
        self.assertEqual({item["name"] for item in selected}, {"Villatoya", "Alborea"})

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
                        "'''",
                    ]
                ),
                encoding="utf-8",
            )

            with patch(
                "ciudades_del_mundo.services.derived_config_seeds.DERIVED_SUBDIVISION_SEED_ROOT",
                root,
            ):
                paths = bundled_derived_subdivision_paths(["spain"])
                records = import_derived_subdivision_path_records(paths[0], force=True)

        self.assertEqual([record.slug for record in records], ["spain_murcia"])
        record = records[0]
        self.assertEqual(record.internal_name, "MURCIA")
        self.assertEqual(record.name, "Murcia")
        self.assertEqual(record.entity_type, "Reino")
        self.assertIn("[[include]]", record.content)
        self.assertIn('flag_url = "https://example.test/murcia-flag.svg"', record.content)
        self.assertIn('coat_url = "https://example.test/murcia-coat.svg"', record.content)
        self.assertIn('names = ["Murcia", "Albacete"]', record.content)
        self.assertIn("[[subtract]]", record.content)
        self.assertIn('groups = ["ALBACETE_A_CUENCA"]', record.content)

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
