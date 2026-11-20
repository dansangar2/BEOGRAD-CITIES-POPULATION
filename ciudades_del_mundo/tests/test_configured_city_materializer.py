from decimal import Decimal

from django.test import TestCase

from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_slug
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config


class ConfiguredCityMaterializerTests(TestCase):
    def test_materializer_keeps_runtime_locality_parent_and_level_on_reapply(self):
        root = AdminArea.objects.create(id='cm_cm', country_code='cm', code='cm', name='Country', level=0)
        district = AdminArea.objects.create(id='cm_d', country_code='cm', code='d', name='District', level=1, parent=root)
        municipality = AdminArea.objects.create(
            id='cm_m', country_code='cm', code='m', name='Urban municipality',
            level=2, parent=district, entity_type='City Municipality', pop_latest=100,
        )
        outside = AdminArea.objects.create(
            id='cm_o', country_code='cm', code='o', name='Outside municipality',
            level=2, parent=district, entity_type='Municipality', pop_latest=50,
        )
        urban = AdminArea.objects.create(
            id='cm_u', country_code='cm', code='u', name='Urban settlement',
            level=3, parent=outside, entity_type='Urban Settlement', pop_latest=80,
        )
        upsert_scraping_config('city_runtime', '''
country_code = "cm"
scrape_schema_version = 2
[[pages]]
source = "admin"
path = ["admin"]
[[cities]]
city = "Complete city"
id = "city"
level = 2
type = "City"
district_types = ["City Municipality"]
from = { 1 = ["d"] }
keep_communes = true
[[parent_overrides]]
codes = ["u"]
parent_code = "city"
level = 4
''')
        previous_state = None
        for _ in range(2):
            materialize_configured_cities_for_slug('city_runtime')
            city = AdminArea.objects.get(id='cm_city')
            municipality.refresh_from_db()
            outside.refresh_from_db()
            urban.refresh_from_db()
            self.assertEqual((city.level, city.parent_id, city.pop_latest), (2, district.id, 100))
            self.assertEqual((municipality.level, municipality.parent_id), (3, city.id))
            self.assertEqual((outside.level, outside.parent_id, outside.pop_latest), (2, district.id, 50))
            self.assertEqual((urban.level, urban.parent_id, urban.pop_latest), (4, city.id, 80))
            self.assertEqual(city.most_populate_city_id, urban.id)
            state = list(AdminArea.objects.filter(country_code='cm').order_by('id').values())
            if previous_state is not None:
                self.assertEqual(state, previous_state)
            previous_state = state
        self.assertEqual(AdminArea.objects.filter(country_code='cm').count(), 6)

    def test_materialized_configured_city_is_available_to_group_source_selector(self):
        root = AdminArea.objects.create(id="mc_mc", country_code="mc", code="mc", name="Merge Country", level=0)
        province = AdminArea.objects.create(
            id="mc_p",
            country_code="mc",
            code="p",
            name="Province",
            level=1,
            entity_type="Province",
            parent=root,
        )
        north = AdminArea.objects.create(
            id="mc_n",
            country_code="mc",
            code="n",
            name="North",
            level=2,
            entity_type="Municipality",
            parent=province,
            area_km2=Decimal("1.50"),
            pop_latest=10,
        )
        south = AdminArea.objects.create(
            id="mc_s",
            country_code="mc",
            code="s",
            name="South",
            level=2,
            entity_type="Municipality",
            parent=province,
            area_km2=Decimal("2.50"),
            pop_latest=30,
        )
        upsert_scraping_config(
            "mergecity",
            """
LEGAL_SUBDIVISION = 2
scrape_schema_version = 2
country_code = "mc"

[[pages]]
source = "admin"
path = ["admin"]

[[cities]]
city = "Metro"
id = "metro"
level = 2
type = "City"
district_types = ["Municipality"]
from = { 1 = ["Province"] }
communes = ["North", "South"]
keep_communes = false
""".strip()
            + "\n",
        )

        result = materialize_configured_cities_for_slug("mergecity")

        self.assertEqual(result.configs_applied, 1)
        metro = AdminArea.objects.get(id="mc_metro")
        north.refresh_from_db()
        south.refresh_from_db()
        self.assertEqual(metro.name, "Metro")
        self.assertEqual(metro.parent_id, province.id)
        self.assertEqual(metro.city_merge_status, AdminArea.CityMergeStatus.UNIFIED)
        self.assertEqual(metro.area_km2, Decimal("4.00"))
        self.assertEqual(metro.pop_latest, 40)
        self.assertEqual(north.level, 3)
        self.assertEqual(north.parent_id, metro.id)
        self.assertEqual(north.city_merge_status, AdminArea.CityMergeStatus.SOURCE)
        self.assertEqual(south.level, 3)
        self.assertEqual(south.parent_id, metro.id)
        self.assertEqual(south.city_merge_status, AdminArea.CityMergeStatus.SOURCE)

        response = self.client.get("/groups/source-data/?country_code=mc&parent_id=mc_p")

        self.assertEqual(response.status_code, 200)
        child_ids = [row["id"] for row in response.json()["children"]]
        self.assertIn(metro.id, child_ids)
        self.assertNotIn(north.id, child_ids)
        self.assertNotIn(south.id, child_ids)

    def test_materializer_restores_shadowed_parent_when_city_id_changes(self):
        root = AdminArea.objects.create(id="sh_sh", country_code="sh", code="sh", name="Shadow Country", level=0)
        region = AdminArea.objects.create(
            id="sh_r",
            country_code="sh",
            code="r",
            name="Region",
            level=1,
            entity_type="Region",
            parent=root,
        )
        AdminArea.objects.create(
            id="sh_s",
            country_code="sh",
            code="s",
            name="Sibling",
            level=2,
            entity_type="Prefecture",
            parent=region,
        )
        shadowed_parent = AdminArea.objects.create(
            id="sh_parent",
            country_code="sh",
            code="parent",
            name="Casablanca",
            level=3,
            entity_type="City",
            parent=region,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        source = AdminArea.objects.create(
            id="sh_source",
            country_code="sh",
            code="source",
            name="Source Commune",
            level=3,
            entity_type="Municipality",
            parent=shadowed_parent,
            area_km2=Decimal("2.00"),
            pop_latest=20,
        )
        upsert_scraping_config(
            "shadowcity",
            """
LEGAL_SUBDIVISION = 3
scrape_schema_version = 2
country_code = "sh"

[[pages]]
source = "admin"
path = ["admin"]

[[cities]]
city = "Casablanca"
id = "parent_city"
level = 3
type = "City"
parent_type = "Prefecture"
district_types = ["Municipality"]
from = { 2 = ["Casablanca"] }
communes = ["Source Commune"]
keep_communes = false
""".strip()
            + "\n",
        )

        materialize_configured_cities_for_slug("shadowcity")

        shadowed_parent.refresh_from_db()
        source.refresh_from_db()
        city = AdminArea.objects.get(id="sh_parent_city")
        self.assertEqual(shadowed_parent.level, 2)
        self.assertEqual(shadowed_parent.entity_type, "Prefecture")
        self.assertEqual(shadowed_parent.city_merge_status, AdminArea.CityMergeStatus.NONE)
        self.assertEqual(city.parent_id, shadowed_parent.id)
        self.assertEqual(city.city_merge_status, AdminArea.CityMergeStatus.UNIFIED)
        self.assertEqual(source.level, 4)
        self.assertEqual(source.parent_id, city.id)
        self.assertEqual(source.city_merge_status, AdminArea.CityMergeStatus.SOURCE)

        response = self.client.get("/configs/shadowcity/editor-data/")

        self.assertEqual(response.status_code, 200)
        parent_labels = [row["label"] for row in response.json()["manual"]["city_builder"]["parent_options"]]
        self.assertIn("Casablanca (Prefecture)", parent_labels)

    def test_materializer_repairs_sources_for_preexisting_unified_city(self):
        root = AdminArea.objects.create(id="rm_rm", country_code="rm", code="rm", name="Repair Country", level=0)
        province = AdminArea.objects.create(
            id="rm_p",
            country_code="rm",
            code="p",
            name="Province",
            level=1,
            entity_type="Province",
            parent=root,
        )
        north = AdminArea.objects.create(
            id="rm_n",
            country_code="rm",
            code="n",
            name="North",
            level=2,
            entity_type="District",
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.SOURCE,
        )
        AdminArea.objects.create(
            id="rm_metro",
            country_code="rm",
            code="metro",
            name="Metro",
            level=2,
            entity_type="City",
            parent=province,
            city_merge_status=AdminArea.CityMergeStatus.UNIFIED,
        )
        upsert_scraping_config(
            "repaircity",
            """
LEGAL_SUBDIVISION = 2
scrape_schema_version = 2
country_code = "rm"

[[pages]]
source = "admin"
path = ["admin"]

[[cities]]
city = "Metro"
id = "metro"
level = 2
type = "City"
district_types = ["District"]
from = { 1 = ["Province"] }
communes = ["North"]
keep_communes = false
""".strip()
            + "\n",
        )

        result = materialize_configured_cities_for_slug("repaircity")

        north.refresh_from_db()
        self.assertEqual(result.configs_applied, 1)
        self.assertEqual(north.level, 3)
        self.assertEqual(north.parent_id, "rm_metro")
        self.assertEqual(north.city_merge_status, AdminArea.CityMergeStatus.SOURCE)
