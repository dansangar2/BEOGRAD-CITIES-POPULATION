from decimal import Decimal
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.domain import AdminAreaSummary, ScrapedAdminArea, ScrapingJobConfig, ScrapingPageConfig


class FakeScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(
                code=country_code,
                name="Testland",
                level=0,
                country_code=country_code,
                area_km2=Decimal("1"),
                pop_latest=100,
            ),
            ScrapedAdminArea(
                code="child",
                name="Child",
                level=1,
                country_code=country_code,
                parent_code=country_code,
                area_km2=Decimal("1"),
                pop_latest=50,
            ),
            ScrapedAdminArea(
                code="child",
                name="Duplicate Child",
                level=1,
                country_code=country_code,
                parent_code=country_code,
                area_km2=Decimal("99"),
                pop_latest=999,
            ),
        ]


class FakeRepository:
    def __init__(self):
        self.reset_countries = []
        self.saved_country = None
        self.saved_entities = []
        self.deleted_ids = set()
        self.most_populated_assignments = []

    def reset_country(self, country_code):
        self.reset_countries.append(country_code)

    def save_many(self, country_code, entities):
        self.saved_country = country_code
        self.saved_entities = list(entities)
        return len(entities), 0

    def delete_missing(self, country_code, ids):
        self.deleted_ids = set(ids)
        return 0

    def list_summaries(self, country_code):
        return [
            AdminAreaSummary(id=f"{country_code}_{country_code}", level=0, parent_id=None, pop_latest=100),
            AdminAreaSummary(id=f"{country_code}_child", level=1, parent_id=f"{country_code}_{country_code}", pop_latest=50),
        ]

    def save_most_populated_assignments(self, assignments):
        self.most_populated_assignments = list(assignments)
        return len(assignments)

    def save_representatives(self, country_code, config):
        return 0


class ScrapeAdminAreasTests(unittest.TestCase):
    def test_run_deduplicates_applies_area_overrides_and_saves_ids(self):
        repository = FakeRepository()
        starts = []
        completes = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeScraper()],
            on_page_start=starts.append,
            on_page_complete=completes.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            legal_subdivision_level=1,
            reset_before_import=True,
            pages=[
                ScrapingPageConfig(
                    path="fake/admin",
                    html_format="table",
                    lowest_level=0,
                    area_km2=Decimal("10"),
                    area_overrides={"child": Decimal("2")},
                )
            ],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 2)
        self.assertEqual(result.created, 2)
        self.assertEqual(repository.reset_countries, ["fake"])
        self.assertEqual(repository.saved_country, "fake")
        self.assertEqual(repository.deleted_ids, {"fake_fake", "fake_child"})
        self.assertEqual([entity.name for entity in repository.saved_entities], ["Testland", "Child"])

        root, child = repository.saved_entities
        self.assertEqual(root.area_km2, Decimal("10"))
        self.assertEqual(root.density, Decimal("10"))
        self.assertEqual(child.area_km2, Decimal("2"))
        self.assertEqual(child.density, Decimal("25"))

        self.assertEqual(starts[0].url, "https://example.test/en/fake/admin/")
        self.assertEqual(completes[0].found, 3)
        self.assertEqual(len(repository.most_populated_assignments), 1)
        self.assertEqual(repository.most_populated_assignments[0].most_populated_id, "fake_child")

    def test_unknown_scraper_fails_before_persistence(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[])
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[ScrapingPageConfig(path="fake/admin", html_format="missing")],
        )

        with self.assertRaisesRegex(ValueError, "Unknown html_format"):
            use_case.run(config)

        self.assertEqual(repository.saved_entities, [])

