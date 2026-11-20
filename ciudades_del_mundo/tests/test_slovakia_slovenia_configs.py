from dataclasses import replace
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, parent=None):
    parent_cell = f'<td class="radm">{parent}</td>' if parent is not None else ''
    return (
        f'<tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    parent_header = '<th class="radm">Parent</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class _MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class CentralEuropeConfigTests(unittest.TestCase):
    def _run_seed(self, slug, html, *, cities=None):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / f'{slug}.toml'
        config = parse_scraping_job_config(slug, path.read_text(encoding='utf-8'))
        selected = tuple(page for page in config.pages if page.path in html)
        self.assertEqual(len(selected), len(html))
        config = replace(config, pages=selected, cities=config.cities if cities is None else cities)

        class SavedPages:
            def get(self, url):
                return html[url.removeprefix(config.base_url).strip('/')]

        repo = _MemoryRepository()
        ScrapeAdminAreas(
            repo, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=3, html_fetcher=SavedPages(),
        ).run(config)
        by_code = {entity.code: entity for entity in repo.entities}
        for entity in repo.entities:
            if entity.level:
                self.assertEqual(entity.level, by_code[entity.parent_code].level + 1)
        return by_code

    def test_slovakia_seed_keeps_regions_at_l1_and_builds_configured_city(self):
        root = '<div class="infosection"><p class="infoname">Slovakia</p></div>'
        region = _row('1', 'Bratislavský kraj', 'Region', 100)
        districts = _row('101', 'Bratislava I', 'City District', 40) + _row('106', 'Malacky', 'District', 60)
        html = {
            'slovakia/cities': _table('', footer=_row('0', 'Slovakia', 'Republic', 100)),
            'slovakia/admin': root + _table(f'<tbody class="admin1">{region}</tbody><tbody class="admin2">{districts}</tbody>'),
            'slovakia/bratislavskykraj': _table(f'<tbody>{districts}</tbody>', footer=region)
            + _table('<tbody>' + _row('528000', 'Bratislava', 'City', 40, '/') + _row('500000', 'Village', 'Municipality', 60, 'Malacky') + '</tbody>', table_id='ts'),
        }
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'slovakia.toml'
        city = parse_scraping_job_config('slovakia', path.read_text(encoding='utf-8')).cities[0]

        by_code = self._run_seed('slovakia', html, cities=[city])

        self.assertEqual(len(by_code), 6)
        self.assertEqual(by_code['slovakia'].level, 0)
        self.assertEqual(by_code['1'].level, 1)
        self.assertEqual(by_code['528000'].parent_code, '1')
        self.assertEqual(by_code['528000'].level, 2)
        self.assertEqual(by_code['528000'].pop_latest, 40)
        self.assertEqual(by_code['101'].parent_code, '528000')
        self.assertEqual(by_code['500000'].parent_code, '106')

    def test_slovenia_seed_uses_real_region_route_and_keeps_municipality_parent(self):
        root = '<div class="infosection"><p class="infoname">Slovenija</p></div>'
        region = _row('10', 'Primorsko-notranjska', 'Region', 100)
        municipality = _row('150', 'Bloke', 'Municipality', 100)
        html = {
            'slovenia/admin': root + _table(f'<tbody class="admin1">{region}</tbody><tbody class="admin2">{municipality}</tbody>'),
            'slovenia/notranjskokraska': _table(f'<tbody>{municipality}</tbody>', footer=region)
            + _table('<tbody>' + _row('150005', 'Nova vas', 'Settlement', 100, 'Bloke') + '</tbody>', table_id='ts'),
        }

        by_code = self._run_seed('slovenia', html)

        self.assertEqual(len(by_code), 4)
        self.assertEqual(by_code['10'].level, 1)
        self.assertEqual(by_code['150'].parent_code, '10')
        self.assertEqual(by_code['150005'].parent_code, '150')
        self.assertEqual(by_code['150005'].level, 3)
