from collections import Counter
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationCitiesScraper, PythonScrapingConfigRepository
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config


DISTRICTS = (
    ("19631", "Blowing Point", 825), ("19643", "East End", 661),
    ("19634", "George Hill", 1124), ("19644", "Island Harbour", 963),
    ("19633", "North Hill", 444), ("19638", "North Side", 1514),
    ("19632", "Sandy Ground", 252), ("19642", "Sandy Hill", 633),
    ("19630", "South Hill", 1689), ("19640", "Stoney Ground", 1577),
    ("19641", "The Farrington", 629), ("19639", "The Quarter", 1079),
    ("19636", "The Valley", 1298), ("19629", "West End", 884),
)


def _row(code, name, population):
    return (
        f'<tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rpop">{population}</td></tr>'
    )


class AnguillaConfigTests(TestCase):
    def test_separate_districts_table_belongs_to_main_island_in_sql_and_api(self):
        content = (Path(__file__).resolve().parents[1] / "subdivisions/anguilla.toml").read_text(encoding="utf-8")
        upsert_scraping_config("anguilla", content)
        config = PythonScrapingConfigRepository().get("anguilla")
        attach_runtime_config_extensions([config])
        self.assertEqual(config.legal_subdivision_level, 2)
        html = (
            '<h2>Islands &amp; Territory</h2><table id="tl"><thead><tr><th>Name</th>'
            '<th class="rpop" data-coldate="2021-12-31">Population</th></tr></thead><tbody>'
            + _row("8956", "Anguilla", 15701) + _row("8957", "Other Islands", "...")
            + '</tbody><tfoot>' + _row("anguilla", "Anguilla", 15701) + '</tfoot></table>'
            '<h2>Districts</h2><table id="ts"><thead><tr><th>Name</th>'
            '<th class="rpop" data-coldate="2011-05-11">Population</th></tr></thead><tbody>'
            + "".join(_row(*row) for row in DISTRICTS) + '</tbody></table>'
        )

        class SavedHtml:
            def get(self, url):
                return html

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper()],
            html_fetcher=SavedHtml(), page_workers=2,
        ).run(config)
        self.assertEqual(result.found, 17)
        self.assertEqual(Counter(AdminArea.objects.values_list("level", flat=True)), {0: 1, 1: 2, 2: 14})
        for code, name, population in DISTRICTS:
            district = AdminArea.objects.get(pk=f"anguilla_{code}")
            self.assertEqual((district.parent_id, district.level, district.name, district.pop_latest),
                             ("anguilla_8956", 2, name, population))
        for code, count in (("anguilla", 2), ("8956", 14), ("8957", 0)):
            response = self.client.get(f"/api/admin-areas/anguilla_{code}/")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["children"]), count)
            self.assertEqual(payload["area"]["subdivision_count"], count)
            if code == "8956":
                self.assertEqual({child["id"] for child in payload["children"]},
                                 {f"anguilla_{row[0]}" for row in DISTRICTS})
        self.assertEqual(AdminArea.objects.get(pk="anguilla_8956").most_populate_city_id, "anguilla_19630")
        self.assertIsNone(AdminArea.objects.get(pk="anguilla_8957").most_populate_city_id)
