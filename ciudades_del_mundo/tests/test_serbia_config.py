from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions


def _row(code, name, status, population, *, parent=None, qid='', area=None):
    parent_cell = f'<td class="radm" data-admid="{parent}">{parent}</td>' if parent is not None else ''
    area_attribute = f' data-area="{area}"' if area is not None else ''
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}"{area_attribute}><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, footer='', *, table_id='tl'):
    parent_header = '<th class="radm">Parent</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2024-06-30">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class SerbiaConfigTests(unittest.TestCase):
    def _run_pages(self, html):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'serbia.toml'
        content = path.read_text(encoding='utf-8')
        config = parse_scraping_job_config('serbia', content)
        config = replace(config, pages=tuple(page for page in config.pages if page.path.rstrip('/') in {key.rstrip('/') for key in html}), cities=[])
        for name, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, name, value)
        self.assertEqual(len(config.pages), len(html))

        class SavedPages:
            def get(self, url):
                key = url if url in html else url.removeprefix(config.base_url).strip('/')
                return html[key]

        class MemoryRepository:
            def save_many(self, country_code, entities):
                self.entities = entities
                return len(entities), 0

            def delete_missing(self, country_code, ids):
                return 0

        repository = MemoryRepository()
        ScrapeAdminAreas(
            repository, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=3, html_fetcher=SavedPages(),
        ).run(config)
        return {entity.code: entity for entity in repository.entities}

    def test_regional_municipalities_stay_at_l2_and_settlements_at_l3(self):
        district = _row('17', 'Bor', 'District', 50)
        municipality = _row('M20791', 'Bor', 'Municipality', 50)
        entities = self._run_pages({
            'serbia/cities': _table('', _row('0', 'Serbia', 'Republic', 50)),
            'serbia/admin': _table('<tbody class="admin1">' + district + '</tbody><tbody class="admin2">' + municipality + '</tbody>'),
            'serbia/bor': _table('<tbody>' + municipality + '</tbody>', district)
            + _table('<tbody>' + _row('700001', 'Example village', 'Settlement', 20, parent='M20791') + '</tbody>', table_id='ts'),
        })
        self.assertEqual(len(entities), 4)
        self.assertEqual((entities['M20791'].level, entities['M20791'].parent_code), (2, '17'))
        self.assertEqual((entities['700001'].level, entities['700001'].parent_code), (3, 'M20791'))

    def test_kosovo_unknown_municipality_keeps_district_parent_and_l3(self):
        entities = self._run_pages({
            'serbia/cities': _table('', _row('0', 'Serbia', 'Republic', 100)),
            'https://www.citypopulation.de/en/kosovo/admin/': _table(
                '<tbody class="admin1">' + _row('6', 'Prishtine', 'District', 80, qid='Q600') + '</tbody>'
                '<tbody class="admin2">' + _row('09', 'Fushe Kosove', 'Municipality', 80) + '</tbody>'
                '<tbody class="admin1">' + _row('7', 'Prizren', 'District', 20, qid='Q700') + '</tbody>'
                '<tbody class="admin2">' + _row('32', 'Mamushe', 'Municipality', 20) + '</tbody>'
            ),
            'https://www.citypopulation.de/en/kosovo/cities/': _table(
                '<tbody>' + _row('8151', 'Prishtine', 'District', 80, qid='Q600')
                + _row('8152', 'Prizren', 'District', 20, qid='Q700') + '</tbody>'
            ) + _table('<tbody>' + _row('32571', 'Bresje', 'Settlement', 10, parent='8151')
                + _row('32573', 'Mamushe', 'Urban Settlement', 30, parent='8152') + '</tbody>', table_id='ts'),
        })
        self.assertEqual(len(entities), 7)
        self.assertEqual((entities['6'].level, entities['6'].parent_code), (1, 'serbia'))
        self.assertEqual((entities['32'].level, entities['32'].parent_code), (2, '7'))
        self.assertEqual((entities['32571'].level, entities['32571'].parent_code), (3, '6'))
        self.assertEqual((entities['32573'].level, entities['32573'].parent_code), (3, '32'))

    def test_kosovo_districts_add_population_and_area_to_serbia_once(self):
        html = {
            'serbia/cities': _table('', _row('0', 'Serbia', 'Republic', 100, area=1000)),
            'serbia/admin': _table(
                '<tbody class="admin1">' + _row('17', 'Bor', 'District', 100, area=1000) + '</tbody>'
                '<tbody class="admin2">' + _row('M20791', 'Bor', 'Municipality', 100, area=1000) + '</tbody>'
            ),
            'https://www.citypopulation.de/en/kosovo/admin/': _table(
                '<tbody class="admin1">' + _row('6', 'Prishtine', 'District', 80, area=8, qid='Q600') + '</tbody>'
                '<tbody class="admin2">' + _row('09', 'Fushe Kosove', 'Municipality', 80, area=8) + '</tbody>'
                '<tbody class="admin1">' + _row('7', 'Prizren', 'District', 20, area=2, qid='Q700') + '</tbody>'
                '<tbody class="admin2">' + _row('32', 'Mamushe', 'Municipality', 20, area=2) + '</tbody>'
            ),
            'https://www.citypopulation.de/en/kosovo/cities/': _table(
                '<tbody>' + _row('8151', 'Prishtine', 'District', 80, area=8, qid='Q600')
                + _row('8152', 'Prizren', 'District', 20, area=2, qid='Q700') + '</tbody>'
            ) + _table('<tbody>' + _row('32573', 'Mamushe', 'Urban Settlement', 10, parent='8152')
                + '</tbody>', table_id='ts'),
        }
        for _ in range(2):
            entities = self._run_pages(html)
            root = entities['serbia']
            self.assertEqual((root.pop_latest, root.area_km2), (200, Decimal('1010')))
            self.assertEqual(root.density, Decimal(200) / Decimal(1010))
            self.assertEqual(len(entities), 8)
            self.assertEqual((entities['6'].pop_latest, entities['6'].area_km2), (80, 8))
            self.assertEqual((entities['32'].pop_latest, entities['32'].area_km2), (20, 2))
            self.assertEqual((entities['32573'].level, entities['32573'].parent_code), (3, '32'))

    def test_wikidata_override_preserves_distinct_obilic_and_obiliq(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'serbia.toml'
        config = parse_scraping_job_config('serbia', path.read_text(encoding='utf-8'))
        page = next(page for page in config.pages if page.path == 'serbia/jablanica')
        html = _table('<tbody>' + _row('M24200', 'Bojnik', 'Municipality', 100) + '</tbody>')
        html += _table('<tbody>' + _row('24382', 'Obilic', 'Settlement', 42, parent='M24200', qid='Q608274') + '</tbody>', table_id='ts')
        entities = CityPopulationCitiesScraper().scrape_configured_html(html, 'https://www.citypopulation.de/en/serbia/jablanica/', 'serbia', page)
        locality = next(entity for entity in entities if entity.code == '24382')
        self.assertEqual(locality.data_wd, 'Q2749459')
        self.assertEqual(locality.pop_latest, 42)
        self.assertEqual((locality.level, locality.parent_code), (3, 'M24200'))
        other = replace(page, wikidata_overrides={})
        original = CityPopulationCitiesScraper().scrape_configured_html(html, 'https://www.citypopulation.de/en/serbia/jablanica/', 'serbia', other)
        self.assertEqual(next(entity for entity in original if entity.code == '24382').data_wd, 'Q608274')

    def test_admin_without_infosection_keeps_districts_and_municipalities(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'serbia.toml'
        config = parse_scraping_job_config('serbia', path.read_text(encoding='utf-8'))
        pages = tuple(page for page in config.pages if page.block_index < 2)
        self.assertEqual(len(pages), 2)
        config = replace(config, pages=pages, cities=[])
        html = {
            'serbia/cities': _table('', _row('0', 'Serbia', 'Republic', 150)),
            'serbia/admin': _table(
                '<tbody class="admin1">' + _row('17', 'Bor', 'District', 50) + '</tbody>'
                '<tbody class="admin2">' + _row('M20791', 'Bor', 'Municipality', 50) + '</tbody>'
                '<tbody class="admin1">' + _row('01', 'Grad Beograd', 'District', 100) + '</tbody>'
                '<tbody class="admin2">' + _row('M00029', 'Barajevo', 'City Municipality', 40)
                + _row('M00778', 'Novi Beograd', 'City Municipality', 60) + '</tbody>'
            ),
        }

        class SavedPages:
            def get(self, url):
                return html[url.removeprefix(config.base_url).strip('/')]

        class MemoryRepository:
            def save_many(self, country_code, entities):
                self.entities = entities
                return len(entities), 0

            def delete_missing(self, country_code, ids):
                return 0

        repository = MemoryRepository()
        ScrapeAdminAreas(
            repository, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=3, html_fetcher=SavedPages(),
        ).run(config)
        entities = {entity.code: entity for entity in repository.entities}
        self.assertEqual(len(entities), 6)
        self.assertEqual(entities['serbia'].level, 0)
        for code in ('17', '01'):
            self.assertEqual((entities[code].level, entities[code].parent_code), (1, 'serbia'))
        for code, parent in (('M20791', '17'), ('M00029', '01'), ('M00778', '01')):
            self.assertEqual((entities[code].level, entities[code].parent_code), (2, parent))
