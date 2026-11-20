import json
from pathlib import Path
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


def _row(code, name, status, population, *, qid='', district=''):
    adm = f'<td class="radm">{district}</td>' if district else ''
    return (f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
            f'<td class="rstatus">{status}</td>{adm}<td class="rpop">{population}</td></tr>')


def _table(body, *, table_id='tl', footer=''):
    return (f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
            '<th class="rpop" data-coldate="2023-01-01">Population</th></tr></thead>'
            f'{body}<tfoot>{footer}</tfoot></table>')


class BelarusConfigTests(TestCase):
    def test_all_regions_keep_small_settlements_city_roles_and_minsk_districts(self):
        content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'belarus.toml').read_text(encoding='utf-8')
        config = parse_scraping_job_config('belarus', content)
        self.assertEqual(config.legal_subdivision_level, 2)
        regions = {'brest': '1', 'gomel': '3', 'grodno': '4', 'minsk': '6', 'mogilev': '7', 'vitebsk': '2'}
        self.assertEqual({page.path for page in config.pages},
                         {'belarus/cities', 'belarus/admin', 'belarus/minskcitydistricts'}
                         | {f'belarus/{slug}' for slug in regions})
        country = _row('belarus', 'Belarus', 'Republic', 9000, qid='Q184')
        capital = _row('5', 'Minsk City', 'City', 1000, qid='Q2280')
        minsk = _row('511', 'Minsk', 'City', 1000, qid='Q2280')
        admin = f'<tbody class="admin1">{capital}</tbody><tbody class="admin2">{minsk}</tbody>'
        pages = {'belarus/cities': _table('', footer=country)}
        for slug, code in regions.items():
            region = _row(code, slug, 'Region', 1000)
            district = _row(f'{code}21', 'District', 'District', 500)
            city = _row(f'{code}11', 'City', 'City', 300, qid=f'Q{code}00')
            admin += f'<tbody class="admin1">{region}</tbody><tbody class="admin2">{district}{city}</tbody>'
            pages[f'belarus/{slug}'] = (
                _table(f'<tbody>{district}{city}</tbody>', footer=region)
                + _table('<tbody>'
                         + _row(f'{code}211', 'Town', 'City', 200, district='District')
                         + _row(f'{code}212', 'Small settlement', 'Urban Settlement', 438, district='District')
                         + _row(f'{code}111', 'City', 'City', 300, qid=f'Q{code}00', district='City')
                         + '</tbody>', table_id='ts')
            )
        pages['belarus/admin'] = _table(admin, footer=country)
        pages['belarus/minskcitydistricts'] = _table(
            '<tbody>' + ''.join(_row(f'511{i}', f'Minsk district {i}', 'City District', 100)
                                for i in range(1, 10)) + '</tbody>', table_id='ts', footer=minsk)

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        for workers in (1, 3):
            with self.subTest(workers=workers), patch(
                'ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
                new=lambda client, url: SavedPages().get(url),
            ):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
            self.assertEqual(result.found, 48)
            self.assertEqual(result.deleted, 0)
            for code in regions.values():
                small = AdminArea.objects.get(pk=f'belarus_{code}212')
                self.assertEqual((small.level, small.parent_id, small.pop_latest), (3, f'belarus_{code}21', 438))
                self.assertEqual(small.pop_latest_date.isoformat(), '2023-01-01')
                locality = AdminArea.objects.get(pk=f'belarus_{code}111')
                self.assertEqual((locality.level, locality.parent_id), (3, f'belarus_{code}11'))
                self.assertEqual(locality.data_wd, AdminArea.objects.get(pk=locality.parent_id).data_wd)
            for code, count in (('121', 2), ('111', 1), ('5', 1), ('511', 9)):
                response = api_admin_area_detail(RequestFactory().get('/'), f'belarus_{code}')
                self.assertEqual(response.status_code, 200)
                payload = json.loads(response.content)
                self.assertEqual(payload['area']['subdivision_count'], count)
                self.assertEqual(len(payload['children']), count)
            self.assertEqual(AdminArea.objects.get(pk='belarus_511').parent_id, 'belarus_5')
