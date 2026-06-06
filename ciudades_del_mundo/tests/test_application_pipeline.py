from decimal import Decimal
import unittest

from ciudades_del_mundo.application.scrape_admin_areas import CachedScrapePage, ScrapeAdminAreas
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


class FakeHtmlScraper(FakeScraper):
    def scrape_page(self, base_url, country_code, page):
        from ciudades_del_mundo.ports import ScrapedHtmlPage

        return ScrapedHtmlPage(
            entities=self.scrape(base_url, country_code, page),
            html="<html>page</html>",
            url="https://example.test/en/fake/admin/",
        )


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

    def test_run_exposes_downloaded_html_on_page_complete_when_scraper_supports_it(self):
        repository = FakeRepository()
        completes = []
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeHtmlScraper()],
            on_page_complete=completes.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            legal_subdivision_level=1,
            pages=[ScrapingPageConfig(path="fake/admin", html_format="table", lowest_level=0)],
        )

        use_case.run(config)

        self.assertEqual(completes[0].html, "<html>page</html>")
        self.assertEqual([entity.name for entity in completes[0].entities], ["Testland", "Child", "Duplicate Child"])

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

    def test_run_reuses_cached_page_without_scraping_it_again(self):
        repository = FakeRepository()
        starts = []
        cached_events = []
        cached_root = ScrapedAdminArea(
            code="fake",
            name="Cached Testland",
            level=0,
            country_code="fake",
            pop_latest=100,
        )
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[],
            on_page_start=starts.append,
            cached_page_loader=lambda page: CachedScrapePage(
                found=1,
                html="<html>cached</html>",
                entities=(cached_root,),
            ),
            on_cached_page=cached_events.append,
        )
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[ScrapingPageConfig(path="fake/admin", html_format="missing", lowest_level=0)],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 1)
        self.assertEqual(starts, [])
        self.assertEqual(cached_events[0].html, "<html>cached</html>")
        self.assertEqual([entity.name for entity in repository.saved_entities], ["Cached Testland"])


class FakeHtmlFetcher:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return f"html:{url}"


class PrefetchHtmlScraper:
    html_format = "table"

    def scrape_html(self, html, url, country_code, level):
        code = url.rstrip("/").split("/")[-1]
        return [
            ScrapedAdminArea(
                code=code,
                name=html,
                level=level,
                country_code=country_code,
                pop_latest=1,
            )
        ]


class CountingPrefetchHtmlScraper(PrefetchHtmlScraper):
    def __init__(self):
        self.calls = []

    def scrape_html(self, html, url, country_code, level):
        self.calls.append((url, level))
        return super().scrape_html(html, url, country_code, level)


class PrefetchPipelineTests(unittest.TestCase):
    def test_page_prefetch_downloads_once_per_url_and_completes_in_config_order(self):
        repository = FakeRepository()
        starts = []
        completes = []
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/b", html_format="table", lowest_level=2),
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=3),
            ],
        )

        fetcher = FakeHtmlFetcher()
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[PrefetchHtmlScraper()],
            on_page_start=starts.append,
            on_page_complete=completes.append,
            page_workers=3,
            html_fetcher=fetcher,
        )

        use_case.run(config)

        fetched_urls = fetcher.urls
        self.assertEqual(
            fetched_urls,
            [
                "https://example.test/en/fake/a/",
                "https://example.test/en/fake/b/",
            ],
        )
        self.assertEqual([event.url for event in starts], [
            "https://example.test/en/fake/a/",
            "https://example.test/en/fake/b/",
            "https://example.test/en/fake/a/",
        ])
        self.assertEqual([event.url for event in completes], [
            "https://example.test/en/fake/a/",
            "https://example.test/en/fake/b/",
            "https://example.test/en/fake/a/",
        ])
        self.assertEqual([entity.level for entity in repository.saved_entities], [1, 2])
        self.assertEqual(repository.saved_entities[0].name, "html:https://example.test/en/fake/a/")


    def test_page_prefetch_reuses_parsed_duplicate_pages(self):
        repository = FakeRepository()
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
            ],
        )

        fetcher = FakeHtmlFetcher()
        scraper = CountingPrefetchHtmlScraper()
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[scraper],
            page_workers=2,
            html_fetcher=fetcher,
        )

        use_case.run(config)

        self.assertEqual(fetcher.urls, ["https://example.test/en/fake/a/"])
        self.assertEqual(scraper.calls, [("https://example.test/en/fake/a/", 1)])

    def test_page_prefetch_requires_injected_html_fetcher(self):
        repository = FakeRepository()
        config = ScrapingJobConfig(
            slug="fake",
            country_code="fake",
            base_url="https://example.test/en/",
            pages=[
                ScrapingPageConfig(path="fake/a", html_format="table", lowest_level=1),
                ScrapingPageConfig(path="fake/b", html_format="table", lowest_level=2),
            ],
        )
        use_case = ScrapeAdminAreas(
            repository=repository,
            scrapers=[FakeHtmlScraper()],
            page_workers=3,
        )

        use_case.run(config)

        self.assertEqual([entity.name for entity in repository.saved_entities], ["Testland", "Child"])


class SpanishSyntheticRootScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(code="spain", name="Spain", level=0, country_code="spain", pop_latest=1),
            ScrapedAdminArea(code="51", name="Ceuta", level=1, country_code="spain", parent_code="spain", pop_latest=1),
            ScrapedAdminArea(
                code="spain",
                name="Ceuta (Autonomous City)",
                level=1,
                country_code="spain",
                parent_code=None,
                pop_latest=1,
                url="https://www.citypopulation.de/en/spain/ceuta/",
            ),
            ScrapedAdminArea(
                code="51001",
                name="Ceuta",
                level=2,
                country_code="spain",
                parent_code="spain",
                pop_latest=1,
                url="https://www.citypopulation.de/en/spain/ceuta/ceuta/51001__ceuta/",
            ),
        ]


class SpanishSyntheticRootTests(unittest.TestCase):
    def test_autonomous_city_synthetic_root_children_attach_to_real_root(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[SpanishSyntheticRootScraper()])
        config = ScrapingJobConfig(
            slug="spain",
            country_code="spain",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="spain/ceuta", html_format="table", lowest_level=1)],
        )

        result = use_case.run(config)

        self.assertEqual(result.found, 3)
        municipality = next(entity for entity in repository.saved_entities if entity.code == "51001")
        self.assertEqual(municipality.parent_code, "51")
        self.assertEqual(municipality.level, 2)
        self.assertEqual(
            [entity.name for entity in repository.saved_entities if entity.code == "spain"],
            ["Spain"],
        )

class RuntimeConfigExtensionScraper:
    html_format = "table"

    def scrape(self, base_url, country_code, page):
        return [
            ScrapedAdminArea(
                code="france",
                name="France",
                level=0,
                country_code="france",
                area_km2=Decimal("543940"),
                pop_latest=68_000_000,
            ),
            ScrapedAdminArea(
                code="IDF",
                name="Île-de-France",
                level=2,
                country_code="france",
                parent_code="france",
                pop_latest=12_000_000,
            ),
            ScrapedAdminArea(
                code="GUF",
                name="French Guiana",
                level=3,
                country_code="france",
                parent_code=None,
                area_km2=Decimal("83534"),
                pop_latest=298_554,
            ),
        ]


class RuntimeConfigExtensionTests(unittest.TestCase):
    def test_synthetic_containers_parent_overrides_and_root_metric_additions(self):
        repository = FakeRepository()
        use_case = ScrapeAdminAreas(repository=repository, scrapers=[RuntimeConfigExtensionScraper()])
        config = ScrapingJobConfig(
            slug="france",
            country_code="france",
            base_url="https://www.citypopulation.de/en/",
            pages=[ScrapingPageConfig(path="france/admin", html_format="table", lowest_level=0)],
        )
        object.__setattr__(
            config,
            "runtime_synthetic_entities",
            (
                {
                    "code": "METRO",
                    "name": "Metropolitan France",
                    "level": 1,
                    "parent_code": "france",
                    "copy_metrics_from": "france",
                },
                {
                    "code": "OVERSEAS",
                    "name": "Overseas France",
                    "level": 1,
                    "parent_code": "france",
                    "metric_source_codes": ("GUF",),
                },
            ),
        )
        object.__setattr__(
            config,
            "runtime_parent_overrides",
            (
                {"match_levels": (2,), "parent_code": "METRO", "exclude_codes": ("METRO", "OVERSEAS")},
                {"codes": ("GUF",), "parent_code": "OVERSEAS", "level": 3},
            ),
        )
        object.__setattr__(config, "runtime_root_metric_sources", ({"code": "GUF"},))

        use_case.run(config)

        by_code = {entity.code: entity for entity in repository.saved_entities}
        self.assertEqual(by_code["METRO"].parent_code, "france")
        self.assertEqual(by_code["METRO"].pop_latest, 68_000_000)
        self.assertEqual(by_code["OVERSEAS"].pop_latest, 298_554)
        self.assertEqual(by_code["IDF"].parent_code, "METRO")
        self.assertEqual(by_code["GUF"].parent_code, "OVERSEAS")
        self.assertEqual(by_code["france"].pop_latest, 68_298_554)
        self.assertEqual(by_code["france"].area_km2, Decimal("627474"))
