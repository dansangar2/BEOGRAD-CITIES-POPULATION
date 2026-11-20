from collections import Counter
from pathlib import Path
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population):
    identifier = f' id="i{code}"' if code else ''
    return (
        f'<tr><td class="rname"{identifier}><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td><td class="rpop">{population}</td></tr>'
    )


def _table(body):
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2025-07-01">Population</th></tr></thead>'
        + body + '</table>'
    )


class CanadaConfigTests(unittest.TestCase):
    def test_provincial_pages_without_roots_keep_divisions_and_municipalities_at_their_levels(self):
        content = (Path(__file__).resolve().parents[1] / 'subdivisions/canada.toml').read_text(encoding='utf-8')
        config = parse_scraping_job_config('canada', content)
        national = '<tbody class="admin0">' + _row('', 'Canada', 'Country', 1300) + '</tbody>'
        html_by_path = {}
        expected = {}
        province_codes = ('10', '11', '12', '13', '24', '35', '46', '47', '48', '59', '60', '61', '62')
        self.assertEqual(len(config.pages), 14)
        for page, province in zip(config.pages[1:], province_codes, strict=True):
            name = page.path.split('/')[1]
            province_type = 'Territory' if province in {'60', '61', '62'} else 'Province'
            division, municipality = province + '01', province + '01001'
            province_row = _row(province, name, province_type, 100)
            division_row = _row(division, name + ' division', 'Census Division', 100)
            national += '<tbody class="admin1">' + province_row + '</tbody>'
            national += '<tbody class="admin2">' + division_row + '</tbody>'
            html_by_path[page.path] = _table(
                '<tbody class="admin1">' + division_row + '</tbody>'
                '<tbody class="admin2">' + _row(municipality, name + ' town', 'Town', 100) + '</tbody>'
            )
            expected[province] = (division, municipality)
        html_by_path[config.pages[0].path] = _table(national)

        class SavedHtml:
            def get(self, url):
                return html_by_path[url.removeprefix(config.base_url).strip('/')]

        class MemoryRepository:
            def save_many(self, country_code, entities):
                self.entities = list(entities)
                return len(self.entities), 0

            def delete_missing(self, country_code, ids):
                return 0

        repository = MemoryRepository()
        result = ScrapeAdminAreas(
            repository, [CityPopulationAdminScraper()], html_fetcher=SavedHtml(), page_workers=4,
        ).run(config)
        entities = repository.entities
        self.assertEqual(result.found, 40)
        self.assertEqual(Counter(e.level for e in entities), {0: 1, 1: 13, 2: 13, 3: 13})
        by_code = {e.code: e for e in entities}
        self.assertEqual(len(by_code), len(entities))
        self.assertEqual(by_code['canada'].pop_latest, 1300)
        for province, (division, municipality) in expected.items():
            self.assertEqual(by_code[province].parent_code, 'canada')
            self.assertEqual(by_code[division].parent_code, province)
            self.assertEqual(by_code[municipality].parent_code, division)
            self.assertEqual(by_code[municipality].entity_type, 'Town')
            for code in (province, division, municipality):
                self.assertEqual(by_code[code].pop_latest, 100)
