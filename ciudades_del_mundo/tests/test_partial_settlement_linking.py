from dataclasses import replace
import unittest

from ciudades_del_mundo.application.citypopulation_linking import normalize_citypopulation_entities
from ciudades_del_mundo.domain import ScrapedAdminArea


class PartialSettlementLinkingTests(unittest.TestCase):
    def test_parts_with_shared_qid_keep_each_municipality_and_population(self):
        areas = [
            ScrapedAdminArea(code='country', name='Country', level=0, country_code='x'),
            ScrapedAdminArea(code='region', name='Region', level=1, country_code='x', parent_code='country'),
            ScrapedAdminArea(code='034', name='Hrastnik', level=2, country_code='x', parent_code='region'),
            ScrapedAdminArea(code='129', name='Trbovlje', level=2, country_code='x', parent_code='region'),
            ScrapedAdminArea(code='034004', name='Čeče', level=3, country_code='x', parent_code='034', entity_type='Settlement (part)', raw_entity_type='Settlement (part)', data_wd='Q2500718', pop_latest=316, annotations='CityPopulation section: cities'),
            ScrapedAdminArea(code='129002', name='Čeče', level=3, country_code='x', parent_code='129', entity_type='Settlement (part)', raw_entity_type='Settlement (part)', data_wd='Q2500718', pop_latest=177, annotations='CityPopulation section: cities'),
        ]
        # Re-reading a page must still deduplicate the very same source part.
        areas.append(replace(areas[-1], url='https://example.test/part/129002/'))

        by_code = {area.code: area for area in normalize_citypopulation_entities('x', areas)}

        self.assertEqual(set(by_code), {'country', 'region', '034', '129', '034004', '129002'})
        for code, parent, population in [('034004', '034', 316), ('129002', '129', 177)]:
            self.assertEqual(by_code[code].parent_code, parent)
            self.assertEqual(by_code[code].pop_latest, population)
            self.assertEqual(by_code[code].data_wd, 'Q2500718')
            self.assertEqual(by_code[code].level, 3)

    def test_whole_settlement_duplicates_still_match_by_qid(self):
        areas = [
            ScrapedAdminArea(code='country', name='Country', level=0, country_code='x'),
            ScrapedAdminArea(code='parent', name='Municipality', level=1, country_code='x', parent_code='country'),
            ScrapedAdminArea(code='short', name='Settlement', level=2, country_code='x', parent_code='parent', entity_type='Settlement', data_wd='Q2500718', pop_latest=493),
            ScrapedAdminArea(code='long_code', name='Settlement', level=2, country_code='x', parent_code='parent', entity_type='Settlement', data_wd='Q2500718', pop_latest=493),
        ]

        result = normalize_citypopulation_entities('x', areas)

        self.assertEqual(len(result), 3)
        settlement = next(area for area in result if area.level == 2)
        self.assertEqual(settlement.pop_latest, 493)
        self.assertEqual(settlement.parent_code, 'parent')
