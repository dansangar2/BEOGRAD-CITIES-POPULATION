import json
from pathlib import Path
import tomllib
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationCitiesScraper,
    PythonScrapingConfigRepository,
)
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_record
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


ISLANDS = (
    ("01", "6308", "New Providence", "Q2467"),
    ("02", "6303", "Grand Bahama", "Q105016"),
    ("03", "6294", "Abaco", "Q322340"),
    ("07", "6298", "Bimini", "Q863476"),
    ("11", "6302", "Exuma and Cays", "Q1505419"),
    ("12", "6304", "Harbour Island", "Q1771794"),
    ("19", "6312", "Spanish Wells", "Q1771681"),
)
LOCALITIES = (
    ("19473", "Bailey Town", "07", 1418, ""),
    ("32935", "Dundas Town", "03", 2890, ""),
    ("17214", "Dunmore Town", "12", 1843, "Q1771794"),
    ("17215", "Freeport", "02", 28000, "Q867573"),
    ("32937", "George Town", "11", 1437, "Q3101961"),
    ("17216", "Marsh Harbour", "03", 4368, "Q1337651"),
    ("32936", "Murphy Town", "03", 1938, ""),
    ("17217", "Nassau", "01", 296732, "Q2467"),
    ("17219", "Spanish Wells", "19", 1453, "Q1771681"),
)


def _row(code, name, population, *, qid="", status="", adm="", older=False):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td>'
        + (f'<td class="rstatus">{status}</td>' if status else "")
        + (f'<td class="radm" data-admid="{adm}">AB</td>' if adm else "")
        + f'<td class="rpop">{population}</td>'
        + ('<td class="rpop">...</td>' if older else "")
        + '</tr>'
    )


def _saved_html():
    header = '<thead><tr><th>Name</th><th class="rpop" data-coldate="2022-04-04">Population</th></tr></thead>'
    root = _row("bahamas", "Bahamas", 398165, qid="Q778")
    admin = '<table id="tl">' + header
    for code, _, name, qid in ISLANDS:
        admin += '<tbody class="admin1">' + _row(code, name, 296732, qid=qid, status="Island") + '</tbody><tbody>'
        if code == "01":
            for number in range(1, 25):
                admin += _row(f'01{number:02}', f'District {number}', 100, status="Supervisory District")
        else:
            admin += _row(code + "01", name, 100, qid=qid, status="Supervisory District")
        admin += '</tbody>'
    admin += '<tfoot>' + root + '</tfoot></table>'
    cities = '<table id="tl">' + header + '<tfoot>' + root + '</tfoot></table>'
    cities += ('<table id="ts"><thead><tr><th>Name</th><th>Adm.</th>'
               '<th class="rpop" data-coldate="2010-05-03">Population</th>'
               '<th class="rpop" data-coldate="2022-04-04">Population</th>'
               '</tr></thead><tbody>')
    island_refs = {code: source for code, source, _, _ in ISLANDS}
    for code, name, island, population, qid in LOCALITIES:
        cities += _row(code, name, population, qid=qid, adm=island_refs[island], older=True)
    cities += '</tbody></table>'
    return {"admin": admin, "cities": cities}


class BahamasConfigTests(TestCase):
    def test_localities_and_unified_nassau_survive_import_and_repeated_materialization(self):
        root = Path(__file__).resolve().parents[1]
        content = (root / 'subdivisions/bahamas.toml').read_text(encoding='utf-8')
        merge_content = (root / 'cities_merge/bahamas.toml').read_text(encoding='utf-8')
        self.assertEqual(tomllib.loads(content)['cities'], tomllib.loads(merge_content)['cities'])
        record = upsert_scraping_config('bahamas', content)
        config = PythonScrapingConfigRepository().get('bahamas')
        attach_runtime_config_extensions([config])
        html = _saved_html()

        class SavedHtml:
            def get(self, url):
                return html[url.rstrip('/').rsplit('/', 1)[-1]]

        with patch.object(CityPopulationClient, 'get', side_effect=SavedHtml().get):
            result = ScrapeAdminAreas(
                DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            ).run(config)
        self.assertEqual(result.deleted, 0)
        self.assertEqual(AdminArea.objects.filter(entity_type='City').count(), 9)
        self.assertEqual(AdminArea.objects.count(), 1 + 7 + 30 + 9)

        for code, name, island, population, _ in LOCALITIES:
            locality = AdminArea.objects.get(pk=f'bahamas_{code}')
            self.assertEqual((locality.name, locality.level, locality.parent_id),
                             (name, 2, f'bahamas_{island}'))
            if code != '17217':
                self.assertEqual(locality.pop_latest, population)
                self.assertEqual(locality.pop_latest_date.year, 2010)

        for _ in range(2):
            previous = list(AdminArea.objects.order_by('id').values())
            materialize_configured_cities_for_record(record)
            self.assertEqual(list(AdminArea.objects.order_by('id').values()), previous)
            city = AdminArea.objects.get(pk='bahamas_17217')
            self.assertEqual((city.pop_latest, city.city_merge_status), (2400, AdminArea.CityMergeStatus.UNIFIED))
            self.assertEqual(AdminArea.objects.get(pk='bahamas_01').pop_latest, 296732)
            sources = city.children.order_by('code')
            self.assertEqual(list(sources.values_list('code', flat=True)),
                             [f'01{number:02}' for number in range(1, 25)])
            self.assertEqual(set(sources.values_list('level', 'city_merge_status')),
                             {(3, AdminArea.CityMergeStatus.SOURCE)})
            for area_id, expected in (
                ('01', {'17217'}), ('03', {'0301', '32935', '17216', '32936'}),
                ('19', {'1901', '17219'}),
                ('17217', {f'01{number:02}' for number in range(1, 25)}),
            ):
                response = api_admin_area_detail(RequestFactory().get('/'), f'bahamas_{area_id}')
                self.assertEqual(response.status_code, 200)
                payload = json.loads(response.content)
                self.assertEqual({child['id'] for child in payload['children']},
                                 {f'bahamas_{code}' for code in expected})
                self.assertEqual(payload['area']['subdivision_count'], len(expected))
