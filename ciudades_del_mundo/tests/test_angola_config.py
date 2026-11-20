from pathlib import Path
import unittest
from unittest.mock import patch

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.domain import CITY_MERGE_SOURCE, CITY_MERGE_UNIFIED
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions


def _row(code, name, status, population, qid=''):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
        f'<td class="rstatus">{status}</td><td class="rpop">{population}</td></tr>'
    )


def _table(body, footer=''):
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2024-09-19">Population</th>'
        f'</tr></thead>{body}<tfoot>{footer}</tfoot></table>'
    )


def _config():
    content = (Path(__file__).resolve().parents[1] / 'subdivisions' / 'angola.toml').read_text(encoding='utf-8')
    config = parse_scraping_job_config('angola', content)
    for name, value in parse_runtime_config_extensions(content).items():
        object.__setattr__(config, name, value)
    return config


def _run_pages(html, *, workers=1):
    config = _config()

    class SavedPages:
        def get(self, url):
            return html[url.removeprefix(config.base_url).strip('/')]

    class MemoryRepository:
        def save_many(self, country_code, entities):
            self.entities = entities
            return len(entities), 0

        def delete_missing(self, country_code, ids):
            return 0

    repository = MemoryRepository()
    with patch('ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get',
               new=lambda client, url: SavedPages().get(url)):
        ScrapeAdminAreas(
            repository, [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=workers, html_fetcher=SavedPages(),
        ).run(config)
    return {entity.code: entity for entity in repository.entities}


class AngolaConfigTests(unittest.TestCase):
    def test_complete_pipeline_keeps_provinces_and_luanda_city_districts(self):
        bengo = _row('01', 'Bengo', 'Province', 20)
        luanda = _row('05', 'Luanda', 'Province', 160)
        municipalities = ''.join(_row(f'05{i:02}', f'District {i}', 'Municipality', 10, f'Q{100+i}') for i in range(1, 17))
        units = ''.join(_row(f'05{i:02}1', f'District {i}', 'Municipality', 10, f'Q{100+i}') for i in range(1, 17))
        html = {
            'angola/cities': _table('', _row('0', 'Angola', 'Republic', 180, 'Q916')),
            'angola/admin': _table(
                f'<tbody class="admin1">{bengo}</tbody><tbody class="admin2">'
                + _row('0101', 'Ambriz', 'Municipality', 20)
                + f'</tbody><tbody class="admin1">{luanda}</tbody><tbody class="admin2">{municipalities}</tbody>'
            ),
            'angola/communes/admin': _table(
                f'<tbody class="admin1">{bengo}</tbody><tbody class="admin2">'
                + _row('01011', 'North', 'Commune', 8, 'Q500')
                + _row('01012', 'South', 'Commune', 12, 'Q500')
                + f'</tbody><tbody class="admin1">{luanda}</tbody><tbody class="admin2">{units}</tbody>'
            ),
        }
        for workers in (1, 3):
            with self.subTest(workers=workers):
                entities = _run_pages(html, workers=workers)
                self.assertEqual(len(entities), 39)
                for code in ('01', '05'):
                    self.assertEqual((entities[code].level, entities[code].parent_code), (1, 'angola'))
                city = entities['luanda_city']
                self.assertEqual((city.level, city.parent_code, city.pop_latest, city.city_merge_status),
                                 (2, '05', 160, CITY_MERGE_UNIFIED))
                self.assertEqual([e.code for e in entities.values() if e.parent_code == '05'], ['luanda_city'])
                self.assertEqual(len([e for e in entities.values() if e.parent_code == city.code]), 16)
                for i in range(1, 17):
                    municipality = entities[f'05{i:02}']
                    unit = entities[f'05{i:02}1']
                    self.assertEqual((municipality.level, municipality.parent_code, municipality.city_merge_status),
                                     (3, city.code, CITY_MERGE_SOURCE))
                    self.assertEqual((unit.level, unit.parent_code, unit.pop_latest, unit.entity_type),
                                     (4, municipality.code, 10, 'Municipality'))
                self.assertEqual((entities['0101'].level, entities['0101'].parent_code), (2, '01'))
                for code, pop in (('01011', 8), ('01012', 12)):
                    self.assertEqual((entities[code].level, entities[code].parent_code, entities[code].pop_latest), (3, '0101', pop))
                for entity in entities.values():
                    if entity.parent_code:
                        self.assertLess(entities[entity.parent_code].level, entity.level)
