"""The production local expert system, not a simulated language provider."""
from copy import deepcopy

from django.test import SimpleTestCase

from ciudades_del_mundo.application.ai.local_territorial import LocalTerritorialEngine
from ciudades_del_mundo.application.ai.territorial import propose
from ciudades_del_mundo.application.territorial_reconciliation import compile_proposal, compare_entities
from ciudades_del_mundo.domain.territorial_reconciliation import ReconciliationError
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import content_hash
from ciudades_del_mundo.tests.territorial_support import baseline_entities, evidence_document, reference_document


class LocalTerritorialTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reference = reference_document()
        cls.evidence = evidence_document()

    def infer(self, reference=None, evidence=None):
        reference = self.reference if reference is None else reference
        evidence = self.evidence if evidence is None else evidence
        return LocalTerritorialEngine().complete_json(system_prompt="", payload={
            "reference": reference, "evidence": evidence, "evidence_version": content_hash(evidence)})

    def test_local_inference_matches_all_frozen_branch_entities_and_metrics(self):
        plan = self.infer()
        self.assertEqual(plan["unresolved"], [])
        actual = compile_proposal(self.reference, self.evidence, plan, content_hash(self.evidence))
        self.assertEqual(compare_entities(baseline_entities(), actual), [])
        self.assertEqual({n["reference_id"] for n in plan["nodes"]}, {e["id"] for e in self.reference["entities"]})

    def test_input_order_does_not_change_correspondences_or_metric_selection(self):
        reference, evidence = deepcopy(self.reference), deepcopy(self.evidence)
        reference["entities"].reverse()
        evidence["rows"].reverse()
        first, reversed_input = self.infer(), self.infer(reference, evidence)
        self.assertEqual(first["nodes"], reversed_input["nodes"])

    def test_missing_identity_is_diagnosed_and_not_invented(self):
        evidence = deepcopy(self.evidence)
        evidence["rows"] = [r for r in evidence["rows"] if r["entity"]["code"] != "020100102"]
        plan = self.infer(evidence=evidence)
        self.assertTrue(any(p["id"] == "ref_020100102" for p in plan["unresolved"]))
        self.assertFalse(any(n["reference_id"] == "ref_020100102" for n in plan["nodes"]))
        with self.assertRaises(ReconciliationError):
            compile_proposal(self.reference, evidence, plan, content_hash(evidence))

    def test_unknown_source_row_cannot_be_excluded_by_the_engine(self):
        evidence = deepcopy(self.evidence)
        row = deepcopy(evidence["rows"][0])
        row["id"] = "unknown-observation"
        row["entity"].update(code="unknown", name="Unknown observed entity")
        evidence["rows"].append(row)
        self.assertTrue(any(p["id"] == row["id"] for p in self.infer(evidence=evidence)["unresolved"]))

    def test_context_with_two_possible_ancestors_remains_ambiguous(self):
        reference, evidence = deepcopy(self.reference), deepcopy(self.evidence)
        for entity in reference["entities"]:
            if entity["id"] in ("ref_01", "ref_02"):
                entity["names"].append("Shared context name")
        row = deepcopy(evidence["rows"][0])
        row.update(id="ambiguous-context", page="separate-page", block="CityPopulation section: infosection")
        row["entity"].update(code="spain", name="Shared context name", level=0, data_wd="", parent_code=None)
        evidence["rows"].append(row)
        plan = self.infer(reference, evidence)
        self.assertTrue(any(p["id"] == row["id"] and "no unique" in p["reason"] for p in plan["unresolved"]))
        self.assertFalse(any(row["id"] in n["evidence_ids"] for n in plan["nodes"]))

    def test_latest_observed_metric_is_selected_without_generating_values(self):
        evidence = deepcopy(self.evidence)
        row = deepcopy(next(r for r in evidence["rows"] if r["entity"]["code"] == "0201001"))
        row.update(id="new-census-observation", page="new-census-page")
        row["entity"].update(pop_latest=99, pop_latest_date="2025-01-01", last_census_year=2025)
        evidence["rows"].append(row)
        plan = self.infer(evidence=evidence)
        node = next(n for n in plan["nodes"] if n["reference_id"] == "ref_0201001")
        self.assertEqual(node["evidence_ids"][0], row["id"])
        actual = compile_proposal(self.reference, evidence, plan, content_hash(evidence))
        entity = next(e for e in actual if e.code == "0201001")
        self.assertEqual((entity.pop_latest, entity.pop_latest_date), (99, "2025-01-01"))

    def test_explicit_exclusion_accounts_for_an_extra_aggregate(self):
        reference, evidence = deepcopy(self.reference), deepcopy(self.evidence)
        row = deepcopy(evidence["rows"][0])
        row["id"] = "aggregate-observation"
        row["entity"].update(code="aggregate", name="Nonterritorial aggregate")
        evidence["rows"].append(row)
        reference["exclusions"] = [{"id": "aggregate-exclusion", "source_codes": ["aggregate"],
            "names": ["Nonterritorial aggregate"], "reason": "Explicit synthetic exclusion", "provenance": "Test reference"}]
        plan = self.infer(reference, evidence)
        self.assertEqual(plan["excluded"][0]["evidence_id"], row["id"])
        compile_proposal(reference, evidence, plan, content_hash(evidence))

    def test_reference_qid_disagreement_is_not_hidden_by_equal_names(self):
        reference = deepcopy(self.reference)
        entity = next(e for e in reference["entities"] if e["id"] == "ref_020100101")
        entity["qid"] = "Q999999999"
        self.assertTrue(self.infer(reference)["unresolved"])

    def test_reference_repetition_can_reuse_observed_parent_but_never_create_a_locality(self):
        evidence = deepcopy(self.evidence)
        code = "03__spain_ceuta__repeat2"
        evidence["rows"] = [r for r in evidence["rows"] if r["entity"]["code"] != code]
        plan = self.infer(evidence=evidence)
        repeated = next(n for n in plan["nodes"] if n["reference_id"] == "ref_" + code)
        original = next(n for n in plan["nodes"] if n["reference_id"] == "ref_03")
        self.assertEqual(repeated["evidence_ids"], original["evidence_ids"])
        self.assertIn("autonomous_city_repetition", repeated["reference_ids"])
        compile_proposal(self.reference, evidence, plan, content_hash(evidence))

    def test_local_engine_passes_the_same_proposal_boundary_as_external_models(self):
        response = propose(LocalTerritorialEngine(), self.reference, self.evidence, content_hash(self.evidence))
        self.assertEqual(response["unresolved"], [])
