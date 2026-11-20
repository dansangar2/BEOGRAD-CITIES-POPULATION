from dataclasses import replace
import unittest

from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.domain import CITY_MERGE_SOURCE, CityConfig, ScrapedAdminArea


class ConfiguredCityParentScopeTests(unittest.TestCase):
    def test_explicit_part_can_aggregate_children_already_at_the_target_child_level(self):
        entities = [
            ScrapedAdminArea(code='country', name='Country', level=0, country_code='x'),
            ScrapedAdminArea(code='district', name='District', level=1, country_code='x', parent_code='country'),
            ScrapedAdminArea(code='mukim', name='Mukim', level=3, country_code='x', parent_code='district'),
            ScrapedAdminArea(code='selected', name='Selected quarter', level=4, country_code='x',
                             parent_code='mukim', entity_type='Quarter', pop_latest=974),
            ScrapedAdminArea(code='other', name='Other quarter', level=4, country_code='x',
                             parent_code='mukim', entity_type='Quarter', pop_latest=27819),
        ]
        config = CityConfig(name='Mukim part', code='part', level=3, entity_type='Mukim',
                            district_types=('Quarter',), parent_from={1: ('district',), 3: ('mukim',)},
                            communes=('selected',))
        result = apply_configured_cities('x', entities, [config])
        by_code = {entity.code: entity for entity in result}
        self.assertEqual((by_code['part'].parent_code, by_code['part'].pop_latest), ('district', 974))
        self.assertEqual((by_code['selected'].parent_code, by_code['selected'].level), ('part', 4))
        self.assertEqual(by_code['other'], entities[-1])
        self.assertEqual(apply_configured_cities('x', result, [config]), result)

    def test_known_parent_keeps_unrelated_prefix_rows_outside_city(self):
        entities = [
            ScrapedAdminArea(code="country", name="Country", level=0, country_code="x"),
            ScrapedAdminArea(code="capital", name="Capital", level=1, country_code="x", parent_code="country"),
            ScrapedAdminArea(code="county", name="County", level=1, country_code="x", parent_code="country"),
            ScrapedAdminArea(code="021", name="Capital XXI", level=2, country_code="x", parent_code="capital", entity_type="City District"),
            ScrapedAdminArea(code="037", name="District", level=2, country_code="x", parent_code="county", entity_type="District"),
            ScrapedAdminArea(code="02149", name="Unrelated Village", level=3, country_code="x", parent_code="037", entity_type="Village", pop_latest=100),
            ScrapedAdminArea(code="02149_child", name="Village Child", level=4, country_code="x", parent_code="02149"),
            ScrapedAdminArea(code="capital_child", name="Actual Capital Child", level=3, country_code="x", parent_code="021"),
        ]
        config = CityConfig(name="Capital", code="city", level=2, entity_type="City", district_types=("City District",), parent_from={1: ("Capital",)}, communes=())

        for keep_communes in (True, False):
            with self.subTest(keep_communes=keep_communes):
                result = apply_configured_cities("x", entities, [replace(config, keep_communes=keep_communes)])
                by_code = {entity.code: entity for entity in result}
                self.assertEqual(by_code["02149"], entities[5])
                self.assertEqual(by_code["02149_child"], entities[6])
                self.assertEqual(by_code["021"].parent_code, "city")
                self.assertEqual(by_code["021"].level, 3)
                self.assertEqual(by_code["capital_child"].level, 4)
                self.assertEqual(by_code["capital_child"].city_merge_status, CITY_MERGE_SOURCE)

    def test_parentless_prefix_descendant_still_follows_source_unit(self):
        entities = [
            ScrapedAdminArea(code="capital", name="Capital", level=1, country_code="x"),
            ScrapedAdminArea(code="021", name="Capital XXI", level=2, country_code="x", parent_code="capital", entity_type="City District"),
            ScrapedAdminArea(code="02101", name="Unlinked Child", level=3, country_code="x"),
        ]
        config = CityConfig(name="Capital", code="city", level=2, entity_type="City", district_types=("City District",), parent_from={1: ("Capital",)}, communes=())

        for keep_communes in (True, False):
            with self.subTest(keep_communes=keep_communes):
                by_code = {entity.code: entity for entity in apply_configured_cities("x", entities, [replace(config, keep_communes=keep_communes)])}
                self.assertEqual(by_code["02101"].level, 4)
                self.assertEqual(by_code["02101"].parent_code, "021")
                self.assertEqual(by_code["02101"].city_merge_status, CITY_MERGE_SOURCE)
