from collections import Counter
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationCitiesScraper, PythonScrapingConfigRepository
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config


ISLANDS = (
    ("7277", "Anegada", 285), ("8542", "Cooper Island", 26),
    ("8543", "Great Camanoe", 6), ("7278", "Jost Van Dyke", 298),
    ("7275", "Tortola", 23419), ("7276", "Virgin Gorda", 3930),
    ("7279", "Other Islands", "..."), ("8544", "Yachts", 18),
)


def _row(code, name, population):
    return (
        f'<tr><td class="rname" id="i{code}"><span itemprop="name">{name}</span></td>'
        f'<td class="rpop">{population}</td></tr>'
    )


class BritishVirginIslandsConfigTests(TestCase):
    def test_separate_city_table_links_road_town_to_tortola_in_sql_and_api(self):
        content = (Path(__file__).resolve().parents[1] / "subdivisions/britishvirginislands.toml").read_text(encoding="utf-8")
        upsert_scraping_config("britishvirginislands", content)
        config = PythonScrapingConfigRepository().get("britishvirginislands")
        attach_runtime_config_extensions([config])
        self.assertEqual(config.legal_subdivision_level, 1)
        html = (
            '<h2>Islands &amp; Territory</h2><table id="tl"><thead><tr><th>Name</th>'
            '<th class="rpop" data-coldate="2010-07-12">Population</th></tr></thead><tbody>'
            + "".join(_row(*row) for row in ISLANDS)
            + '</tbody><tfoot>' + _row("britishvirginislands", "British Virgin Islands", 28054)
            + '</tfoot></table><h2>Major Town</h2><table id="ts"><thead><tr><th>Name</th>'
            '<th class="rpop" data-coldate="2001-05-12">Population</th></tr></thead><tbody>'
            + _row("796", "Road Town", 6330) + '</tbody></table>'
        )

        class SavedHtml:
            def get(self, url):
                return html

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationCitiesScraper()],
            html_fetcher=SavedHtml(), page_workers=2,
        ).run(config)
        self.assertEqual(result.found, 10)
        self.assertEqual(Counter(AdminArea.objects.values_list("level", flat=True)), {0: 1, 1: 8, 2: 1})
        road_town = AdminArea.objects.get(code="796")
        self.assertEqual((road_town.parent_id, road_town.level, road_town.pop_latest),
                         ("britishvirginislands_7275", 2, 6330))
        self.assertEqual(AdminArea.objects.get(code="7275").most_populate_city_id, road_town.id)
        self.assertIsNone(AdminArea.objects.get(code="8544").most_populate_city_id)
        for code, name, population in ISLANDS:
            island = AdminArea.objects.get(code=code)
            self.assertEqual((island.name, island.parent_id, island.pop_latest),
                             (name, "britishvirginislands_britishvirginislands", None if population == "..." else population))
        for code, count in (("britishvirginislands", 8), *((row[0], int(row[0] == "7275")) for row in ISLANDS), ("796", 0)):
            response = self.client.get(f"/api/admin-areas/britishvirginislands_{code}/")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["children"]), count)
            self.assertEqual(payload["area"]["subdivision_count"], count)
            if code == "7275":
                self.assertEqual([child["id"] for child in payload["children"]], [road_town.id])
