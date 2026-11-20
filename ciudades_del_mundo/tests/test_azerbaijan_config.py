import json
from pathlib import Path
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_record
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.services.visual_assets import ensure_visual_assets_for_admin_area
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail


class AzerbaijanConfigTests(TestCase):
    def setUp(self):
        content = (Path(__file__).parents[1] / "subdivisions" / "azerbaijan.toml").read_text(encoding="utf-8")
        self.record = upsert_scraping_config("azerbaijan", content)
        self.root = self.area("azerbaijan", "Azerbaijan", 0, data_wd="Q227")
        self.region = self.area("01", "Bakı", 1, parent=self.root, entity_type="Economic Region", pop_latest=1200)
        self.republic = self.area("19", "Naxçıvan", 1, parent=self.root,
                                  entity_type="Autonomous Republic", data_wd="Q131083")

    def area(self, code, name, level, **kwargs):
        return AdminArea.objects.create(id=f"azerbaijan_{code}", country_code="azerbaijan",
                                        code=code, name=name, level=level, **kwargs)

    def detail(self, area):
        return json.loads(api_admin_area_detail(RequestFactory().get("/"), area.id).content)

    def test_partial_city_merge_preserves_peripheral_districts_and_reapplies_without_double_counting(self):
        central = {"0101", "0104", "0107", "0108", "0109", "0111", "0112"}
        peripheral = {"0102", "0103", "0105", "0106", "0110"}
        for code in sorted(central | peripheral):
            self.area(code, code, 2, parent=self.region, entity_type="District", pop_latest=100)
        other = self.area("1101", "Naxçıvan", 2, parent=self.republic, entity_type="City", pop_latest=50)
        previous = None
        for _ in range(2):
            materialize_configured_cities_for_record(self.record)
            city = AdminArea.objects.get(id="azerbaijan_baku_city")
            self.assertEqual((city.level, city.parent_id, city.pop_latest, city.city_merge_status),
                             (2, self.region.id, 700, AdminArea.CityMergeStatus.UNIFIED))
            for code in central:
                source = AdminArea.objects.get(id=f"azerbaijan_{code}")
                self.assertEqual((source.level, source.parent_id, source.pop_latest, source.city_merge_status),
                                 (3, city.id, 100, AdminArea.CityMergeStatus.SOURCE))
            for code in peripheral:
                source = AdminArea.objects.get(id=f"azerbaijan_{code}")
                self.assertEqual((source.level, source.parent_id, source.pop_latest, source.city_merge_status),
                                 (2, self.region.id, 100, AdminArea.CityMergeStatus.NONE))
            other.refresh_from_db()
            self.assertEqual((other.parent_id, other.level, other.pop_latest), (self.republic.id, 2, 50))
            self.region.refresh_from_db()
            self.assertEqual(self.region.pop_latest, 1200)
            payload = self.detail(self.region)
            self.assertEqual({row["id"] for row in payload["children"]},
                             {city.id, *(f"azerbaijan_{code}" for code in peripheral)})
            self.assertEqual(payload["area"]["subdivision_count"], 6)
            self.assertEqual({group["level"] for group in payload["child_groups"]}, {2})
            city_payload = self.detail(city)
            self.assertEqual(city_payload["area"]["subdivision_count"], 7)
            self.assertEqual({row["id"] for row in city_payload["children"]},
                             {f"azerbaijan_{code}" for code in central})
            self.assertEqual({group["level"] for group in city_payload["child_groups"]}, {3})
            snapshot = list(AdminArea.objects.order_by("id").values())
            if previous is not None:
                self.assertEqual(snapshot, previous)
            previous = snapshot

    def test_republic_symbols_override_wrong_discovery_without_matching_homonymous_city(self):
        city = self.area("1101", "Naxçıvan", 2, parent=self.republic, entity_type="City", data_wd="Q230104")
        with (
            patch("ciudades_del_mundo.services.visual_assets._wikidata_candidates", return_value=({}, {})),
            patch("ciudades_del_mundo.services.visual_assets.commons_visual_asset_candidate_for_name", return_value=None),
        ):
            result = ensure_visual_assets_for_admin_area(self.republic.id, languages=("en",))
        self.assertEqual((result.found, result.errors), (2, 0))
        payload = self.detail(self.republic)
        for kind, filename in (("flag", "Flag of Azerbaijan.svg"), ("coat", "Emblem of Azerbaijan.svg")):
            asset = payload["area"][f"{kind}_asset"]
            self.assertEqual(asset["commons_filename"], filename)
            self.assertEqual(asset["wikidata_id"], "Q131083")
            self.assertTrue(asset["image_url"])
            self.assertFalse(self.detail(city)["area"][f"{kind}_asset"])
        self.assertEqual({row["id"] for row in payload["children"]}, {city.id})
