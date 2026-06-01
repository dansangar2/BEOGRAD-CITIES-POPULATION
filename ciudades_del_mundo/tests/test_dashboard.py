from django.test import TestCase
from django.utils import translation

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea


class DashboardViewTests(TestCase):
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
