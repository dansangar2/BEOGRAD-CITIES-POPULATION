from decimal import Decimal

from django.test import TestCase

from ciudades_del_mundo.domain import DivisionSourceType, RepresentationConfig, RepresentationSystem, parse_pages
from ciudades_del_mundo.services.scraping_configs import parse_config_metadata
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
        self.assertEqual([page.block_index for page in pages], [0, 0, 0])
        self.assertEqual([page.path_index for page in pages], [0, 1, 2])

    def test_parse_pages_rejects_missing_path_and_negative_area(self):
        with self.assertRaisesRegex(ValueError, "path"):
            parse_pages([{"source": "cities"}], slug="spain")

        with self.assertRaisesRegex(ValueError, "no puede ser negativo"):
            parse_pages([{"source": "cities", "path": "admin", "area_km2": "-1"}], slug="spain")

    def test_parse_pages_rejects_legacy_source(self):
        with self.assertRaisesRegex(ValueError, "source debe ser 'cities' o 'admin'"):
            parse_pages([{"source": "auto", "path": "ceuta"}], slug="spain")

    def test_parse_pages_skips_disabled_blocks(self):
        pages = parse_pages(
            [
                {"source": "admin", "path": "admin"},
                {"source": "cities", "path": "broken", "enabled": False},
                {"source": "cities", "path": "andalucia"},
            ],
            slug="spain",
            schema_version=2,
        )

        self.assertEqual([page.path for page in pages], ["spain/admin", "spain/andalucia"])
        self.assertEqual([page.block_index for page in pages], [0, 2])

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

    def test_wikimedia_v3_metadata_is_no_longer_valid(self):
        metadata = parse_config_metadata(
            "spain",
            """
scrape_schema_version = 3
source = "wikimedia"
country_code = "spain"
name = "Spain"
wikidata_id = "Q29"
""",
        )

        self.assertFalse(metadata.is_valid)
        self.assertEqual(metadata.schema_version, 3)
        self.assertEqual(metadata.pages_count, 0)
        self.assertIn("página", metadata.validation_error)
