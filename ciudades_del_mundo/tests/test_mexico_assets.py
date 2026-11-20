import json
import tomllib
from pathlib import Path
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets import (
    _assign_wikidata_assets_from_scraped_data_wd,
    _citypopulation_country_wikidata_id,
    _mirror_country_assets_to_root_admin_areas,
)
from ciudades_del_mundo.models import AdminArea, VisualAsset
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.handlers.dashboard import api_admin_area_detail, api_country_detail


class MexicoVisualAssetTests(TestCase):
    def test_explicit_national_identity_prevents_homonymous_state_discovery(self):
        module = "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets."
        with (
            patch(module + "_country_root_data_wd", return_value="Q82112") as stored_qid,
            patch(module + "_fetch_citypopulation_html") as fetch,
        ):
            qid = _citypopulation_country_wikidata_id(
                None, slug="mexico", country_code="mexico", config_data={"wikidata_id": "Q96"},
            )
        self.assertEqual(qid, "Q96")
        stored_qid.assert_not_called()
        fetch.assert_not_called()

    def test_country_and_homonymous_state_keep_distinct_assets_after_assignment(self):
        content = (Path(__file__).parents[1] / "subdivisions" / "mexico.toml").read_text(encoding="utf-8")
        data = tomllib.loads(content)
        self.assertEqual(data["wikidata_id"], "Q96")
        self.assertEqual(data["scrape_schema_version"], 2)
        upsert_scraping_config("mexico", content)
        root = AdminArea.objects.create(
            id="mexico_mexico", code="mexico", country_code="mexico",
            name="Mexico", level=0, pop_latest=126014024,
        )
        state = AdminArea.objects.create(
            id="mexico_15", code="15", country_code="mexico",
            name="M\u00e9xico", level=1, parent=root, data_wd="Q82112", pop_latest=16992418,
        )
        VisualAsset.objects.create(
            entity_type="country", entity_key="mexico", country_code="mexico", kind="coat",
            wikidata_id="Q82112", commons_filename="Coat of arms of Mexico State.svg",
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        VisualAsset.objects.create(
            entity_type="admin_area", entity_key=state.id, country_code="mexico", kind="flag",
            wikidata_id="Q82112", commons_filename="Flag of Mexico.svg",
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        module = "ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets."
        for state_filenames in (
            {"coat": "Coat of arms of Mexico State.svg"},
            {"coat": "Coat of arms of Mexico State.svg", "flag": "Flag of Mexico.svg"},
        ):
            with (
                patch(module + "_wikidata_visual_asset_filenames", return_value={
                    "flag": "Flag of Mexico.svg", "coat": "Coat of arms of Mexico.svg",
                }) as national_lookup,
                patch(module + "_wikidata_visual_asset_filenames_for_ids", return_value={
                    "Q82112": state_filenames,
                }),
                patch(module + "commons_visual_asset_candidate_for_name") as commons_search,
            ):
                _assign_wikidata_assets_from_scraped_data_wd(
                    "mexico", "mexico", config_data=data, required_kinds=["flag", "coat"], levels=[1],
                )
                _mirror_country_assets_to_root_admin_areas("mexico")
            national_lookup.assert_called_once_with("Q96")
            commons_search.assert_not_called()

        factory = RequestFactory()
        request = factory.get("/", {"include_children": "0"})
        country = json.loads(api_country_detail(request, "mexico").content)["country"]
        root_payload = json.loads(api_admin_area_detail(request, root.id).content)["area"]
        state_payload = json.loads(api_admin_area_detail(request, state.id).content)["area"]
        for payload in (country, root_payload):
            self.assertEqual(payload["flag_asset"]["wikidata_id"], "Q96")
            self.assertEqual(payload["flag_asset"]["commons_filename"], "Flag of Mexico.svg")
            self.assertEqual(payload["coat_asset"]["commons_filename"], "Coat of arms of Mexico.svg")
            self.assertTrue(payload["flag_asset"]["image_url"])
        self.assertEqual(state_payload["flag_asset"]["wikidata_id"], "Q82112")
        self.assertEqual(state_payload["flag_asset"]["commons_filename"], "Flag of the State of Mexico.svg")
        self.assertEqual(state_payload["coat_asset"]["commons_filename"], "Coat of arms of Mexico State.svg")
        state.refresh_from_db()
        self.assertEqual(state.parent_id, root.id)
        self.assertEqual(state.pop_latest, 16992418)
