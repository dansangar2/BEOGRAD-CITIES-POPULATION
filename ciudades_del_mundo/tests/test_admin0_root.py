from collections import Counter
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.domain import parse_pages, ScrapingJobConfig
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesAdminScraper


def _row(code, name, status, population):
    identifier = f' id="i{code}"' if code else ''
    return (
        f'<tr><td class="rname"{identifier}><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td><td class="rpop">{population}</td></tr>'
    )


def _html(*, code='', root_first=False):
    root = '<tbody class="admin0">' + _row(code, 'Example', 'Sovereign State', '1,000') + '</tbody>'
    children = (
        '<tbody class="admin1">' + _row('A', 'Region', 'Development Region', '1,000') + '</tbody>'
        '<tbody class="admin2">' + _row('AA', 'County', 'County', '1,000') + '</tbody>'
    )
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        + (root + children if root_first else children + root) + '</table>'
    )


class Admin0RootTests(unittest.TestCase):
    def _page(self, **options):
        return parse_pages(
            [{'source': 'admin', 'path': ['admin'], **options}], slug='example', schema_version=2,
        )[0]

    def _parse(self, html, page):
        scraper = CityPopulationCitiesAdminScraper() if page.html_format == 'citiesadmin' else CityPopulationAdminScraper()
        return scraper.scrape_configured_html(
            html=html, url='https://www.citypopulation.de/en/example/admin/',
            country_code='example', page=page,
        )

    def test_admin0_total_is_country_root_once_with_or_without_identifier(self):
        for code in ('', 'NATIONAL'):
            for root_first in (False, True):
                for source in ('admin', 'citiesadmin'):
                    with self.subTest(code=code, root_first=root_first, source=source):
                        page = self._page(source=source, include={'infosection': True, 'admin1': True, 'admin2': True})
                        entities = self._parse(_html(code=code, root_first=root_first), page)
                        self.assertEqual([e.code for e in entities], ['example', 'A', 'AA'])
                        root, region, county = entities
                        self.assertEqual((root.level, root.name, root.entity_type), (0, 'Example', 'Sovereign State'))
                        self.assertEqual((root.pop_latest, root.pop_latest_date), (1000, '2025-01-01'))
                        self.assertEqual((region.level, region.parent_code), (1, 'example'))
                        self.assertEqual((county.level, county.parent_code), (2, 'A'))

    def test_excluded_root_does_not_turn_admin0_into_a_subdivision(self):
        page = self._page(include={'infosection': False, 'admin1': True, 'admin2': True})
        entities = self._parse(_html(code='NATIONAL'), page)
        self.assertEqual([e.code for e in entities], ['A', 'AA'])

    def test_national_total_is_not_used_as_a_county_root(self):
        entities = self._parse(_html(), self._page(force_highest_level=2))
        self.assertNotIn('example', [e.code for e in entities])

    def test_country_block_passes_validation_linking_and_simulated_persistence(self):
        page = self._page()
        config = ScrapingJobConfig(
            slug='example', country_code='example', base_url='https://www.citypopulation.de/en/', pages=(page,),
        )

        class SavedHtml:
            def get(self, url):
                return _html()

        class MemoryRepository:
            def save_many(self, country_code, entities):
                self.entities = entities
                return len(entities), 0

            def delete_missing(self, country_code, ids):
                return 0

        repository = MemoryRepository()
        ScrapeAdminAreas(repository, [CityPopulationAdminScraper()], page_workers=2, html_fetcher=SavedHtml()).run(config)
        self.assertEqual(Counter(e.level for e in repository.entities), {0: 1, 1: 1, 2: 1})
