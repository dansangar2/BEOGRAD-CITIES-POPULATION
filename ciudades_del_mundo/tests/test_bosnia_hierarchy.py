from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.application.citypopulation_linking import apply_runtime_config_extensions
from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping import CityPopulationCitiesAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions


def _row(code, name, status, population, *, parent=None, qid=''):
    parent_cell = f'<td class="radm">{parent}</td>' if parent else ''
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'{parent_cell}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id='tl', footer=''):
    parent_header = '<th class="radm">Municipality</th>' if table_id == 'ts' else ''
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent_header}'
        '<th class="rpop" data-coldate="2013-10-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class MemoryRepository:
    def save_many(self, country_code, entities):
        self.entities = entities
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        return 0


class BosniaHierarchyTests(unittest.TestCase):
    def test_srpska_keeps_entity_and_canton_roles_above_l3_municipalities(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'bosnia.toml'
        content = path.read_text(encoding='utf-8')
        config = parse_scraping_job_config('bosnia', content)
        canton = _row('6755', 'Bosansko-Podrinjski', 'Cant', 100, qid='Q18256')
        district = _row('6871', 'Brčko', 'ADist', 80, qid='Q194483')
        federation = _row('2112', 'Federacija Bosna i Hercegovina', 'AReg', 100, qid='Q11198')
        srpska = _row('2111', 'Republika Srpska', 'AReg', 100, qid='Q11196')
        municipality = _row('11452', 'Goražde', 'Municipality', 100, qid='Q131128')
        rs_municipality = _row('20010', 'Banja Luka', 'City', 100, qid='Q131127')
        brcko_municipality = _row('30163', 'Brčko', 'Municipality', 80, qid='Q322828')
        pages = {
            'bosnia/cities': _table(
                f'<tbody class="adm">{district}</tbody><tbody>{canton}</tbody>'
                f'<tbody class="adm">{federation}</tbody><tbody class="adm">{srpska}</tbody>',
                footer=_row('212', 'Bosnia and Herzegovina', '', 280, qid='Q225'),
            ) + _table('<tbody>' + _row('11739', 'Banja Luka', 'Urban Settlement', 60, parent='Republika Srpska', qid='Q131127') + '</tbody>', table_id='ts'),
            'bosnia/bosanskopodrinjski': _table(f'<tbody>{municipality}</tbody>', footer=canton)
            + _table('<tbody>' + _row('117994', 'Goražde', 'Urban Settlement', 70, parent='Goražde', qid='Q131128') + '</tbody>', table_id='ts'),
            'bosnia/republikasrpska': _table(f'<tbody>{rs_municipality}</tbody>', footer=_row('200', 'Republika Srpska', 'Entity', 100, qid='Q11196'))
            + _table('<tbody>' + _row('200026', 'Banja Luka', 'Urban Settlement', 60, parent='Banja Luka', qid='Q131127') + '</tbody>', table_id='ts'),
            'bosnia/brcko': _table(f'<tbody>{brcko_municipality}</tbody>', footer=_row('300', 'Brčko', 'District', 80, qid='Q194483'))
            + _table('<tbody>' + _row('300101', 'Bijela', 'Settlement', 30, parent='Brčko')
                     + _row('300136', 'Brčko', 'Urban Settlement', 50, parent='Brčko', qid='Q322828') + '</tbody>', table_id='ts'),
        }
        config = replace(config, pages=tuple(p for p in config.pages if p.path in pages), cities=[])
        self.assertEqual(len(config.pages), len(pages))
        for key, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, key, value)
        saved_html = {config.base_url + key + '/': value for key, value in pages.items()}

        class SavedPages:
            def get(self, url):
                return saved_html[url]

        for workers in (1, 3):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, 'get', side_effect=saved_html.__getitem__):
                repo = MemoryRepository()
                ScrapeAdminAreas(
                    repo, [CityPopulationCitiesScraper(), CityPopulationCitiesAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                rows = {e.code: e for e in repo.entities}
                self.assertEqual(len(rows), 13)
                self.assertEqual({e.code for e in rows.values() if e.level == 1}, {'2111', '2112', '6871'})
                self.assertEqual((rows['200'].level, rows['200'].parent_code), (2, '2111'))
                self.assertNotIn('300', rows)
                for code, parent in [('11452', '6755'), ('20010', '200'), ('30163', '6871')]:
                    self.assertEqual((rows[code].level, rows[code].parent_code), (3, parent))
                for code, parent in [('117994', '11452'), ('200026', '20010'), ('300101', '30163'), ('300136', '30163')]:
                    self.assertEqual((rows[code].level, rows[code].parent_code), (4, parent))
                self.assertEqual(rows['6755'].parent_code, '2112')
                self.assertEqual(rows['20010'].pop_latest, 100)
                self.assertEqual(rows['200026'].pop_latest, 60)

    def test_sarajevo_unifies_exactly_four_municipalities_and_preserves_sources(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'bosnia.toml'
        content = path.read_text(encoding='utf-8')
        config = parse_scraping_job_config('bosnia', content)
        canton = _row('6758', 'Sarajevo', 'Cant', 150, qid='Q18276')
        municipalities = [
            ('10839', 'Centar Sarajevo', 10), ('10871', 'Novi Grad Sarajevo', 20),
            ('11568', 'Novo Sarajevo', 30), ('11584', 'Stari Grad Sarajevo', 40),
        ]
        municipal_rows = ''.join(_row(code, name, 'City (Constituent Municipality)', pop) for code, name, pop in municipalities)
        municipal_rows += _row('10928', 'Vogošća', 'Municipality', 50)
        settlements = ''.join(_row(code + '001', name + ' locality', 'Settlement', pop, parent=name) for code, name, pop in municipalities)
        settlements += _row('146013', 'Vogošća', 'Urban Settlement', 50, parent='Vogošća')
        html = {
            'bosnia/cities': _table(
                '<tbody class="adm">' + _row('2112', 'Federacija Bosna i Hercegovina', 'AReg', 150, qid='Q11198') + '</tbody>'
                + f'<tbody>{canton}</tbody>', footer=_row('212', 'Bosnia and Herzegovina', '', 150, qid='Q225'),
            ) + _table('<tbody>' + _row('11746', 'Sarajevo', 'Urban Settlement', 120, parent='Sarajevo', qid='Q11194') + '</tbody>', table_id='ts'),
            'bosnia/sarajevo': _table(f'<tbody>{municipal_rows}</tbody>', footer=canton)
            + _table(f'<tbody>{settlements}</tbody>', table_id='ts'),
        }
        config = replace(config, pages=tuple(p for p in config.pages if p.path in html), cities=[c for c in config.cities if c.name == 'Sarajevo'])
        for key, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, key, value)
        saved_html = {config.base_url + key + '/': value for key, value in html.items()}

        class SavedPages:
            def get(self, url):
                return saved_html[url]

        for workers in (1, 3):
            with self.subTest(workers=workers), patch.object(CityPopulationClient, 'get', side_effect=saved_html.__getitem__):
                repo = MemoryRepository()
                ScrapeAdminAreas(
                    repo, [CityPopulationCitiesScraper(), CityPopulationCitiesAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
                rows = {e.code: e for e in repo.entities}
                city = rows['11746']
                self.assertEqual((city.name, city.level, city.parent_code, city.city_merge_status, city.pop_latest), ('Sarajevo', 3, '6758', 1, 100))
                self.assertEqual({e.code for e in rows.values() if e.parent_code == '11746'}, {code for code, _, _ in municipalities})
                for code, _, pop in municipalities:
                    self.assertEqual((rows[code].level, rows[code].parent_code, rows[code].city_merge_status, rows[code].pop_latest), (4, '11746', 2, pop))
                    self.assertEqual((rows[code + '001'].level, rows[code + '001'].parent_code, rows[code + '001'].pop_latest), (5, code, pop))
                self.assertEqual((rows['10928'].level, rows['10928'].parent_code, rows['10928'].pop_latest), (3, '6758', 50))
                self.assertEqual(rows['146013'].level, 4)

    def test_east_sarajevo_unifies_six_rs_units_and_excludes_urban_aggregate(self):
        path = Path(__file__).resolve().parents[1] / 'subdivisions' / 'bosnia.toml'
        content = path.read_text(encoding='utf-8')
        config = parse_scraping_job_config('bosnia', content)
        for key, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, key, value)
        city_config = next(c for c in config.cities if c.name == 'Istočno Sarajevo')
        units = [
            ('20176', 'Istočna Ilidža', 14437), ('20206', 'Istočno Novo Sarajevo', 10401),
            ('20214', 'Istočni Stari Grad', 1116), ('20443', 'Pale', 20359),
            ('20532', 'Sokolac', 11620), ('20591', 'Trnovo (RS)', 1983),
        ]
        def area(code, name, level, parent=None, **values):
            return ScrapedAdminArea(code=code, name=name, level=level, country_code='bosnia', parent_code=parent, **values)
        entities = [
            area('bosnia', 'Bosnia', 0), area('2111', 'Republika Srpska', 1, 'bosnia'),
            area('200', 'Republika Srpska', 2, '2111'), area('6758', 'Sarajevo', 2, 'bosnia'),
            area('20168', 'Zvornik', 3, '200', entity_type='City', pop_latest=54407),
            area('fbh_trnovo', 'Trnovo', 3, '6758', entity_type='Municipality', pop_latest=1500),
            area('209xxx', 'Sarajevo', 4, '20168', entity_type='Urban Settlement', pop_latest=13641),
        ]
        for code, name, population in units:
            entities.append(area(code, name, 3, '200', entity_type='City (Constituent Municipality)', pop_latest=population))
            entities.append(area(code + '_place', name + ' locality', 4, code, entity_type='Settlement', pop_latest=10))
        transformed = apply_configured_cities('bosnia', entities, [city_config])
        transformed = apply_runtime_config_extensions(config, transformed)
        rows = {e.code: e for e in transformed}
        city = rows['istocno_sarajevo']
        self.assertEqual((city.level, city.parent_code, city.city_merge_status, city.pop_latest), (3, '200', 1, 59916))
        self.assertEqual({e.code for e in transformed if e.parent_code == city.code}, {code for code, _, _ in units})
        for code, _, population in units:
            self.assertEqual((rows[code].level, rows[code].parent_code, rows[code].city_merge_status, rows[code].pop_latest), (4, city.code, 2, population))
            self.assertEqual((rows[code + '_place'].level, rows[code + '_place'].parent_code), (5, code))
        self.assertNotIn('209xxx', rows)
        self.assertEqual((rows['fbh_trnovo'].level, rows['fbh_trnovo'].parent_code), (3, '6758'))
        self.assertEqual((rows['20168'].level, rows['20168'].parent_code), (3, '200'))
