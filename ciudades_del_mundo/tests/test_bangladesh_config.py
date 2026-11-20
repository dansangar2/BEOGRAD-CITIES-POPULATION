from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, qid=""):
    return (f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
            f'<td class="rstatus">{status}</td><td class="rpop">{population}</td></tr>')


def _table(body, footer=""):
    return ('<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
            '<th class="rpop" data-coldate="2022-06-14">Population</th></tr></thead>'
            f'{body}<tfoot>{footer}</tfoot></table>')


class BangladeshConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = (Path(__file__).resolve().parents[1] / 'subdivisions/bangladesh.toml').read_text(encoding='utf-8')
        cls.config = parse_scraping_job_config('bangladesh', cls.content)
        for name, value in parse_runtime_config_extensions(cls.content).items():
            object.__setattr__(cls.config, name, value)

    def test_complete_plan_keeps_existing_levels_and_all_eight_divisions(self):
        self.assertEqual(len(self.config.pages), 11)
        self.assertEqual(self.config.legal_subdivision_level, 2)
        details = [p for p in self.config.pages if p.path.count('/') == 2 and not p.path.startswith('bangladesh/admin/')]
        self.assertEqual({p.path.split('/')[1] for p in details}, {
            'barisal', 'chittagong', 'dhaka', 'khulna', 'mymensingh', 'rajshahi', 'rangpur', 'sylhet',
        })
        self.assertTrue(all(p.force_highest_level == 2 and p.preserve_source_codes for p in details))
        self.assertEqual(len(self.config.cities), 9)
        dhaka = next(city for city in self.config.cities if city.code == 'dhaka_city')
        self.assertTrue(dhaka.keep_communes)
        self.assertEqual((dhaka.child_code, dhaka.child_level, dhaka.child_entity_type),
                         ('dhaka_municipality', 3, 'City'))

    def test_unions_and_unified_cities_preserve_identity_population_and_districts(self):
        division_for = {'06': 'barisal', '15': 'chittagong', '19': 'chittagong', '22': 'chittagong',
                        '26': 'dhaka', '33': 'dhaka', '47': 'khulna', '61': 'mymensingh',
                        '67': 'dhaka', '81': 'rajshahi', '85': 'rangpur', '91': 'sylhet'}
        country = _row('bangladesh', 'Bangladesh', 'PRep', 100000, 'Q902')
        majors = {division: '' for division in division_for.values()}
        national_children = {district: '' for district in division_for}
        expected_populations = {}
        for city in self.config.cities:
            if city.code == 'dhaka_city':
                continue
            children = ''.join(_row(code, code, 'City District', 10) for code in city.communes)
            population = 10 * len(city.communes)
            expected_populations[city.code] = population
            city_row = _row(city.code, city.name, 'City Corporation', population, city.data_wd)
            district = city.code[:2]
            national_children[district] += city_row
            majors[division_for[district]] += (f'<tbody class="admin1">{city_row}</tbody>'
                                               f'<tbody class="admin2">{children}</tbody>')
        for code, name, pop, child in [('2625', 'Dhaka North', 60, '2625002'), ('2620', 'Dhaka South', 40, '2620005')]:
            city_row = _row(code, name, 'City Corporation', pop)
            national_children['26'] += city_row
            majors['dhaka'] += (f'<tbody class="admin1">{city_row}</tbody>'
                               f'<tbody class="admin2">{_row(child, child, "City District", pop)}</tbody>')
        for district, division in division_for.items():
            code = district + '99'
            subdistrict = _row(code, code, 'Subdistrict', 100)
            national_children[district] += subdistrict
            # The municipality and union share a QID but remain distinct source units.
            children = _row(code + '142', 'Rural union', 'Union', 60, 'Q12345')
            children += _row(code + '033', 'Urban municipality', 'Municipality', 40, 'Q12345')
            majors[division] += (f'<tbody class="admin1">{subdistrict}</tbody>'
                                 f'<tbody class="admin2">{children}</tbody>')
        national = ''
        for district, children in national_children.items():
            if district == '22':
                # The separate detail page supplies this otherwise absent district.
                continue
            national += (f'<tbody class="admin1">{_row(district, district, "District", 1000)}</tbody>'
                         f'<tbody class="admin2">{children}</tbody>')
        pages = {
            'bangladesh/cities': _table('', country),
            'bangladesh/admin': _table(national, country),
            'bangladesh/admin/22__coxs_bazar': _table('', _row('22', "Cox's Bazar", 'District', 1000, 'Q1122278')),
            **{'bangladesh/' + division + '/admin': _table(body) for division, body in majors.items()},
        }

        class SavedHtml:
            def get(self, url):
                return pages[url.removeprefix('https://www.citypopulation.de/en/').strip('/')]

        for workers in (1, 3):
            with self.subTest(workers=workers), patch(
                'ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
                new=lambda client, url: SavedHtml().get(url),
            ):
                ScrapeAdminAreas(DjangoAdminAreaRepository(),
                                 [CityPopulationAdminScraper(), CityPopulationCitiesScraper()],
                                 html_fetcher=SavedHtml(), page_workers=workers).run(self.config)
            dhaka = AdminArea.objects.get(code='dhaka_city')
            self.assertEqual((dhaka.level, dhaka.parent_id, dhaka.pop_latest, dhaka.data_wd),
                             (2, 'bangladesh_26', 100, 'Q1354'))
            self.assertEqual(dhaka.city_merge_status, AdminArea.CityMergeStatus.UNIFIED)
            municipality = AdminArea.objects.get(code='dhaka_municipality')
            self.assertEqual((municipality.name, municipality.level, municipality.parent_id,
                              municipality.pop_latest, municipality.data_wd, municipality.city_merge_status),
                             ('Dhaka', 3, dhaka.pk, 100, 'Q1354', AdminArea.CityMergeStatus.UNIFIED))
            for code in ('2625', '2620'):
                source = AdminArea.objects.get(code=code)
                self.assertEqual((source.parent_id, source.level, source.city_merge_status),
                                 (municipality.pk, 4, AdminArea.CityMergeStatus.SOURCE))
            for code, parent, population in [('2625002', '2625', 60), ('2620005', '2620', 40)]:
                district = AdminArea.objects.get(code=code)
                self.assertEqual((district.parent_id, district.level, district.pop_latest),
                                 ('bangladesh_' + parent, 5, population))
                payload = self.client.get('/api/admin-areas/bangladesh_' + parent + '/').json()
                self.assertEqual(payload['area']['subdivision_count'], 1)
                self.assertEqual({child['id'] for child in payload['children']}, {district.pk})
                self.assertEqual(payload['children'][0]['level'], 5)
            payload = self.client.get('/api/admin-areas/' + dhaka.pk + '/').json()
            self.assertEqual(payload['area']['subdivision_count'], 1)
            self.assertEqual([child['id'] for child in payload['children']], [municipality.pk])
            payload = self.client.get('/api/admin-areas/' + municipality.pk + '/').json()
            self.assertEqual(payload['area']['subdivision_count'], 2)
            self.assertEqual({child['id'] for child in payload['children']},
                             {'bangladesh_2625', 'bangladesh_2620'})
            for code, population in expected_populations.items():
                city = AdminArea.objects.get(code=code)
                self.assertEqual((city.pop_latest, city.level, city.parent_id, city.city_merge_status),
                                 (population, 2, 'bangladesh_' + code[:2], AdminArea.CityMergeStatus.UNIFIED))
                self.assertTrue(city.data_wd)
            for district in division_for:
                union = AdminArea.objects.get(code=district + '99142')
                municipality = AdminArea.objects.get(code=district + '99033')
                self.assertEqual((union.level, union.parent_id, union.pop_latest),
                                 (3, 'bangladesh_' + district + '99', 60))
                self.assertEqual(union.data_wd, municipality.data_wd)
            for area in AdminArea.objects.exclude(level=0).select_related('parent'):
                self.assertEqual(area.level, area.parent.level + 1, area.pk)
            payload = self.client.get('/api/admin-areas/bangladesh_2699/').json()
            self.assertEqual(payload['area']['subdivision_count'], 2)
            self.assertEqual({child['id'] for child in payload['children']},
                             {'bangladesh_2699142', 'bangladesh_2699033'})
