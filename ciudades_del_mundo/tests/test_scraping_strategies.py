"""Scraping entrypoint integration with an isolated, simulated AI module."""
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ciudades_del_mundo.infrastructure.ai.json_provider import OpenAICompatibleJsonProvider
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore
from ciudades_del_mundo.models import AdminArea, TerritorialRun, WebTask
from ciudades_del_mundo.services.scraping_configs import upsert_scraping_config
from ciudades_del_mundo.tests.territorial_support import (
    SimulatedProvider, evidence_document, frozen_fixture, reference_document,
)


class ScrapingStrategyOptionTests(SimpleTestCase):
    def test_ai_requires_explicit_country_evidence_and_separate_review(self):
        invalid = [
            ["--strategy", "ai"],
            ["spain", "france", "--strategy", "ai"],
            ["spain", "--strategy", "ai"],
            ["spain", "--strategy", "ai", "--discover", "--list-pages"],
            ["spain", "--strategy", "ai", "--discover", "--ai-enrich"],
            ["spain", "--strategy", "ai", "--territorial-run", "run", "--discover"],
            ["spain", "--strategy", "ai", "--plan-hash", "hash"],
            ["spain", "--evidence-id", "1"],
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(CommandError):
                call_command("scrape_subdivisions", *args, stdout=StringIO())


class ScrapingStrategyIntegrationTests(TransactionTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(BASE_DIR=Path(directory.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.store = DjangoTerritorialStore()
        self.store.import_reference(reference_document(), reviewed_by="synthetic test reviewer")
        self.evidence = self.store.import_evidence(evidence_document())

    def propose(self, provider=None):
        provider = provider or SimulatedProvider()
        with patch.object(OpenAICompatibleJsonProvider, "from_env", return_value=provider), patch(
            "ciudades_del_mundo.management.commands.scrape_subdivisions.PythonScrapingConfigRepository",
            side_effect=AssertionError("AI strategy must not load a traditional recipe")), patch(
            "ciudades_del_mundo.management.commands.scrape_subdivisions._ensure_scraping_schema_ready",
            side_effect=AssertionError("AI strategy must not automatically migrate SQL")):
            call_command("scrape_subdivisions", "spain", "--strategy", "ai", "--ai-engine", "provider", "--evidence-id", str(self.evidence.pk), stdout=StringIO())
        return TerritorialRun.objects.latest("created_at")

    def test_scraping_command_uses_ai_without_a_traditional_recipe_and_only_proposes(self):
        provider = SimulatedProvider()
        with patch("ciudades_del_mundo.management.commands.scrape_subdivisions.write_config_progress") as progress:
            run = self.propose(provider)
        self.assertEqual([call.args[1] for call in progress.call_args_list], ["observing", "proposing", "validating", "validated"])
        self.assertEqual(run.status, "validated")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(AdminArea.objects.count(), 0)
        self.assertEqual(run.evidence_id, self.evidence.pk)

    def test_scraping_command_reviews_and_applies_without_calling_the_model_again(self):
        run = self.propose()
        prior_updated_at = run.updated_at
        with patch.object(OpenAICompatibleJsonProvider, "from_env", side_effect=AssertionError("Review/apply must not call AI")):
            review = StringIO()
            call_command("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", run.pk, stdout=review)
            payload = json.loads(review.getvalue())
            self.assertEqual(payload["plan_hash"], run.plan_hash)
            self.assertIn("expected", payload)
            self.assertIn("observed", payload)
            self.assertIn("differences", payload)
            run.refresh_from_db()
            self.assertEqual(run.updated_at, prior_updated_at)
            output = StringIO()
            args = ["spain", "--strategy", "ai", "--territorial-run", run.pk, "--plan-hash", run.plan_hash]
            call_command("scrape_subdivisions", *args, stdout=output)
            first_result = json.loads(output.getvalue())["result"]
            repeated = StringIO()
            call_command("scrape_subdivisions", *args, stdout=repeated)
            self.assertEqual(json.loads(repeated.getvalue())["result"], first_result)
        self.assertEqual(AdminArea.objects.get(pk="spain_020100101").parent_id, "spain_0201001")
        self.assertEqual(first_result["deleted"], 0)
        run.refresh_from_db()
        self.assertEqual(run.approved_plan, run.plan)

    def test_foreign_country_and_unreviewed_plan_hash_do_not_apply(self):
        run = self.propose()
        for country, plan_hash in (("france", run.plan_hash), ("spain", "stale")):
            with self.subTest(country=country), self.assertRaises(CommandError):
                call_command("scrape_subdivisions", country, "--strategy", "ai", "--territorial-run", run.pk,
                    "--plan-hash", plan_hash, stdout=StringIO())
        self.assertEqual(AdminArea.objects.count(), 0)

    def test_invalid_ai_response_preserves_diagnostics_and_current_areas(self):
        existing = AdminArea.objects.create(id="spain_keep", country_code="spain", code="keep", name="Existing", level=0)
        with self.assertRaises(CommandError):
            self.propose(SimulatedProvider({"schema_version": 999}))
        run = TerritorialRun.objects.latest("created_at")
        self.assertEqual(run.status, "invalid")
        self.assertTrue(run.diagnostics)
        self.assertEqual(list(AdminArea.objects.values_list("pk", flat=True)), [existing.pk])

    def test_scraping_task_cancellation_reaches_the_ai_module_without_provider_calls(self):
        task = WebTask.objects.create(id="cancelled-ai", key="territorial:spain", label="test",
            status="cancelled", cancel_requested=True, created_at=timezone.now())
        with patch("ciudades_del_mundo.management.commands.scrape_subdivisions.current_task_id", return_value=task.pk), patch.object(
            OpenAICompatibleJsonProvider, "from_env", side_effect=AssertionError("Cancelled task must not call provider")), self.assertRaises(CommandError):
            call_command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", str(self.evidence.pk), stdout=StringIO())
        self.assertEqual(TerritorialRun.objects.latest("created_at").status, "cancelled")
        self.assertEqual(AdminArea.objects.count(), 0)

    def test_default_strategy_still_reads_sql_recipe_and_lists_pages_without_ai(self):
        upsert_scraping_config("spain", frozen_fixture()["traditional_content"])
        output = StringIO()
        with patch.object(OpenAICompatibleJsonProvider, "from_env", side_effect=AssertionError("Traditional mode must not call AI")):
            call_command("scrape_subdivisions", "spain", "--list-pages", stdout=output)
        self.assertIn("https://www.citypopulation.de/en/spain/admin/", output.getvalue())
        self.assertEqual(TerritorialRun.objects.count(), 0)

    def test_local_engine_proposes_and_applies_without_credentials_or_provider(self):
        from ciudades_del_mundo.application.territorial_reconciliation import compare_entities
        from ciudades_del_mundo.tests.territorial_support import baseline_entities
        with patch.object(OpenAICompatibleJsonProvider, "from_env", side_effect=AssertionError("Local engine must not use a provider")):
            call_command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", str(self.evidence.pk), stdout=StringIO())
            run = TerritorialRun.objects.latest("created_at")
            self.assertEqual(run.status, "validated")
            self.assertEqual(run.metrics["engine"], "local")
            self.assertEqual(run.metrics["provider_calls"], 0)
            self.assertIsNone(run.metrics["usage"])
            self.assertEqual(compare_entities(baseline_entities(), self.store.compiled(run)), [])
            call_command("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", run.pk,
                "--plan-hash", run.plan_hash, stdout=StringIO())
        self.assertEqual(AdminArea.objects.filter(country_code="spain").count(), len(baseline_entities()))
