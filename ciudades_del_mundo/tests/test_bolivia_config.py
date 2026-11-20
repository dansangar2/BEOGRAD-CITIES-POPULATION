import json
from dataclasses import replace
from pathlib import Path
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
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


def _row(code, name, status, population, qid="", *, url=""):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'<td class="rpop">{population}</td>'
        + (f'<td class="sc"><a href="{url}">Details</a></td>' if url else '')
        + '</tr>'
    )


def _table(body, footer=""):
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2024-03-23">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class BoliviaConfigTests(TestCase):
    def test_sql_import_links_municipal_units_to_provinces_in_both_execution_modes(self):
        content = (Path(__file__).parents[1] / "subdivisions/bolivia.toml").read_text(encoding="utf-8")
        upsert_scraping_config("bolivia", content)
        config = PythonScrapingConfigRepository().get("bolivia")
        # This regression isolates the original three administrative pages.
        config = replace(config, pages=config.pages[:3])
        self.assertEqual(config.legal_subdivision_level, 2)
        root = _row("bolivia", "Bolivia", "Plurinational State", 11365333, "Q750")
        branches = (
            ("08", "Beni", 488260, (
                ("0808", "Itenez", 24903, (
                    ("080801", "Magdalena", "Municipality", 10000, ""),
                    ("080802", "Baures", "Municipality", 7488, "Q1814580"),
                )),
                ("0809", "Territorio Indigena Multietnico", 3973, (
                    ("080901", "Territorio Indigena Multietnico", "Indigenous-origin Peasant Territory", 3973, ""),
                )),
            )),
            ("07", "Santa Cruz", 3120605, (
                ("0707", "Cordillera", 139728, (
                    ("070702", "Charagua", "Indigenous-origin Peasant Territory", 30000, ""),
                    ("070705", "Gutierrez", "Indigenous-origin Peasant Territory", 12000, ""),
                )),
            )),
        )
        provinces_html = municipal_html = ""
        for code, name, population, provinces in branches:
            department = '<tbody class="admin1">' + _row(code, name, "Department", population) + '</tbody>'
            provinces_html += department + '<tbody class="admin2">'
            municipal_html += department + '<tbody class="admin2">'
            for province_code, province_name, province_population, municipalities in provinces:
                provinces_html += _row(province_code, province_name, "Province", province_population)
                municipal_html += ''.join(_row(*municipality) for municipality in municipalities)
            provinces_html += '</tbody>'
            municipal_html += '</tbody>'
        pages = {
            "bolivia/cities": _table("", root),
            "bolivia/dept/admin": _table(provinces_html, root),
            "bolivia/admin": _table(municipal_html, root),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        snapshots = []
        for workers in (1, 4):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, "get", side_effect=SavedPages().get):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                self.assertEqual((result.found, result.deleted), (11, 0))
                country = AdminArea.objects.get(code="bolivia")
                self.assertEqual((country.level, country.parent_id, country.data_wd, country.pop_latest),
                                 (0, None, "Q750", 11365333))
                for department, name, population, provinces in branches:
                    area = AdminArea.objects.get(code=department)
                    self.assertEqual((area.level, area.parent_id, area.pop_latest), (1, country.id, population))
                    for code, name, population, municipalities in provinces:
                        province = AdminArea.objects.get(code=code)
                        self.assertEqual((province.level, province.parent_id, province.pop_latest),
                                         (2, area.id, population))
                        for municipal_code, name, status, population, qid in municipalities:
                            municipality = AdminArea.objects.get(code=municipal_code)
                            self.assertEqual(
                                (municipality.level, municipality.parent_id, municipality.entity_type,
                                 municipality.pop_latest, municipality.data_wd),
                                (3, province.id, status, population, qid),
                            )
                            self.assertEqual(municipality.pop_latest_date.isoformat(), "2024-03-23")
                        response = api_admin_area_detail(RequestFactory().get('/'), province.id)
                        self.assertEqual(response.status_code, 200)
                        payload = json.loads(response.content)
                        self.assertEqual({child['id'] for child in payload['children']},
                                         {f'bolivia_{row[0]}' for row in municipalities})
                        self.assertEqual(payload['area']['subdivision_count'], len(municipalities))
                        self.assertEqual({group['level'] for group in payload['child_groups']}, {3})
                self.assertEqual(AdminArea.objects.count(), 11)
                for area in AdminArea.objects.exclude(level=0).select_related('parent'):
                    self.assertEqual(area.parent.level, area.level - 1, area.code)
                snapshots.append(list(AdminArea.objects.order_by('id').values()))
        self.assertEqual(snapshots[0], snapshots[1])

    def test_urban_settlements_keep_municipal_parents_and_their_own_census(self):
        content = (Path(__file__).parents[1] / "subdivisions/bolivia.toml").read_text(encoding="utf-8")
        upsert_scraping_config("bolivia", content)
        config = PythonScrapingConfigRepository().get("bolivia")
        self.assertEqual([page.path for page in config.pages[3:]], [
            'bolivia/beni', 'bolivia/chuquisaca', 'bolivia/cochabamba', 'bolivia/lapaz',
            'bolivia/oruro', 'bolivia/pando', 'bolivia/potosi', 'bolivia/santacruz', 'bolivia/tarija',
        ])
        # A settlement can share its QID with a municipality or have a different
        # name; both are separate territorial roles with independent populations.
        branches = (
            ('08', 'Beni', 'beni', '0808', 'Itenez', (
                ('080802', 'Baures', 'Q1814580', (('08080201002001', 'Baures', 2127, 'Q1814580'),)),
                ('080801', 'Magdalena', '', (('08080103001001', 'Bella Vista', 2440, ''),)),
            )),
            ('01', 'Chuquisaca', 'chuquisaca', '0101', 'Oropeza', (
                ('010101', 'Sucre', 'Q2907', (('01010101001001', 'Sucre', 237480, 'Q2907'),)),
            )),
            ('03', 'Cochabamba', 'cochabamba', '0301', 'Cercado (Cochabamba)', (
                ('030101', 'Cochabamba', '', (('03010101001001', 'Cochabamba', 630587, ''),)),
            )),
            ('02', 'La Paz', 'lapaz', '0201', 'Murillo', (
                ('020101', 'La Paz', 'Q1491', (('02010101001001', 'La Paz', 757184, 'Q1491'),)),
            )),
            ('04', 'Oruro', 'oruro', '0401', 'Cercado (Oruro)', (
                ('040101', 'Oruro', '', (('04010101001001', 'Oruro', 264683, ''),)),
            )),
            ('09', 'Pando', 'pando', '0901', 'Nicolas Suarez', (
                ('090101', 'Cobija', '', (('09010101001001', 'Cobija', 42849, ''),)),
            )),
            ('05', 'Potosi', 'potosi', '0501', 'Tomas Frias', (
                ('050101', 'Potosi', '', (('05010101001001', 'Potosi', 174973, ''),)),
            )),
            ('07', 'Santa Cruz', 'santacruz', '0707', 'Cordillera', (
                ('070703', 'Cabezas', '', (('07070301001001', 'Abapo', 2386, ''),)),
                ('070702', 'Charagua', '', (('07070201001001', 'Charagua', 3496, ''),)),
            )),
            ('06', 'Tarija', 'tarija', '0601', 'Cercado (Tarija)', (
                ('060101', 'Tarija', '', (('06010101001001', 'Tarija', 179528, ''),)),
            )),
        )
        root = _row('bolivia', 'Bolivia', 'Plurinational State', 11365333, 'Q750')
        pages = {'bolivia/cities': _table('', root)}
        provinces_html = municipalities_html = ''
        localities = []
        for code, name, path, province, province_name, municipalities in branches:
            department = '<tbody class="admin1">' + _row(code, name, 'Department', 1000000) + '</tbody>'
            provinces_html += department + '<tbody class="admin2">' + _row(
                province, province_name, 'Province', 900000,
                url=f'/en/bolivia/dept/admin/{path}/{province}__province/',
            ) + '</tbody>'
            municipalities_html += department + '<tbody class="admin2">'
            settlements = ''
            for municipality, municipal_name, qid, towns in municipalities:
                status = 'Indigenous-origin Peasant Territory' if municipality == '070702' else 'Municipality'
                municipalities_html += _row(
                    municipality, municipal_name, status, 850000, qid,
                    url=f'/en/bolivia/admin/{path}/{municipality}__municipality/',
                )
                for town_code, town_name, population, town_qid in towns:
                    settlements += _row(
                        town_code, town_name, 'Urban Settlement', population, town_qid,
                        url=f'/en/bolivia/{path}/province/{town_code}__settlement/',
                    ).replace(
                        '<td class="rpop">', f'<td class="radm" data-admid="{province}">{province_name}</td><td class="rpop">',
                    )
                    localities.append((town_code, municipality, population, town_qid))
            municipalities_html += '</tbody>'
            # Older province context must not overwrite the current province.
            pages[f'bolivia/{path}'] = (
                _table('<tbody>' + _row(province, province_name, 'Province', 800000) + '</tbody>')
                + '<table id="ts"><thead><tr><th>Name</th><th>Status</th><th>Province</th>'
                '<th class="rpop" data-coldate="2012-11-21">Population</th></tr></thead>'
                f'<tbody>{settlements}</tbody></table>'
            )
        pages['bolivia/dept/admin'] = _table(provinces_html, root)
        pages['bolivia/admin'] = _table(municipalities_html, root)

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        snapshots = []
        for workers in (1, 4):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, 'get', side_effect=SavedPages().get):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                expected_codes = {'bolivia'}
                for department, _, _, province, _, municipalities in branches:
                    expected_codes.update((department, province))
                    for municipality, _, _, towns in municipalities:
                        expected_codes.add(municipality)
                        expected_codes.update(town[0] for town in towns)
                self.assertEqual(set(AdminArea.objects.values_list('code', flat=True)), expected_codes)
                self.assertEqual(result.found, 41)
                for code, municipality, population, qid in localities:
                    town = AdminArea.objects.get(code=code)
                    self.assertEqual((town.level, town.parent_id, town.pop_latest, town.data_wd, town.entity_type),
                                     (4, f'bolivia_{municipality}', population, qid, 'Urban Settlement'))
                    self.assertEqual(town.pop_latest_date.isoformat(), '2012-11-21')
                for code in {town[1] for town in localities}:
                    municipality = AdminArea.objects.get(code=code)
                    self.assertEqual(municipality.pop_latest, 850000)
                    self.assertEqual(municipality.pop_latest_date.isoformat(), '2024-03-23')
                    response = api_admin_area_detail(RequestFactory().get('/'), municipality.id)
                    self.assertEqual(response.status_code, 200)
                    payload = json.loads(response.content)
                    children = {f'bolivia_{row[0]}' for row in localities if row[1] == code}
                    self.assertEqual({row['id'] for row in payload['children']}, children)
                    self.assertEqual(payload['area']['subdivision_count'], len(children))
                    self.assertEqual({group['level'] for group in payload['child_groups']}, {4})
                self.assertEqual(set(AdminArea.objects.filter(level=2).values_list('pop_latest', flat=True)), {900000})
                self.assertEqual(AdminArea.objects.count(), 41)
                for area in AdminArea.objects.exclude(level=0).select_related('parent'):
                    self.assertEqual(area.parent.level, area.level - 1, area.code)
                snapshots.append(list(AdminArea.objects.order_by('id').values()))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0], snapshots[1])
