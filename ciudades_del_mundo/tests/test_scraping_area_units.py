"""Area units must be normalized before linking, rollups and SQL persistence."""

from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig, parse_pages
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import (
    CityPopulationAdminScraper,
    CityPopulationCitiesAdminScraper,
    CityPopulationCitiesScraper,
)
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scrape_resume import ScrapeResumeStore


def _row(code, name, area, population=100, *, cell_class="rarea", data_area=None):
    attribute = f' data-area="{data_area}"' if data_area is not None else ""
    return (
        f'<tr><td class="rname" id="i{code}"{attribute}>{name}</td>'
        f'<td class="{cell_class}">{area}</td><td class="rpop">{population}</td></tr>'
    )


def _table(unit="hect", *, root_area="78,003", root_container="tfoot", cell_class="rarea", data_area=None):
    return (
        '<table id="tl"><thead><tr><th>Name</th>'
        f'<th class="rarea">Area A ({unit})</th>'
        '<th class="rpop" data-coldate="2025-01-01">Population</th></tr></thead>'
        '<tbody class="admin1">'
        + _row("A", "Region", "7,871", 100, cell_class=cell_class, data_area=data_area)
        + '</tbody>'
        + f'<{root_container}>' + _row("ROOT", "Example", root_area, 1000, cell_class=cell_class, data_area=data_area)
        + f'</{root_container.split()[0]}></table>'
    )


class ScrapingAreaUnitTests(unittest.TestCase):
    def test_hectares_in_body_and_country_total_across_scraping_systems(self):
        for scraper_type in (CityPopulationAdminScraper, CityPopulationCitiesScraper, CityPopulationCitiesAdminScraper):
            for unit in ("hect", "hectares", "ha"):
                for root_container in ("tfoot", 'tbody class="admin0"'):
                    with self.subTest(scraper=scraper_type.__name__, unit=unit, root=root_container):
                        entities = scraper_type().scrape_html(
                            _table(unit, root_container=root_container),
                            "https://example.invalid/example/", "example", 0,
                        )
                        by_code = {e.code: e for e in entities}
                        self.assertEqual(by_code["example"].area_km2, 780.03)
                        self.assertEqual(by_code["A"].area_km2, 78.71)
                        self.assertAlmostEqual(by_code["example"].density, 1000 / 780.03)
                        self.assertEqual(by_code["A"].parent_code, "example")

    def test_km2_and_missing_unit_keep_existing_values(self):
        for unit in ("km²", "km2", ""):
            with self.subTest(unit=unit):
                entities = CityPopulationAdminScraper().scrape_html(
                    _table(unit, root_area="780.03"), "https://example.invalid/", "example", 0,
                )
                self.assertEqual(entities[0].area_km2, 780.03)

    def test_data_area_is_already_km2_even_when_visible_column_uses_hectares(self):
        entities = CityPopulationAdminScraper().scrape_html(
            _table(data_area="780.03"), "https://example.invalid/", "example", 0,
        )
        self.assertTrue(all(e.area_km2 == 780.03 for e in entities))

    def test_hidden_hectare_cells_are_not_converted_twice(self):
        entities = CityPopulationAdminScraper().scrape_html(
            _table(cell_class="noviz"), "https://example.invalid/", "example", 0,
        )
        self.assertEqual(entities[0].area_km2, 780.03)
        self.assertEqual(entities[1].area_km2, 78.71)

    def test_explicit_legacy_divisor_is_not_applied_twice(self):
        client = CityPopulationClient()
        row = BeautifulSoup(_table(), client.parser).find("tfoot").find("tr")
        parsed = client.parse_tr_tl(
            row, 0, 0, "2025-01-01", 2025, "example", "https://example.invalid/", area_divisor=100,
        )
        self.assertEqual(parsed.area_km2, 780.03)

    def test_infosection_converts_explicit_hectares_but_preserves_machine_area(self):
        for data_area, expected in ((None, 374.24), ("374.24", 374.24)):
            attribute = f' data-area="{data_area}"' if data_area else ""
            html = (
                '<div class="infosection"><p class="infoname">Example</p>'
                f'<p class="infotext"{attribute}><span class="val">37,424</span> hectares Area</p>'
                '</div>'
            )
            with self.subTest(data_area=data_area):
                entities = CityPopulationCitiesScraper().scrape_html(
                    html, "https://example.invalid/", "example", 0,
                )
                self.assertEqual(entities[0].area_km2, expected)

    def test_pre_fix_resume_checkpoint_is_not_reused(self):
        page = SimpleNamespace(index=1, path="cities", html_format="cities", lowest_level=0,
                               url="https://example.invalid/", html=_table(), found=1,
                               entities=(ScrapedAdminArea(code="example", name="Example", level=0,
                                                          country_code="example", area_km2=78003),))
        with TemporaryDirectory() as directory:
            store = ScrapeResumeStore(slug="example", country_code="example", content_hash="same",
                                     root=Path(directory))
            store.save_page(page)
            self.assertIsNotNone(store.load_page(page))
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            payload["version"] = 1
            store.path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(store.load_page(page))


class ScrapingAreaPersistenceTests(TestCase):
    def test_scraping_converts_hectares_before_persisting_country_and_regions(self):
        pages = parse_pages([{"source": "admin", "path": "admin",
                              "include": {"infosection": True, "admin1": True, "admin2": False}}],
                            slug="example", schema_version=2)
        self._run(pages, {"admin/": _table()})
        root = AdminArea.objects.get(country_code="example", level=0)
        region = AdminArea.objects.get(country_code="example", code="A")
        self.assertEqual(root.area_km2, Decimal("780.03"))
        self.assertEqual(region.area_km2, Decimal("78.71"))
        self.assertEqual(region.parent_id, root.id)

    def test_overseas_hectares_are_converted_before_sum_to_root_and_repetition(self):
        pages = parse_pages([
            {"source": "cities", "path": "cities",
             "include": {"infosection": True, "major_subdivision": False, "cities": False}},
            {"source": "citiesadmin", "path": "cities/overseas", "sum_to_root": True,
             "repeat": {"infosection": 2},
             "include": {"infosection": True, "major_subdivision": True,
                         "minor_subdivision": False, "cities": False}},
        ], slug="example", schema_version=2)
        mainland = ('<div class="infosection"><p class="infoname">Example</p>'
                    '<span data-area="543941" data-newpop="1000" data-newdate="2025-01-01"></span></div>')
        overseas = _table(root_area="37,424").replace("Example", "Overseas")
        self._run(pages, {"cities/": mainland, "cities/overseas/": overseas})
        root = AdminArea.objects.get(country_code="example", level=0)
        self.assertEqual(root.area_km2, Decimal("544315.24"))
        self.assertEqual(root.pop_latest, 2000)
        repeats = AdminArea.objects.filter(country_code="example", name="Overseas").order_by("level")
        self.assertEqual(list(repeats.values_list("level", "area_km2")),
                         [(1, Decimal("374.24")), (2, Decimal("374.24"))])
        self.assertEqual(root.children.get().area_km2, Decimal("374.24"))

    def _run(self, pages, html_by_path):
        class SavedHtml:
            def get(self, url):
                return html_by_path[url.removeprefix("https://example.invalid/en/example/")]

        config = ScrapingJobConfig(slug="example", country_code="example",
                                   base_url="https://example.invalid/en/", pages=tuple(pages))
        with patch.object(CityPopulationClient, "get", side_effect=SavedHtml().get):
            ScrapeAdminAreas(
                DjangoAdminAreaRepository(),
                [CityPopulationAdminScraper(), CityPopulationCitiesScraper(), CityPopulationCitiesAdminScraper()],
            ).run(config)
