from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, RequestFactory

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_record
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config, upsert_scraping_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


def _row(code, name, status, population, *, qid='', municipality=''):
    parent = f'<td class="radm">{municipality}</td>' if municipality else ''
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
        f'<td class="rstatus">{status}</td>{parent}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer='', date='2025-01-01'):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        f'<th class="rpop" data-coldate="{date}">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class ArmeniaConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'armenia.toml').read_text(encoding='utf-8')
        cls.config = parse_scraping_job_config('armenia', cls.content)

    def test_plan_includes_all_ten_provinces_and_unifies_twelve_capital_districts(self):
        pages = [p for p in self.config.pages if p.path not in ('armenia/cities', 'armenia/admin')]
        self.assertEqual({p.path for p in pages}, {
            'armenia/aragatsotn', 'armenia/ararat', 'armenia/armavir', 'armenia/gegharkunik',
            'armenia/kotayk', 'armenia/lori', 'armenia/shirak', 'armenia/syunik',
            'armenia/tavush', 'armenia/vayotsdzor',
        })
        self.assertEqual(len(pages), 10)
        self.assertEqual(self.config.legal_subdivision_level, 2)
        for page in pages:
            self.assertEqual(page.force_highest_level, 2)
            self.assertIsNone(page.parent_level)
            self.assertEqual(page.include_sections, ('major_subdivision', 'cities'))
            self.assertTrue(page.preserve_source_codes)
        city, = self.config.cities
        self.assertEqual((city.code, city.level, city.parent_from), ('yerevan_city', 2, {1: ('01',)}))
        self.assertEqual(set(city.communes), {f'01{i:02}' for i in range(1, 13)})

    def test_pipeline_and_reapplication_preserve_localities_capital_roles_and_api(self):
        import json

        config = replace(self.config, pages=tuple(p for p in self.config.pages if p.path in {
            'armenia/cities', 'armenia/admin', 'armenia/aragatsotn',
        }))
        country = _row('armenia', 'Armenia', 'Republic', 1500, qid='Q399')
        province = _row('02', 'Aragatsotn', 'Province', 300)
        capital = _row('01', 'Yerevan', 'Province-Level City', 1200, qid='Q1953')
        districts = ''.join(_row(f'01{i:02}', f'District {i}', 'City District', 100) for i in range(1, 13))
        municipality = _row('0201', 'Ashtarak', 'Municipality', 300, qid='Q1')
        aparan = _row('0202', 'Aparan', 'Municipality', 50)
        pages = {
            'armenia/cities': _table('', footer=country),
            'armenia/admin': _table(
                f'<tbody class="admin1">{province}</tbody><tbody class="admin2">{municipality}{aparan}</tbody>'
                f'<tbody class="admin1">{capital}</tbody><tbody class="admin2">{districts}</tbody>',
                footer=country,
            ),
            'armenia/aragatsotn': _table(
                '<tbody>' + _row('0201', 'Ashtarak', 'Municipality', 200, qid='Q1') + aparan + '</tbody>',
                footer=province, date='2011-10-12',
            ) + _table(
                '<tbody>'
                + _row('0200111', 'Ashtarak', 'Town', 150, qid='Q1', municipality='Ashtarak')
                + _row('0200402', 'Agarak', 'Village', 50, qid='Q2', municipality='Ashtarak')
                + _row('0201802', 'Aparan village', 'Village', 50, municipality='Aparan')
                + '</tbody>', table_id='ts', date='2011-10-12',
            ),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        for workers in (1, 3):
            with patch('ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
                       new=lambda client, url: SavedPages().get(url)):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
            self.assertEqual(result.found, 21)
            self.assertEqual(AdminArea.objects.get(code='0201', country_code='armenia').pop_latest, 300)
            for code in ('0200111', '0200402'):
                locality = AdminArea.objects.get(pk=f'armenia_{code}')
                self.assertEqual((locality.level, locality.parent_id, locality.pop_latest_date.isoformat()),
                                 (3, 'armenia_0201', '2011-10-12'))
            self.assertEqual(AdminArea.objects.get(pk='armenia_0201802').parent_id, 'armenia_0202')

        record = upsert_scraping_config('armenia', self.content)
        for _ in range(2):
            materialize_configured_cities_for_record(record)
            city = AdminArea.objects.get(pk='armenia_yerevan_city')
            self.assertEqual((city.level, city.parent_id, city.city_merge_status, city.pop_latest),
                             (2, 'armenia_01', AdminArea.CityMergeStatus.UNIFIED, 1200))
            self.assertEqual(city.children.count(), 12)
            self.assertEqual(city.children.filter(level=3, city_merge_status=AdminArea.CityMergeStatus.SOURCE).count(), 12)
            self.assertEqual(AdminArea.objects.get(pk='armenia_01').children.count(), 1)
            self.assertEqual(AdminArea.objects.count(), 21)
            for code, expected in (('01', 1), ('yerevan_city', 12), ('0201', 2)):
                response = api_admin_area_detail(RequestFactory().get('/'), f'armenia_{code}')
                payload = json.loads(response.content)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(payload['children']), expected)
                self.assertEqual(payload['area']['subdivision_count'], expected)
                if code == 'yerevan_city':
                    for kind in ('flag', 'coat'):
                        self.assertEqual(payload['area']['visual_assets'][kind]['wikidata_id'], 'Q1953')
                        self.assertTrue(payload['area']['visual_assets'][kind]['image_url'])
