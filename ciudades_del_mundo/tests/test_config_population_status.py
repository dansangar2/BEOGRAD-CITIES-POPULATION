"""Population filters use persisted rows as well as task history."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from ciudades_del_mundo.models import AdminArea, ScrapingConfig, WebTask
from ciudades_del_mundo.web.queries.configs import (
    _eligible_config_slugs_for_bulk,
    _filtered_config_summaries,
)
from ciudades_del_mundo.web.tasks import task_manager


class ConfigPopulationStatusTests(TestCase):
    def setUp(self):
        recovery = patch.object(task_manager, "_recovery_done", True)
        recovery.start()
        self.addCleanup(recovery.stop)
        progress = patch(
            "ciudades_del_mundo.web.queries.configs.read_task_config_progress",
            return_value={},
        )
        progress.start()
        self.addCleanup(progress.stop)
        bootstrap = patch("ciudades_del_mundo.web.handlers.configs.ensure_initial_scraping_configs")
        bootstrap.start()
        self.addCleanup(bootstrap.stop)
        ScrapingConfig.objects.all().delete()
        self.record = ScrapingConfig.objects.create(
            slug="population-alias", country_code="population-country",
            name="Population Country", content="", content_hash="test",
        )

    def add_data(self):
        return AdminArea.objects.create(
            id="population-country-root", country_code=self.record.country_code,
            code="root", name=self.record.name, level=0,
        )

    def add_task(self, *, action="scrape", status=WebTask.Status.SUCCEEDED, old=False):
        return WebTask.objects.create(
            id="population-task", key=f"{action}:{self.record.slug}",
            label="Population task", status=status,
            created_at=timezone.now() - timedelta(days=1) if old else timezone.now(),
            args=["scrape_subdivisions_with_assets", self.record.slug],
        )

    def summary(self):
        response = self.client.get(f"/configs/{self.record.slug}/summary/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_existing_data_without_task_history_is_populated_in_api_and_table(self):
        self.add_data()
        summary = self.summary()
        self.assertEqual(summary["task_status"], "populated")
        self.assertEqual(summary["status_filter"], "populated")
        self.assertEqual(summary["rows"], 1)
        self.assertTrue(summary["can_clear"])
        response = self.client.get("/configs/table/")
        self.assertContains(response, 'data-status="populated"')
        self.assertNotContains(self.client.get("/configs/table/?status=unpopulated"), 'data-config-row=')

    def test_editing_config_does_not_make_existing_data_unpopulated(self):
        self.add_data()
        self.add_task(old=True)
        self.assertEqual(self.summary()["status_filter"], "populated")
        self.assertNotIn(self.record.slug, _eligible_config_slugs_for_bulk("scrape-unpopulated"))

    def test_existing_data_is_excluded_from_filter_and_bulk_without_hiding_workflow(self):
        self.add_data()
        for action, status, expected in (
            ("validate-config", WebTask.Status.SUCCEEDED, "validated"),
            ("scrape", WebTask.Status.FAILED, "failed"),
            ("scrape", WebTask.Status.RUNNING, "populating"),
            ("clear-config", WebTask.Status.RUNNING, "clearing"),
        ):
            with self.subTest(action=action, status=status):
                WebTask.objects.all().delete()
                self.add_task(action=action, status=status)
                self.assertEqual(self.summary()["status_filter"], expected)
                self.assertEqual(_filtered_config_summaries({"status": "unpopulated"}), [])
                self.assertNotIn(self.record.slug, _eligible_config_slugs_for_bulk("scrape-unpopulated"))
                self.assertIn(self.record.slug, [row["slug"] for row in _filtered_config_summaries({"status": expected})])

    def test_empty_config_remains_eligible_for_unpopulated_filter_and_bulk(self):
        for status in (None, WebTask.Status.FAILED):
            with self.subTest(status=status):
                if status:
                    self.add_task(status=status)
                self.assertEqual(self.summary()["status_filter"], "failed" if status else "pending")
                self.assertEqual(
                    [row["slug"] for row in _filtered_config_summaries({"status": "unpopulated"})],
                    [self.record.slug],
                )
                self.assertIn(self.record.slug, _eligible_config_slugs_for_bulk("scrape-unpopulated"))

    def test_validation_error_remains_visible_with_existing_data(self):
        self.add_data()
        self.record.is_valid = False
        self.record.validation_error = "Invalid configuration"
        self.record.save()
        self.assertEqual(self.summary()["status_filter"], "failed")
        self.assertEqual(_filtered_config_summaries({"status": "unpopulated"}), [])
