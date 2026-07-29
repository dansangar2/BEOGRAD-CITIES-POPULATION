from decimal import Decimal
import unittest

from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.domain import CITY_MERGE_SOURCE, CITY_MERGE_UNIFIED, CityConfig, ScrapedAdminArea


class ConfiguredCitiesTests(unittest.TestCase):
    def test_city_merge_status_numeric_contract_matches_config_editor(self):
        self.assertEqual(CITY_MERGE_UNIFIED, 1)
        self.assertEqual(CITY_MERGE_SOURCE, 2)

    def test_configured_city_aggregates_communes_and_shifts_sources_below_city(self):
        entities = [
            ScrapedAdminArea(code="country", name="Country", level=0, country_code="x"),
            ScrapedAdminArea(code="province", name="Province", level=1, country_code="x", parent_code="country"),
            ScrapedAdminArea(
                code="north",
                name="North",
                level=2,
                country_code="x",
                entity_type="District",
                parent_code="province",
                area_km2=Decimal("1.25"),
                pop_latest=10,
                pop_latest_date="2020-01-01",
                last_census_year=2020,
            ),
            ScrapedAdminArea(
                code="south",
                name="South",
                level=2,
                country_code="x",
                entity_type="District",
                parent_code="province",
                area_km2=Decimal("2.75"),
                pop_latest=30,
                pop_latest_date="2021-01-01",
                last_census_year=2021,
            ),
        ]
        config = CityConfig(
            name="Metro",
            code="metro",
            level=2,
            entity_type="City",
            district_types=("District",),
            parent_from={1: ("Province",)},
            communes=("North", "South"),
        )

        result = apply_configured_cities("x", entities, [config])

        by_code = {entity.code: entity for entity in result}
        city = by_code["metro"]
        self.assertEqual(city.parent_code, "province")
        self.assertEqual(city.area_km2, Decimal("4.00"))
        self.assertEqual(city.pop_latest, 40)
        self.assertEqual(city.pop_latest_date, "2021-01-01")
        self.assertEqual(city.last_census_year, 2021)
        self.assertEqual(city.city_merge_status, CITY_MERGE_UNIFIED)

        self.assertEqual(by_code["north"].level, 3)
        self.assertEqual(by_code["north"].parent_code, "metro")
        self.assertEqual(by_code["north"].city_merge_status, CITY_MERGE_SOURCE)
        self.assertEqual(by_code["south"].level, 3)
        self.assertEqual(by_code["south"].parent_code, "metro")
        self.assertEqual(by_code["south"].city_merge_status, CITY_MERGE_SOURCE)

    def test_missing_configured_commune_reports_city_name(self):
        entities = [
            ScrapedAdminArea(code="country", name="Country", level=0, country_code="x"),
            ScrapedAdminArea(code="province", name="Province", level=1, country_code="x", parent_code="country"),
        ]
        config = CityConfig(
            name="Metro",
            code="metro",
            level=2,
            entity_type="City",
            district_types=("District",),
            parent_from={1: ("Province",)},
            communes=("Missing",),
        )

        with self.assertRaisesRegex(ValueError, "Metro"):
            apply_configured_cities("x", entities, [config])

