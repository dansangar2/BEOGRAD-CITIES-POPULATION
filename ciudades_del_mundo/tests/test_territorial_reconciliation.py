"""Offline evidence, validation, regression and atomic application acceptance."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ciudades_del_mundo.application.territorial_reconciliation import compare_entities, compile_proposal, evidence_rows, propose
from ciudades_del_mundo.domain.territorial_reconciliation import ReconciliationError, parse_reference, parse_plan, validate_plan
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import (
    ControlledTerritorialDiscovery, content_hash, controlled_url, verify_evidence,
)
from ciudades_del_mundo.models import AdminArea, DynamicTranslation, ScrapingConfig, TerritorialRun, TerritorialEvidence, VisualAsset, WebTask
from ciudades_del_mundo.ports.ai import AiProviderResponseError
from ciudades_del_mundo.services.territorial_reconciliation import (
    ReconciliationCancelled, effective_status, execute_run, task_checkpoint,
)
from ciudades_del_mundo.tests.territorial_support import (
    SimulatedProvider, baseline_entities, evidence_document, frozen_fixture, reference_document, valid_proposal,
)


class ProposalValidationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reference = reference_document()
        cls.evidence = evidence_document()
        cls.version = content_hash(cls.evidence)

    def proposal(self):
        return valid_proposal(self.reference, self.evidence, self.version)

    def compile(self, plan):
        return compile_proposal(self.reference, self.evidence, plan, self.version)

    def test_spain_branch_comparison_has_no_entity_metric_or_relationship_differences(self):
        self.assertEqual(self.reference, frozen_fixture()["reference"])
        actual = self.compile(self.proposal())
        self.assertEqual(compare_entities(baseline_entities(), actual), [])
        by_code = {e.code: e for e in actual}
        self.assertEqual({e.code for e in actual if e.parent_code == "01"}, {"0101", "0102"})
        self.assertEqual(by_code["0201"].parent_code, "02")
        self.assertEqual((by_code["02"].level, by_code["0201"].level), (1, 2))
        for prefix, name in (("03", "ceuta"), ("04", "melilla")):
            province = prefix + f"__spain_{name}__repeat2"
            self.assertEqual(by_code[province].parent_code, prefix)
            municipality = prefix + "001"
            self.assertEqual(by_code[municipality].parent_code, province)
            self.assertEqual(by_code[municipality + "01"].parent_code, municipality)
        self.assertEqual({e.code for e in actual if e.parent_code == "0201001"}, {"020100101", "020100102"})
        self.assertEqual(by_code["0201001"].data_wd, by_code["020100101"].data_wd)
        self.assertNotEqual(by_code["0201001"].id, by_code["020100101"].id)
        self.assertEqual(by_code["020100101"].data_wd, by_code["020100102"].data_wd)
        self.assertEqual(by_code["020100101"].pop_latest + by_code["020100102"].pop_latest, by_code["0201001"].pop_latest)

    def test_existing_strategy_still_matches_the_captured_output_on_identical_pages(self):
        from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
        from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
        from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
        from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
        fixture = frozen_fixture()
        config = parse_scraping_job_config("spain", fixture["traditional_content"])
        pages = {p.path: p for p in config.pages}
        sevilla = pages["spain/localities/sevilla"]
        pages["spain/localities/seville"] = replace(sevilla, path="spain/localities/seville")
        config = replace(config, pages=[pages[path] for path in fixture["selected_paths"]])

        class Repository:
            def save_many(self, country, entities):
                self.entities = entities
                return len(entities), 0

            def delete_missing(self, country, ids):
                return 0

        repo = Repository()
        with patch.object(CityPopulationClient, "get", side_effect=fixture["pages"].__getitem__):
            ScrapeAdminAreas(repo, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()]).run(config)
        self.assertEqual(compare_entities(baseline_entities(), repo.entities), [])
        self.assertEqual(compare_entities(repo.entities, self.compile(self.proposal())), [])

    def test_schema_coverage_evidence_parents_roles_and_metrics_are_checked(self):
        cases = []
        for field, value in (("country", "foreign"), ("schema_version", True), ("evidence_version", "stale")):
            p = self.proposal(); p[field] = value; cases.append(p)
        for field, value in (("level", 2), ("parent", "ref_010100101"), ("role", "locality"),
                             ("evidence_ids", ["missing"]), ("reference_ids", ["unknown"]), ("reason", "")):
            p = self.proposal(); p["nodes"][0][field] = value; cases.append(p)
        p = self.proposal(); p["nodes"][0]["population"] = 42; cases.append(p)
        p = self.proposal(); p["nodes"].pop(); cases.append(p)
        p = self.proposal(); p["nodes"].append(p["nodes"][0]); cases.append(p)
        p = self.proposal(); p["unresolved"] = [{"id": "ref_01", "reason": "ambiguous branch"}]; cases.append(p)
        for index, p in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ReconciliationError):
                self.compile(p)

    def test_a_source_row_cannot_be_silently_omitted_or_substituted(self):
        p = self.proposal()
        node = next(n for n in p["nodes"] if n["reference_id"] == "ref_03")
        node["evidence_ids"] = node["evidence_ids"][:1]
        with self.assertRaisesRegex(ReconciliationError, "unaccounted"):
            self.compile(p)
        p = self.proposal(); p["nodes"][0]["evidence_ids"] = p["nodes"][-1]["evidence_ids"]
        with self.assertRaisesRegex(ReconciliationError, "name/QID"):
            self.compile(p)

    def test_reference_repetitions_require_a_sourced_consecutive_exception(self):
        r = deepcopy(self.reference); r["exceptions"] = []
        with self.assertRaisesRegex(ReconciliationError, "repetition"):
            parse_reference(r)
        r = deepcopy(self.reference); r["entities"][1]["parent"] = r["entities"][1]["id"]
        with self.assertRaisesRegex(ReconciliationError, "parent"):
            parse_reference(r)
        p = self.proposal()
        node = next(n for n in p["nodes"] if n["reference_id"] == "ref_03__spain_ceuta__repeat2")
        node["reference_ids"] = [node["reference_id"]]
        with self.assertRaisesRegex(ReconciliationError, "exception citation"):
            self.compile(p)

    def test_source_parent_disagreement_is_detected_across_pages(self):
        evidence = deepcopy(self.evidence)
        for row in evidence["rows"]:
            if row["entity"]["code"] == "020100101":
                row["entity"]["parent_code"] = "0102001"
        version = content_hash(evidence)
        with self.assertRaisesRegex(ReconciliationError, "cross-page observed parent"):
            compile_proposal(self.reference, evidence, valid_proposal(self.reference, evidence, version), version)

    def test_only_reviewed_identity_exclusions_can_account_for_nonterritorial_rows(self):
        reference = deepcopy(self.reference)
        evidence = deepcopy(self.evidence)
        extra = deepcopy(evidence["rows"][0])
        extra["id"] = "synthetic:aggregate"
        extra["entity"].update(code="aggregate", name="Nonterritorial aggregate", parent_code=None)
        evidence["rows"].append(extra)
        version = content_hash(evidence)
        plan = valid_proposal(reference, evidence, version)
        with self.assertRaisesRegex(ReconciliationError, "unaccounted"):
            compile_proposal(reference, evidence, plan, version)
        reference["exclusions"] = [{"id": "nonterritorial", "source_codes": ["aggregate"],
            "names": ["Nonterritorial aggregate"], "reason": "Declared nonterritorial source aggregate",
            "provenance": "Synthetic independent reference test rule"}]
        plan["excluded"] = [{"evidence_id": extra["id"], "reference_id": "nonterritorial", "reason": "Exact reviewed aggregate identity"}]
        self.assertEqual(compare_entities(baseline_entities(), compile_proposal(reference, evidence, plan, version)), [])
        plan["excluded"][0]["evidence_id"] = evidence["rows"][0]["id"]
        with self.assertRaisesRegex(ReconciliationError, "also mapped"):
            compile_proposal(reference, evidence, plan, version)

    def test_same_qid_does_not_merge_distinct_source_settlement_parts(self):
        ev = deepcopy(self.evidence)
        for row in ev["rows"]:
            if row["entity"]["code"] == "020100102":
                row["entity"]["data_wd"] = "Q2807"
        version = content_hash(ev)
        plan = valid_proposal(self.reference, ev, version)
        result = validate_plan(parse_plan(plan), parse_reference(self.reference), evidence_rows(ev), version)
        selected = [e for e in result if e.data_wd == "Q2807" and e.level == 4]
        self.assertEqual({e.code for e in selected}, {"020100101", "020100102"})
        bad = deepcopy(plan)
        first = next(n for n in bad["nodes"] if n["reference_id"] == "ref_020100101")
        second = next(n for n in bad["nodes"] if n["reference_id"] == "ref_020100102")
        first["evidence_ids"] += second["evidence_ids"]
        with self.assertRaises(ReconciliationError):
            validate_plan(parse_plan(bad), parse_reference(self.reference), evidence_rows(ev), version)

    def test_controlled_tools_retry_limits_and_cancellation(self):
        calls = []
        valid = self.proposal()

        class ToolProvider:
            model = "fake"

            def complete_json(self, **kwargs):
                calls.append(deepcopy(kwargs["payload"]))
                if len(calls) == 1:
                    return {"tool": "reference", "ids": ["ref_spain"]}
                return valid

        actual = propose(ToolProvider(), self.reference, self.evidence, self.version)
        self.assertEqual(actual, valid)
        self.assertEqual(calls[1]["tool_results"][0]["result"][0]["id"], "ref_spain")
        for response in ({"tool": "sql", "ids": ["DROP TABLE"]}, {"tool": "evidence", "ids": ["https://evil.invalid/"]}):
            with self.assertRaisesRegex(ReconciliationError, "tool"):
                propose(SimulatedProvider(response), self.reference, self.evidence, self.version)
        with self.assertRaisesRegex(ReconciliationError, "call limit"):
            propose(SimulatedProvider({"tool": "reference", "ids": ["ref_spain"]}), self.reference, self.evidence, self.version, max_calls=2)
        with self.assertRaisesRegex(ReconciliationError, "consumption"):
            propose(SimulatedProvider(), self.reference, self.evidence, self.version, max_payload_chars=20)
        with self.assertRaisesRegex(ReconciliationError, "time budget"):
            propose(SimulatedProvider(), self.reference, self.evidence, self.version, deadline_seconds=0)
        with self.assertRaises(ReconciliationCancelled):
            propose(SimulatedProvider(), self.reference, self.evidence, self.version,
                checkpoint=lambda: (_ for _ in ()).throw(ReconciliationCancelled("cancelled")))

    def test_comparator_identifies_entity_and_cause(self):
        rows = baseline_entities()
        changed = [replace(e, parent_code="bad", pop_latest=999) if e.code == "020100101" else e for e in rows]
        differences = compare_entities(rows, changed)
        self.assertEqual({(d["id"], d["cause"]) for d in differences}, {
            ("spain_020100101", "parent_code"), ("spain_020100101", "pop_latest")})
        self.assertEqual(compare_entities(rows, rows[:-1])[-1]["cause"], "missing")

    def test_frozen_observations_cannot_be_changed_independently_of_html(self):
        ev = deepcopy(self.evidence); ev["rows"][0]["entity"]["pop_latest"] = 999
        with self.assertRaisesRegex(ReconciliationError, "observations differ"):
            verify_evidence(ev)

    def test_discovery_is_bounded_same_country_and_does_not_use_a_recipe(self):
        reference = parse_reference(self.reference)
        page = next(iter(frozen_fixture()["pages"].values()))
        root = "https://www.citypopulation.de/en/spain/"
        html = page + '<a href="admin/">Admin</a><a href="https://evil.invalid/">ignore</a><a href="/en/france/">ignore</a>'
        seen = []
        def fetch(url):
            seen.append(url)
            return html if url == root else page
        ev = ControlledTerritorialDiscovery(fetch=fetch).discover(reference)
        self.assertEqual(seen, [root, root + "admin/"])
        self.assertEqual(len(ev["pages"]), 2)
        with self.assertRaisesRegex(ReconciliationError, "limit"):
            ControlledTerritorialDiscovery(fetch=fetch, max_pages=1).discover(reference)
        for url in ("http://www.citypopulation.de/en/spain/", root + "../france/", root + "%2e%2e/france/", root + "?url=evil", "https://127.0.0.1/en/spain/"):
            with self.subTest(url=url), self.assertRaises(ReconciliationError):
                controlled_url(url, "spain/")


class SqlReconciliationTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(BASE_DIR=Path(self.directory.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.store = DjangoTerritorialStore()
        self.reference = self.store.import_reference(reference_document(), reviewed_by="offline test reviewer")
        self.evidence = self.store.import_evidence(evidence_document())

    def run_record(self):
        return self.store.create_run("spain", evidence_id=self.evidence.pk)

    def test_propose_review_apply_idempotency_and_no_missing_row_deletion(self):
        configs_before = list(ScrapingConfig.objects.order_by("pk").values())
        old = AdminArea.objects.create(id="spain_unobserved", country_code="spain", code="unobserved", name="Keep existing", level=4)
        run = self.run_record()
        run = execute_run(run.pk, provider=SimulatedProvider())
        self.assertEqual(run.status, "validated")
        self.assertEqual(AdminArea.objects.count(), 1)
        self.assertIsNone(run.metrics["usage"])
        before = self.evidence.version
        first = self.store.apply(run.pk, expected_plan_hash=run.plan_hash)
        self.assertEqual(self.store.apply(run.pk, expected_plan_hash=run.plan_hash), first)
        self.assertTrue(AdminArea.objects.filter(pk=old.pk).exists())
        self.assertEqual(first["deleted"], 0)
        self.assertEqual(self.evidence.version, before)
        self.assertEqual(list(ScrapingConfig.objects.order_by("pk").values()), configs_before)
        run.refresh_from_db()
        self.assertEqual(run.approved_plan, run.plan)
        self.assertEqual(AdminArea.objects.get(pk="spain_020100101").parent_id, "spain_0201001")

    def test_invalid_proposal_is_retained_with_diagnostics_and_no_area_writes(self):
        provider = SimulatedProvider({"schema_version": 999})
        run = self.run_record()
        with self.assertRaises(ReconciliationError):
            execute_run(run.pk, provider=provider)
        run.refresh_from_db()
        self.assertEqual(run.status, "invalid")
        self.assertEqual(run.plan, {"schema_version": 999})
        self.assertTrue(run.diagnostics)
        self.assertEqual(AdminArea.objects.count(), 0)

    def test_provider_is_outside_transactions_and_can_retry_once(self):
        from django.db import connection
        provider = SimulatedProvider()
        original = provider.complete_json
        calls = []
        def complete(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            calls.append(1)
            if len(calls) == 1:
                raise AiProviderResponseError("temporary")
            return original(**kwargs)
        with patch.object(provider, "complete_json", side_effect=complete):
            run = execute_run(self.run_record().pk, provider=provider)
        self.assertEqual(run.status, "validated")
        self.assertEqual(len(calls), 2)
        self.assertEqual(run.metrics["provider_calls"], 2)

    def test_reference_versions_are_immutable(self):
        changed = reference_document(); changed["scope"] = "changed"
        with self.assertRaisesRegex(ReconciliationError, "immutable"):
            self.store.import_reference(changed, reviewed_by="someone")

    def test_stale_area_config_evidence_and_reference_inputs_block_application(self):
        mutations = [
            lambda: AdminArea.objects.create(id="spain_new", country_code="spain", code="new", name="Concurrent", level=0),
            lambda: ScrapingConfig.objects.create(slug="spain", country_code="spain", content="changed", content_hash="uncached"),
            lambda: TerritorialEvidence.objects.filter(pk=self.evidence.pk).update(content={}),
            lambda: self.reference.__class__.objects.filter(pk=self.reference.pk).update(reviewed_by="changed"),
        ]
        for mutation in mutations:
            run = execute_run(self.run_record().pk, provider=SimulatedProvider())
            mutation()
            with self.assertRaisesRegex(ReconciliationError, "changed"):
                self.store.apply(run.pk)
            AdminArea.objects.all().delete()
            ScrapingConfig.objects.all().delete()
            TerritorialEvidence.objects.filter(pk=self.evidence.pk).update(content=evidence_document())
            self.reference.__class__.objects.filter(pk=self.reference.pk).update(reviewed_by="offline test reviewer")

    def test_apply_rolls_back_even_when_cancelled_after_repository_save(self):
        run = execute_run(self.run_record().pk, provider=SimulatedProvider())
        calls = []
        def checkpoint():
            calls.append(1)
            if len(calls) == 3:
                raise ReconciliationCancelled("cancel after save")
        with self.assertRaises(ReconciliationCancelled):
            self.store.apply(run.pk, checkpoint=checkpoint)
        self.assertEqual(AdminArea.objects.count(), 0)
        run.refresh_from_db()
        self.assertEqual(run.status, "validated")

    def test_existing_ids_metrics_assets_and_merge_flags_survive(self):
        DjangoAdminAreaRepository().save_many("spain", baseline_entities())
        existing = AdminArea.objects.get(pk="spain_0201001")
        existing.city_merge_status = 2
        existing.annotations = "existing merge annotation"
        existing.representatives = 3
        existing.save()
        existing.capitals.add(AdminArea.objects.get(pk="spain_020100101"))
        now = timezone.now()
        asset = VisualAsset.objects.create(entity_type="admin_area", entity_key=existing.pk, kind="flag",
            country_code="spain", created_at=now, updated_at=now)
        translation = DynamicTranslation.objects.create(subject_type="admin_area", subject_key=existing.pk,
            country_code="spain", field="name", language="sr", text="Existing translation")
        root = AdminArea.objects.get(pk="spain_spain")
        # An absent source branch must remain available to derived calculations.
        remote = AdminArea.objects.create(id="spain_unobserved", country_code="spain", code="unobserved",
            name="Unobserved branch", parent=root, level=4, pop_latest=2000)
        root.most_populate_city = remote
        root.save()
        run = execute_run(self.run_record().pk, provider=SimulatedProvider())
        self.assertEqual(run.comparison[0]["cause"], "city_merge_status")
        self.store.apply(run.pk)
        existing.refresh_from_db()
        self.assertEqual((existing.pk, existing.city_merge_status, existing.representatives, existing.annotations),
            ("spain_0201001", 2, 3, "existing merge annotation"))
        self.assertEqual(list(existing.capitals.values_list("id", flat=True)), ["spain_020100101"])
        self.assertTrue(VisualAsset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(DynamicTranslation.objects.get(pk=translation.pk).text, "Existing translation")
        root.refresh_from_db()
        self.assertEqual(root.most_populate_city_id, remote.pk)

    def test_mutated_plan_is_not_the_approved_plan(self):
        run = execute_run(self.run_record().pk, provider=SimulatedProvider())
        old_hash = run.plan_hash
        changed = deepcopy(run.plan); changed["nodes"][0]["reason"] = "changed after review"
        TerritorialRun.objects.filter(pk=run.pk).update(plan=changed, plan_hash=content_hash(changed))
        with self.assertRaisesRegex(ReconciliationError, "approved"):
            self.store.apply(run.pk, expected_plan_hash=old_hash)

    def test_cancellation_before_proposal_preserves_the_current_data(self):
        run = self.run_record()
        provider = SimulatedProvider()
        def checkpoint():
            raise ReconciliationCancelled("cancel")
        with self.assertRaises(ReconciliationCancelled):
            execute_run(run.pk, provider=provider, checkpoint=checkpoint)
        run.refresh_from_db()
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(AdminArea.objects.count(), 0)

    def test_missing_reference_has_an_actionable_error(self):
        with self.assertRaisesRegex(ReconciliationError, "reviewed SQL catalog"):
            self.store.create_run("unknown", evidence_id=self.evidence.pk)

    def test_cli_import_propose_review_and_apply_use_sql_and_do_not_call_provider_on_review(self):
        from ciudades_del_mundo.infrastructure.ai.json_provider import OpenAICompatibleJsonProvider
        reference_path = Path(self.directory.name) / "reference.json"
        pages_path = Path(self.directory.name) / "pages.json"
        reference_path.write_text(json.dumps(reference_document()), encoding="utf-8")
        pages_path.write_text(json.dumps(frozen_fixture()["pages"]), encoding="utf-8")
        call_command("import_territorial_reference", str(reference_path), "--reviewed-by", "CLI reviewer", stdout=StringIO())
        call_command("import_territorial_evidence", "spain", str(pages_path), stdout=StringIO())
        with patch.object(OpenAICompatibleJsonProvider, "from_env", return_value=SimulatedProvider()):
            call_command("reconcile_territory", "spain", "--new", "--evidence-id", str(self.evidence.pk), stdout=StringIO())
        run = TerritorialRun.objects.latest("created_at")
        output = StringIO()
        with patch.object(OpenAICompatibleJsonProvider, "from_env", side_effect=AssertionError("Unexpected provider call")):
            call_command("reconcile_territory", run.pk, "--show", stdout=output)
            self.assertIn('"expected"', output.getvalue())
            self.assertIn('"observed"', output.getvalue())
            call_command("reconcile_territory", run.pk, "--apply", "--plan-hash", run.plan_hash, stdout=StringIO())
        run.refresh_from_db()
        self.assertEqual(run.status, "applied")

    def test_interrupted_task_keeps_auditable_run_and_can_reuse_its_evidence_in_a_new_run(self):
        task = WebTask.objects.create(id="interrupted", key="territorial:spain", label="test", created_at=timezone.now(), status="failed")
        run = self.run_record()
        run.task = task
        run.status = "running"
        run.save()
        self.assertEqual(effective_status(run), "interrupted")
        with self.assertRaises(ReconciliationCancelled):
            task_checkpoint(task.pk)
        retry = execute_run(self.run_record().pk, provider=SimulatedProvider())
        self.assertEqual(retry.evidence_id, run.evidence_id)
        self.assertEqual(retry.status, "validated")

    def test_concurrent_inputs_during_provider_call_invalidate_the_run(self):
        provider = SimulatedProvider()
        original = provider.complete_json
        def complete(**kwargs):
            AdminArea.objects.create(id="spain_concurrent", country_code="spain", code="concurrent", name="New", level=0)
            return original(**kwargs)
        with patch.object(provider, "complete_json", side_effect=complete), self.assertRaisesRegex(ReconciliationError, "inputs changed"):
            execute_run(self.run_record().pk, provider=provider)
        self.assertEqual(list(AdminArea.objects.values_list("pk", flat=True)), ["spain_concurrent"])

    def test_real_probe_requires_both_explicit_opt_ins_before_any_provider_call(self):
        with patch.dict(os.environ, {"CIUDADES_TERRITORIAL_REAL_TEST": "0"}), self.assertRaisesRegex(CommandError, "requires"):
            call_command("probe_territorial_provider", "--allow-paid", stdout=StringIO())
        with patch.dict(os.environ, {"CIUDADES_TERRITORIAL_REAL_TEST": "1"}), self.assertRaisesRegex(CommandError, "requires"):
            call_command("probe_territorial_provider", stdout=StringIO())


class ProviderConsumptionTests(SimpleTestCase):
    def test_real_reported_usage_and_output_limit_are_exposed_without_cost_estimates(self):
        from ciudades_del_mundo.infrastructure.ai.json_provider import OpenAICompatibleJsonProvider
        provider = OpenAICompatibleJsonProvider(api_key="test", model="fake", max_completion_tokens=123)
        response = {"choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}}
        with patch("ciudades_del_mundo.infrastructure.ai.json_provider.urlopen", return_value=BytesIO(json.dumps(response).encode())) as opener:
            provider.complete_json(system_prompt="test", payload={})
        self.assertEqual(json.loads(opener.call_args.args[0].data)["max_completion_tokens"], 123)
        self.assertEqual(provider.usage_history, [response["usage"]])
