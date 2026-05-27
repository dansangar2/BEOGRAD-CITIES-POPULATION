from decimal import Decimal
import unittest

from ciudades_del_mundo.application.entity_merges import apply_entity_merges
from ciudades_del_mundo.domain import CITY_MERGE_SOURCE, CITY_MERGE_UNIFIED, EntityMergeConfig, ScrapedAdminArea


class EntityMergeTests(unittest.TestCase):
    def test_apply_entity_merges_creates_unified_entity_and_marks_sources(self):
        entities = [
            ScrapedAdminArea(code="p", name="Province", level=1, country_code="x"),
            ScrapedAdminArea(
                code="a",
                name="Metro 1",
                level=2,
                country_code="x",
                parent_code="p",
                entity_type="District",
                area_km2=Decimal("1.25"),
                pop_latest=100,
                pop_latest_date="2020-01-01",
                last_census_year=2020,
            ),
            ScrapedAdminArea(
                code="b",
                name="Metro 2",
                level=2,
                country_code="x",
                parent_code="p",
                entity_type="District",
                area_km2=Decimal("2.75"),
                pop_latest=300,
                pop_latest_date="2021-01-01",
                last_census_year=2021,
            ),
            ScrapedAdminArea(
                code="c",
                name="Other",
                level=2,
                country_code="x",
                parent_code="p",
                entity_type="Municipality",
                pop_latest=500,
            ),
        ]
        config = EntityMergeConfig(entity_types=("District",), entity_type="City")

        result = apply_entity_merges(entities, [config])

        by_code = {entity.code: entity for entity in result}
        self.assertEqual(by_code["a"].city_merge_status, CITY_MERGE_SOURCE)
        self.assertEqual(by_code["b"].city_merge_status, CITY_MERGE_SOURCE)
        self.assertEqual(by_code["c"].city_merge_status, 0)

        merged = by_code["a-merged"]
        self.assertEqual(merged.name, "Metro")
        self.assertEqual(merged.parent_code, "p")
        self.assertEqual(merged.entity_type, "City")
        self.assertEqual(merged.city_merge_status, CITY_MERGE_UNIFIED)
        self.assertEqual(merged.area_km2, Decimal("4.00"))
        self.assertEqual(merged.pop_latest, 400)
        self.assertEqual(merged.density, Decimal("100"))
        self.assertEqual(merged.pop_latest_date, "2021-01-01")
        self.assertEqual(merged.last_census_year, 2021)

