from dataclasses import replace
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, *, qid="", department=None):
    parent = f'<td class="radm" data-admid="{department}">{department}</td>' if department else ""
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'{parent}<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id="tl", footer=""):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2022-05-18">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class ArgentinaConfigTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[1] / "subdivisions" / "argentina.toml"
        cls.config = parse_scraping_job_config("argentina", path.read_text(encoding="utf-8"))

    def test_locality_plan_covers_the_23_provinces(self):
        pages = [page for page in self.config.pages if page.html_format == "cities" and page.path != "argentina/cities"]
        self.assertEqual(self.config.legal_subdivision_level, 2)
        self.assertEqual(len(pages), 23)
        self.assertEqual(len({page.path for page in pages}), 23)
        self.assertTrue({"argentina/buenosaires", "argentina/tierradelfuego", "argentina/tucuman"}
                        <= {page.path for page in pages})
        for page in pages:
            self.assertEqual(page.force_highest_level, 3)
            self.assertEqual(page.parent_level, 2)
            self.assertEqual(page.include_sections, ("cities",))
            self.assertTrue(page.preserve_source_codes)

    def test_localities_and_shared_qid_parts_keep_departments_and_reach_api(self):
        config = replace(self.config, pages=tuple(
            page for page in self.config.pages
            if page.path in {"argentina/cities", "argentina/admin", "argentina/catamarca", "argentina/caba/admin"}
        ))
        country = _row("argentina", "Argentina", "Republic", 46044703, qid="Q414")
        province = _row("10", "Catamarca", "Province", 429562)
        capital = _row("10049", "Capital", "Department", 186947, qid="Q1")
        other = _row("10035", "Belen", "Department", 30569)
        caba = _row("02", "Ciudad Autonoma de Buenos Aires", "Federal District", 3121707)
        city = _row("02000", "Buenos Aires", "City", 3121707)
        commune = _row("CABA01", "Comuna 1", "Commune", 223554, qid="Q3")
        second_commune = _row("CABA02", "Comuna 2", "Commune", 161645, qid="Q4")
        pages = {
            "argentina/cities": _table("", footer=country),
            "argentina/admin": _table(
                f'<tbody class="admin1">{province}</tbody><tbody class="admin2">{capital}{other}</tbody>'
                f'<tbody class="admin1">{caba}</tbody><tbody class="admin2">{city}</tbody>',
                footer=country,
            ),
            "argentina/catamarca": _table(
                '<tbody>' + _row("10049", "Capital", "Department", 1, qid="Q1") + other + '</tbody>',
                footer=province,
            ) + _table(
                '<tbody>'
                + _row("10049010", "Catamarca", "Locality", 185694, qid="Q1", department="10049")
                + _row("10049020", "Shared", "Locality (part)", 123, qid="Q2", department="10049")
                + _row("10035010", "Shared", "Locality (part)", 456, qid="Q2", department="10035")
                + '</tbody>', table_id="ts",
            ),
            "argentina/caba/admin": _table(
                f'<tbody class="admin1">{commune}</tbody><tbody class="admin2">'
                + _row("CABA011", "Constitucion", "Neighborhood", 43838)
                + f'</tbody><tbody class="admin1">{second_commune}</tbody><tbody class="admin2">'
                + _row("CABA021", "Recoleta", "Neighborhood", 160609, qid="Q4")
                + '</tbody>', footer=caba,
            ),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip("/")]

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
            page_workers=2, html_fetcher=SavedPages(),
        ).run(config)
        self.assertEqual(result.found, 14)
        self.assertEqual(AdminArea.objects.get(pk="argentina_10049").pop_latest, 186947)
        expected = {"10049010": ("10049", 185694), "10049020": ("10049", 123), "10035010": ("10035", 456)}
        for code, (parent, population) in expected.items():
            locality = AdminArea.objects.get(pk=f"argentina_{code}")
            self.assertEqual((locality.level, locality.parent_id, locality.pop_latest),
                             (3, f"argentina_{parent}", population))
        self.assertEqual(AdminArea.objects.get(pk="argentina_02000").parent_id, "argentina_02")
        locality = AdminArea.objects.get(pk="argentina_02000_locality")
        self.assertEqual((locality.level, locality.parent_id, locality.entity_type),
                         (3, "argentina_02000", "Locality"))
        self.assertEqual(locality.data_wd, "Q1486")
        self.assertEqual(AdminArea.objects.get(pk="argentina_CABA01").parent_id, locality.id)
        self.assertEqual(AdminArea.objects.get(pk="argentina_CABA021").parent_id, "argentina_CABA02")
        for area in AdminArea.objects.exclude(level=0).select_related("parent"):
            self.assertEqual(area.level, area.parent.level + 1)
        response = self.client.get("/api/admin-areas/argentina_10049/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["area"]["subdivision_count"], 2)
        self.assertEqual({child["id"] for child in payload["children"]},
                         {"argentina_10049010", "argentina_10049020"})
        payload = self.client.get(f"/api/admin-areas/{locality.id}/").json()
        self.assertEqual(payload["area"]["subdivision_count"], 2)
        self.assertEqual({child["id"] for child in payload["children"]},
                         {"argentina_CABA01", "argentina_CABA02"})
