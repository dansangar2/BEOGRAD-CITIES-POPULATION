from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, qid=''):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2020-09-21">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class AfghanistanConfigTests(TestCase):
    def test_kabul_sectors_keep_capital_district_and_ordinary_branches(self):
        content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'afghanistan.toml').read_text(encoding='utf-8')
        config = parse_scraping_job_config('afghanistan', content)
        for name, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, name, value)
        self.assertEqual(config.legal_subdivision_level, 2)
        self.assertEqual(len(config.pages), 3)
        codes = [f'kab{number:02}' for number in [*range(1, 14), 15, 16, 17]]
        populations = [117810, 143303, 173165, 369455, 341413, 377649, 451758, 375646,
                       325026, 398589, 312097, 57357, 263662, 426448, 185183, 115989]
        country = _row('0', 'Afghanistan', 'Country', 31390200, 'Q889')
        province = _row('01', 'Kābul', 'Province', 5204667, 'Q188933')
        capital = _row('0101', 'Kābul', 'Capital District', 4434550, 'Q6344428')
        ordinary = _row('0102', 'Paghmān', 'District', 100)
        pages = {
            'afghanistan/cities': _table('', footer=country),
            'afghanistan/admin': _table(
                f'<tbody class="admin1">{province}</tbody>'
                f'<tbody class="admin2">{capital}{ordinary}</tbody>', footer=country),
            'afghanistan/kabulcity': _table(
                '<tbody>' + ''.join(_row(code, str(int(code[3:])), 'City District', pop)
                                      for code, pop in zip(codes, populations)) + '</tbody>',
                table_id='ts', footer=_row('kabul', 'Kābul [Kabul]', 'City', 4434550, 'Q5838')),
        }

        class SavedHtml:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
            page_workers=3, html_fetcher=SavedHtml(),
        ).run(config)
        self.assertEqual(AdminArea.objects.count(), 20)
        district = AdminArea.objects.get(pk='afghanistan_0101')
        self.assertEqual((district.level, district.parent_id, district.data_wd, district.pop_latest),
                         (2, 'afghanistan_01', 'Q6344428', 4434550))
        self.assertEqual(district.entity_type, 'Capital District')
        self.assertEqual(AdminArea.objects.get(pk='afghanistan_0102').parent_id, 'afghanistan_01')
        self.assertFalse(AdminArea.objects.filter(code__in=['kab14', 'kab18', 'kabul']).exists())
        for area in AdminArea.objects.exclude(level=0).select_related('parent'):
            self.assertEqual(area.parent.level, area.level - 1, area.code)
        for code, pop in zip(codes, populations):
            sector = AdminArea.objects.get(pk=f'afghanistan_{code}')
            self.assertEqual((sector.level, sector.parent_id, sector.entity_type, sector.pop_latest),
                             (3, district.id, 'City District', pop))
            self.assertEqual(sector.pop_latest_date.isoformat(), '2020-09-21')
        response = self.client.get(f'/api/admin-areas/{district.id}/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['area']['subdivision_count'], 16)
        self.assertEqual({child['id'] for child in payload['children']}, {f'afghanistan_{code}' for code in codes})
