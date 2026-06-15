from decimal import Decimal

from django.test import TestCase

from ciudades_del_mundo.domain import DivisionSourceType, RepresentationConfig, RepresentationSystem, parse_pages
from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository


class ScrapingConfigTests(TestCase):
    def test_parse_pages_expands_paths_and_parses_decimal_overrides(self):
        pages = parse_pages(
            [
                {
                    "source": "cities",
                    "path": ["admin", "spain/andalucia", "https://example.test/full"],
                    "force_highest_level": 2,
                    "area_km2": "123.45",
                    "area_overrides": {"A": "1.50"},
                    "include": {"infosection": False, "major_subdivision": False, "cities": True},
                    "sum_to_root": True,
                }
            ],
            slug="spain",
        )

        self.assertEqual([page.path for page in pages], ["spain/admin", "spain/andalucia", "https://example.test/full"])
        self.assertTrue(all(page.html_format == DivisionSourceType.CITIES for page in pages))
        self.assertTrue(all(page.lowest_level == 2 for page in pages))
        self.assertTrue(all(page.area_km2 == Decimal("123.45") for page in pages))
        self.assertTrue(all(page.area_overrides == {"A": Decimal("1.50")} for page in pages))
        self.assertTrue(all(page.include_infosection is False for page in pages))
        self.assertTrue(all(page.include_major_subdivision is False for page in pages))
        self.assertTrue(all(page.include_cities is True for page in pages))
        self.assertTrue(all(page.force_highest_level == 2 for page in pages))
        self.assertTrue(all(page.sum_to_root is True for page in pages))

    def test_parse_pages_rejects_missing_path_and_negative_area(self):
        with self.assertRaisesRegex(ValueError, "path"):
            parse_pages([{"source": "cities"}], slug="spain")

        with self.assertRaisesRegex(ValueError, "no puede ser negativo"):
            parse_pages([{"source": "cities", "path": "admin", "area_km2": "-1"}], slug="spain")

    def test_parse_pages_rejects_legacy_source(self):
        with self.assertRaisesRegex(ValueError, "source debe ser 'cities' o 'admin'"):
            parse_pages([{"source": "auto", "path": "ceuta"}], slug="spain")

    def test_representation_total_for_populations_supports_habitant_mode(self):
        config = RepresentationConfig.from_mapping(
            {"level": 2, "system": "dhondt", "habitant": 1000}
        )

        self.assertEqual(config.system, RepresentationSystem.DHONDT)
        self.assertEqual(config.total_for_populations([1, 999, 1000, 1001, None, -5]), 5)

    def test_migrated_sql_configs_load_without_network(self):
        repository = PythonScrapingConfigRepository()
        for slug in ("spain", "italy", "belgium", "france"):
            with self.subTest(slug=slug):
                config = repository.get(slug)
                self.assertEqual(config.slug, slug)
                self.assertTrue(config.country_code)
                self.assertTrue(config.base_url.startswith("https://"))
                self.assertGreater(len(config.pages), 0)
                self.assertTrue(all(page.path for page in config.pages))
                self.assertTrue(all(page.html_format in {item.value for item in DivisionSourceType} for page in config.pages))
