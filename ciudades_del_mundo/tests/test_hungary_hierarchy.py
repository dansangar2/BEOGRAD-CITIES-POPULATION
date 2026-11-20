from dataclasses import replace
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _division_row(code, name, status, population, qid, scope='admin'):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'<td class="rpop">{population}</td><td class="sc">'
        f'<a itemprop="url" href="/en/hungary/{scope}/{code}__{name}/">→</a></td></tr>'
    )


def _division_table(body, footer=""):
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


def _settlement_table(code, name, district, population):
    return (
        '<table id="ts"><thead><tr><th>Name</th><th>Status</th><th class="radm">District</th>'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        f'<tbody><tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">Village</td><td class="radm">{district}</td>'
        f'<td class="rpop">{population}</td></tr></tbody></table>'
    )


class HungaryHierarchyTests(TestCase):
    def test_seed_keeps_county_district_settlement_chain_in_sql_and_detail_api(self):
        seed = Path(__file__).resolve().parents[1] / 'subdivisions' / 'hungary.toml'
        config = parse_scraping_job_config('hungary', seed.read_text(encoding='utf-8'))
        paths = {'hungary/cities', 'hungary/admin', 'hungary/bacskiskun', 'hungary/bekes'}
        config = replace(config, pages=tuple(page for page in config.pages if page.path in paths))
        county = _division_row('02', 'Bács-Kiskun', 'County', 100, 'Q181018')
        capital = _division_row('01', 'Budapest', 'County-Level City', 300, 'Q1781')
        bekes = _division_row('04', 'Békés', 'County', 200, 'Q191616')
        kalocsa = _division_row('037', 'Kalocsa', 'District', 100, 'Q912473')
        bekes_district = _division_row('045', 'Békés', 'District', 200, 'Q996150')
        capital_districts = (
            _division_row('001', 'Budapest I.', 'City District', 100, 'Q1041661', 'admin/budapest')
            + _division_row('021', 'Budapest XXI.', 'City District', 200, 'Q911368', 'admin/budapest')
        )
        pages = {
            'hungary/cities': _division_table('', _division_row('0', 'Hungary', 'Rep', 600, 'Q28')),
            'hungary/admin': _division_table(
                f'<tbody class="admin1">{county}</tbody><tbody class="admin2">{kalocsa}</tbody>'
                f'<tbody class="admin1">{bekes}</tbody><tbody class="admin2">{bekes_district}</tbody>'
                f'<tbody class="admin1">{capital}</tbody><tbody class="admin2">{capital_districts}</tbody>'
            ),
            'hungary/bacskiskun': _division_table(f'<tbody>{kalocsa}</tbody>', county)
            + _settlement_table('02149', 'Foktő', 'Kalocsa', 100),
            'hungary/bekes': _division_table(f'<tbody>{bekes_district}</tbody>', bekes)
            + _settlement_table('09760', 'Békés', 'Békés', 200),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=2, html_fetcher=SavedPages(),
        ).run(config)

        self.assertEqual(result.created, 11)
        county_area = AdminArea.objects.get(pk='hungary_02')
        district = AdminArea.objects.get(pk='hungary_037')
        settlement = AdminArea.objects.get(pk='hungary_02149')
        self.assertEqual(district.parent_id, county_area.id)
        self.assertEqual(settlement.parent_id, district.id)
        self.assertEqual(settlement.level, 3)
        self.assertEqual(settlement.city_merge_status, 0)
        self.assertEqual(settlement.pop_latest, 100)
        self.assertEqual(AdminArea.objects.get(pk='hungary_09760').parent_id, 'hungary_045')
        for area in AdminArea.objects.exclude(level=0).select_related('parent'):
            self.assertIsNotNone(area.parent)
            self.assertEqual(area.level, area.parent.level + 1)

        for code, expected_children in [('02', ['037']), ('037', ['02149']), ('04', ['045']), ('045', ['09760'])]:
            response = self.client.get(f'/api/admin-areas/hungary_{code}/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload['area']['subdivision_count'], len(expected_children), (code, [child['id'] for child in payload['children']]))
            self.assertEqual([child['id'] for child in payload['children']], [f'hungary_{child}' for child in expected_children])
        city = AdminArea.objects.get(pk='hungary_01000')
        self.assertEqual(city.parent_id, 'hungary_01')
        self.assertEqual(set(city.children.values_list('code', flat=True)), {'001', '021'})
