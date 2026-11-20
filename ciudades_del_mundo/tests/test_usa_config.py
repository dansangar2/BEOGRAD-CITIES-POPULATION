from dataclasses import replace
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import (
    ScrapeAdminAreas,
    ScrapeBlockValidationError,
)
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, parent=None):
    parent_cell = f'<td class="radm">{parent}</td>' if parent else ''
    return (
        f'<tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    parent_header = '<th class="radm">County</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2025-07-01">Population</th></tr></thead>'
        f'<tbody>{body}</tbody><tfoot>{footer}</tfoot></table>'
    )


def _national_page(state, county):
    return (
        '<div class="infosection"><p class="infoname">USA</p></div>'
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2025-07-01">Population</th></tr></thead>'
        f'<tbody class="admin1">{state}</tbody><tbody class="admin2">{county}</tbody></table>'
    )


class _MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class USAConfigTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions/usa.toml'
        self.content = path.read_text(encoding='utf-8')
        self.config = parse_scraping_job_config('usa', self.content)
        counties = [
            ('15001', 'Hawaii', 209790), ('15003', 'Honolulu', 998747),
            ('15005', 'Kalawao', 81), ('15007', 'Kauai', 73840), ('15009', 'Maui', 163688),
        ]
        self.hawaii_html = _table(
            ''.join(_row(code, name, 'County', population) for code, name, population in counties),
            footer=_row('HI', 'Hawaii', 'State', 1446146),
        ) + _table('', table_id='ts')

    def _config_for(self, *paths):
        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path in paths))
        for name, value in parse_runtime_config_extensions(self.content).items():
            object.__setattr__(config, name, value)
        return config

    def _run_pages(self, pages):
        config = self._config_for(*pages)

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        repository = _MemoryRepository()
        ScrapeAdminAreas(repository, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
                         html_fetcher=SavedPages(), page_workers=3).run(config)
        return {area.code: area for area in repository.entities}

    def test_empty_hawaii_city_table_preserves_state_and_all_five_counties(self):
        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path in {'usa/admin', 'usa/hawaii'}))
        hawaii_html = self.hawaii_html
        national_html = _national_page(_row('HI', 'Hawaii', 'State', 1446146),
                                       _row('15001', 'Hawaii', 'County', 209790))

        class SavedPages:
            def get(self, url):
                return national_html if url.endswith('/admin/') else hawaii_html

        repository = _MemoryRepository()
        ScrapeAdminAreas(repository, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
                         html_fetcher=SavedPages(), page_workers=3).run(config)
        by_code = {area.code: area for area in repository.entities}
        self.assertEqual(set(by_code), {'usa', 'HI', '15001', '15003', '15005', '15007', '15009'})
        self.assertEqual(by_code['HI'].level, 1)
        self.assertEqual(by_code['HI'].parent_code, 'usa')
        self.assertEqual(by_code['HI'].pop_latest, 1446146)
        for code in set(by_code) - {'usa', 'HI'}:
            self.assertEqual(by_code[code].level, 2)
            self.assertEqual(by_code[code].parent_code, 'HI')
            self.assertEqual(by_code[code].entity_type, 'County')
        self.assertEqual(by_code['15003'].pop_latest, 998747)
        self.assertEqual(by_code['15005'].pop_latest, 81)

    def test_other_states_still_reject_a_missing_city_section_before_persistence(self):
        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path == 'usa/alabama'))
        hawaii_html = self.hawaii_html

        class SavedPages:
            def get(self, url):
                return hawaii_html

        repository = _MemoryRepository()
        with self.assertRaises(ScrapeBlockValidationError) as raised:
            ScrapeAdminAreas(repository, [CityPopulationCitiesScraper()],
                             html_fetcher=SavedPages(), page_workers=3).run(config)
        self.assertEqual([problem.code for problem in raised.exception.problems], ['SCR-BLOCK-SECTION'])
        self.assertIn("'cities'", raised.exception.problems[0].message)
        self.assertFalse(hasattr(repository, 'entities'))

    def test_ordinary_state_still_has_cities_at_level_three(self):
        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path in {'usa/admin', 'usa/alabama'}))
        html = _table(_row('01001', 'Autauga', 'County', 61464),
                      footer=_row('AL', 'Alabama', 'State', 5157699))
        html += _table(_row('0162328', 'Prattville', 'City', 39902, 'Autauga'), table_id='ts')
        national_html = _national_page(_row('AL', 'Alabama', 'State', 5157699),
                                       _row('01001', 'Autauga', 'County', 61464))

        class SavedPages:
            def get(self, url):
                return national_html if url.endswith('/admin/') else html

        repository = _MemoryRepository()
        ScrapeAdminAreas(repository, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
                         html_fetcher=SavedPages(), page_workers=3).run(config)
        by_code = {area.code: area for area in repository.entities}
        self.assertEqual(by_code['AL'].level, 1)
        self.assertEqual(by_code['01001'].level, 2)
        self.assertEqual(by_code['0162328'].level, 3)
        self.assertEqual(by_code['0162328'].parent_code, '01001')
        self.assertEqual(by_code['0162328'].pop_latest, 39902)

    def test_hawaii_places_fill_missing_localities_under_their_counties(self):
        places = _table(_row('15001', 'Hawaii', 'County', 200635)
                        + _row('15003', 'Honolulu', 'County', 1016507))
        places += _table(_row('1514650', 'Hilo', 'Census-Designated Place', 44186, 'Hawaii')
                         + _row('1571550', 'Honolulu', 'Census-Designated Place', 350956, 'Honolulu'),
                         table_id='ts')
        by_code = self._run_pages({
            'usa/admin': _national_page(_row('HI', 'Hawaii', 'State', 1446146),
                                       _row('15001', 'Hawaii', 'County', 209790)),
            'usa/hawaii': self.hawaii_html,
            'usa/places/hawaii': places,
        })
        for code, county in [('1514650', '15001'), ('1571550', '15003')]:
            self.assertIn(code, by_code)
            self.assertEqual(by_code[code].level, 3)
            self.assertEqual(by_code[code].parent_code, county)
            self.assertEqual(by_code[code].entity_type, 'Census-Designated Place')
        self.assertEqual(by_code['15003'].pop_latest, 998747)
        self.assertEqual(len([e for e in by_code.values() if e.parent_code == 'HI']), 5)

    def test_adding_census_places_preserves_newer_incorporated_population_and_id(self):
        state = _row('AL', 'Alabama', 'State', 5157699)
        county = _row('01001', 'Autauga', 'County', 61464)
        current = _table(county, footer=state)
        current += _table(_row('0162328', 'Prattville', 'City', 39902, 'Autauga'), table_id='ts')
        census = _table(county) + _table(
            _row('0162328', 'Prattville', 'City', 37000, 'Autauga')
            + _row('0100170', 'Autaugaville CDP', 'Census-Designated Place', 1200, 'Autauga'),
            table_id='ts',
        )
        census = census.replace('2025-07-01', '2020-04-01')
        by_code = self._run_pages({
            'usa/admin': _national_page(state, county),
            'usa/alabama': current,
            'usa/places/alabama': census,
        })
        self.assertEqual(by_code['0162328'].id, 'usa_0162328')
        self.assertEqual(by_code['0162328'].pop_latest, 39902)
        self.assertEqual(str(by_code['0162328'].pop_latest_date), '2025-07-01')
        self.assertEqual(by_code['0100170'].parent_code, '01001')
        self.assertEqual(len(by_code), 5)

    def test_mcd_county_code_is_not_confused_with_a_place_code_prefix(self):
        state = _row('IL', 'Illinois', 'State', 12800000)
        county = _row('17031', 'Cook', 'County', 5200000)
        municipal = _national_page(county, _row('1703181100', 'Wheeling', 'Township', 155000))
        municipal = municipal.replace('>USA<', '>Illinois<').replace(
            '</table>', f'<tfoot>{state}</tfoot></table>',
        )
        # A seven-digit place code can prefix an unrelated ten-digit MCD code.
        incorporated = _table(county, footer=state) + _table(
            _row('1703181', 'Aviston', 'Village', 2300, 'Cook'), table_id='ts',
        )
        by_code = self._run_pages({
            'usa/admin': _national_page(state, county),
            'usa/illinois': incorporated,
            'usa/illinois/admin': municipal,
        })
        self.assertEqual(by_code['1703181100'].parent_code, '17031')
        self.assertEqual(by_code['1703181100'].level, 3)
        self.assertEqual(by_code['1703181100'].entity_type, 'Township')
        self.assertEqual(by_code['1703181'].parent_code, '17031')
        self.assertEqual(by_code['1703181'].level, 3)

    def test_census_area_keeps_its_county_role_and_its_own_type(self):
        state = _row('AK', 'Alaska', 'State', 740000)
        counties = _row('02013', 'Aleutians East', 'Borough', 3500)
        counties += _row('02016', 'Aleutians West', 'Census Area', 5200)
        places = _table(counties) + _table(
            _row('0203440', 'Atka', 'Census-Designated Place', 100, 'Aleutians West'),
            table_id='ts',
        )
        by_code = self._run_pages({
            'usa/admin': _national_page(state, counties),
            'usa/places/alaska': places,
        })
        self.assertEqual(by_code['02013'].entity_type, 'Borough')
        self.assertEqual(by_code['02016'].entity_type, 'Census Area')
        self.assertEqual(by_code['02016'].raw_entity_type, 'Census Area')
        self.assertEqual(by_code['02013'].level, 2)
        self.assertEqual(by_code['02016'].level, 2)
        self.assertEqual(by_code['0203440'].parent_code, '02016')
        self.assertEqual(by_code['0203440'].level, 3)

    def test_place_county_column_wins_over_an_unrelated_fips_prefix(self):
        state = _row('MD', 'Maryland', 'State', 6200000)
        counties = _row('24019', 'Dorchester', 'County', 32000)
        counties += _row('24033', "Prince George's", 'County', 967000)
        places = _table(counties) + _table(
            _row('2401925', 'Aquasco', 'Census-Designated Place', 900, "Prince George's"),
            table_id='ts',
        )
        by_code = self._run_pages({
            'usa/admin': _national_page(state, counties),
            'usa/places/maryland': places,
        })
        self.assertEqual(by_code['2401925'].parent_code, '24033')
        self.assertEqual(by_code['2401925'].level, 3)
