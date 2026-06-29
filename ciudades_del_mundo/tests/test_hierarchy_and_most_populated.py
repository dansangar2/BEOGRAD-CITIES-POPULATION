import unittest

from ciudades_del_mundo.domain import (
    CITY_MERGE_SOURCE,
    CITY_MERGE_UNIFIED,
    AdminAreaSummary,
    ScrapedAdminArea,
    assign_parent_codes_by_level,
    calculate_most_populated_assignments,
)


class HierarchyTests(unittest.TestCase):
    def test_assign_parent_codes_by_level_uses_stack_and_keeps_explicit_parent(self):
        entities = [
            ScrapedAdminArea(code="root", name="Root", level=0, country_code="x"),
            ScrapedAdminArea(code="a", name="A", level=1, country_code="x"),
            ScrapedAdminArea(code="aa", name="AA", level=2, country_code="x"),
            ScrapedAdminArea(code="b", name="B", level=1, country_code="x", parent_code="manual"),
            ScrapedAdminArea(code="bb", name="BB", level=2, country_code="x"),
        ]

        result = assign_parent_codes_by_level(entities)

        self.assertEqual([entity.parent_code for entity in result], [None, "root", "a", "manual", "b"])


class MostPopulatedTests(unittest.TestCase):
    def test_calculate_most_populated_ignores_source_rows_and_uses_highest_available_level(self):
        areas = [
            AdminAreaSummary(id="root", level=0, parent_id=None, pop_latest=1000),
            AdminAreaSummary(id="region", level=1, parent_id="root", pop_latest=500),
            AdminAreaSummary(id="source-city", level=2, parent_id="region", pop_latest=999, city_merge_status=CITY_MERGE_SOURCE),
            AdminAreaSummary(id="city-a", level=2, parent_id="region", pop_latest=100),
            AdminAreaSummary(id="city-b", level=2, parent_id="region", pop_latest=200, city_merge_status=CITY_MERGE_UNIFIED),
            AdminAreaSummary(id="district-a", level=3, parent_id="city-a", pop_latest=1000),
        ]

        assignments = calculate_most_populated_assignments(areas, legal_subdivision_level=2)

        self.assertEqual(
            [(item.area_id, item.most_populated_id) for item in assignments],
            [("root", "district-a"), ("region", "district-a"), ("city-a", "district-a")],
        )
