from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, *, parent=None, qid=''):
    parent_cell = f'<td class="radm">{parent}</td>' if parent else ''
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    parent_header = '<th class="radm">Municipality</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2021-08-31">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class CroatiaHierarchyTests(unittest.TestCase):
    def test_counties_municipalities_and_settlements_keep_separate_roles(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'croatia.toml'
        config = parse_scraping_job_config('croatia', path.read_text(encoding='utf-8'))
        county = _row('07', 'Bjelovar-Bilogora', 'County', 200, qid='Q58060')
        city_county = _row('22', 'Grad Zagreb', 'County-level City', 100, qid='Q1435')
        municipalities = _row('0183', 'Berek', 'Municipality', 80, qid='Q793317') + _row('0248', 'Bjelovar', 'Town', 120, qid='Q5707')
        city_municipality = _row('0000', 'Grad Zagreb', 'City', 100, qid='Q1435')
        pages = {
            'croatia/cities': _table('', footer=_row('0', 'Croatia', 'Republic', 300)),
            'croatia/admin': _table(
                f'<tbody class="admin1">{county}</tbody><tbody class="admin2">{municipalities}</tbody>'
                f'<tbody class="admin1">{city_county}</tbody><tbody class="admin2">{city_municipality}</tbody>'
            ),
            'croatia/bjelovarbilogora': '<div class="infosection"><p class="infoname">Bjelovar-Bilogora</p></div>'
            + _table(f'<tbody>{municipalities}</tbody>', footer=county)
            + _table('<tbody>' + _row('070183001', 'Berek', 'Settlement', 80, parent='Berek', qid='Q793317')
                     + _row('070248001', 'Bjelovar', 'Settlement', 120, parent='Bjelovar', qid='Q5707') + '</tbody>', table_id='ts'),
            'croatia/gradzagreb': '<div class="infosection"><p class="infoname">Grad Zagreb</p></div>'
            + _table(f'<tbody>{city_municipality}</tbody>', footer=city_county)
            + _table('<tbody>' + _row('220000001', 'Adamovec', 'Settlement', 10, parent='Grad Zagreb')
                     + _row('220000069', 'Zagreb', 'Settlement', 90, parent='Grad Zagreb', qid='Q1435') + '</tbody>', table_id='ts')
            + _table('<tbody>' + _row('district', 'City district', 'City District', 20) + '</tbody>', table_id='districts'),
        }
        config = replace(config, pages=tuple(p for p in config.pages if p.path in pages))
        self.assertEqual(len(config.pages), len(pages))
        saved_html = {config.base_url + key + '/': value for key, value in pages.items()}

        class SavedPages:
            def get(self, url):
                return saved_html[url]

        for workers in (1, 3):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, 'get', side_effect=saved_html.__getitem__):
                repo = MemoryRepository()
                ScrapeAdminAreas(
                    repo, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                by_code = {e.code: e for e in repo.entities}
                self.assertEqual(len(by_code), 10)
                expected = {
                    '07': (1, 'croatia'), '22': (1, 'croatia'),
                    '0183': (2, '07'), '0248': (2, '07'), '0000': (2, '22'),
                    '070183001': (3, '0183'), '070248001': (3, '0248'),
                    '220000001': (3, '0000'), '220000069': (3, '0000'),
                }
                for code, hierarchy in expected.items():
                    self.assertEqual((by_code[code].level, by_code[code].parent_code), hierarchy)
                self.assertEqual(by_code['22'].entity_type, 'County-level City')
                self.assertEqual(by_code['0000'].entity_type, 'City')
                self.assertEqual(by_code['220000069'].pop_latest, 90)
                self.assertNotIn('district', by_code)
