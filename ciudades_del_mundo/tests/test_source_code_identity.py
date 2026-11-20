from dataclasses import replace
import unittest

from ciudades_del_mundo.application.citypopulation_linking import normalize_citypopulation_entities
from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping import CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _area(code, name, level, parent=None, *, qid='', population=None, section='minor_subdivision'):
    return ScrapedAdminArea(
        code=code, name=name, level=level, country_code='example', parent_code=parent,
        data_wd=qid, pop_latest=population,
        url=f'https://www.citypopulation.de/en/example/region/admin/{code}/',
        annotations=f'CityPopulation section: {section}; Forced highest level: {level}',
    )


def _roots():
    return [
        _area('example', 'Country', 0),
        _area('region', 'Region', 1, 'example'),
        _area('county_a', 'County A', 2, 'region'),
        _area('county_b', 'County B', 2, 'region'),
    ]


class SourceCodeIdentityTests(unittest.TestCase):
    def _normalize(self, rows, *, preserve=True):
        if preserve:
            rows = [replace(row, preserve_source_code=True) for row in rows]
        result = normalize_citypopulation_entities('example', rows)
        return {row.code: row for row in result}

    def test_homonymous_communes_in_different_counties_keep_their_codes_and_children(self):
        rows = _roots() + [
            _area('commune_a', 'Czarna', 3, 'county_a', qid='Q1', population=100),
            _area('commune_b', 'Czarna', 3, 'county_b', qid='Q2', population=200),
            _area('village_a', 'Village A', 4, 'commune_a', section='cities'),
            _area('village_b', 'Village B', 4, 'commune_b', section='cities'),
        ]
        result = self._normalize(rows)
        self.assertEqual(set(result), {row.code for row in rows})
        self.assertEqual((result['commune_a'].parent_code, result['commune_a'].pop_latest), ('county_a', 100))
        self.assertEqual((result['commune_b'].parent_code, result['commune_b'].pop_latest), ('county_b', 200))
        self.assertEqual(result['village_a'].parent_code, 'commune_a')
        self.assertEqual(result['village_b'].parent_code, 'commune_b')

    def test_distinct_localities_with_shared_name_and_qid_keep_their_population_rows(self):
        rows = _roots() + [
            _area('commune', 'Commune', 3, 'county_a'),
            replace(_area('village', 'Radzikow', 4, 'commune', qid='Q3', population=100, section='cities'), entity_type='Village'),
            replace(_area('settlement', 'Radzikow', 4, 'commune', qid='Q3', population=20, section='cities'), entity_type='Settlement'),
        ]
        result = self._normalize(rows)
        self.assertEqual((result['village'].pop_latest, result['settlement'].pop_latest), (100, 20))
        self.assertEqual((result['village'].entity_type, result['settlement'].entity_type), ('Village', 'Settlement'))

    def test_repeated_source_code_still_deduplicates(self):
        commune = _area('commune', 'Commune', 3, 'county_a', qid='Q4')
        rows = _roots() + [commune, replace(commune, url=commune.url + 'repeat/')]
        result = self._normalize(rows)
        self.assertEqual(len(result), len(_roots()) + 1)
        self.assertEqual(result['commune'].parent_code, 'county_a')

    def test_commune_and_its_locality_keep_distinct_roles_with_shared_qid(self):
        rows = _roots() + [
            _area('commune', 'City', 3, 'county_a', qid='Q5'),
            _area('locality', 'City', 4, 'commune', qid='Q5', section='cities'),
        ]
        result = self._normalize(rows)
        self.assertEqual((result['commune'].level, result['locality'].level), (3, 4))
        self.assertEqual(result['locality'].parent_code, 'commune')

    def test_repeated_code_retains_newer_metrics_with_the_valid_parent_and_source_type(self):
        old = replace(_area('city', 'City', 4, 'commune', qid='Q13', population=100, section='cities'),
                      entity_type='Urban Place', pop_latest_date='2021-03-31')
        recent = replace(old, parent_code=None, pop_latest=90, pop_latest_date='2025-12-31', entity_type='City')
        result = self._normalize(_roots() + [_area('commune', 'Commune', 3, 'county_a'), old, recent])
        self.assertEqual((result['city'].pop_latest, result['city'].pop_latest_date), (90, '2025-12-31'))
        self.assertEqual((result['city'].parent_code, result['city'].entity_type), ('commune', 'Urban Place'))

    def test_numeric_code_reuse_between_commune_and_locality_gets_stable_scoped_id(self):
        rows = _roots() + [
            _area('0414032', 'Commune', 3, 'county_a', qid='Q6'),
            _area('other_commune', 'Other', 3, 'county_b'),
            _area('0414032', 'Village', 4, 'other_commune', qid='Q7', section='cities'),
        ]
        rows.append(replace(rows[-1], url=rows[-1].url + 'repeat/'))
        result = self._normalize(rows)
        self.assertEqual(len(result), len(_roots()) + 3)
        self.assertEqual(result['0414032'].parent_code, 'county_a')
        locality = result['other_commune_0414032']
        self.assertEqual((locality.level, locality.parent_code), (4, 'other_commune'))

    def test_default_matching_still_merges_alias_codes_by_qid(self):
        rows = _roots() + [
            _area('commune', 'Commune', 3, 'county_a'),
            _area('first', 'Village', 4, 'commune', qid='Q8', section='cities'),
            _area('second', 'Village', 4, 'commune', qid='Q8', section='cities'),
        ]
        result = self._normalize(rows, preserve=False)
        self.assertEqual(len([row for row in result.values() if row.level == 4]), 1)

    def test_locality_code_collision_does_not_redirect_later_children_of_original_commune(self):
        rows = _roots() + [
            _area('0804023', 'Bytom', 3, 'county_a', qid='Q10'),
            _area('0605072', 'Other commune', 3, 'county_b'),
            _area('0804023', 'Radwanowka', 4, '0605072', qid='Q11', section='cities'),
            _area('0988359', 'Bytom', 4, '0804023', qid='Q12', section='cities'),
        ]
        result = self._normalize(rows)
        self.assertEqual(result['0605072_0804023'].parent_code, '0605072')
        self.assertEqual((result['0988359'].parent_code, result['0988359'].level), ('0804023', 4))

    def test_locality_only_page_uses_commune_table_as_context_and_propagates_option(self):
        content = '''scrape_schema_version = 2
[[pages]]
source = "cities"
path = ["localities/region"]
include = { infosection = false, major_subdivision = false, cities = true }
force_highest_level = 4
table_levels = { tl = 3, ts = 4 }
preserve_source_codes = true
'''
        config = parse_scraping_job_config('example', content)
        page = config.pages[0]
        self.assertTrue(page.preserve_source_codes)
        html = '''<table id="tl"><thead><tr><th>Name</th><th>Status</th>
<th class="rpop" data-coldate="2021-03-31">Population</th></tr></thead><tbody>
<tr><td class="rname" id="icommune"><span itemprop="name">Commune</span></td>
<td class="rstatus">Rural Commune</td><td class="rpop">10</td></tr></tbody></table>
<table id="ts"><thead><tr><th>Name</th><th>Status</th><th class="radm">Commune</th>
<th class="rpop" data-coldate="2021-03-31">Population</th></tr></thead><tbody>
<tr><td class="rname" id="ivillage"><span itemprop="name">Village</span></td>
<td class="rstatus">Village</td><td class="radm" data-admid="commune">Commune</td>
<td class="rpop">10</td></tr></tbody></table>'''
        entities = CityPopulationCitiesScraper().scrape_configured_html(
            html, 'https://www.citypopulation.de/en/example/localities/region/', 'example', page,
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual((entities[0].code, entities[0].level, entities[0].parent_code), ('village', 4, 'commune'))
        self.assertTrue(entities[0].preserve_source_code)
        renamed_page = replace(page, code_overrides={'commune': 'stored_commune', 'village': 'stored_village'})
        renamed = CityPopulationCitiesScraper().scrape_configured_html(
            html, 'https://www.citypopulation.de/en/example/localities/region/', 'example', renamed_page,
        )
        self.assertEqual((renamed[0].code, renamed[0].parent_code), ('stored_village', 'stored_commune'))
