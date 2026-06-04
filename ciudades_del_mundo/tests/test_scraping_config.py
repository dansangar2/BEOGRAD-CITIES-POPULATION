from decimal import Decimal

from django.test import TestCase

from ciudades_del_mundo.domain import DivisionSourceType, RepresentationConfig, RepresentationSystem, parse_pages
from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository


class ScrapingConfigTests(TestCase):
    def test_parse_pages_expands_paths_and_parses_decimal_overrides(self):
        pages = parse_pages(
            [
                {
                    "source": "table",
                    "path": ["admin", "spain/andalucia", "https://example.test/full"],
                    "lowest_level": 2,
                    "area_km2": "123.45",
                    "area_overrides": {"A": "1.50"},
                }
            ],
            slug="spain",
        )

        self.assertEqual([page.path for page in pages], ["spain/admin", "spain/andalucia", "https://example.test/full"])
        self.assertTrue(all(page.html_format == DivisionSourceType.TABLE for page in pages))
        self.assertTrue(all(page.lowest_level == 2 for page in pages))
        self.assertTrue(all(page.area_km2 == Decimal("123.45") for page in pages))
        self.assertTrue(all(page.area_overrides == {"A": Decimal("1.50")} for page in pages))

    def test_parse_pages_rejects_missing_path_and_negative_area(self):
        with self.assertRaisesRegex(ValueError, "path"):
            parse_pages([{"source": "table"}], slug="spain")

        with self.assertRaisesRegex(ValueError, "no puede ser negativo"):
            parse_pages([{"source": "table", "path": "admin", "area_km2": "-1"}], slug="spain")

    def test_representation_total_for_populations_supports_habitant_mode(self):
        config = RepresentationConfig.from_mapping(
            {"level": 2, "system": "dhondt", "habitant": 1000}
        )

        self.assertEqual(config.system, RepresentationSystem.DHONDT)
        self.assertEqual(config.total_for_populations([1, 999, 1000, 1001, None, -5]), 5)

    def test_real_sql_configs_load_without_network(self):
        repository = PythonScrapingConfigRepository()
        slugs = repository.list_slugs()

        self.assertGreater(len(slugs), 20)
        for slug in slugs:
            with self.subTest(slug=slug):
                config = repository.get(slug)
                self.assertEqual(config.slug, slug)
                self.assertTrue(config.country_code)
                self.assertTrue(config.base_url.startswith("https://"))
                self.assertGreater(len(config.pages), 0)
                self.assertTrue(all(page.path for page in config.pages))
                self.assertTrue(all(page.html_format in {item.value for item in DivisionSourceType} for page in config.pages))
