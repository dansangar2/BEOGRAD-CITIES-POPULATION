from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


def _row(code, name, population, *, qid="", status="", parent=""):
    return (
        f'<tr><td class="rname" id="i{code}" data-wd="{qid}">{name}</td>'
        f'<td class="rstatus">{status}</td><td class="radm" data-admid="{parent}"></td>'
        f'<td class="rpop">{population}</td></tr>'
    )


def _table(body, *, table_id="tl", footer=""):
    return (
        f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th><th>Adm.</th>'
        '<th class="rpop" data-coldate="2017-05-30">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>'
    )


class BhutanConfigTests(TestCase):
    def test_towns_keep_source_districts_without_replacing_administrative_rows(self):
        content = (Path(__file__).parents[1] / "subdivisions/bhutan.toml").read_text(encoding="utf-8")
        config = parse_scraping_job_config("bhutan", content)
        for name, value in parse_runtime_config_extensions(content).items():
            object.__setattr__(config, name, value)
        self.assertEqual(config.legal_subdivision_level, 2)
        root = _row("bhutan", "Bhutan", 727145, qid="Q917")
        districts = (
            ("BT002", "5782", "Chukha", "Q652754", 68966,
             ("BT00204", "Darla", "Q5210140", "Gewog", 6500)),
            ("BT006", "5787", "Lhuentse", "Q598793", 14437,
             ("BT00601", "Gangzur", "Q5520969", "Gewog", 3000)),
            ("BT014", "5796", "Thimphu", "Q254889", 138736,
             ("BT01409", "Thimphu", "Q9270", "Municipality", 114551)),
        )
        admin, major = "", ""
        for code, alias, name, qid, population, unit in districts:
            admin += '<tbody class="admin1">' + _row(code, name, population, qid=qid, status="District") + '</tbody>'
            ucode, uname, uqid, utype, upop = unit
            admin += '<tbody class="admin2">' + _row(ucode, uname, upop, qid=uqid, status=utype) + '</tbody>'
            major += _row(alias, name, population, qid=qid, status="District")
        towns = (
            ("38117", "Darla", 1037, "Q5210140", "5782", "BT002"),
            ("27723", "Tsimalakha", 2135, "", "5782", "BT002"),
            ("38122", "Autsho", 775, "Q4827112", "5787", "BT006"),
            ("14214", "Thimphu", 114551, "Q9270", "5796", "BT014"),
            ("38133", "Khasadrapchu", 966, "", "5796", "BT014"),
        )
        pages = {
            "bhutan/admin": _table(admin, footer=root),
            "bhutan/cities": _table(f'<tbody>{major}</tbody>', footer=root) + _table(
                '<tbody>' + ''.join(_row(code, name, population, qid=qid, parent=alias)
                                     for code, name, population, qid, alias, parent in towns) + '</tbody>',
                table_id="ts",
            ),
        }

        class SavedPages:
            def get(self, url):
                return pages[url.removeprefix(config.base_url).strip("/")]

        for workers in (1, 4):
            with self.subTest(workers=workers), patch(
                "ciudades_del_mundo.infrastructure.scraping.city_population_client.CityPopulationClient.get",
                new=lambda client, url: SavedPages().get(url),
            ):
                runner = ScrapeAdminAreas(
                    DjangoAdminAreaRepository(), [CityPopulationCitiesScraper(), CityPopulationAdminScraper()],
                    html_fetcher=SavedPages(), page_workers=workers,
                )
                if workers == 1:
                    runner.run(replace(config, pages=config.pages[:2]))
                    before = {row["id"]: row for row in AdminArea.objects.values(
                        "id", "name", "level", "parent_id", "pop_latest", "data_wd", "entity_type",
                    )}
                result = runner.run(config)
                self.assertEqual((result.found, result.deleted), (12, 0))
                self.assertEqual(result.created, 5 if workers == 1 else 0)
                self.assertEqual(before, {row["id"]: row for row in AdminArea.objects.filter(
                    pk__in=before,
                ).values("id", "name", "level", "parent_id", "pop_latest", "data_wd", "entity_type")})
                for code, name, population, qid, alias, parent in towns:
                    town = AdminArea.objects.get(pk=f"bhutan_{code}")
                    self.assertEqual((town.level, town.parent_id, town.pop_latest, town.data_wd),
                                     (3, f"bhutan_{parent}", population, qid))
                    self.assertEqual(town.pop_latest_date.isoformat(), "2017-05-30")
                for code, alias, name, qid, population, unit in districts:
                    response = self.client.get(f"/api/admin-areas/bhutan_{code}/")
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    expected = {f"bhutan_{row[0]}" for row in towns if row[5] == code} | {f"bhutan_{unit[0]}"}
                    self.assertEqual({child["id"] for child in payload["children"]}, expected)
                    self.assertEqual(payload["area"]["subdivision_count"], len(expected))
                    self.assertEqual({group["level"] for group in payload["child_groups"]}, {2, 3})
