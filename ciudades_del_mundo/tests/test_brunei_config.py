"""Brunei's six saved source tables, without network or production SQL."""
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from django.test import RequestFactory, TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper, CityPopulationCitiesScraper, PythonScrapingConfigRepository,
)
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_record
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


URBAN_AREAS = {
    'bandar_seri_begawan': ('Bandar Seri Begawan', '1', 266682, 17, 142),
    '19594': ('Bangar', '4', 451, 1, 1),
    '36331': ('Binturan', '3', 984, 1, 1),
    '19592': ('Kuala Belait', '2', 27819, 1, 6),
    '36308': ('Lumut', '2', 7640, 1, 2),
    '19596': ('Seria', '2', 16340, 1, 5),
    '36310': ('Sungai Teraban', '2', 974, 1, 1),
    '19595': ('Tutong', '3', 8596, 1, 9),
}


class BruneiConfigTests(TestCase):
    def test_full_plan_preserves_leaves_and_unifies_only_urban_quarters(self):
        root = Path(__file__).parents[1]
        record = upsert_scraping_config('brunei', (root / 'subdivisions/brunei.toml').read_text(encoding='utf-8'))
        config = PythonScrapingConfigRepository().get('brunei')
        attach_runtime_config_extensions([config])
        pages = json.loads((root / 'tests/fixtures/brunei_pages.json').read_text(encoding='utf-8'))
        self.assertEqual((len(config.pages), config.legal_subdivision_level), (6, 3))
        places = BeautifulSoup(pages['brunei/cities'], 'html.parser')
        source_urban_names = {
            row.select_one('[itemprop=name]').get_text(strip=True)
            for row in places.select('table#ts tbody tr')
            if row.select_one('.rstatus') and row.select_one('.rstatus').get_text(strip=True) == 'UA'
        }
        self.assertEqual(source_urban_names, {value[0] for value in URBAN_AREAS.values()})

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        source = []
        for page in config.pages[2:]:
            source.extend(CityPopulationAdminScraper().scrape_configured_html(
                html=pages[page.path], url=config.base_url + page.path + '/', country_code='brunei', page=page,
            ))
        leaves = [row for row in source if row.level == 4]
        self.assertEqual(len(leaves), 444)
        counts = Counter(row.entity_type for row in leaves)
        self.assertEqual((counts['Village'], counts['Quarter']), (235, 166))
        for workers in (1, 4):
            with self.subTest(workers=workers), patch(
                'ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
                new=lambda client, url: SavedPages().get(url),
            ):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    html_fetcher=SavedPages(), page_workers=workers,
                ).run(config)
                self.assertEqual((result.found, result.deleted), (511, 0))
                cities = AdminArea.objects.filter(entity_type='City')
                self.assertEqual(set(cities.values_list('code', flat=True)), set(URBAN_AREAS))
                self.assertEqual(set(cities.values_list('level', flat=True)), {2})
                country_view = self.client.get('/api/countries/brunei/?level=2&include_children=0').json()
                self.assertEqual({row['id'] for row in country_view['table']['rows']},
                                 {f'brunei_{code}' for code in URBAN_AREAS})
                self.assertEqual({row['level'] for row in country_view['table']['rows']}, {2})
                leaf_option = next(row for row in country_view['levels'] if row['value'] == 4)
                self.assertIn('Quarter', leaf_option['label'])
                self.assertIn('Village', leaf_option['label'])
                selected = set()
                for code, (name, parent, population, unit_count, leaf_count) in URBAN_AREAS.items():
                    urban = cities.get(code=code)
                    self.assertEqual((urban.name, urban.parent_id, urban.pop_latest, urban.city_merge_status),
                                     (name, f'brunei_{parent}', population, AdminArea.CityMergeStatus.UNIFIED))
                    units = list(urban.children.all())
                    self.assertEqual(len(units), unit_count)
                    self.assertEqual({unit.level for unit in units}, {3})
                    self.assertEqual(sum(unit.pop_latest for unit in units), population)
                    parts = list(AdminArea.objects.filter(parent__in=units))
                    self.assertEqual(len(parts), leaf_count)
                    self.assertEqual({part.level for part in parts}, {4})
                    self.assertEqual({part.entity_type for part in parts}, {'Town' if code == '19594' else 'Quarter'})
                    self.assertEqual(sum(part.pop_latest for part in parts), population)
                    self.assertFalse(selected.intersection(part.id for part in parts))
                    selected.update(part.id for part in parts)
                self.assertEqual(len(selected), 167)
                teraban = AdminArea.objects.get(pk='brunei_20107')
                self.assertEqual((teraban.parent_id, teraban.parent.parent_id), ('brunei_201_36310', 'brunei_36310'))
                city = AdminArea.objects.get(pk='brunei_bandar_seri_begawan')
                self.assertEqual((city.level, city.parent_id, city.pop_latest, city.data_wd, city.city_merge_status),
                                 (2, 'brunei_1', 266682, 'Q9279', AdminArea.CityMergeStatus.UNIFIED))
                mukims = list(city.children.all())
                self.assertEqual(len(mukims), 17)
                self.assertEqual({row.level for row in mukims}, {3})
                self.assertEqual(sum(row.pop_latest for row in mukims), 266682)
                quarters = AdminArea.objects.filter(parent__in=mukims)
                self.assertEqual(quarters.count(), 142)
                self.assertEqual(set(quarters.values_list('entity_type', 'level')), {('Quarter', 4)})
                by_code = {row.code: row for row in AdminArea.objects.all()}
                for leaf in leaves:
                    current = by_code[leaf.code]
                    for field in ('name', 'pop_latest', 'entity_type', 'data_wd'):
                        self.assertEqual(getattr(current, field), getattr(leaf, field), (leaf.code, field))
                    self.assertEqual(str(current.pop_latest_date), str(leaf.pop_latest_date))
                    self.assertEqual(current.area_km2, Decimal(str(leaf.area_km2)).quantize(Decimal('0.01')))
                for mukim in (row for row in source if row.entity_type == 'Mukim'):
                    current = by_code[mukim.code]
                    remainder = by_code.get(mukim.code + '_remainder')
                    extra = by_code.get('201_36310') if mukim.code == '201' else None
                    self.assertEqual(current.pop_latest + (remainder.pop_latest if remainder else 0)
                                     + (extra.pop_latest if extra else 0),
                                     mukim.pop_latest, mukim.code)
                    self.assertEqual(current.level, 3)
                self.assertEqual(by_code['401'].parent_id, 'brunei_3')
                self.assertEqual(by_code['brunei'].pop_latest, 440715)
                for row in by_code.values():
                    expected = {child.id for child in by_code.values() if child.parent_id == row.id}
                    if row.parent_id:
                        self.assertLess(by_code[row.parent_id.removeprefix('brunei_')].level, row.level)
                    response = api_admin_area_detail(RequestFactory().get('/'), row.id)
                    self.assertEqual(response.status_code, 200)
                    payload = json.loads(response.content)
                    self.assertEqual({child['id'] for child in payload['children']}, expected, row.id)
                    self.assertEqual(payload['area']['subdivision_count'], len(expected), row.id)
                    child_types = {by_code[child.removeprefix('brunei_')].entity_type for child in expected}
                    if child_types == {'Quarter'}:
                        self.assertTrue(payload['child_groups'][0]['label'].endswith(' · Quarter'), row.id)

        fields = ('id', 'name', 'level', 'parent_id', 'entity_type', 'pop_latest', 'pop_latest_date',
                  'data_wd', 'city_merge_status')
        before = list(AdminArea.objects.order_by('id').values(*fields))
        # SQL stores areas at two decimals; aggregate that persisted precision
        # once before checking complete repeatability of the materializer.
        materialize_configured_cities_for_record(record)
        self.assertEqual(list(AdminArea.objects.order_by('id').values(*fields)), before)
        fields += ('area_km2',)
        before = list(AdminArea.objects.order_by('id').values(*fields))
        for _ in range(2):
            materialize_configured_cities_for_record(record)
            self.assertEqual(list(AdminArea.objects.order_by('id').values(*fields)), before)
