from dataclasses import replace
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions


def _row(code, name, status, population, parent=None):
    parent_cell = f'<td class="radm">{parent}</td>' if parent else ''
    return (
        f'<tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, footer='', table_id='tl'):
    parent_header = '<th class="radm">District</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2024-12-31">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class _MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class CzechRepublicConfigTests(unittest.TestCase):
    def test_admin_without_infosection_and_regional_pages_keep_territorial_roles(self):
        content = (Path(__file__).resolve().parents[1] / 'subdivisions/czechrep.toml').read_text(encoding='utf-8')
        config = parse_scraping_job_config('czechrep', content)
        self.assertEqual(config.legal_subdivision_level, 3)
        self.assertEqual(config.pages[0].lowest_level, 0)
        self.assertTrue(all(p.lowest_level == 2 for p in config.pages[2:]))
        self.assertEqual(len(config.pages[2:]), 13)
        region = _row('CZ031', 'South Bohemia', 'Region', 100)
        district = _row('CZ0311', 'District A', 'District', 100)
        prague = _row('CZ010', 'Praha', 'Region', 80)
        prague_district = _row('CZ0100', 'Praha', 'City', 80)
        pages = {
            'czechrep/cities': _table('', footer=_row('0', 'Czech Republic', 'Republic', 180)),
            'czechrep/admin': _table(
                f'<tbody class="admin1">{region}</tbody><tbody class="admin2">{district}</tbody>'
                f'<tbody class="admin1">{prague}</tbody><tbody class="admin2">{prague_district}</tbody>'
            ),
            'czechrep/jihoceskykraj': _table(f'<tbody>{district}</tbody>', footer=region)
            + _table('<tbody>' + _row('500001', 'Municipality A', 'Municipality', 100, 'District A') + '</tbody>', table_id='ts'),
        }
        config = replace(config, pages=tuple(p for p in config.pages if p.path in pages))
        for key, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, key, value)

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        repository = _MemoryRepository()
        ScrapeAdminAreas(
            repository, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
            html_fetcher=SavedPages(), page_workers=3,
        ).run(config)
        by_code = {area.code: area for area in repository.entities}
        self.assertEqual(len(by_code), 7)
        for area in repository.entities:
            if area.level:
                self.assertEqual(area.level, by_code[area.parent_code].level + 1)
        self.assertEqual(by_code['CZ0311'].parent_code, 'CZ031')
        self.assertEqual(by_code['500001'].level, 3)
        self.assertEqual(by_code['500001'].parent_code, 'CZ0311')
        self.assertEqual(by_code['CZ0100'].parent_code, 'CZ010')
        self.assertEqual(by_code['CZ0100_city'].parent_code, 'CZ0100')
        self.assertEqual(by_code['CZ0100_city'].pop_latest, 80)
