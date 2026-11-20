from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, *, qid="", parent=""):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        + (f'<td class="radm" data-admid="{parent}">{parent}</td>' if parent else '')
        + f'<td class="rpop">{population}</td></tr>'
    )


def _table(body, footer="", table_id="tl"):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2022-08-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class BrazilConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content = (Path(__file__).parents[1] / 'subdivisions/brazil.toml').read_text(encoding='utf-8')
        cls.config = parse_scraping_job_config('brazil', cls.content)

    def test_plan_covers_every_state_federal_capital_and_eleven_city_subdivisions(self):
        self.assertEqual(len(self.config.pages), 44)
        urban = self.config.pages[6:33]
        self.assertEqual(len(urban), 27)
        self.assertEqual(len({page.path for page in urban}), 27)
        self.assertEqual({page.path for page in urban}, {
            'brazil/' + path for path in (
                'acre', 'alagoas', 'amapa', 'amazonas', 'bahia', 'ceara',
                'espiritosanto', 'goias', 'maranhao', 'matogrosso', 'matogrossodosul',
                'minasgerais', 'para', 'paraiba', 'parana', 'pernambuco', 'piaui',
                'riodejaneiro', 'riograndedonorte', 'riograndedosul', 'rondonia',
                'roraima', 'santacatarina', 'saopaulo', 'sergipe', 'tocantins',
                'cities/distritofederal',
            )
        })
        for page in urban:
            self.assertEqual((page.force_highest_level, page.parent_level), (4, 3))
            self.assertEqual(page.include_sections, ('cities',))
            self.assertTrue(page.preserve_source_codes)
        self.assertTrue(all(page.preserve_source_codes for page in self.config.pages[1:6]))
        cities = self.config.pages[33:]
        self.assertEqual({page.path for page in cities}, {
            'brazil/' + path for path in ('belem', 'belohorizonte', 'brasilia', 'curitiba',
                                         'fortaleza', 'goiania', 'manaus', 'recife', 'rio',
                                         'salvador', 'saopaulocity')
        })
        for page in cities:
            self.assertEqual(page.html_format, 'cities')
            self.assertEqual(page.force_highest_level, 5)
            self.assertEqual(page.include_sections, ('major_subdivision',))
            self.assertTrue(page.preserve_source_codes)

    def test_homonymous_municipalities_in_different_states_keep_ibge_identity(self):
        config = replace(self.config, pages=tuple(page for page in self.config.pages if page.path in {
            'brazil/cities', 'brazil/regiaonordeste/admin', 'brazil/bahia',
        }))
        for key, value in parse_runtime_config_extensions(self.content).items():
            object.__setattr__(config, key, value)
        country = _row('brazil', 'Brazil', 'Fed Rep', 203080756)
        paraiba = _row('25', 'Paraiba', 'State', 4000000)
        bahia = _row('29', 'Bahia', 'State', 14000000)
        conde_pb = _row('2504603', 'Conde', 'Municipality', 27000, qid='Q123')
        conde_ba = _row('2908606', 'Conde', 'Municipality', 24000, qid='Q123')
        pages = {
            'brazil/cities': _table('', country),
            'brazil/regiaonordeste/admin': _table(
                f'<tbody class="admin1">{paraiba}</tbody><tbody class="admin2">{conde_pb}</tbody>'
                f'<tbody class="admin1">{bahia}</tbody><tbody class="admin2">{conde_ba}</tbody>', country),
            'brazil/bahia': _table(f'<tbody>{conde_ba}</tbody>', bahia) + _table(
                '<tbody>' + _row('290860600', 'Conde', 'Urban Place', 10000, qid='Q123', parent='Conde')
                + '</tbody>', table_id='ts'),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=4, html_fetcher=SavedPages(),
        ).run(config)
        self.assertEqual(result.found, 6)
        self.assertEqual(AdminArea.objects.get(code='2504603').parent_id, 'brazil_25')
        self.assertEqual(AdminArea.objects.get(code='2908606').parent_id, 'brazil_29')
        area = AdminArea.objects.get(code='290860600')
        self.assertEqual((area.level, area.parent_id, area.pop_latest), (4, 'brazil_2908606', 10000))

    def test_municipalities_contain_localities_and_localities_contain_city_subdivisions(self):
        config = replace(self.config, pages=tuple(page for page in self.config.pages if page.path in {
            'brazil/cities', 'brazil/regiaocentrooeste/admin', 'brazil/regiaosudeste/admin',
            'brazil/minasgerais', 'brazil/cities/distritofederal', 'brazil/belohorizonte', 'brazil/brasilia',
            'brazil/saopaulo', 'brazil/saopaulocity',
        }))
        for key, value in parse_runtime_config_extensions(self.content).items():
            object.__setattr__(config, key, value)
        country = _row('brazil', 'Brazil', 'Fed Rep', 203080756, qid='Q155')
        minas = _row('31', 'Minas Gerais', 'State', 20538718)
        federal = _row('53', 'Distrito Federal', 'Federal District', 2817381)
        belo = _row('3106200', 'Belo Horizonte', 'Municipality', 2315560, qid='Q42800')
        brasilia = _row('5300108', 'Brasilia', 'Municipality', 2817381, qid='Q2844')
        state_sp = _row('35', 'Sao Paulo', 'State', 44411238)
        sao_paulo = _row('3550308', 'Sao Paulo', 'Municipality', 11451999, qid='Q174')
        pages = {
            'brazil/cities': _table('', country),
            'brazil/regiaosudeste/admin': _table(
                f'<tbody class="admin1">{minas}</tbody><tbody class="admin2">{belo}</tbody>'
                f'<tbody class="admin1">{state_sp}</tbody><tbody class="admin2">{sao_paulo}</tbody>', country),
            'brazil/regiaocentrooeste/admin': _table(
                f'<tbody class="admin1">{federal}</tbody><tbody class="admin2">{brasilia}</tbody>', country),
            'brazil/minasgerais': _table(f'<tbody>{belo}</tbody>', minas) + _table(
                '<tbody>' + _row('310620000', 'Belo Horizonte', 'Urban Place', 2315560,
                                 qid='Q42800', parent='3106200') + '</tbody>', table_id='ts'),
            'brazil/cities/distritofederal': _table(f'<tbody>{federal}</tbody>', country) + _table(
                '<tbody>' + _row('2470', 'Brasilia', 'Federal Capital', 2718082, qid='Q2844')
                + '</tbody>', table_id='ts'),
            'brazil/belohorizonte': '<section class="infosection"><div class="infoname">Belo Horizonte</div></section>' + _table(
                '<tbody>' + _row('31062000567', 'Oeste', 'Municipal Subdistrict', 308406, qid='Q123')
                + _row('31062002561', 'Barreiro', 'Municipal Subdistrict', 278144)
                + _row('31062002567', 'Oeste', 'Municipal Subdistrict', 712, qid='Q123', parent='Barreiro')
                + '</tbody>', table_id='ts'),
            'brazil/brasilia': '<section class="infosection"><div class="infoname">Brasilia</div></section>' + _table(
                '<tbody>' + _row('53001080501', 'Plano Piloto', 'Municipal Subdistrict', 198697)
                + '</tbody>', table_id='ts'),
            'brazil/saopaulo': _table(f'<tbody>{sao_paulo}</tbody>', state_sp) + _table(
                '<tbody>' + _row('355030800', 'Sao Paulo', 'Urban Place', 11434786,
                                 qid='Q174', parent='3550308') + '</tbody>', table_id='ts'),
            'brazil/saopaulocity': '<section class="infosection"><div class="infoname">Sao Paulo</div></section>' + _table(
                '<tbody>' + _row('35503080100', 'Agua Rasa', 'Municipal District', 85788)
                + '</tbody>', table_id='ts'),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        for workers in (1, 4):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, 'get', side_effect=SavedPages().get):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                self.assertEqual((result.found, result.deleted), (15, 0),
                                 list(AdminArea.objects.values_list('code', 'level', 'parent_id')))
                for code, population in [('310620000', 2315560), ('5300108_urban', 2718082),
                                         ('355030800', 11434786)]:
                    area = AdminArea.objects.get(code=code)
                    self.assertEqual(area.level, 4)
                    self.assertEqual(area.parent_id, 'brazil_' + code[:7])
                    self.assertEqual(area.pop_latest, population)
                for code, population in [('31062000567', 308406), ('31062002561', 278144),
                                         ('31062002567', 712), ('53001080501', 198697),
                                         ('35503080100', 85788)]:
                    area = AdminArea.objects.get(code=code)
                    locality = '5300108_urban' if code.startswith('53') else code[:7] + '00'
                    self.assertEqual((area.level, area.parent_id, area.pop_latest),
                                     (5, 'brazil_' + locality, population))
                self.assertEqual(AdminArea.objects.get(code='5300108').pop_latest, 2817381)
                self.assertEqual(AdminArea.objects.get(code='31').level, 2)
                payload = self.client.get('/api/admin-areas/brazil_3106200/').json()
                self.assertEqual(payload['area']['subdivision_count'], 1)
                self.assertEqual({child['id'] for child in payload['children']}, {'brazil_310620000'})
                payload = self.client.get('/api/admin-areas/brazil_310620000/').json()
                self.assertEqual(payload['area']['subdivision_count'], 3)
                self.assertEqual({child['id'] for child in payload['children']}, {
                    'brazil_31062000567', 'brazil_31062002561', 'brazil_31062002567',
                })
                for municipality, locality, subdivision in (
                    ('5300108', '5300108_urban', '53001080501'),
                    ('3550308', '355030800', '35503080100'),
                ):
                    payload = self.client.get(f'/api/admin-areas/brazil_{municipality}/').json()
                    self.assertEqual(payload['area']['subdivision_count'], 1)
                    self.assertEqual({child['id'] for child in payload['children']}, {'brazil_' + locality})
                    payload = self.client.get(f'/api/admin-areas/brazil_{locality}/').json()
                    self.assertEqual(payload['area']['subdivision_count'], 1)
                    self.assertEqual({child['id'] for child in payload['children']}, {'brazil_' + subdivision})
                for area in AdminArea.objects.filter(level__gte=3).select_related('parent'):
                    self.assertEqual(area.level, area.parent.level + 1)
