import ast
import json
import os
from io import StringIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from django.core.management import call_command
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from django.utils import translation

from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    NuevoAdminArea,
    ScrapingConfig,
    SubdivisionGroup,
    VisualAsset,
    VisualAssetTranslation,
)
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.log_retention import cleanup_old_logs
from ciudades_del_mundo.web.task_progress import task_progress_path
from ciudades_del_mundo.web.tasks import ManagedTask, TaskManager, task_manager
from ciudades_del_mundo.web.views import (
    _admin_area_detail_payload,
    _area_capital_display_names,
    _area_related_places,
    _eligible_config_slugs_for_bulk,
    _latest_bulk_config_progress,
    _inferred_generated_page_level,
    _render_config_from_manual_post,
    _render_recipe_from_form,
    _can_clear_config_row,
    _can_scrape_config_status,
    _validate_config_text,
    _validate_recipe_text,
)


def _config_action_form_tag(html: str, action: str) -> str:
    match = re.search(r'<form[^>]*data-config-action-form="' + re.escape(action) + r'"[^>]*>', html)
    return match.group(0) if match else ""


class _Relation:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Object:
    def __init__(self, **values):
        self.__dict__.update(values)


class _RecordingTaskManager(TaskManager):
    def __init__(self, *args, **kwargs):
        self.started_workers = []
        super().__init__(*args, **kwargs)

    def _start_worker(self, task_id: str) -> None:
        self.started_workers.append(task_id)


class DerivedSectionWebTests(TestCase):
    def test_new_country_configuration_flow_persists_toml(self):
        response = self.client.post(
            "/new-countries/new/",
            {
                "slug": "testland",
                "name": "Testland",
                "source_country_code": "aa",
                "description": "Demo",
            },
        )

        self.assertEqual(response.status_code, 302)
        country = DerivedCountry.objects.get(slug="testland")
        self.assertEqual(country.source_country_code, "aa")

        response = self.client.post(
            "/new-countries/testland/configs/new/",
            {
                "slug": "republic",
                "name": "Republic",
                "source_country_code": "aa",
                "derived_country_code": "testland_republic",
                "is_active": "1",
                "content": 'kind = "derived_country_config"\n[selection]\ninclude_codes = []\n',
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="republic")
        self.assertEqual(config.derived_country_code, "testland_republic")
        self.assertIn('kind = "derived_country_config"', config.content)
        self.assertEqual(self.client.get("/new-countries/testland/").status_code, 200)
        self.assertEqual(self.client.get("/new-countries/testland/configs/republic/view/").status_code, 200)

    def test_new_country_visual_selection_generates_sql_toml(self):
        country = DerivedCountry.objects.create(slug="testland", name="Testland", source_country_code="aa")
        root = AdminArea.objects.create(
            id="aa",
            country_code="aa",
            code="aa",
            name="Source Country",
            level=0,
        )
        region = AdminArea.objects.create(
            id="aa_1",
            country_code="aa",
            code="1",
            name="Region",
            level=1,
            parent=root,
        )
        city = AdminArea.objects.create(
            id="aa_11",
            country_code="aa",
            code="11",
            name="City",
            level=2,
            parent=region,
        )

        response = self.client.post(
            "/new-countries/testland/configs/new/",
            {
                "slug": "visual",
                "name": "Visual",
                "source_country_code": "aa",
                "derived_country_code": "visual_land",
                "is_active": "1",
                "selection_json": json.dumps({"selected_ids": [region.id, city.id]}),
            },
        )

        self.assertEqual(response.status_code, 302)
        config = DerivedCountryConfig.objects.get(country=country, slug="visual")
        self.assertIn('source_country_code = "aa"', config.content)
        self.assertIn('include_ids = ["aa_1"]', config.content)
        self.assertIn('subtract_ids = ["aa_11"]', config.content)
        self.assertIn('operation = "add"', config.content)
        self.assertIn('operation = "subtract"', config.content)

    def test_new_country_source_children_endpoint_returns_direct_children(self):
        root = AdminArea.objects.create(
            id="bb",
            country_code="bb",
            code="bb",
            name="Source Country",
            level=0,
        )
        region = AdminArea.objects.create(
            id="bb_1",
            country_code="bb",
            code="1",
            name="Region",
            level=1,
            parent=root,
        )
        AdminArea.objects.create(
            id="bb_11",
            country_code="bb",
            code="11",
            name="City",
            level=2,
            parent=region,
        )

        response = self.client.get("/new-countries/source-children/?country_code=bb")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["children"]], ["bb_1"])
        self.assertTrue(payload["children"][0]["has_children"])

    def test_new_country_import_toml_queues_one_task_per_seed(self):
        with patch("ciudades_del_mundo.web.views.bundled_new_country_config_paths") as paths:
            paths.return_value = [Path("one.toml"), Path("two.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post("/new-countries/import-toml/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(start.call_args_list[0].kwargs["args"], ["sync_derived_configs", "new-countries", "one", "--force"])
        self.assertEqual(start.call_args_list[1].kwargs["args"], ["sync_derived_configs", "new-countries", "two", "--force"])

    def test_group_flow_persists_toml(self):
        response = self.client.post(
            "/groups/new/",
            {
                "slug": "historic_group",
                "name": "Historic group",
                "source_country_code": "aa",
                "description": "Reusable",
                "content": 'kind = "subdivision_group"\n[selection]\ninclude_codes = []\n',
            },
        )

        self.assertEqual(response.status_code, 302)
        group = SubdivisionGroup.objects.get(slug="historic_group")
        self.assertEqual(group.source_country_code, "aa")
        self.assertEqual(self.client.get("/groups/").status_code, 200)
        self.assertEqual(self.client.get("/groups/historic_group/").status_code, 200)

    def test_group_import_toml_queues_one_task_per_seed(self):
        with patch("ciudades_del_mundo.web.views.bundled_subdivision_group_paths") as paths:
            paths.return_value = [Path("group_one.toml"), Path("group_two.toml")]
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(id="queued", status=ManagedTask.Status.QUEUED, is_active=True)
                response = self.client.post("/groups/import-toml/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(start.call_count, 2)
        self.assertEqual(start.call_args_list[0].kwargs["args"], ["sync_derived_configs", "groups", "group_one", "--force"])
        self.assertEqual(start.call_args_list[1].kwargs["args"], ["sync_derived_configs", "groups", "group_two", "--force"])


class WebInterfaceHelperTests(TestCase):
    def test_generated_page_level_inference_keeps_source_and_route_independent(self):
        self.assertEqual(_inferred_generated_page_level("morocco", "admin", "benimellalkhenifra/admin"), 1)
        self.assertEqual(_inferred_generated_page_level("morocco", "admin", "admin"), 0)
        self.assertEqual(_inferred_generated_page_level("gibraltar", "cities", "admin"), 0)
        self.assertEqual(_inferred_generated_page_level("france", "cities", "cities/guyane"), 1)

    def test_area_map_helpers_include_capitals_and_major_city(self):
        capital = _Object(id="capital-1", name="Capital source")
        area = _Object(
            name="Region",
            country_code="testland",
            level=1,
            parent=None,
            capitals=_Relation([capital]),
            capital_names_by_language={"es": {"capital-1": "Capital traducida"}},
            most_populate_city=_Object(name="Big City"),
        )

        self.assertEqual(_area_capital_display_names(area, "es"), ["Capital traducida"])
        places = _area_related_places(area, "es")
        self.assertIn(
            {"kind": "Capital registrada", "name": "Capital traducida", "query": "Capital traducida, Region"},
            places,
        )
        self.assertIn(
            {"kind": "Ciudad mayor registrada", "name": "Big City", "query": "Big City, Region"},
            places,
        )


    def test_admin_area_detail_groups_children_by_level(self):
        root = AdminArea.objects.create(
            id="test_root",
            country_code="testcountry",
            code="testcountry",
            name="Test Country",
            level=0,
            pop_latest=1000,
        )
        province = AdminArea.objects.create(
            id="test_province",
            country_code="testcountry",
            code="province",
            name="Province",
            entity_type="Province",
            level=2,
            parent=root,
            pop_latest=500,
        )
        AdminArea.objects.create(
            id="test_commune",
            country_code="testcountry",
            code="commune",
            name="Commune",
            entity_type="Commune",
            level=3,
            parent=province,
            pop_latest=300,
        )
        AdminArea.objects.create(
            id="test_place",
            country_code="testcountry",
            code="place",
            name="Urban Place",
            entity_type="Urban Place",
            level=4,
            parent=province,
            pop_latest=200,
        )

        payload = _admin_area_detail_payload(province)

        self.assertEqual([group["level"] for group in payload["child_groups"]], [3, 4])
        self.assertEqual([row["name"] for row in payload["child_groups"][0]["children"]], ["Commune"])
        self.assertEqual([row["name"] for row in payload["child_groups"][1]["children"]], ["Urban Place"])

    def test_validate_config_text_accepts_minimal_toml(self):
        _validate_config_text(
            "testland",
            """
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""",
        )

    def test_manual_config_save_prefers_visible_level_fields_over_stale_pages_json(self):
        post = QueryDict("", mutable=True)
        post.update(
            {
                "manual_country_code": "netherlands",
                "manual_legal_subdivision": "2",
                "pages_json": (
                    '[{"source": "admin", "path": ["admin"], '
                    '"include": {"infosection": true, "major_subdivision": true, "minor_subdivision": true}}, '
                    '{"source": "cities", "path": ["drenthe"], "parent_level": 3, '
                    '"include": {"infosection": false, "major_subdivision": false, "cities": true}}]'
                ),
            }
        )
        post.setlist("page_source", ["admin", "cities"])
        post.setlist("page_enabled", ["true", "true"])
        post.setlist("page_force_highest_level", ["", "3"])
        post.setlist("page_parent_level", ["", "2"])
        post.setlist("page_include_infosection", ["true", "true"])
        post.setlist("page_include_major_subdivision", ["true", "false"])
        post.setlist("page_include_minor_subdivision", ["true", "false"])
        post.setlist("page_include_cities", ["false", "true"])

        content = _render_config_from_manual_post("netherlands", post, current_content="")

        self.assertIn('path = ["drenthe"]', content)
        self.assertIn("force_highest_level = 3", content)
        self.assertIn("parent_level = 2", content)
        self.assertIn("include = { cities = true, infosection = true, major_subdivision = false }", content)

    def test_config_edit_autosave_post_persists_visible_manual_page_changes(self):
        ScrapingConfig.objects.update_or_create(
            slug="netherlands",
            defaults={
                "country_code": "netherlands",
                "name": "Netherlands",
                "content": """
LEGAL_SUBDIVISION = 2
scrape_schema_version = 2

[[pages]]
source = "admin"
path = ["admin"]
include = { infosection = true, major_subdivision = true, minor_subdivision = true }

[[pages]]
source = "cities"
path = ["drenthe"]
parent_level = 3
include = { cities = true, infosection = false, major_subdivision = false }
""".strip()
                + "\n",
            },
        )
        pages_json = json.dumps(
            [
                {
                    "source": "admin",
                    "path": ["admin"],
                    "include": {
                        "infosection": True,
                        "major_subdivision": True,
                        "minor_subdivision": True,
                    },
                },
                {
                    "source": "cities",
                    "path": ["drenthe"],
                    "parent_level": 3,
                    "include": {
                        "cities": True,
                        "infosection": False,
                        "major_subdivision": False,
                    },
                },
            ]
        )

        response = self.client.post(
            "/configs/netherlands/",
            data={
                "editor_mode": "manual",
                "manual_country_code": "netherlands",
                "manual_legal_subdivision": "2",
                "pages_json": pages_json,
                "page_source": ["admin", "cities"],
                "page_enabled": ["true", "false"],
                "page_force_highest_level": ["", "3"],
                "page_parent_level": ["", "2"],
                "page_include_infosection": ["true", "true"],
                "page_include_major_subdivision": ["true", "false"],
                "page_include_minor_subdivision": ["true", "false"],
                "page_include_cities": ["false", "true"],
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        content = ScrapingConfig.objects.get(slug="netherlands").content
        self.assertIn('path = ["drenthe"]', content)
        self.assertIn("enabled = false", content)
        self.assertIn("force_highest_level = 3", content)
        self.assertIn("parent_level = 2", content)
        self.assertIn("include = { cities = true, infosection = true, major_subdivision = false }", content)

    def test_config_edit_keeps_citypopulation_config_as_main_editor(self):
        upsert_scraping_config(
            "spain",
            """
scrape_schema_version = 2

[[pages]]
source = "admin"
path = ["admin"]
""".strip() + "\n",
        )

        response = self.client.get("/configs/spain/")

        self.assertEqual(response.status_code, 200)
        front = ScrapingConfig.objects.get(slug="spain")
        self.assertIn("[[pages]]", front.content)
        self.assertIn('source = "admin"', front.content)
        self.assertFalse(ScrapingConfig.objects.filter(slug="old-spain").exists())
        self.assertContains(response, "admin")

    def test_recipe_form_renders_importable_python_with_numeric_dat_keys(self):
        content = _render_recipe_from_form(
            {
                "slug": "testland",
                "root_name": "Testland",
                "source_country": "spain",
                "municipal_level": "3",
                "representation_level": "2",
                "representation_total": "100",
                "representation_min": "1",
                "divisions_json": """
[
  {
    "name": "Provincia",
    "code": "PRO",
    "dat": {"2": ["Madrid"]}
  }
]
""",
            }
        )

        _validate_recipe_text(content, filename="testland.py")
        tree = ast.parse(content)
        divisions_node = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "DIVISIONS" for target in node.targets)
        )
        divisions = ast.literal_eval(divisions_node)
        self.assertEqual(divisions[0]["dat"], {2: ["Madrid"]})

    def test_web_translation_catalogs_are_loaded(self):
        with translation.override("en"):
            self.assertEqual(translation.gettext("Panel"), "Dashboard")
        with translation.override("fr"):
            self.assertEqual(translation.gettext("Panel"), "Tableau de bord")
        with translation.override("de"):
            self.assertEqual(translation.gettext("Panel"), "Übersicht")
        with translation.override("ru"):
            self.assertEqual(translation.gettext("Panel"), "Панель")
            self.assertEqual(translation.gettext("Buscar pais"), "\u041d\u0430\u0439\u0442\u0438 \u0441\u0442\u0440\u0430\u043d\u0443")

    def test_user_facing_text_sources_are_not_mojibake(self):
        repo_root = Path(__file__).resolve().parents[2]
        expected_by_path = {
            "ciudades_del_mundo/templates/ciudades_del_mundo/config_list.html": [
                "Configuraciones pa\u00edses",
                "Nueva configuraci\u00f3n",
                "En ejecuci\u00f3n",
                "Pa\u00eds o c\u00f3digo",
            ],
            "ciudades_del_mundo/management/commands/ensure_visual_assets.py": [
                "pa\u00edses",
                "configuraci\u00f3n",
                "L\u00edmite",
                "im\u00e1genes",
                "p\u00e1ginas",
            ],
            "ciudades_del_mundo/services/visual_assets.py": [
                "respuesta vac\u00eda al descargar la imagen",
            ],
            "locale/ru/LC_MESSAGES/django.po": [
                "\u041d\u0430\u0439\u0442\u0438 \u0441\u0442\u0440\u0430\u043d\u0443",
                "\u0421\u0442\u0440\u043e\u043a \u043d\u0430 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0435",
                "\u0421\u0442\u0438\u043b\u044c",
                "\u0421\u0432\u0435\u0442\u043b\u044b\u0439",
                "\u0422\u0451\u043c\u043d\u044b\u0439",
                "\u0420\u0435\u0442\u0440\u043e 80",
                "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435",
            ],
        }
        mojibake_markers = ("\u00c3", "\u00c2", "\ufffd")

        for relative_path, expected_strings in expected_by_path.items():
            with self.subTest(path=relative_path):
                text = (repo_root / relative_path).read_text(encoding="utf-8-sig")
                self.assertFalse(any(marker in text for marker in mojibake_markers))
                for expected in expected_strings:
                    self.assertIn(expected, text)

    def test_task_manager_reads_existing_db_task_history(self):
        with TemporaryDirectory() as tmpdir:
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            log_path = Path(tmpdir) / "task-1.log"
            task = ManagedTask.objects.create(
                id="task-1",
                key="validate:test",
                label="Validar test",
                args=["validate_subdivision_configs", "test"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=timezone.now(),
                started_at=timezone.now(),
                finished_at=timezone.now(),
                log_path=str(log_path),
            )
            with manager._lock:
                manager._append_output_locked(task, "ok\n")
                task.save(update_fields=["output", "log_path", "updated_at"])

            reloaded = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = reloaded.get("task-1")

        self.assertIsNotNone(task)
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(task.output_text, "ok\n")
        self.assertEqual(reloaded.output_text(task), "ok\n")
        self.assertEqual(reloaded.latest_for_key("validate:test").id, "task-1")

    def test_task_manager_reads_full_log_file_after_output_tail_is_trimmed(self):
        with TemporaryDirectory() as tmpdir:
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = ManagedTask.objects.create(
                id="task-log",
                key="validate:log",
                label="Validar log",
                args=["validate_subdivision_configs", "log"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=timezone.now(),
                started_at=timezone.now(),
                finished_at=timezone.now(),
                log_path=str(Path(tmpdir) / "task-log.log"),
            )
            with manager._lock:
                for index in range(260):
                    manager._append_output_locked(task, f"line {index}\n")
                task.save(update_fields=["output", "log_path", "updated_at"])

            reloaded = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            task = reloaded.get("task-log")
            full_output = reloaded.output_text(task)

        self.assertIsNotNone(task)
        self.assertLess(len(task.output), 260)
        self.assertIn("line 0\n", full_output)
        self.assertIn("line 259\n", full_output)

    def test_task_manager_groups_scrape_logs_by_country(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True

            task = manager.start(
                key="scrape:algeria",
                label="Popular Argelia",
                args=["scrape_subdivisions_with_assets", "algeria"],
            )

        self.assertEqual(Path(task.log_path).parts, (".web_task_logs", "algeria", f"{task.id}.log"))

    def test_cleanup_old_logs_removes_files_older_than_retention(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            old_log = root / ".web_task_logs" / "algeria" / "old.log"
            fresh_log = root / ".web_task_logs" / "algeria" / "fresh.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old", encoding="utf-8")
            fresh_log.write_text("fresh", encoding="utf-8")
            old_timestamp = time.time() - 91 * 24 * 60 * 60
            os.utime(old_log, (old_timestamp, old_timestamp))

            removed = cleanup_old_logs(base_dir=root, days=90)

            self.assertEqual(removed, 1)
            self.assertFalse(old_log.exists())
            self.assertTrue(fresh_log.exists())


    def test_task_manager_queues_tasks_past_running_limit(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True
            tasks = [
                manager.start(
                    key=f"validate:test-{index}",
                    label=f"Validar test {index}",
                    args=["validate_subdivision_configs", f"test-{index}"],
                )
                for index in range(5)
            ]

            statuses = list(
                ManagedTask.objects.filter(key__startswith="validate:test-")
                .order_by("created_at", "id")
                .values_list("status", flat=True)
            )
            self.assertEqual(statuses, ["running", "queued", "queued", "queued", "queued"])
            self.assertEqual(manager.started_workers, [tasks[0].id])

            first = ManagedTask.objects.get(id=tasks[0].id)
            first.status = ManagedTask.Status.SUCCEEDED
            first.finished_at = timezone.now()
            first.save(update_fields=["status", "finished_at", "updated_at"])
            with manager._lock:
                task_ids = manager._dispatch_queued_locked()
            manager._start_workers(task_ids)

            tasks[1].refresh_from_db()
            self.assertEqual(tasks[1].status, "running")
            self.assertEqual(manager.started_workers, [tasks[0].id, tasks[1].id])

    def test_task_manager_cancels_queued_task_without_starting_worker(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
            manager._recovery_done = True
            first = manager.start(
                key="validate:first",
                label="Validar primero",
                args=["validate_subdivision_configs"],
            )
            second = manager.start(
                key="validate:second",
                label="Validar segundo",
                args=["validate_subdivision_configs"],
            )

            manager.cancel(second.id)
            first.refresh_from_db()
            second.refresh_from_db()

        self.assertEqual(first.status, "running")
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(manager.started_workers, [first.id])

    def test_can_clear_requires_rows_and_no_active_operation(self):
        for status in ["pending", "validated", "populated", "failed"]:
            with self.subTest(status=status):
                self.assertTrue(_can_clear_config_row({"rows": 3}, status))

        for status in ["validating", "populating", "clearing", "running", "queued"]:
            with self.subTest(status=status):
                self.assertFalse(_can_clear_config_row({"rows": 3}, status))

        self.assertFalse(_can_clear_config_row({"rows": 0}, "populated"))

    def test_can_scrape_matches_config_lifecycle(self):
        for status in ["pending", "failed", "validated"]:
            with self.subTest(status=status):
                self.assertTrue(_can_scrape_config_status(status))

        for status in ["populated", "validating", "populating", "clearing", "running", "queued"]:
            with self.subTest(status=status):
                self.assertFalse(_can_scrape_config_status(status))


class TaskManagerDatabaseTests(TestCase):
    def test_task_status_maps_finished_scrape_progress_to_populated(self):
        slug = "zzstatusdone"
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-done-status",
                key=f"scrape:{slug}",
                label="Popular status",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            response = self.client.get(f"/tasks/{task.id}/status/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["config_progress"][slug]["status"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_task_status_synthesizes_finished_scrape_progress_when_file_is_missing(self):
        slug = "zzstatusmissing"
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-done-missing",
                key=f"scrape:{slug}",
                label="Popular status missing",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            progress_path = task_progress_path(task.id)
            if progress_path.exists():
                progress_path.unlink()

            response = self.client.get(f"/tasks/{task.id}/status/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["config_progress"][slug]["status"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_bulk_config_summary_maps_finished_scrape_progress_to_populated(self):
        slug = "zzbulkdone"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Bulk Done Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-all-done-status",
                key="scrape:all",
                label="Popular todas",
                args=["validate_and_scrape_configs"],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            bulk_task, status = _latest_bulk_config_progress(slug)
            response = self.client.get(f"/configs/{slug}/summary/")

            self.assertEqual(bulk_task, task)
            self.assertEqual(status, "populated")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["task_status"], "populated")
            self.assertEqual(response.json()["status_filter"], "populated")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_output_since_text_reads_incremental_log_from_offset(self):
        with TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "task.log"
            log_path.write_text("line 0\nline 1\n", encoding="utf-8", newline="")
            task = ManagedTask.objects.create(
                id="task-incremental-log",
                key="validate:incremental-log",
                label="Validar log incremental",
                args=["validate_subdivision_configs", "log"],
                status=ManagedTask.Status.RUNNING,
                created_at=timezone.now(),
                started_at=timezone.now(),
                log_path=str(log_path),
            )
            manager = TaskManager(history_path=Path(tmpdir) / "tasks.json")
            initial_offset = manager.output_offset(task)

            with log_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write("line 2\n")

            delta, next_offset, reset = manager.output_since_text(task, initial_offset)

        self.assertFalse(reset)
        self.assertEqual(delta, "line 2\n")
        self.assertGreater(next_offset, initial_offset)


class ConfigSourceEntitiesViewTests(TestCase):
    def test_populated_config_only_shows_clear_when_rows_exist(self):
        slug = "zztestpopulatedonlyclear"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populated Only Clear Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Populatedland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-finished",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertFalse(_can_scrape_config_status("populated"))
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            clear_form = re.search(r'<form[^>]*data-config-action-form="clear"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertIsNotNone(clear_form)
            self.assertIn("hidden", scrape_form.group(0))
            self.assertNotIn("hidden", clear_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_disables_validate_button_while_validation_is_active(self):
        slug = "zztestvalidate"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validate Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-active",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/validate/", html)
            self.assertIn("config-status-validating config-status-loading", html)
            self.assertIn('class="config-status-dots" aria-hidden="true"', html)
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_links_failed_validation_to_task_log(self):
        slug = "zztestvalidatefailed"
        task_id = "validate-failed-log-link"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validate Failed Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id=task_id,
                key=f"validate-config:{slug}",
                label="Validar test fallido",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.FAILED,
                returncode=1,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f'href="/tasks/{task_id}/"', html)
            self.assertIn("config-status-failed", html)
            self.assertIn(">Fallo</a>", html)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_uses_loading_badge_for_active_population(self):
        slug = "zztestpopulateactive"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populate Active Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/scrape/", html)
            self.assertIn("config-status-populating config-status-loading", html)
            self.assertIn('<span class="config-status-label">Populando</span>', html)
            self.assertIn('class="config-status-dots" aria-hidden="true"', html)
            self.assertNotIn("\u2026Populando", html)
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_populating_after_prevalidation_hides_popular_button(self):
        slug = "zztestpopulateafterprevalidation"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Populate After Prevalidation Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-after-prevalidation-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["validate_and_scrape_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            path = task_progress_path(task.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"task_id": task.id, "configs": {slug: {"status": "populating"}}}),
                encoding="utf-8",
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn("config-status-populating config-status-loading", html)
            self.assertIn("hidden", _config_action_form_tag(html, "scrape"))
            self.assertIn("hidden", _config_action_form_tag(html, "validate"))
            self.assertIn("hidden", _config_action_form_tag(html, "clear"))
            self.assertNotIn("hidden", _config_action_form_tag(html, "stop"))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_shows_stop_button_while_population_is_active(self):
        slug = "zzteststopbutton"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Stop Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-stop-active",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/stop/", html)
            self.assertIn('data-config-action-form="stop"', html)
            self.assertIn('data-config-action="stop"', html)
            self.assertIn(">Parar<", html)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_stop_config_task_returns_to_validated_state(self):
        slug = "zzteststopstate"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Stop State Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-before-stop",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            task = ManagedTask.objects.create(
                id="scrape-stop-state",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.post(
                f"/configs/{slug}/task/stop/",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "validated")
            task.refresh_from_db()
            self.assertEqual(task.status, ManagedTask.Status.CANCELLED)

            summary = self.client.get(f"/configs/{slug}/summary/").json()
            self.assertEqual(summary["task_status"], "validated")
            self.assertEqual(summary["status_filter"], "validated")
            self.assertTrue(summary["can_scrape"])
            self.assertFalse(summary["can_resume"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_cancelled_scrape_row_returns_to_validated_without_resume_button(self):
        slug = "zztestcancelledscrapevalidated"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Cancelled Scrape Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-cancelled",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.CANCELLED,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn("config-status-validated", html)
            self.assertNotIn(f"/configs/{slug}/task/resume/", html)
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertNotIn("hidden", scrape_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_pending_popular_action_validates_before_scraping(self):
        slug = "zztestpendingpopularvalidates"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Pending Popular Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="pending-popular-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"scrape:{slug}")
            self.assertEqual(kwargs["args"], ["validate_and_scrape_configs", slug, "--no-download-assets", "--page-workers=4"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_import_toml_starts_background_sync_task(self):
        original_recovery_done = task_manager._recovery_done
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="import-toml-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    "/configs/import-toml/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], "config-import:toml")
            self.assertEqual(kwargs["args"], ["sync_scraping_configs", "--force"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_single_config_import_toml_reads_only_matching_seed(self):
        slug = "zzimportone"
        with (
            patch("ciudades_del_mundo.web.views.bundled_toml_config_paths", return_value=[Path(f"subdivisions/{slug}.toml")]) as paths,
            patch("ciudades_del_mundo.web.views.sqlite_write_lock_if_needed", return_value=None),
            patch("ciudades_del_mundo.web.views.sync_scraping_configs_from_toml", return_value=1) as sync,
        ):
            response = self.client.post(
                f"/configs/{slug}/import-toml/",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        paths.assert_called_once_with([slug])
        sync.assert_called_once_with(force=True, only_if_empty=False, slugs=[slug])
        self.assertTrue(response.json()["imported"])

    def test_bulk_popular_all_uses_country_workers(self):
        original_recovery_done = task_manager._recovery_done
        for slug in ("zztestbulkworkersa", "zztestbulkworkersb"):
            upsert_scraping_config(
                slug,
                """
name = "Bulk Workers Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
            )
        try:
            task_manager._recovery_done = True
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="bulk-workers-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    "/configs/all/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], "scrape:all")
            self.assertEqual(kwargs["args"][-3:], ["--no-download-assets", "--page-workers=4", "--country-workers=1"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_validated_popular_action_scrapes_directly(self):
        slug = "zztestvalidatedpopulardirect"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Validated Popular Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-direct-popular",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="validated-popular-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/scrape/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"scrape:{slug}")
            self.assertEqual(kwargs["args"], ["scrape_subdivisions_with_assets", slug, "--no-download-assets", "--page-workers=4"])
        finally:
            task_manager._recovery_done = original_recovery_done


    def test_validate_command_clears_previous_config_error_on_success(self):
        slug = "zztestvalidateclearsfailure"
        upsert_scraping_config(
            slug,
            """
name = "Validate Clears Failure"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        ScrapingConfig.objects.filter(slug=slug).update(is_valid=False, validation_error="old failure")

        call_command("validate_subdivision_configs", slug, stdout=StringIO())

        record = ScrapingConfig.objects.get(slug=slug)
        self.assertTrue(record.is_valid)
        self.assertEqual(record.validation_error, "")
        summary = self.client.get(f"/configs/{slug}/summary/").json()
        self.assertEqual(summary["status_filter"], "validated")
        self.assertTrue(summary["can_scrape"])

    def test_config_table_shows_clear_button_when_rows_exist_and_no_operation_is_active(self):
        slug = "zztestclearbutton"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clearland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertIn('data-config-action-form="clear"', html)
            self.assertIn('data-config-action="clear"', html)
            self.assertNotRegex(html, r'data-config-action-form="clear"[^>]*hidden')
            self.assertIn(">Limpiar<", html)
            self.assertNotIn("status-icon", html)

            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-clear-button-active",
                key=f"validate-config:{slug}",
                label="Validar test",
                args=["validate_subdivision_configs", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            response = self.client.get(f"/configs/table/?q={slug}")
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertRegex(html, r'data-config-action-form="clear"[^>]*hidden')
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_cancelled_clear_returns_to_previous_available_actions(self):
        slug = "zztestcancelledclearnotonly"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Cancelled Clear Not Only Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Cancelledland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="clear-cancelled-not-only",
                key=f"clear-config:{slug}",
                label="Limpiar test",
                args=["clear_config_data", slug],
                status=ManagedTask.Status.CANCELLED,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            clear_form = re.search(r'<form[^>]*data-config-action-form="clear"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertIsNotNone(clear_form)
            self.assertNotIn("hidden", scrape_form.group(0))
            self.assertNotIn("hidden", clear_form.group(0))
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_config_table_hides_clear_button_while_clear_is_active(self):
        slug = "zztestclearbuttonactiveclear"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Active Clear Button Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Active Clearland",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="clear-button-active-clear",
                key=f"clear-config:{slug}",
                label="Limpiar test",
                args=["clear_config_data", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )

            response = self.client.get(f"/configs/table/?q={slug}")

            self.assertEqual(response.status_code, 200)
            html = response.content.decode("utf-8")
            self.assertIn(f"/configs/{slug}/task/clear/", html)
            self.assertRegex(html, r'data-config-action-form="clear"[^>]*hidden')
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_starts_clear_command(self):
        slug = "zztestclearaction"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Action Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Action Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-action-populated",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="clear-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"clear-config:{slug}")
            self.assertEqual(kwargs["args"], ["clear_config_data_with_assets", slug])
            self.assertEqual(response.json()["label"], f"Limpiar: {slug}")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_rejects_active_operation_even_with_rows(self):
        slug = "zztestclearactive"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Clear Active Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Active Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-active",
                key=f"scrape:{slug}",
                label="Popular active",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.RUNNING,
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 400)
            start.assert_not_called()
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_task_action_passes_country_code_to_command(self):
        slug = "zztestclearcountryaction"
        country_code = "zzrealclearcountry"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            f"""
name = "Clear Action CountryCode Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Action CountryCode Land",
            level=0,
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="scrape-clear-country-populated",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            with patch("ciudades_del_mundo.web.views.task_manager.start") as start:
                start.return_value = _Object(
                    id="clear-country-started",
                    status=ManagedTask.Status.RUNNING,
                )
                response = self.client.post(
                    f"/configs/{slug}/task/clear/",
                    HTTP_ACCEPT="application/json",
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(response.status_code, 200)
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs["key"], f"clear-config:{slug}")
            self.assertEqual(kwargs["args"], ["clear_config_data_with_assets", country_code])
            self.assertEqual(response.json()["label"], f"Limpiar: {slug}")
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_data_command_deletes_rows_and_returns_to_pending(self):
        slug = "zztestclearcommand"
        original_recovery_done = task_manager._recovery_done
        record = upsert_scraping_config(
            slug,
            """
name = "Clear Command Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        record.is_valid = False
        record.validation_error = "previous validation failure"
        record.save(update_fields=["is_valid", "validation_error"])
        root = AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Command Land",
            level=0,
        )
        AdminArea.objects.create(
            id=f"{slug}_child",
            country_code=slug,
            code="child",
            name="Child",
            level=1,
            parent=root,
        )
        try:
            task_manager._recovery_done = True
            call_command("clear_config_data", slug)

            self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 0)
            record.refresh_from_db()
            self.assertFalse(record.is_valid)
            self.assertEqual(record.validation_error, "")

            summary = self.client.get(f"/configs/{slug}/summary/").json()
            self.assertEqual(summary["rows"], 0)
            self.assertEqual(summary["task_status"], "pending")
            self.assertEqual(summary["status_filter"], "pending")
            self.assertTrue(summary["can_validate"])
            self.assertFalse(summary["can_clear"])
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_clear_config_data_command_deletes_visual_assets_and_translations(self):
        slug = "zztestclearassetslug"
        country_code = "zzrealclearassetcountry"
        upsert_scraping_config(
            slug,
            f"""
name = "Clear Assets Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Assets Land",
            level=0,
        )
        child = AdminArea.objects.create(
            id=f"{country_code}_child",
            country_code=country_code,
            code="child",
            name="Child",
            level=1,
            parent=root,
        )
        now = timezone.now()
        country_asset = VisualAsset.objects.create(
            entity_type="country",
            entity_key=slug,
            entity_name="Clear Assets Land",
            country_code="",
            kind="flag",
            status="found",
            created_at=now,
            updated_at=now,
        )
        admin_asset = VisualAsset.objects.create(
            entity_type="admin_area",
            entity_key=child.id,
            entity_name="Child",
            country_code="",
            kind="coat",
            status="found",
            created_at=now,
            updated_at=now,
        )
        unrelated_asset = VisualAsset.objects.create(
            entity_type="country",
            entity_key="otherland",
            entity_name="Otherland",
            country_code="otherland",
            kind="flag",
            status="found",
            created_at=now,
            updated_at=now,
        )
        VisualAssetTranslation.objects.create(
            asset=country_asset,
            language="es",
            title="Bandera",
            created_at=now,
            updated_at=now,
        )
        VisualAssetTranslation.objects.create(
            asset=admin_asset,
            language="es",
            title="Escudo",
            created_at=now,
            updated_at=now,
        )

        call_command("clear_config_data", country_code)

        self.assertFalse(VisualAsset.objects.filter(id__in=[country_asset.id, admin_asset.id]).exists())
        self.assertEqual(VisualAssetTranslation.objects.count(), 0)
        self.assertTrue(VisualAsset.objects.filter(id=unrelated_asset.id).exists())

    def test_clear_config_data_command_deletes_local_visual_asset_files(self):
        slug = "zztestclearmedia"
        upsert_scraping_config(
            slug,
            """
name = "Clear Media Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Media Land",
            level=0,
        )
        with TemporaryDirectory() as tmpdir, self.settings(MEDIA_ROOT=Path(tmpdir)):
            local_path = Path("visual_assets") / "flag" / slug / f"{slug}_flag.svg"
            absolute_path = Path(tmpdir) / local_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_text("<svg></svg>", encoding="utf-8")
            now = timezone.now()
            VisualAsset.objects.create(
                entity_type="country",
                entity_key=slug,
                entity_name="Clear Media Land",
                country_code=slug,
                kind="flag",
                status="downloaded",
                local_path=str(local_path),
                local_exists=True,
                created_at=now,
                updated_at=now,
            )

            call_command("clear_config_data", slug)

            self.assertFalse(absolute_path.exists())
            self.assertEqual(VisualAsset.objects.filter(country_code=slug).count(), 0)

    def test_clear_config_data_command_accepts_country_code_not_only_slug(self):
        slug = "zztestclearcountryslug"
        country_code = "zzrealclearcommand"
        record = upsert_scraping_config(
            slug,
            f"""
name = "Clear Command CountryCode Test"
country_code = "{country_code}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        AdminArea.objects.create(
            id=f"{country_code}_root",
            country_code=country_code,
            code="root",
            name="Clear Command CountryCode Land",
            level=0,
        )
        AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Different Slug Land",
            level=0,
        )

        call_command("clear_config_data", country_code)

        self.assertEqual(AdminArea.objects.filter(country_code=country_code).count(), 0)
        self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 1)
        record.refresh_from_db()
        self.assertFalse(record.is_valid)
        self.assertEqual(record.validation_error, "")

    def test_clear_config_data_command_uses_small_delete_batches(self):
        slug = "zztestclearbatch"
        upsert_scraping_config(
            slug,
            """
name = "Clear Batch Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id=f"{slug}_root",
            country_code=slug,
            code="root",
            name="Clear Batch Land",
            level=0,
        )
        parent = root
        created = []
        for index in range(1, 8):
            area = AdminArea.objects.create(
                id=f"{slug}_child_{index}",
                country_code=slug,
                code=f"child-{index}",
                name=f"Child {index}",
                level=min(index, 5),
                parent=parent,
            )
            created.append(area)
            parent = area

        root.capitals.add(created[0], created[1])
        created[2].most_populate_city = created[-1]
        created[2].save(update_fields=["most_populate_city"])
        derived = NuevoAdminArea.objects.create(
            id=f"{slug}-derived",
            country_code=slug,
            code="derived",
            name="Derived",
            level=0,
            most_populate_city=created[-1],
        )
        derived.capitals.add(created[0])
        derived.municipios_originales.add(created[1], created[2])

        stdout = StringIO()
        with patch(
            "ciudades_del_mundo.management.commands.clear_config_data.CLEAR_DELETE_BATCH_SIZE",
            2,
        ):
            call_command("clear_config_data", slug, stdout=stdout)

        self.assertEqual(AdminArea.objects.filter(country_code=slug).count(), 0)
        derived.refresh_from_db()
        self.assertIsNone(derived.most_populate_city_id)
        self.assertEqual(derived.capitals.count(), 0)
        self.assertEqual(derived.municipios_originales.count(), 0)
        output = stdout.getvalue()
        self.assertIn(f"Limpiando: country_code={slug} filas=8 lote=2", output)
        self.assertIn("Limpiando: lote=1", output)
        self.assertIn("borradas=", output)

    def test_bulk_scrape_unpopulated_excludes_already_populated_configs(self):
        validated_slug = "zztestvalidatedonly"
        populated_slug = "zztestpopulatedskip"
        original_recovery_done = task_manager._recovery_done
        for slug, name in (
            (validated_slug, "Validated Only"),
            (populated_slug, "Populated Skip"),
        ):
            upsert_scraping_config(
                slug,
                f"""
name = "{name}"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
            )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            ManagedTask.objects.create(
                id="validate-unpopulated-only",
                key=f"validate-config:{validated_slug}",
                label="Validar test",
                args=["validate_subdivision_configs", validated_slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )
            ManagedTask.objects.create(
                id="scrape-populated-skip",
                key=f"scrape:{populated_slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", populated_slug],
                status=ManagedTask.Status.SUCCEEDED,
                returncode=0,
                created_at=now,
                started_at=now,
                finished_at=now,
                updated_at=now,
            )

            eligible = _eligible_config_slugs_for_bulk("scrape-unpopulated")

            self.assertIn(validated_slug, eligible)
            self.assertNotIn(populated_slug, eligible)
        finally:
            task_manager._recovery_done = original_recovery_done

    def test_source_entities_use_configured_country_code_for_levels_and_parents(self):
        slug = "zztestalias"
        upsert_scraping_config(
            slug,
            """
name = "Alias"
country_code = "realcountry"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
force_highest_level = 0
""".strip() + "\n",
        )
        root = AdminArea.objects.create(
            id="real_root",
            country_code="realcountry",
            code="root",
            name="Real Country",
            level=0,
        )
        parent = AdminArea.objects.create(
            id="real_parent",
            country_code="realcountry",
            code="parent",
            name="Parent",
            level=1,
            entity_type="Region",
            parent=root,
        )
        AdminArea.objects.create(
            id="real_child",
            country_code="realcountry",
            code="child",
            name="Child",
            level=2,
            entity_type="Municipality",
            parent=parent,
        )

        response = self.client.get(f"/configs/{slug}/source-entities/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["entities"]], ["real_parent", "real_child"])
        self.assertEqual([row["value"] for row in payload["levels"]], ["1", "2"])
        self.assertEqual([row["label"] for row in payload["levels"]], ["Region (1)", "Municipio (1)"])
        self.assertEqual([row["value"] for row in payload["entity_types"]], ["Municipio", "Region"])
        self.assertIn("real_parent", {row["value"] for row in payload["parents"]})
        self.assertEqual(payload["parents_by_level"]["1"], [])
        self.assertIn("real_parent", {row["value"] for row in payload["parents_by_level"]["2"]})

        form_response = self.client.get(f"/configs/{slug}/")
        self.assertEqual(form_response.status_code, 200)
        form_html = form_response.content.decode("utf-8")
        level_select = form_html.split('data-transfer-filter="level"', 1)[1].split("</select>", 1)[0]
        self.assertNotIn('<option value="">Todos</option>', level_select)
        self.assertIn('<option value="1">Region (1)</option>', form_html)
        self.assertIn('<option value="2">Municipio (1)</option>', form_html)
        self.assertIn('"id": "real_parent"', form_html)
