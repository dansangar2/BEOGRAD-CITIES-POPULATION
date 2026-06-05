import ast
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from django.test import TestCase
from django.utils import timezone
from django.utils import translation

from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.web.task_progress import task_progress_path
from ciudades_del_mundo.web.tasks import ManagedTask, TaskManager, task_manager
from ciudades_del_mundo.web.views import (
    _area_capital_display_names,
    _area_related_places,
    _eligible_config_slugs_for_bulk,
    _latest_bulk_config_progress,
    _render_recipe_from_form,
    _can_scrape_config_status,
    _validate_config_text,
    _validate_recipe_text,
)


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


class WebInterfaceHelperTests(unittest.TestCase):
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

    def test_validate_config_text_accepts_minimal_toml(self):
        _validate_config_text(
            "testland",
            """
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
lowest_level = 0
""",
        )

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

    def test_task_manager_persists_and_loads_task_history(self):
        with TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "tasks.json"
            manager = TaskManager(history_path=history_path)
            with manager._lock:
                manager._tasks["task-1"] = ManagedTask(
                    id="task-1",
                    key="validate:test",
                    label="Validar test",
                    args=["validate_subdivision_configs", "test"],
                    status="succeeded",
                    returncode=0,
                )
                manager._append_output_locked(manager._tasks["task-1"], "ok\n")
                manager._latest_by_key["validate:test"] = "task-1"
                manager._save_locked()

            reloaded = TaskManager(history_path=history_path)
            task = reloaded.get("task-1")

        self.assertIsNotNone(task)
        self.assertEqual(task.status, "succeeded")
        self.assertEqual(task.output_text, "ok\n")
        self.assertEqual(reloaded.output_text(task), "ok\n")
        self.assertEqual(reloaded.latest_for_key("validate:test").id, "task-1")

    def test_task_manager_reads_full_log_file_after_output_tail_is_trimmed(self):
        with TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "tasks.json"
            manager = TaskManager(history_path=history_path)
            with manager._lock:
                task = ManagedTask(
                    id="task-log",
                    key="validate:log",
                    label="Validar log",
                    args=["validate_subdivision_configs", "log"],
                    status="succeeded",
                    returncode=0,
                    log_path=manager._relative_log_path("task-log"),
                )
                manager._tasks[task.id] = task
                manager._latest_by_key[task.key] = task.id
                for index in range(260):
                    manager._append_output_locked(task, f"line {index}\n")
                manager._save_locked()

            reloaded = TaskManager(history_path=history_path)
            task = reloaded.get("task-log")
            full_output = reloaded.output_text(task)

        self.assertIsNotNone(task)
        self.assertLess(len(task.output), 260)
        self.assertIn("line 0\n", full_output)
        self.assertIn("line 259\n", full_output)

    def test_task_manager_limits_running_tasks_and_dispatches_queue(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=3,
            )
            tasks = [
                manager.start(
                    key=f"validate:test-{index}",
                    label=f"Validar test {index}",
                    args=["validate_subdivision_configs", f"test-{index}"],
                )
                for index in range(5)
            ]

            self.assertEqual(
                [task.status for task in tasks],
                ["running", "running", "running", "queued", "queued"],
            )
            self.assertEqual(manager.started_workers, [task.id for task in tasks[:3]])

            with manager._lock:
                tasks[0].status = "succeeded"
                task_ids_to_start = manager._dispatch_queued_locked()
            manager._start_workers(task_ids_to_start)

        self.assertEqual(task_ids_to_start, [tasks[3].id])
        self.assertEqual(tasks[3].status, "running")
        self.assertEqual(tasks[4].status, "queued")
        self.assertEqual(manager.started_workers, [task.id for task in tasks[:4]])

    def test_task_manager_cancels_queued_task_without_starting_worker(self):
        with TemporaryDirectory() as tmpdir:
            manager = _RecordingTaskManager(
                history_path=Path(tmpdir) / "tasks.json",
                max_running_tasks=1,
            )
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

        self.assertEqual(first.status, "running")
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(manager.started_workers, [first.id])


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
lowest_level = 0
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
    def test_populated_config_can_be_scraped_again(self):
        slug = "zztestrepopulate"
        original_recovery_done = task_manager._recovery_done
        upsert_scraping_config(
            slug,
            """
name = "Repopulate Test"
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
lowest_level = 0
""".strip() + "\n",
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
            self.assertTrue(_can_scrape_config_status("populated"))
            self.assertIn(f"/configs/{slug}/task/scrape/", html)
            self.assertIn('data-config-action="scrape"', html)
            scrape_form = re.search(r'<form[^>]*data-config-action-form="scrape"[^>]*>', html)
            self.assertIsNotNone(scrape_form)
            self.assertNotIn("hidden", scrape_form.group(0))
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
lowest_level = 0
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
            self.assertIn('data-config-action="validate" disabled', html)
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
lowest_level = 0
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
lowest_level = 0
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
                status=ManagedTask.Status.QUEUED,
                created_at=now,
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

    def test_stop_config_task_marks_config_as_stopped(self):
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
lowest_level = 0
""".strip() + "\n",
        )
        try:
            task_manager._recovery_done = True
            now = timezone.now()
            task = ManagedTask.objects.create(
                id="scrape-stop-state",
                key=f"scrape:{slug}",
                label="Popular test",
                args=["scrape_subdivisions_with_assets", slug],
                status=ManagedTask.Status.QUEUED,
                created_at=now,
                updated_at=now,
            )

            response = self.client.post(
                f"/configs/{slug}/task/stop/",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "stopped")
            task.refresh_from_db()
            self.assertEqual(task.status, ManagedTask.Status.CANCELLED)

            summary = self.client.get(f"/configs/{slug}/summary/").json()
            self.assertEqual(summary["task_status"], "stopped")
            self.assertEqual(summary["status_filter"], "stopped")
            self.assertTrue(summary["can_scrape"])
        finally:
            task_manager._recovery_done = original_recovery_done

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
lowest_level = 0
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
lowest_level = 0
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
