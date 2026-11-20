import json
from pathlib import Path
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


def _row(code, name, population, *, qid="", status="", department=""):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
        f'<td class="rstatus">{status}</td><td class="radm">{department}</td>'
        f'<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id="tl", footer=""):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th><th>Department</th>'
        '<th class="rpop" data-coldate="2013-05-11">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class BeninConfigTests(TestCase):
    def test_towns_keep_commune_parents_source_population_and_distinct_identity(self):
        content = (Path(__file__).parents[1] / "subdivisions" / "benin.toml").read_text(encoding="utf-8")
        config = parse_scraping_job_config("benin", content)
        for name, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, name, value)
        self.assertEqual(config.legal_subdivision_level, 2)
        root = _row("benin", "Benin", 10008749, qid="Q962", status="Republic")
        branches = (
            ("03", "Atlantique", "031", "Abomey-Calavi", "Q2473939", 655965),
            ("08", "Littoral", "081", "Cotonou", "Q43595", 679012),
            ("09", "Mono", "095", "Houéyogbé", "Q2412969", 101893),
            ("10", "Ouémé", "109", "Sèmè-Kpodji", "Q2100355", 224207),
        )
        admin = ""
        for department, name, code, commune, qid, population in branches:
            admin += '<tbody class="admin1">' + _row(department, name, population, status="Department") + '</tbody>'
            admin += '<tbody class="admin2">' + _row(code, commune, population, qid=qid, status="Commune") + '</tbody>'
        towns = (
            ("24926", "Abomey-Calavi", "Q2473939", "031", 117824),
            ("24949", "Godomey", "Q2757920", "031", 253262),
            ("24950", "Zinvié", "Q4024500", "031", 18157),
            ("2047", "Cotonou", "Q43595", "081", 679012),
            ("34335", "Sè", "Q595541", "095", 26627),
            ("24943", "Sèmè-Kpodji", "Q2100355", "109", 28255),
            ("24951", "Ekpé", "", "109", 75313),
        )
        city_rows = "".join(_row(code, name, population, qid=qid, department=parent[:2])
                            for code, name, qid, parent, population in towns)
        pages = {
            "benin/cities": _table("", footer=root) + _table(f"<tbody>{city_rows}</tbody>", table_id="ts"),
            "benin/admin": _table(admin, footer=root),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip("/")]

        previous = None
        for workers in (1, 4):
            with self.subTest(workers=workers), patch(
                "ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get",
                new=lambda client, url: SavedPages().get(url),
            ):
                result = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    page_workers=workers, html_fetcher=SavedPages(),
                ).run(config)
            self.assertEqual(result.found, 16)
            self.assertEqual(result.deleted, 0)
            for code, name, qid, parent, population in towns:
                town = AdminArea.objects.get(pk=f"benin_{code}")
                self.assertEqual((town.level, town.parent_id, town.pop_latest, town.data_wd),
                                 (3, f"benin_{parent}", population, qid))
                self.assertEqual(town.pop_latest_date.isoformat(), "2013-05-11")
            for department, name, code, commune, qid, population in branches:
                area = AdminArea.objects.get(pk=f"benin_{code}")
                self.assertEqual((area.level, area.parent_id, area.pop_latest),
                                 (2, f"benin_{department}", population))
                response = api_admin_area_detail(RequestFactory().get("/"), area.id)
                self.assertEqual(response.status_code, 200)
                payload = json.loads(response.content)
                expected = {f"benin_{row[0]}" for row in towns if row[3] == code}
                self.assertEqual({child["id"] for child in payload["children"]}, expected)
                self.assertEqual(payload["area"]["subdivision_count"], len(expected))
                self.assertEqual({group["level"] for group in payload["child_groups"]}, {3})
            snapshot = list(AdminArea.objects.order_by("id").values())
            if previous is not None:
                self.assertEqual(snapshot, previous)
            previous = snapshot
