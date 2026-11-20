"""Regression checks for editor form ownership and live legal source levels."""

from hashlib import sha256
import json
from pathlib import Path
import tomllib
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ciudades_del_mundo.models import AdminArea, ScrapingConfig, WebTask
from ciudades_del_mundo.services.nuevo_admin_builder import _expand_to_municipal, effective_source_municipal_level
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.forms.scraping import _render_config_from_manual_post
from ciudades_del_mundo.web.queries.configs import _latest_bulk_config_progress
from ciudades_del_mundo.web.tasks import task_manager


class ConfigActionsTests(TestCase):
    def setUp(self):
        self.recovery = patch.object(task_manager, "_recovery_done", True)
        self.recovery.start()
        self.addCleanup(self.recovery.stop)
        self.post = QueryDict("", mutable=True)
        self.post.update({
            "editor_mode": "manual",
            "manual_country_code": "austria",
            "manual_legal_subdivision": "2",
            "pages_json": '[{"source":"admin","path":["admin"]}]',
        })
        self.content = _render_config_from_manual_post("austria", self.post)
        self.record = upsert_scraping_config("austria", self.content)
        self.root = AdminArea.objects.create(
            id="at_root", country_code="austria", code="AT", name="Austria", level=0,
        )
        self.region = AdminArea.objects.create(
            id="at_region", country_code="austria", code="1", name="Region", level=1, parent=self.root,
        )
        self.municipality = AdminArea.objects.create(
            id="at_municipality", country_code="austria", code="11", name="Municipality", level=2,
            parent=self.region, pop_latest=100,
        )
        self.locality = AdminArea.objects.create(
            id="at_locality", country_code="austria", code="111", name="Locality", level=3,
            parent=self.municipality, pop_latest=40,
        )
        self.task = WebTask.objects.create(
            id="at-completed", key="scrape:austria", label="Popular Austria",
            args=["scrape_subdivisions_with_assets", "austria"],
            status=WebTask.Status.SUCCEEDED, returncode=0, created_at=timezone.now(),
        )

    def test_editor_actions_keep_form_owners_and_city_payload_inside_manual_form(self):
        response = self.client.get("/configs/austria/")
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.content, "html.parser")
        self.assertFalse(html.select("form form"), "Nested forms detach the Popular button in browsers")
        for mode in ("manual", "file"):
            form = html.select_one(f'form[data-config-editor-form="{mode}"]')
            for action in ("validate", "scrape", "clear"):
                button = form.select_one(f'[data-config-action="{action}"]')
                self.assertIs(button.find_parent("form"), form)
                self.assertEqual(button["formaction"], f"/configs/austria/task/{action}/")
        manual = html.select_one("form.config-manual-form")
        self.assertIs(manual.select_one('[name="config_cities_json"]').find_parent("form"), manual)
        for action in ("import", "export"):
            external = html.select_one(f"#config-city-merge-{action}")
            button = manual.select_one(f'[form="config-city-merge-{action}"]')
            self.assertIsNotNone(button)
            self.assertIsNotNone(external.select_one('[name="csrfmiddlewaretoken"]'))
            self.assertIsNone(external.find_parent("form"))

    def test_popular_launches_task_from_saved_sql_config(self):
        with patch.object(task_manager, "start", return_value=self.task) as start:
            response = self.client.post("/configs/austria/task/scrape/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], self.task.id)
        self.assertEqual(start.call_args.kwargs["key"], "scrape:austria")
        self.assertEqual(start.call_args.kwargs["args"][0], "scrape_subdivisions_with_assets")

    def test_legal_level_changes_apply_without_scraping_or_rewriting_source_rows(self):
        rows = list(AdminArea.objects.order_by("id").values())
        task_count = WebTask.objects.count()
        self.assertEqual(_expand_to_municipal([self.root], "austria"), {self.municipality.id})
        for mode, level, expected_id in (("manual", 3, self.locality.id), ("file", 0, self.root.id)):
            with self.subTest(mode=mode, level=level):
                data = self.post.copy()
                data["editor_mode"] = mode
                data["manual_legal_subdivision"] = str(level)
                data["content"] = self.content.replace("LEGAL_SUBDIVISION = 2", f"LEGAL_SUBDIVISION = {level}")
                with patch.object(task_manager, "start") as start:
                    response = self.client.post("/configs/austria/", data=data, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200)
                start.assert_not_called()
                current = ScrapingConfig.objects.get(pk="austria")
                self.assertEqual(current.updated_at, self.record.updated_at)
                self.assertEqual(current.content_hash, sha256(current.content.encode("utf-8")).hexdigest())
                self.assertEqual(effective_source_municipal_level("austria"), level)
                self.assertEqual(_expand_to_municipal([self.root], "austria"), {expected_id})
                self.assertEqual(list(AdminArea.objects.order_by("id").values()), rows)
                summary = self.client.get("/configs/austria/summary/").json()
                self.assertEqual(summary["status_filter"], "populated")
                self.assertEqual(WebTask.objects.count(), task_count)
                if level == 3:
                    builder = response.json()["city_builder"]
                    self.assertEqual(builder["legal_level"], 3)
                    self.assertEqual(builder["parent_level"], 2)
                    self.assertEqual([r["id"] for r in builder["parent_options"]], [self.municipality.id])

    def test_scraping_changes_invalidate_previous_population(self):
        current = upsert_scraping_config("austria", self.content.replace('["admin"]', '["other"]'))
        self.assertGreater(current.updated_at, self.record.updated_at)
        self.assertEqual(self.client.get("/configs/austria/summary/").json()["status_filter"], "pending")

    def test_legal_only_edit_keeps_bulk_task_current(self):
        self.task.key = "scrape:all"
        self.task.save(update_fields=["key"])
        record = upsert_scraping_config("austria", self.content.replace("LEGAL_SUBDIVISION = 2", "LEGAL_SUBDIVISION = 3"))
        with patch("ciudades_del_mundo.web.queries.configs.read_task_config_progress", return_value={
            "austria": {"status": "populated"},
        }):
            task, status = _latest_bulk_config_progress("austria", record=record)
        self.assertEqual(task.pk, self.task.pk)
        self.assertEqual(status, "populated")

    def test_unchanged_autosave_preserves_completed_task(self):
        upsert_scraping_config("austria", self.content)
        self.assertEqual(self.client.get("/configs/austria/summary/").json()["status_filter"], "populated")


class ManualPagePreservationTests(SimpleTestCase):
    def test_austria_manual_save_preserves_repeated_vienna_roots_and_locality_sections(self):
        content = (Path(__file__).resolve().parents[1] / "subdivisions/austria.toml").read_text(encoding="utf-8")
        original = tomllib.loads(content)
        visible_fields = {"source", "path", "include", "repeat", "sum_to_root", "force_highest_level", "parent_level"}
        post = QueryDict("", mutable=True)
        # Reorder rows, including the two uses of the same URL, as the UI allows.
        ordered = list(reversed(original["pages"]))
        post.update({
            "manual_country_code": "austria", "manual_legal_subdivision": "3",
            "pages_json": json.dumps([{k: v for k, v in p.items() if k in visible_fields} for p in ordered]),
        })
        saved = tomllib.loads(_render_config_from_manual_post("austria", post, current_content=content))
        self.assertEqual(saved["pages"], ordered)
        self.assertEqual(saved["parent_overrides"], original["parent_overrides"])
        localities = next(p for p in saved["pages"] if p["path"][0].startswith("localities/"))
        self.assertFalse(localities["include"]["infosection"])

    def test_manual_save_retains_advanced_maps_and_country_identity(self):
        content = '''scrape_schema_version = 2
wikidata_id = "Q40"
[[pages]]
source = "cities"
path = ["example"]
root_code = "stable-code"
root_parent_code = "parent"
root_level = 2
table_levels = { tl = 3, ts = 4 }
status_levels = { City = 3 }
area_overrides = { "A.1" = 2.75 }
'''
        post = QueryDict("", mutable=True)
        post.update({"pages_json": '[{"source":"cities","path":["example"],"force_highest_level":2}]'})
        saved = tomllib.loads(_render_config_from_manual_post("example", post, current_content=content))
        original = tomllib.loads(content)
        self.assertEqual(saved["wikidata_id"], "Q40")
        for key in ("root_code", "root_parent_code", "root_level", "table_levels", "status_levels", "area_overrides"):
            self.assertEqual(saved["pages"][0][key], original["pages"][0][key])
