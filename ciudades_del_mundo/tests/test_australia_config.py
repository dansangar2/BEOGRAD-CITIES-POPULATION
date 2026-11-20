import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, RequestFactory

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


def _row(code, name, status, population, qid=''):
    return (f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
            f'<td class="rstatus">{status}</td><td class="rpop">{population}</td></tr>')


def _table(body, *, table_id='tl', footer='', date='2021-08-11'):
    return (f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
            f'<th class="rpop" data-coldate="{date}">Population</th></tr></thead>'
            f'{body}<tfoot>{footer}</tfoot></table>')


class AustraliaConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.content=(Path(__file__).resolve().parents[1]/'subdivisions'/'australia.toml').read_text(encoding='utf-8')
        cls.config=parse_scraping_job_config('australia',cls.content)
        for name,value in parse_runtime_config_extensions(cls.content).items():
            object.__setattr__(cls.config,name,value)

    def test_plan_covers_states_and_external_territories(self):
        self.assertEqual(len(self.config.pages),14)
        self.assertEqual(self.config.legal_subdivision_level,2)
        localities=[p for p in self.config.pages if p.path not in ('australia/cities','australia/admin')]
        self.assertEqual({p.path for p in localities},{'australia/'+p for p in (
            'newsouthwales','victoria','queensland','southaustralia','westernaustralia',
            'tasmania','northernterritory','australiancapitalterritory',
            'cities/christmasisland','cities/cocosislands','cities/jervisbay','cities/norfolkisland',
        )})
        for p in localities:
            self.assertEqual(p.include_sections,('cities',))
            self.assertEqual(p.force_highest_level,3)
            self.assertTrue(p.preserve_source_codes)

    def test_localities_keep_statistical_state_parents_and_all_lga_data(self):
        config=replace(self.config,pages=tuple(p for p in self.config.pages if p.path in (
            'australia/cities','australia/admin','australia/newsouthwales',
            'australia/victoria','australia/cities/christmasisland',
        )))
        for name,value in parse_runtime_config_extensions(self.content).items():
            object.__setattr__(config,name,value)
        country=_row('australia','Australia','Commonwealth',10000,'Q408')
        nsw=_row('1','New South Wales','Federal State',5000,'Q3224')
        vic=_row('2','Victoria','Federal State',4000,'Q36687')
        territories=_row('9','Other Territories','Territories',1000)
        lga=_row('11500','Council','Area',5000,'Q1')
        island=_row('99200','Christmas Island','Territory',1000,'Q31063')
        pages={
            'australia/cities':_table('',footer=country),
            'australia/admin':_table(
                f'<tbody class="admin1">{nsw}</tbody><tbody class="admin2">{lga}</tbody>'
                f'<tbody class="admin1">{vic}{territories}</tbody><tbody class="admin2">{island}</tbody>',
                footer=country,date='2024-06-30',
            ),
            'australia/newsouthwales':_table('',footer=nsw)+_table(
                '<tbody>'+_row('115001','Urban center','Urban Center',100,'Q1')
                +_row('122001','Village','Locality',50,'Q2')
                +_row('111001x','Cross-border city (in: NSW-Vic.)','Urban Center',300,'Q3')+'</tbody>',table_id='ts',
            ),
            'australia/victoria':_table('',footer=vic)+_table(
                '<tbody>'+_row('222001','Village','Locality',75,'Q2')+'</tbody>',table_id='ts',
            ),
            'australia/cities/christmasisland':_table('<tbody>'+island+'</tbody>',footer=country)
            +_table('<tbody>'+_row('19428','The Settlement','Loc',1000,'Q30980')+'</tbody>',table_id='ts'),
        }

        class SavedPages:
            def get(self,url):
                return pages[url.removeprefix(config.base_url).strip('/')]

        for workers in (1,3):
            with patch('ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
                       new=lambda client,url:SavedPages().get(url)):
                result=ScrapeAdminAreas(DjangoAdminAreaRepository(),
                    [CityPopulationCitiesScraper(),CityPopulationAdminScraper()],
                    page_workers=workers,html_fetcher=SavedPages()).run(config)
            self.assertEqual(result.found,11)
            for code,parent,pop in (
                ('115001','1',100),('122001','1',50),('111001x','1',300),
                ('222001','2',75),('19428','99200',1000),
            ):
                row=AdminArea.objects.get(pk='australia_'+code)
                self.assertEqual((row.level,row.parent_id,row.pop_latest),(3,'australia_'+parent,pop))
            council=AdminArea.objects.get(pk='australia_11500')
            self.assertEqual((council.level,council.parent_id,council.pop_latest),(2,'australia_1',5000))
            self.assertEqual(council.pop_latest_date.isoformat(),'2024-06-30')
            payload=json.loads(api_admin_area_detail(RequestFactory().get('/'),'australia_1').content)
            self.assertEqual(payload['area']['subdivision_count'],4)
            self.assertEqual({g['level']:len(g['children']) for g in payload['child_groups']},{2:1,3:3})
