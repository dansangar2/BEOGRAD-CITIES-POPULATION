from dataclasses import replace
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config, upsert_scraping_config


def _row(code, name, status, population, *, qid='', parent=None):
    identifier = f' id="i{code}"' if code else ''
    parent_cell = f'<td class="radm">{parent}</td>' if parent else ''
    return (
        f'<tr><td class="rname"{identifier} data-wd="{qid}" data-area="10">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    parent_header = '<th class="radm">Commune</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class RomaniaConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'romania.toml').read_text(encoding='utf-8')

    def test_plan_uses_41_county_locality_pages_without_the_nonexistent_capital_route(self):
        config = parse_scraping_job_config('romania', self.content)
        self.assertEqual(config.legal_subdivision_level, 3)
        self.assertEqual(len(config.pages), 42)
        self.assertEqual(config.pages[0].path, 'romania/admin')
        self.assertNotIn('romania/localities/bucuresti', [page.path for page in config.pages])
        self.assertTrue(all(page.force_highest_level == 2 for page in config.pages[1:]))

    def test_sql_recipe_preserves_capital_roles_and_homonymous_county_municipality_locality(self):
        upsert_scraping_config('romania', self.content)
        config = parse_scraping_job_config('romania', self.content)
        paths = {'romania/admin', 'romania/localities/arad'}
        config = replace(config, pages=tuple(page for page in config.pages if page.path in paths))
        attach_runtime_config_extensions([config])
        county = _row('RO421', 'Arad', 'County', 100, qid='Q179559')
        capital = _row('RO321', 'București', 'City', 50, qid='Q19660')
        municipality = _row('009262', 'Arad', 'City', 100, qid='Q173591')
        pages = {
            'romania/admin': _table(
                '<tbody class="admin1">' + _row('RO42', 'Vest', 'Development Region', 100) + '</tbody>'
                '<tbody class="admin2">' + county + '</tbody>'
                '<tbody class="admin1">' + _row('RO32', 'București - Ilfov', 'Development Region', 50) + '</tbody>'
                '<tbody class="admin2">' + capital + '</tbody>'
                '<tbody class="admin0">' + _row('', 'Romania', 'Sovereign State', 150) + '</tbody>'
            ),
            'romania/localities/arad': _table('<tbody>' + municipality + '</tbody>', footer=county)
            + _table(
                '<tbody>' + _row('009271', 'Arad', 'Locality', 90, qid='Q173591', parent='Arad')
                + _row('009280', 'Village', 'Locality', 10, parent='Arad') + '</tbody>', table_id='ts',
            ),
        }

        class SavedHtml:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
            page_workers=2, html_fetcher=SavedHtml(),
        ).run(config)

        expected = {
            'RO42': (1, 'romania'), 'RO32': (1, 'romania'),
            'RO421': (2, 'RO42'), 'RO321': (2, 'RO32'),
            '009262': (3, 'RO421'), 'RO321_city': (3, 'RO321'),
            '009271': (4, '009262'), '009280': (4, '009262'),
        }
        self.assertEqual(AdminArea.objects.count(), 15)
        for code, (level, parent) in expected.items():
            area = AdminArea.objects.get(pk=f'romania_{code}')
            self.assertEqual((area.level, area.parent_id), (level, f'romania_{parent}'))
        city = AdminArea.objects.get(pk='romania_RO321_city')
        capital = AdminArea.objects.get(pk='romania_RO321')
        self.assertEqual((city.data_wd, city.pop_latest, city.area_km2), (capital.data_wd, capital.pop_latest, capital.area_km2))
        self.assertEqual(city.data_wd, 'Q19660')
        sector_populations = {
            '179141': 217367, '179150': 291557, '179169': 374737,
            '179178': 268018, '179187': 240288, '179196': 324994,
        }
        for code, population in sector_populations.items():
            sector = AdminArea.objects.get(pk=f'romania_{code}')
            self.assertEqual((sector.level, sector.parent_id), (4, city.id))
            self.assertEqual((sector.entity_type, sector.raw_entity_type), ('Borough', 'Sector'))
            self.assertEqual((sector.pop_latest, str(sector.pop_latest_date), sector.last_census_year), (population, '2021-12-01', 2021))
            self.assertTrue(sector.data_wd)
        self.assertEqual(sum(sector_populations.values()), 1716961)
        self.assertEqual(AdminArea.objects.get(pk='romania_romania').pop_latest, 150)
        for code, children in [('RO321', ['RO321_city']), ('009262', ['009271', '009280']), ('RO321_city', list(sector_populations))]:
            payload = self.client.get(f'/api/admin-areas/romania_{code}/').json()
            self.assertEqual(payload['area']['subdivision_count'], len(children))
            self.assertEqual({child['id'] for child in payload['children']}, {f'romania_{child}' for child in children})
