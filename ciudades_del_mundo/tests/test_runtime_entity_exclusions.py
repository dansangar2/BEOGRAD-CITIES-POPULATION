from django.test import TestCase

from ciudades_del_mundo.application.citypopulation_linking import apply_runtime_config_extensions
from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config


class RuntimeEntityExclusionTests(TestCase):
    def test_sql_exclusions_are_scoped_to_one_config_and_exact_source_codes(self):
        pages = '\n[[pages]]\nsource = "cities"\npath = ["cities"]\n'
        upsert_scraping_config('excluded', 'scrape_schema_version = 2\nexclude_entity_codes = ["urban"]\n' + pages)
        upsert_scraping_config('included', 'scrape_schema_version = 2\n' + pages)
        repository = PythonScrapingConfigRepository()
        configs = [repository.get('excluded'), repository.get('included')]
        attach_runtime_config_extensions(configs)
        for config in configs:
            with self.subTest(config=config.slug):
                rows = [
                    ScrapedAdminArea(code='urban', name='Aggregate', level=4, country_code=config.country_code, pop_latest=100),
                    ScrapedAdminArea(code='urban_real', name='Real locality', level=4, country_code=config.country_code, pop_latest=50),
                ]
                actual = apply_runtime_config_extensions(config, rows)
                expected = rows[1:] if config.slug == 'excluded' else rows
                self.assertEqual(actual, expected)

    def test_excluding_parent_keeps_unselected_children_for_link_validation(self):
        upsert_scraping_config(
            'branch',
            'scrape_schema_version = 2\nexclude_entity_codes = ["parent"]\n'
            '[[pages]]\nsource = "cities"\npath = ["cities"]\n',
        )
        config = PythonScrapingConfigRepository().get('branch')
        attach_runtime_config_extensions([config])
        parent = ScrapedAdminArea(code='parent', name='Parent', level=1, country_code='branch')
        child = ScrapedAdminArea(code='child', name='Child', level=2, country_code='branch', parent_code='parent')
        self.assertEqual(apply_runtime_config_extensions(config, [parent, child]), [child])
