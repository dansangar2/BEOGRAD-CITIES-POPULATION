from dataclasses import replace
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, *, qid='', area=10):
    url = '/en/ukraine/admin/' + (f'{code}__{code}/' if code != '0' else '')
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}" data-area="{area}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'<td class="rpop">{population}</td><td class="sc"><a itemprop="url" href="{url}">Details</a></td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2022-01-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class UkraineConfigTests(TestCase):
    def test_saved_page_layouts_preserve_ordinary_and_special_city_branches(self):
        content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'ukraine.toml').read_text(encoding='utf-8')
        config = parse_scraping_job_config('ukraine', content)
        self.assertEqual(len(config.pages), 30)
        self.assertEqual(len({page.path for page in config.pages}), 29)
        province = _row('71', 'Cherkasy', 'Oblast', 100, qid='Q161808')
        district = _row('7108', 'Cherkasy district', 'District', 100, qid='Q103796930')
        kyiv_region = _row('80', 'M. Kyïv', 'City', 2952301, qid='Q1899', area=836)
        kyiv_city = _row('8000', 'Kyïv', 'City', 2952301, qid='Q1899', area=826.55)
        sevastopol_region = _row('85', "M. Sevastopol'", 'City with Environs', 547820, qid='Q7525', area=864)
        sevastopol_city = _row('85000', "Sevastopol'", 'City with Environs', 547820, qid='Q2659239', area=848.9)
        kyiv_districts = [f'K{number:02d}' for number in range(1, 11)]
        settlements = [('85363103', 'Inkerman', 'City', 13604), ('85369653', 'Kacha', 'Urban Settlement', 6020), ('85101101', "Sevastopol'", 'City', 497168)]
        pages = {
            'ukraine/cities': _table('', footer=_row('0', 'Ukraine', 'Republic', 40000000, qid='Q212')),
            'ukraine/admin': _table(
                '<tbody class="admin1">' + province + '</tbody><tbody class="admin2">' + district + '</tbody>'
                '<tbody class="admin1">' + kyiv_region + '</tbody><tbody class="admin2">' + kyiv_city + '</tbody>'
                '<tbody class="admin1">' + sevastopol_region + '</tbody><tbody class="admin2">' + sevastopol_city + '</tbody>'
            ),
            'ukraine/cerkasy': _table('<tbody>' + district + '</tbody>', footer=province)
            + _table('<tbody>' + _row('710800100100', 'Cherkasy city', 'City', 50)
                     + _row('710800100200', 'Same name', 'City', 20, qid='Q10')
                     + _row('710800100300', 'Same name', 'Urban Settlement', 5, qid='Q11') + '</tbody>', table_id='ts'),
            # No infosection, tl table or footer: only the ten city districts.
            'ukraine/kievcity': _table('<tbody>' + ''.join(_row(code, code, 'City District', 1000) for code in kyiv_districts) + '</tbody>', table_id='ts'),
            # Both tl body and footer reuse regional code 85.
            'ukraine/msevastopol': _table('<tbody>' + sevastopol_region + '</tbody>', footer=sevastopol_region)
            + _table('<tbody>' + ''.join(_row(*row) for row in settlements) + '</tbody>', table_id='ts'),
        }
        config = replace(config, pages=tuple(page for page in config.pages if page.path in pages))
        self.assertEqual(len(config.pages), 6)

        class SavedHtml:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
            page_workers=3, html_fetcher=SavedHtml(),
        ).run(config)
        self.assertEqual(AdminArea.objects.count(), 23)
        expected_children = {
            'ukraine': ['71', '80', '85'], '71': ['7108'], '7108': ['710800100100', '710800100200', '710800100300'],
            '80': ['8000'], '8000': kyiv_districts, '85': ['85000'],
            '85000': [row[0] for row in settlements],
        }
        for code, child_codes in expected_children.items():
            response = self.client.get(f'/api/admin-areas/ukraine_{code}/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload['area']['subdivision_count'], len(child_codes), code)
            self.assertEqual({child['id'] for child in payload['children']}, {f'ukraine_{child}' for child in child_codes}, code)
        for area in AdminArea.objects.exclude(level=0).select_related('parent'):
            self.assertEqual(area.parent.level, area.level-1, area.code)
        for code, population, qid, area_km2 in [('80', 2952301, 'Q1899', 836), ('8000', 2952301, 'Q1899', 826.55), ('85', 547820, 'Q7525', 864), ('85000', 547820, 'Q2659239', 848.9)]:
            area = AdminArea.objects.get(pk=f'ukraine_{code}')
            self.assertEqual((area.pop_latest, area.data_wd, float(area.area_km2)), (population, qid, area_km2))
        self.assertEqual(AdminArea.objects.get(pk='ukraine_ukraine').data_wd, 'Q212')
        self.assertEqual(list(AdminArea.objects.filter(name='Same name').order_by('code').values_list('pop_latest', flat=True)), [20, 5])
        self.assertEqual(set(AdminArea.objects.filter(level=3).values_list('entity_type', flat=True)), {'City', 'Urban Settlement', 'City District'})
