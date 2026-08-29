import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config


class DashboardViewTests(TestCase):
    def _insert_visual_asset(
        self,
        *,
        entity_type: str = "country",
        entity_key: str,
        kind: str,
        entity_name: str = "AA Country",
        country_code: str | None = None,
        local_path: str = "",
        local_exists: bool = False,
        remote_url: str = "",
        commons_filename: str = "",
        source: str = "test",
        status: str = "downloaded",
    ) -> int:
        now = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ciudades_del_mundo_visual_asset
                    (entity_type, entity_key, entity_name, country_code, kind,
                     wikidata_id, commons_filename, remote_url, local_path,
                     local_exists, source, status, error, license_name, author,
                     attribution, source_url, created_at, updated_at)
                VALUES
                    (%s, %s, %s, %s, %s, '', %s, %s, %s,
                     %s, %s, %s, '', '', '', '', '', %s, %s)
                """,
                [
                    entity_type,
                    entity_key,
                    entity_name,
                    country_code if country_code is not None else entity_key,
                    kind,
                    commons_filename,
                    remote_url,
                    local_path,
                    local_exists,
                    source,
                    status,
                    now,
                    now,
                ],
            )
            return int(cursor.lastrowid)

    def _insert_visual_asset_translation(
        self,
        *,
        asset_id: int,
        language: str = "es",
        title: str = "",
        description: str = "",
        blazon: str = "",
    ) -> None:
        now = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ciudades_del_mundo_visual_asset_translation
                    (asset_id, language, title, description, blazon, source, needs_review, created_at, updated_at)
                VALUES
                    (%s, %s, %s, %s, %s, 'test', 0, %s, %s)
                """,
                [asset_id, language, title, description, blazon, now, now],
            )

    def test_dashboard_metrics_show_countries_and_subdivisions_without_seat_card(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            pop_latest=300,
        )
        AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="city",
            name="City",
            level=1,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="bb_root",
            country_code="bb",
            code="root",
            name="BB Country",
            level=0,
            pop_latest=200,
        )
        NuevoAdminArea.objects.create(
            id="derived_a",
            country_code="derived_a",
            code="root",
            name="Derived_A",
            level=0,
            pop_latest=600,
        )
        NuevoAdminArea.objects.create(
            id="derived_a_child",
            country_code="derived_a",
            code="child",
            name="Child",
            level=1,
            pop_latest=100,
        )
        NuevoAdminArea.objects.create(
            id="derived_b",
            country_code="derived_b",
            code="root",
            name="Derived B",
            level=0,
            pop_latest=400,
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["admin_country_count"], 2)
        self.assertEqual(response.context["admin_area_count"], 3)
        self.assertEqual(response.context["nuevo_country_count"], 2)
        self.assertEqual(response.context["nuevo_area_count"], 3)
        self.assertEqual(response.context["dashboard_population_url"], "/api/countries/")
        self.assertEqual(response.context["dashboard_derived_url"], "/api/derived/")
        self.assertEqual(response.context["dashboard_country_detail_base_url"], "/api/countries/__country__/")
        self.assertContains(response, 'data-dashboard-population')
        self.assertContains(response, 'data-url="/api/countries/"')
        self.assertContains(response, 'data-url="/api/derived/"')
        self.assertContains(response, 'data-chart-widget')
        self.assertContains(response, 'data-theme-select')
        self.assertContains(response, 'data-theme-effects')
        self.assertContains(response, 'value="dark"')
        self.assertContains(response, 'value="dracula"')
        self.assertContains(response, 'value="retro80"')
        self.assertContains(response, 'value="rainbow"')
        self.assertContains(response, "Ver datos")
        self.assertContains(response, "Paises y subdivisiones")
        self.assertContains(response, "Nuevos paises")
        self.assertEqual(
            [row["label"] for row in response.context["derived_bars"]],
            ["Derived A", "Derived B"],
        )
        self.assertNotContains(response, "Areas con escanos")

    def test_dashboard_population_data_uses_only_level_zero_rows(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            pop_latest=300,
        )
        AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="city",
            name="City",
            level=1,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="bb_root",
            country_code="bb",
            code="root",
            name="BB Country",
            level=0,
            pop_latest=200,
        )

        response = self.client.get("/dashboard/population/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["type"], "donut")
        self.assertEqual(data["total"], 500)
        self.assertEqual(
            data["countries"],
            [
                {
                    "code": "aa",
                    "label": "AA Country",
                    "population": 300,
                    "area_km2": None,
                    "wikidata_id": "",
                    "detail_url": "/dashboard/country/aa/",
                },
                {
                    "code": "bb",
                    "label": "BB Country",
                    "population": 200,
                    "area_km2": None,
                    "wikidata_id": "",
                    "detail_url": "/dashboard/country/bb/",
                },
            ],
        )
        self.assertEqual(
            data["items"],
            [
                {"key": "aa", "label": "AA Country", "value": 300, "detail_url": "/dashboard/country/aa/"},
                {"key": "bb", "label": "BB Country", "value": 200, "detail_url": "/dashboard/country/bb/"},
            ],
        )

    def test_dashboard_population_data_collapses_multiple_level_zero_rows_per_country(self):
        AdminArea.objects.create(
            id="ws_a",
            country_code="westernsahara",
            code="a",
            name="Aousserd",
            level=0,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="ws_b",
            country_code="westernsahara",
            code="b",
            name="Boujdour",
            level=0,
            pop_latest=200,
        )

        with translation.override("es"):
            response = self.client.get("/dashboard/population/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["countries"],
            [
                {
                    "code": "westernsahara",
                    "label": "Sahara Occidental",
                    "population": 300,
                    "area_km2": None,
                    "wikidata_id": "Q6250",
                    "detail_url": "/dashboard/country/westernsahara/",
                }
            ],
        )

    def test_api_country_data_excludes_hidden_city_merge_status_three_rows(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        AdminArea.objects.create(
            id="aa_visible",
            country_code="aa",
            code="visible",
            name="Visible",
            level=1,
            entity_type="Province",
            parent=root,
            area_km2=40,
            pop_latest=400,
        )
        AdminArea.objects.create(
            id="aa_hidden",
            country_code="aa",
            code="hidden",
            name="Hidden",
            level=1,
            entity_type="Province",
            parent=root,
            area_km2=60,
            pop_latest=600,
            city_merge_status=3,
        )
        AdminArea.objects.create(
            id="hidden_root",
            country_code="hiddenland",
            code="hiddenland",
            name="Hiddenland",
            level=0,
            pop_latest=9999,
            city_merge_status=3,
        )

        response = self.client.get("/api/countries/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.json()["countries"]], ["aa"])

        response = self.client.get("/api/countries/aa/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["country"]["subdivision_count"], 1)
        self.assertEqual([row["name"] for row in data["table"]["rows"]], ["Visible"])
        self.assertEqual(
            [row["label"] for row in data["first_order"]["population_chart"]["items"]],
            ["Visible"],
        )
        self.assertEqual([row["name"] for row in data["first_order"]["cards"]], ["Visible"])

    def test_dashboard_country_detail_returns_general_table_and_first_order_charts(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
            density=10,
        )
        AdminArea.objects.create(
            id="aa_one",
            country_code="aa",
            code="one",
            name="One",
            level=1,
            entity_type="Province",
            parent=root,
            area_km2=40,
            pop_latest=400,
            density=10,
        )
        AdminArea.objects.create(
            id="aa_two",
            country_code="aa",
            code="two",
            name="Two",
            level=1,
            entity_type="Province",
            parent=root,
            area_km2=60,
            pop_latest=600,
            density=10,
        )

        response = self.client.get("/dashboard/country/aa/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["country"]["name"], "AA Country")
        self.assertEqual(data["country"]["population"], 1000)
        self.assertEqual(data["levels"][0]["value"], 1)
        self.assertEqual([row["name"] for row in data["table"]["rows"]], ["One", "Two"])
        self.assertEqual(
            [(row["label"], row["value"]) for row in data["first_order"]["population_chart"]["items"]],
            [("One", 400.0), ("Two", 600.0)],
        )
        self.assertEqual(
            [(row["name"], row["population_percent"], row["area_percent"]) for row in data["first_order"]["cards"]],
            [("One", 40.0, 40.0), ("Two", 60.0, 60.0)],
        )
        self.assertEqual(data["first_order"]["cards"][0]["detail_url"], "/api/admin-areas/aa_one/")
        self.assertEqual(data["first_order"]["cards"][0]["child_count"], 0)

    def test_dashboard_country_detail_exposes_large_levels_with_pagination(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        region = AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            area_km2=100,
            pop_latest=1000,
        )
        for index in range(105):
            AdminArea.objects.create(
                id=f"aa_commune_{index}",
                country_code="aa",
                code=f"commune_{index}",
                name=f"Commune {index:03d}",
                level=2,
                entity_type="Commune",
                parent=region,
                area_km2=10,
                pop_latest=100,
            )

        response = self.client.get("/api/countries/aa/?level=2&page_size=500")

        data = response.json()
        self.assertEqual([level["value"] for level in data["levels"]], [1, 2])
        self.assertEqual(data["levels"][1]["label"], "Commune")
        self.assertEqual(data["levels"][1]["count"], 105)
        self.assertEqual(data["selected_level"], 2)
        self.assertEqual(len(data["table"]["rows"]), 100)
        self.assertEqual(data["table"]["rows"][0]["name"], "Commune 000")
        self.assertEqual(data["table"]["pagination"]["page_size"], 100)
        self.assertEqual(data["table"]["pagination"]["total"], 105)
        self.assertEqual(data["table"]["pagination"]["num_pages"], 2)

    def test_dashboard_country_detail_keeps_all_types_in_large_level(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        region = AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        province = AdminArea.objects.create(
            id="aa_province",
            country_code="aa",
            code="province",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
        )
        AdminArea.objects.create(
            id="aa_province_child",
            country_code="aa",
            code="province-child",
            name="Province Child",
            level=3,
            entity_type="District",
            parent=province,
        )
        for index in range(3):
            AdminArea.objects.create(
                id=f"aa_municipality_{index}",
                country_code="aa",
                code=f"municipality_{index}",
                name=f"Municipality {index}",
                level=2,
                entity_type="Municipality",
                parent=region,
            )

        response = self.client.get("/api/countries/aa/?level=2")

        data = response.json()
        self.assertEqual(
            [(level["value"], level["label"], level["count"]) for level in data["levels"]],
            [(1, "Region", 1), (2, "Municipality / Province", 4), (3, "District", 1)],
        )
        self.assertEqual(data["selected_level"], 2)
        self.assertEqual(data["selected_filter"], "2")
        self.assertEqual(
            [row["name"] for row in data["table"]["rows"]],
            ["Municipality 0", "Municipality 1", "Municipality 2", "Province"],
        )

    def test_api_admin_area_detail_returns_direct_children_for_recursive_browser(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        region = AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            area_km2=80,
            pop_latest=800,
        )
        AdminArea.objects.create(
            id="aa_city",
            country_code="aa",
            code="city",
            name="City",
            level=2,
            entity_type="Municipality",
            parent=region,
            area_km2=20,
            pop_latest=200,
        )
        AdminArea.objects.create(
            id="aa_hidden",
            country_code="aa",
            code="hidden",
            name="Hidden",
            level=2,
            entity_type="Municipality",
            parent=region,
            area_km2=10,
            pop_latest=100,
            city_merge_status=3,
        )

        response = self.client.get("/api/admin-areas/aa_region/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["area"]["name"], "Region")
        self.assertEqual(data["area"]["subdivision_count"], 1)
        self.assertEqual(data["area"]["detail_url"], "/api/admin-areas/aa_region/")
        self.assertEqual(
            data["children"],
            [
                {
                    "id": "aa_city",
                    "name": "City",
                    "entity_type": "Municipality",
                    "level": 2,
                    "area_km2": 20.0,
                    "population": 200,
                    "density": 10.0,
                    "population_percent": 25.0,
                    "area_percent": 25.0,
                    "child_count": 0,
                    "detail_url": "/api/admin-areas/aa_city/",
                }
            ],
        )

    def test_api_admin_area_detail_uses_same_name_root_child_with_descendants_for_mixed_levels(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        ceuta = AdminArea.objects.create(
            id="aa_ceuta",
            country_code="aa",
            code="CEU",
            name="Ceuta",
            level=1,
            entity_type="Autonomous City",
            parent=root,
            area_km2=10,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="aa_ceuta_flat",
            country_code="aa",
            code="51",
            name="Ceuta",
            level=2,
            entity_type="Autonomous City",
            parent=ceuta,
            area_km2=10,
            pop_latest=100,
        )
        municipality = AdminArea.objects.create(
            id="aa_ceuta_municipality",
            country_code="aa",
            code="51001",
            name="Ceuta",
            level=3,
            entity_type="Municipality",
            parent=root,
            area_km2=10,
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="aa_ceuta_seat",
            country_code="aa",
            code="510010001",
            name="Ceuta seat",
            level=4,
            entity_type="Locality",
            parent=municipality,
            area_km2=4,
            pop_latest=40,
        )

        country_response = self.client.get("/api/countries/aa/")

        self.assertEqual(country_response.status_code, 200)
        card = country_response.json()["first_order"]["cards"][0]
        self.assertEqual(card["child_count"], 1)
        self.assertEqual(
            card["children"],
            [
                {
                    "name": "Ceuta",
                    "entity_type": "Municipio",
                    "population": 100,
                    "area_km2": 10.0,
                    "population_percent": 100.0,
                    "area_percent": 100.0,
                }
            ],
        )

        detail_response = self.client.get("/api/admin-areas/aa_ceuta/")

        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["area"]["subdivision_count"], 1)
        self.assertEqual([row["id"] for row in detail["children"]], ["aa_ceuta_municipality"])
        self.assertEqual(detail["children"][0]["child_count"], 1)

    def test_dashboard_country_detail_adds_second_order_shares_by_level(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        region = AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            area_km2=80,
            pop_latest=800,
        )
        AdminArea.objects.create(
            id="aa_province",
            country_code="aa",
            code="province",
            name="Province",
            level=2,
            entity_type="Province",
            parent=region,
            area_km2=20,
            pop_latest=200,
        )
        AdminArea.objects.create(
            id="aa_municipality",
            country_code="aa",
            code="municipality",
            name="Municipality",
            level=2,
            entity_type="Municipality",
            parent=region,
            area_km2=10,
            pop_latest=100,
        )

        response = self.client.get("/dashboard/country/aa/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["first_order"]["cards"][0]["children"],
            [
                {
                    "name": "Municipality",
                    "entity_type": "Municipality",
                    "population": 100,
                    "area_km2": 10.0,
                    "population_percent": 12.5,
                    "area_percent": 12.5,
                },
                {
                    "name": "Province",
                    "entity_type": "Province",
                    "population": 200,
                    "area_km2": 20.0,
                    "population_percent": 25.0,
                    "area_percent": 25.0,
                },
            ],
        )

    def test_dashboard_derived_data_returns_reusable_bar_chart_payload(self):
        NuevoAdminArea.objects.create(
            id="derived_a",
            country_code="derived_a",
            code="root",
            name="Derived_A",
            level=0,
            pop_latest=600,
        )
        NuevoAdminArea.objects.create(
            id="derived_b",
            country_code="derived_b",
            code="root",
            name="Derived B",
            level=0,
            pop_latest=400,
        )

        response = self.client.get("/dashboard/derived/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["type"], "bar")
        self.assertEqual(
            [(row["label"], row["value"]) for row in data["items"]],
            [("Derived A", 600), ("Derived B", 400)],
        )

    def test_stats_data_returns_dynamic_chart_payloads(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            pop_latest=300,
        )

        response = self.client.get("/stats/data/")

        self.assertEqual(response.status_code, 200)
        charts = response.json()["charts"]
        self.assertIn("country_population", charts)
        self.assertIn("country_area", charts)
        self.assertIn("admin_levels", charts)
        self.assertEqual(charts["country_population"]["type"], "bar")
        self.assertEqual(charts["country_population"]["items"][0]["label"], "AA Country")

    def test_countries_page_uses_api_country_browser(self):
        response = self.client.get("/countries/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-stats-countries')
        self.assertContains(response, 'data-url="/api/countries/"')
        self.assertContains(response, 'data-children-title')
        self.assertContains(response, 'data-open-label')

    def test_stats_redirects_to_countries_page(self):
        response = self.client.get("/stats/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/countries/")

    def test_visual_identity_detail_placeholder(self):
        response = self.client.get("/identity/flag/Test_Flag.svg/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detalles heraldicos")
        self.assertContains(response, "Test_Flag.svg")

    def test_api_country_summary_returns_api_detail_urls_for_cards(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )

        response = self.client.get("/api/countries/")

        self.assertEqual(response.status_code, 200)
        country = response.json()["countries"][0]
        self.assertEqual(country["detail_url"], "/api/countries/aa/")
        self.assertEqual(country["area_km2"], 12.0)
        self.assertIn("charts", response.json())

    def test_api_country_summary_exposes_database_and_local_visual_assets(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=Path(tmpdir), MEDIA_URL="/media/"):
            flag_path = Path(tmpdir) / "visual_assets" / "flag" / "aa" / "Flag.svg"
            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("<svg></svg>", encoding="utf-8")
            coat_path = Path(tmpdir) / "visual_assets" / "coat" / "aa" / "Coat.svg"
            coat_path.parent.mkdir(parents=True, exist_ok=True)
            coat_path.write_text("<svg></svg>", encoding="utf-8")
            self._insert_visual_asset(
                entity_key="aa",
                kind="flag",
                local_path="visual_assets\\flag\\aa\\Flag.svg",
                local_exists=True,
                source="local",
            )
            self._insert_visual_asset(
                entity_key="aa",
                kind="coat",
                local_path="visual_assets\\coat\\aa\\Coat.svg",
                local_exists=True,
                source="local",
            )

            response = self.client.get("/api/countries/")

            self.assertEqual(response.status_code, 200)
            country = response.json()["countries"][0]
            self.assertEqual(country["flag_asset"]["local_url"], "/media/visual_assets/flag/aa/Flag.svg")
            self.assertEqual(country["flag_asset"]["image_url"], "/media/visual_assets/flag/aa/Flag.svg")
            self.assertEqual(country["coat_asset"]["source"], "local")
            self.assertEqual(country["coat_asset"]["local_url"], "/media/visual_assets/coat/aa/Coat.svg")

            detail = self.client.get("/api/countries/aa/").json()["country"]
            self.assertEqual(detail["flag_asset"]["local_url"], "/media/visual_assets/flag/aa/Flag.svg")
            self.assertEqual(detail["coat_asset"]["local_url"], "/media/visual_assets/coat/aa/Coat.svg")

    def test_api_country_summary_ignores_unregistered_local_visual_asset_folders(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=Path(tmpdir), MEDIA_URL="/media/"):
            flag_path = Path(tmpdir) / "visual_assets" / "flag" / "aa" / "Flag.svg"
            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("<svg></svg>", encoding="utf-8")
            coat_path = Path(tmpdir) / "visual_assets" / "coat" / "aa" / "Coat.svg"
            coat_path.parent.mkdir(parents=True, exist_ok=True)
            coat_path.write_text("<svg></svg>", encoding="utf-8")

            response = self.client.get("/api/countries/")

            self.assertEqual(response.status_code, 200)
            country = response.json()["countries"][0]
            self.assertEqual(country["visual_assets"], {})
            self.assertEqual(country["flag_asset"], {})
            self.assertEqual(country["coat_asset"], {})

            detail = self.client.get("/api/countries/aa/").json()["country"]
            self.assertEqual(detail["visual_assets"], {})
            self.assertEqual(detail["flag_asset"], {})
            self.assertEqual(detail["coat_asset"], {})

    def test_api_country_summary_ignores_config_visual_asset_fallbacks_without_sql_asset_row(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )
        upsert_scraping_config(
            "aa",
            """
name = "AA Country"
country_code = "aa"

[visual_assets.flag]
commons_filename = "Flag_of_AA.svg"

[visual_assets.coat]
remote_url = "https://commons.wikimedia.org/wiki/Special:FilePath/Coat_of_AA.svg"

[[pages]]
source = "cities"
path = ["aa/"]
force_highest_level = 0
""",
        )

        response = self.client.get("/api/countries/")

        self.assertEqual(response.status_code, 200)
        country = response.json()["countries"][0]
        self.assertEqual(country["visual_assets"], {})
        self.assertEqual(country["flag_asset"], {})
        self.assertEqual(country["coat_asset"], {})

        detail = self.client.get("/api/countries/aa/").json()["country"]
        self.assertEqual(detail["visual_assets"], {})
        self.assertEqual(detail["flag_asset"], {})
        self.assertEqual(detail["coat_asset"], {})

    def test_api_country_summary_ignores_wrong_citypopulation_language_flag(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=Path(tmpdir), MEDIA_URL="/media/"):
            flag_path = Path(tmpdir) / "visual_assets" / "flag" / "aa" / "canada_2_3.svg"
            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("<svg></svg>", encoding="utf-8")
            self._insert_visual_asset(
                entity_key="aa",
                kind="flag",
                local_path="visual_assets\\flag\\aa\\canada_2_3.svg",
                local_exists=True,
                remote_url="https://www.citypopulation.de/images/flags/canada_2_3.svg",
            )

            response = self.client.get("/api/countries/")

            self.assertEqual(response.status_code, 200)
            flag = response.json()["countries"][0]["flag_asset"]
            self.assertEqual(flag["local_url"], "")
            self.assertEqual(flag["image_url"], "")
            self.assertEqual(flag["remote_url"], "")

    def test_api_country_summary_uses_remote_asset_as_display_image(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            area_km2=12,
            pop_latest=300,
        )
        self._insert_visual_asset(
            entity_key="aa",
            kind="flag",
            remote_url="https://commons.wikimedia.org/wiki/Special:FilePath/Flag.svg?width=1600",
            commons_filename="Flag.svg",
            local_path="",
            local_exists=False,
            status="found",
        )

        response = self.client.get("/api/countries/")

        self.assertEqual(response.status_code, 200)
        flag = response.json()["countries"][0]["flag_asset"]
        self.assertEqual(flag["remote_url"], "https://commons.wikimedia.org/wiki/Special:FilePath/Flag.svg?width=1600")
        self.assertEqual(flag["local_url"], "")
        self.assertEqual(flag["image_url"], "https://commons.wikimedia.org/wiki/Special:FilePath/Flag.svg?width=1600")

    def test_visual_identity_detail_renders_entity_ficha_with_translations(self):
        asset_id = self._insert_visual_asset(
            entity_key="aa",
            kind="flag",
            entity_name="AA Country",
            remote_url="https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_AA.svg?width=500",
            commons_filename="Flag of AA.svg",
            status="found",
        )
        self._insert_visual_asset_translation(
            asset_id=asset_id,
            language="es",
            title="Bandera de AA",
            description="Descripcion guardada en español",
        )
        self._insert_visual_asset_translation(
            asset_id=asset_id,
            language="en",
            title="Flag of AA",
            description="Stored English description",
        )

        response = self.client.get("/identity/flag/entity/country/aa/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Ficha", html)
        self.assertIn("AA Country", html)
        self.assertIn("Descripcion guardada en español", html)
        self.assertIn("Stored English description", html)
        self.assertIn("https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_AA.svg?width=500", html)
        self.assertIn("Ver en Commons", html)

    def test_visual_identity_legacy_local_path_resolves_saved_asset(self):
        relative_path = "visual_assets/flag/aa/aa_aa_flag.svg"
        asset_id = self._insert_visual_asset(
            entity_key="aa",
            kind="flag",
            local_path=relative_path,
            local_exists=True,
            commons_filename="Flag of AA.svg",
            remote_url="https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_AA.svg?width=500",
            status="found",
        )
        self._insert_visual_asset_translation(
            asset_id=asset_id,
            language="es",
            description="Descripcion heredada del asset",
        )

        response = self.client.get(f"/identity/flag/{relative_path}/")

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Descripcion heredada del asset", html)
        self.assertIn("https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_AA.svg?width=500", html)
        self.assertIn("Ver en Commons", html)

    def test_api_admin_area_detail_exposes_local_visual_assets(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            area_km2=80,
            pop_latest=800,
        )

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=Path(tmpdir), MEDIA_URL="/media/"):
            coat_path = Path(tmpdir) / "visual_assets" / "coat" / "aa" / "aa_region" / "Coat.svg"
            coat_path.parent.mkdir(parents=True, exist_ok=True)
            coat_path.write_text("<svg></svg>", encoding="utf-8")

            response = self.client.get("/api/admin-areas/aa_region/")

            self.assertEqual(response.status_code, 200)
            area = response.json()["area"]
            self.assertEqual(area["coat_asset"]["source"], "local")
            self.assertEqual(area["coat_asset"]["local_url"], "/media/visual_assets/coat/aa/aa_region/Coat.svg")

    def test_api_admin_area_detail_uses_seal_as_coat_fallback_and_returns_metadata(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="aa",
            name="AA Country",
            level=0,
            area_km2=100,
            pop_latest=1000,
        )
        AdminArea.objects.create(
            id="aa_region",
            country_code="aa",
            code="region",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
            area_km2=80,
            pop_latest=800,
        )

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=Path(tmpdir), MEDIA_URL="/media/"):
            seal_path = Path(tmpdir) / "visual_assets" / "seal" / "aa" / "aa_region" / "Seal.svg"
            seal_path.parent.mkdir(parents=True, exist_ok=True)
            seal_path.write_text("<svg></svg>", encoding="utf-8")
            asset_id = self._insert_visual_asset(
                entity_type="admin_area",
                entity_key="aa_region",
                entity_name="Region",
                country_code="aa",
                kind="seal",
                local_path="visual_assets\\seal\\aa\\aa_region\\Seal.svg",
                local_exists=True,
            )
            self._insert_visual_asset_translation(
                asset_id=asset_id,
                title="Seal of Region",
                description="Stored seal description",
                blazon="Stored heraldic description",
            )

            response = self.client.get("/api/admin-areas/aa_region/")

            self.assertEqual(response.status_code, 200)
            area = response.json()["area"]
            self.assertEqual(area["seal_asset"]["local_url"], "/media/visual_assets/seal/aa/aa_region/Seal.svg")
            self.assertEqual(area["coat_asset"]["local_url"], "/media/visual_assets/seal/aa/aa_region/Seal.svg")
            self.assertEqual(area["seal_asset"]["translations"]["es"]["description"], "Stored seal description")
            self.assertEqual(area["seal_asset"]["translations"]["es"]["blazon"], "Stored heraldic description")

    def test_country_display_names_use_translation_catalog(self):
        AdminArea.objects.create(
            id="brazil_root",
            country_code="brazil",
            code="root",
            name="Brazil",
            level=0,
            pop_latest=300,
        )

        with translation.override("es"):
            response = self.client.get("/dashboard/population/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["countries"][0]["label"], "Brasil")

    def test_spain_detail_uses_spanish_geography_labels(self):
        root = AdminArea.objects.create(
            id="spain_root",
            country_code="spain",
            code="spain",
            name="España",
            level=0,
            entity_type="Kingdom",
            area_km2=100,
            pop_latest=1000,
        )
        catalonia = AdminArea.objects.create(
            id="spain_cat",
            country_code="spain",
            code="cat",
            name="Cataluña",
            level=1,
            entity_type="Autonomous Community",
            parent=root,
            area_km2=60,
            pop_latest=700,
        )
        AdminArea.objects.create(
            id="spain_lleida",
            country_code="spain",
            code="lleida",
            name="Lleida",
            level=2,
            entity_type="Province",
            parent=catalonia,
            area_km2=20,
            pop_latest=200,
        )

        with translation.override("es"):
            response = self.client.get("/api/countries/spain/?level=2")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["levels"][1]["entity_type"], "Provincia")
        self.assertEqual(
            data["table"]["rows"][0],
            {
                "name": "Lérida",
                "area_km2": 20.0,
                "population": 200,
                "density": 10.0,
                "population_percent": 20.0,
                "area_percent": 20.0,
                "parent": "Cataluña",
                "entity_type": "Provincia",
            },
        )
        self.assertEqual(data["first_order"]["cards"][0]["entity_type"], "Comunidad autónoma")
        self.assertEqual(data["first_order"]["cards"][0]["children"][0]["name"], "Lérida")

    def test_spain_detail_translates_geography_labels_for_supported_languages(self):
        root = AdminArea.objects.create(
            id="spain_root_multilang",
            country_code="spain",
            code="spain-multilang",
            name="Espa\u00f1a",
            level=0,
            entity_type="Kingdom",
            area_km2=100,
            pop_latest=1000,
        )
        catalonia = AdminArea.objects.create(
            id="spain_cat_multilang",
            country_code="spain",
            code="cat-multilang",
            name="Catalu\u00f1a",
            level=1,
            entity_type="Autonomous Community",
            parent=root,
            area_km2=60,
            pop_latest=700,
        )
        AdminArea.objects.create(
            id="spain_lleida_multilang",
            country_code="spain",
            code="lleida-multilang",
            name="Lleida",
            level=2,
            entity_type="Province",
            parent=catalonia,
            area_km2=20,
            pop_latest=200,
        )

        expectations = {
            "en": ("Spain", "Lleida", "Province", "Autonomous community"),
            "fr": ("Espagne", "L\u00e9rida", "Province", "Communaut\u00e9 autonome"),
            "de": ("Spanien", "L\u00e9rida", "Provinz", "Autonome Gemeinschaft"),
            "ru": (
                "\u0418\u0441\u043f\u0430\u043d\u0438\u044f",
                "\u041b\u044c\u0435\u0439\u0434\u0430",
                "\u041f\u0440\u043e\u0432\u0438\u043d\u0446\u0438\u044f",
                "\u0410\u0432\u0442\u043e\u043d\u043e\u043c\u043d\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u0441\u0442\u0432\u043e",
            ),
            "it": ("Spagna", "L\u00e9rida", "Provincia", "Comunit\u00e0 autonoma"),
            "sr": (
                "\u0428\u043f\u0430\u043d\u0438\u0458\u0430",
                "\u0409\u0435\u0438\u0434\u0430",
                "\u041f\u0440\u043e\u0432\u0438\u043d\u0446\u0438\u0458\u0430",
                "\u0410\u0443\u0442\u043e\u043d\u043e\u043c\u043d\u0430 \u0437\u0430\u0458\u0435\u0434\u043d\u0438\u0446\u0430",
            ),
            "sr-latn": ("\u0160panija", "Ljeida", "Provincija", "Autonomna zajednica"),
            "ar": (
                "\u0625\u0633\u0628\u0627\u0646\u064a\u0627",
                "\u0644\u0627\u0631\u062f\u0629",
                "\u0645\u0642\u0627\u0637\u0639\u0629",
                "\u0645\u0646\u0637\u0642\u0629 \u062d\u0643\u0645 \u0630\u0627\u062a\u064a",
            ),
        }
        for language_code, expected in expectations.items():
            country_name, province_name, province_type, first_order_type = expected
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language_code
            with self.subTest(language_code=language_code):
                response = self.client.get(
                    "/api/countries/spain/?level=2",
                    HTTP_ACCEPT_LANGUAGE=language_code,
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["country"]["name"], country_name)
                self.assertEqual(data["table"]["rows"][0]["name"], province_name)
                self.assertEqual(data["table"]["rows"][0]["entity_type"], province_type)
                self.assertEqual(data["first_order"]["cards"][0]["entity_type"], first_order_type)

    def test_country_selectors_use_database_root_names_as_labels(self):
        AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
        )
        NuevoAdminArea.objects.create(
            id="derived_a",
            country_code="derived_a",
            code="root",
            name="Derived_A",
            level=0,
        )

        response = self.client.get("/areas/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="aa">AA Country</option>', html=True)

        response = self.client.get("/delete/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="aa">AA Country (aa)</option>', html=True)
        self.assertContains(
            response,
            '<option value="derived_a">Derived A (derived_a)</option>',
            html=True,
        )
