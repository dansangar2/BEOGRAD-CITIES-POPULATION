from dataclasses import replace
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, qid="", municipality=None):
    parent = f'<td class="radm" data-admid="{municipality}">{municipality}</td>' if municipality else ""
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'{parent}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id="tl", footer="", date="2013-08-10"):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        f'<th class="rpop" data-coldate="{date}">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class HondurasConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[1] / "subdivisions" / "honduras.toml"
        cls.config = parse_scraping_job_config("honduras", path.read_text(encoding="utf-8"))

    def test_plan_covers_all_departments_and_preserves_legal_municipalities(self):
        self.assertEqual(self.config.legal_subdivision_level, 2)
        pages = [page for page in self.config.pages if page.path not in {"honduras/admin", "honduras/cities"}]
        self.assertEqual(len(pages), 18)
        self.assertEqual(len({page.path for page in pages}), 18)
        self.assertIn("honduras/franciscomorazan", {page.path for page in pages})
        self.assertIn("honduras/islasdelabahia", {page.path for page in pages})
        for page in pages:
            self.assertEqual(page.force_highest_level, 3)
            self.assertEqual(page.parent_level, 2)
            self.assertIn("cities", page.include_sections)
            self.assertNotIn("major_subdivision", page.include_sections)

    def test_localities_remain_distinct_from_municipalities_and_reach_detail_api(self):
        config = replace(self.config, pages=tuple(
            page for page in self.config.pages
            if page.path in {"honduras/cities", "honduras/admin", "honduras/atlantida", "honduras/franciscomorazan"}
        ))
        department = _row("01", "Atlantida", "Department", 436252, "Q622619")
        capital_department = _row("08", "Francisco Morazan", "Department", 1508906, "Q484955")
        municipalities = _row("0101", "La Ceiba", "Municipality", 197267, "Q240219") + _row("0107", "Tela", "Municipality", 96758)
        capital_municipality = _row("0801", "Distrito Central (Tegucigalpa)", "Municipality", 1157509, "Q3238")
        latest_municipalities = _row("0101", "La Ceiba", "Municipality", 241267, "Q240219") + _row("0107", "Tela", "Municipality", 96758)
        pages = {
            "honduras/cities": _table("", footer=_row("honduras", "Honduras", "Republic", 8303771, "Q783")),
            "honduras/admin": _table(
                f'<tbody class="admin1">{department}</tbody><tbody class="admin2">{latest_municipalities}</tbody>'
                f'<tbody class="admin1">{capital_department}</tbody><tbody class="admin2">{capital_municipality}</tbody>',
                footer=_row("honduras", "Honduras", "Republic", 9745149, "Q783"), date="2023-07-01",
            ),
            "honduras/atlantida": _table(f'<tbody>{municipalities}</tbody>', footer=department) + _table(
                '<tbody>'
                + _row("010101001", "La Ceiba", "Main Locality", 176212, "Q240219", "0101")
                + _row("010101002", "Agua Caliente", "Rural Locality", 234, municipality="0101")
                + _row("010701002", "Agua Caliente", "Rural Locality", 221, municipality="0107")
                + '</tbody>', table_id="ts",
            ),
            "honduras/franciscomorazan": _table(f'<tbody>{capital_municipality}</tbody>', footer=capital_department) + _table(
                '<tbody>' + _row("080101001", "Tegucigalpa", "Main Locality", 996658, "Q3238", "0801")
                + '</tbody>', table_id="ts",
            ),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip("/")]

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=2, html_fetcher=SavedPages(),
        ).run(config)
        self.assertEqual(result.found, 10)
        municipality = AdminArea.objects.get(pk="honduras_0101")
        self.assertEqual(municipality.pop_latest, 241267)
        self.assertEqual(municipality.pop_latest_date.year, 2023)
        expected = {"010101001": ("0101", 176212), "010101002": ("0101", 234),
                    "010701002": ("0107", 221), "080101001": ("0801", 996658)}
        for code, (parent, population) in expected.items():
            locality = AdminArea.objects.get(pk=f"honduras_{code}")
            self.assertEqual(locality.level, 3)
            self.assertEqual(locality.parent_id, f"honduras_{parent}")
            self.assertEqual(locality.pop_latest, population)
        for area in AdminArea.objects.exclude(level=0).select_related("parent"):
            self.assertEqual(area.level, area.parent.level + 1)
        response = self.client.get("/api/admin-areas/honduras_0101/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["area"]["subdivision_count"], 2)
        self.assertEqual({child["id"] for child in payload["children"]}, {"honduras_010101001", "honduras_010101002"})
