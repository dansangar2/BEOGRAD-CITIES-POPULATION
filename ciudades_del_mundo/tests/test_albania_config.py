from collections import Counter
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, status, population, qid):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">'
        f'<span itemprop="name">{name}</span></td><td class="rstatus">{status}</td>'
        f'<td class="rpop">{population}</td></tr>'
    )


def _table(body, footer=""):
    return (
        '<table id="tl"><thead><tr><th>Name</th><th>Status</th>'
        '<th class="rpop" data-coldate="2023-09-18">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class AlbaniaConfigTests(TestCase):
    def test_rootless_municipal_page_keeps_levels_and_homonymous_units_in_sql_and_api(self):
        seed = Path(__file__).resolve().parents[1] / "subdivisions/albania.toml"
        config = parse_scraping_job_config("albania", seed.read_text(encoding="utf-8"))
        self.assertEqual(config.legal_subdivision_level, 3)
        belsh = _row("043", "Belsh", "Municipality", 17123, "Q13037405")
        cerrik = _row("042", "Cerrik", "Municipality", 25162, "Q16350055")
        tirana = _row("111", "Tirane", "Municipality", 598176, "Q16350058")
        pages = {
            "albania/admin": _table(
                '<tbody class="admin1">'
                + _row("04", "Elbasan", "County", 232580, "Q231096")
                + f'</tbody><tbody class="admin2">{belsh}{cerrik}</tbody>'
                + '<tbody class="admin1">'
                + _row("11", "Tirane", "County", 758513, "Q229892")
                + f'</tbody><tbody class="admin2">{tirana}</tbody>',
                _row("albania", "Albania", "Republic", 2402113, "Q222"),
            ),
            "albania/mun/admin": _table(
                f'<tbody class="admin1">{belsh}</tbody><tbody class="admin2">'
                + _row("04105", "Fierze", "Municipal Unit", 2053, "Q3744643")
                + f'</tbody><tbody class="admin1">{cerrik}</tbody><tbody class="admin2">'
                + _row("04116", "Fierze", "Municipal Unit", 3000, "Q2618277")
                + f'</tbody><tbody class="admin1">{tirana}</tbody><tbody class="admin2">'
                + _row("11215", "Tirane", "Municipal Unit", 389323, "Q19689")
                + '</tbody>',
            ),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip("/")]

        result = ScrapeAdminAreas(
            DjangoAdminAreaRepository(), [CityPopulationAdminScraper()],
            html_fetcher=SavedPages(), page_workers=2,
        ).run(config)
        self.assertEqual(result.found, 9)
        self.assertEqual(Counter(AdminArea.objects.values_list("level", flat=True)), {0: 1, 1: 2, 2: 3, 3: 3})
        for area in AdminArea.objects.exclude(level=0).select_related("parent"):
            self.assertEqual(area.parent.level, area.level - 1)
        for code, parent, population in [("04105", "043", 2053), ("04116", "042", 3000), ("11215", "111", 389323)]:
            unit = AdminArea.objects.get(pk=f"albania_{code}")
            self.assertEqual((unit.parent_id, unit.pop_latest), (f"albania_{parent}", population))
            response = self.client.get(f"/api/admin-areas/albania_{parent}/")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["area"]["subdivision_count"], 1)
            self.assertEqual([child["id"] for child in payload["children"]], [unit.id])
