from dataclasses import replace
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.application.citypopulation_linking import apply_runtime_config_extensions
from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, parent=None, qid=''):
    parent_cell = f'<td class="radm">{parent}</td>' if parent is not None else ''
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, table_id='tl', footer=''):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        + ('<th>County</th>' if table_id == 'ts' else '')
        + '<th class="rpop" data-coldate="2025-12-31">Population</th></tr></thead>'
        + body + f'<tfoot>{footer}</tfoot></table>'
    )


def _municipal_page(state, districts, municipalities):
    return _table('<tbody>' + districts + '</tbody>', footer=state) + _table(
        '<tbody>' + municipalities + '</tbody>', table_id='ts',
    )


class _MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class GermanyConfigTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'germany.toml'
        content = path.read_text(encoding='utf-8')
        self.config = parse_scraping_job_config('germany', content)
        for name, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(self.config, name, value)

    def test_plan_covers_every_state_and_keeps_the_existing_legal_level(self):
        self.assertEqual(self.config.legal_subdivision_level, 2)
        municipal_pages = [p for p in self.config.pages if p.include_cities and p.include_infosection]
        self.assertEqual(len(municipal_pages), 16)
        self.assertEqual({p.force_highest_level for p in municipal_pages}, {1})
        self.assertTrue(all(p.preserve_source_codes for p in municipal_pages))
        self.assertEqual(len([p for p in self.config.pages if '/settlements/' in p.path]), 16)
        self.assertEqual(len([p for p in self.config.pages if p.path.endswith('/admin') and p.force_highest_level == 4]), 15)

    def test_pipeline_preserves_municipal_roles_and_attaches_settlements_and_boroughs(self):
        bayern = _row('09', 'Bayern', 'State', 100)
        berlin = _row('11', 'Berlin', 'State', 80, qid='Q64')
        hamburg = _row('02', 'Hamburg', 'State', 60, qid='Q1055')
        roth = _row('09576', 'Roth', 'County', 100)
        berlin_district = _row('11000', 'Berlin', 'County-level City', 80, qid='Q64')
        hamburg_district = _row('02000', 'Hamburg', 'County-level City', 60, qid='Q1055')
        html = {
            'germany/cities': _table('', footer=_row('0', 'Germany', 'Federal Republic', 240)),
            'germany/admin': '<div class="infosection"><p class="infoname">Germany</p></div>' + _table(
                '<tbody class="admin1">' + bayern + '</tbody><tbody class="admin2">' + roth
                + '</tbody><tbody class="admin1">' + berlin + '</tbody><tbody class="admin2">'
                + berlin_district + '</tbody><tbody class="admin1">' + hamburg
                + '</tbody><tbody class="admin2">' + hamburg_district + '</tbody>',
            ),
            'germany/bayern': _municipal_page(bayern, roth, _row('09576111', 'Abenberg', 'City', 100, 'Roth', 'Q261822')),
            'germany/berlin': _municipal_page(berlin, berlin_district, _row('11000000', 'Berlin', 'County-level City', 80, 'Berlin', 'Q64')),
            'germany/hamburg': _municipal_page(hamburg, hamburg_district, _row('02000000', 'Hamburg', 'County-level City', 60, 'Hamburg', 'Q1055')),
            'germany/settlements/bayern': _municipal_page(bayern, roth,
                _row('09576111x0', 'Abenberg', 'Settlement', 70, 'Roth', 'Q261822')
                + _row('09576111x0001', 'Other settlement', 'Settlement', 30, 'Roth')),
            'germany/berlin/admin': _table('<tbody class="admin1">' + _row('B01', 'Mitte', 'Borough', 80)
                + '</tbody><tbody class="admin2">' + _row('B0106', 'Gesundbrunnen', 'Quarter', 80) + '</tbody>'),
            'germany/hamburg/admin': _table('<tbody class="admin1">' + _row('HH2', 'Altona', 'Borough', 60)
                + '</tbody><tbody class="admin2">' + _row('HH201', 'Altona-Altstadt', 'Quarter', 60) + '</tbody>'),
        }
        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path in html))
        for name, value in vars(self.config).items():
            if name.startswith('runtime_'):
                object.__setattr__(config, name, value)

        class SavedPages:
            def get(self, url):
                return html[url.removeprefix(config.base_url).strip('/')]

        repo = _MemoryRepository()
        ScrapeAdminAreas(repo, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=3, html_fetcher=SavedPages()).run(config)
        by_code = {e.code: e for e in repo.entities}
        for entity in repo.entities:
            if entity.level:
                self.assertEqual(by_code[entity.parent_code].level + 1, entity.level)
        for code, parent in [('11000000', '11000'), ('02000000', '02000'), ('09576111', '09576'),
                             ('09576111x0', '09576111'), ('09576111x0001', '09576111'),
                             ('B01', '11000000'), ('B0106', 'B01'), ('HH2', '02000000'), ('HH201', 'HH2')]:
            self.assertEqual(by_code[code].parent_code, parent)
        self.assertEqual(by_code['11000000'].level, 3)
        self.assertEqual(by_code['B01'].level, 4)
        self.assertEqual(by_code['B0106'].level, 5)
        self.assertEqual(by_code['09576111x0'].pop_latest, 70)

    def test_historical_settlements_follow_confirmed_mergers_and_the_split_of_rodeberg(self):
        parents = {
            '06635005x0': '06635001', '14522450x0': '14522275', '14522620x0YZE': '14522275',
            '01059141x0': '01059126', '16076004x0': '16076094', '16076084x0': '16076094',
            '16061007x0': '16061119', '16061065x0': '16061119', '16061067x0': '16061119',
            '16061096x0': '16061119', '16061097x0': '16061119', '16061111x0': '16061119',
            '16064055x0M20': '16064046', '16064055x0YP1': '16061118', '16063028x0': '16063104',
            '16076026x0': '16076003', '16067036x0': '16067092', '16061049x0CWE': '16061045',
            '16064072x0': '16064071', '16064058x0': '16064076', '16066073x0': '16066042',
        }
        rows = [ScrapedAdminArea(code=code, name=code, level=5, country_code='germany', pop_latest=350)
                for code in parents]
        actual = apply_runtime_config_extensions(self.config, rows)
        self.assertEqual({e.code: e.parent_code for e in actual}, parents)
        self.assertEqual({e.level for e in actual}, {4})
        self.assertTrue(all(e.pop_latest == 350 for e in actual))

    def test_trudering_riem_is_loaded_from_its_detail_page_without_duplicating_its_quarters(self):
        page = next(p for p in self.config.pages if p.path.endswith('/M15__trudering_riem'))
        html = _table('<tbody class="admin1">' + _row('M15', 'Trudering - Riem', 'Borough', 75, qid='Q538037')
                      + '</tbody><tbody class="admin2">' + _row('M151', 'Trudering - Riem', 'Quarter', 30) + '</tbody>')
        rows = CityPopulationAdminScraper().scrape_configured_html(
            html, self.config.base_url + page.path + '/', 'germany', page,
        )
        self.assertEqual(len(rows), 1)
        row = apply_runtime_config_extensions(self.config, rows)[0]
        self.assertEqual((row.code, row.level, row.parent_code, row.entity_type),
                         ('M15', 4, '09162000', 'Borough'))
        self.assertEqual((row.pop_latest, row.data_wd), (75, 'Q538037'))
