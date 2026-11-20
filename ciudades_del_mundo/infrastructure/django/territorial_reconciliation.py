"""SQL inputs, input fingerprints and atomic, non-destructive plan application."""
from contextlib import nullcontext
from dataclasses import fields, replace
import json
import uuid

from django.db import transaction
from django.utils import timezone

from ciudades_del_mundo.application.citypopulation_linking import normalize_citypopulation_entities
from ciudades_del_mundo.application.territorial_reconciliation import compare_entities, compile_proposal
from ciudades_del_mundo.domain.admin_area import ScrapedAdminArea
from ciudades_del_mundo.domain.most_populated import calculate_most_populated_assignments
from ciudades_del_mundo.domain.territorial_reconciliation import (
    ENGINE_VERSION, ReconciliationError, parse_reference,
)
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import content_hash, verify_evidence
from ciudades_del_mundo.models import (
    AdminArea, ScrapingConfig, TerritorialReference, TerritorialEvidence, TerritorialRun,
)


def json_data(value):
    return json.loads(json.dumps(value, default=str))


class DjangoTerritorialStore:
    def save_run(self, run, update_fields):
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            run.save(update_fields=update_fields)

    def claim_run(self, run_id):
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            return bool(TerritorialRun.objects.filter(pk=run_id, status="pending").update(status="running"))

    def reference(self, country: str) -> dict:
        record = TerritorialReference.objects.filter(country=country).order_by("-pk").first()
        if record is None:
            raise ReconciliationError(f"reference: no reviewed SQL catalog for {country}. Import one explicitly.")
        return record.content

    def import_reference(self, document: dict, *, reviewed_by: str):
        reference = parse_reference(document)
        if not reviewed_by.strip() or len(reviewed_by) > 255:
            raise ReconciliationError("reference: a named human reviewer is required.")
        digest = content_hash(document)
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            record, created = TerritorialReference.objects.get_or_create(country=reference.country, version=reference.version,
                defaults={"content": document, "content_hash": digest, "reviewed_by": reviewed_by})
            if not created and (record.content_hash != digest or content_hash(record.content) != digest):
                raise ReconciliationError("reference: immutable version already exists with different content; use a new version.")
        return record

    def import_evidence(self, document: dict):
        verify_evidence(document)
        digest = content_hash(document)
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            record, _ = TerritorialEvidence.objects.get_or_create(country=document["country"], version=digest,
                defaults={"content": document})
            if content_hash(record.content) != digest:
                raise ReconciliationError("evidence: stored immutable version has been altered.")
        return record

    def create_run(self, country: str, *, evidence_id=None, discover=False):
        reference = TerritorialReference.objects.filter(country=country).order_by("-pk").first()
        if reference is None:
            raise ReconciliationError(f"reference: missing reviewed SQL catalog for {country}.")
        parse_reference(reference.content)
        evidence = None
        if not discover:
            evidence = TerritorialEvidence.objects.filter(country=country, pk=evidence_id).first()
            if evidence is None:
                raise ReconciliationError("evidence: select a frozen SQL version or explicitly request discovery.")
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            return TerritorialRun.objects.create(id=uuid.uuid4().hex, country=country, reference=reference, evidence=evidence)

    def snapshot(self, run) -> dict:
        """Fingerprint actual values, not cached content_hash columns."""
        reference = TerritorialReference.objects.get(pk=run.reference_id)
        evidence = TerritorialEvidence.objects.get(pk=run.evidence_id)
        latest_reference = TerritorialReference.objects.filter(country=run.country).order_by("-pk").values_list("pk", flat=True).first()
        latest_evidence = TerritorialEvidence.objects.filter(country=run.country).order_by("-pk").values_list("pk", flat=True).first()
        rows = list(AdminArea.objects.filter(country_code=run.country).order_by("id").values())
        # M2M links are preserved by save_many; including them detects concurrent
        # edits even if a caller used bulk SQL without updating timestamps.
        capitals = list(AdminArea.capitals.through.objects.filter(from_adminarea__country_code=run.country)
                        .order_by("pk").values())
        configs = list(ScrapingConfig.objects.filter(country_code=run.country).order_by("slug").values())
        return json_data({"engine": ENGINE_VERSION, "reference_id": reference.pk,
            "reference_hash": content_hash(reference.content), "reference_version": reference.version,
            "reviewed_by": reference.reviewed_by, "evidence_id": evidence.pk,
            "evidence_hash": content_hash(evidence.content), "evidence_version": evidence.version,
            "latest_reference": latest_reference, "latest_evidence": latest_evidence,
            "configs": configs, "areas": rows, "capitals": capitals})

    def verify_inputs(self, run):
        reference = parse_reference(run.reference.content)
        if reference.country != run.country or run.reference.country != run.country or run.evidence.country != run.country:
            raise ReconciliationError("run: foreign country reference/evidence.")
        if reference.version != run.reference.version:
            raise ReconciliationError("reference: stored version differs from the reference document.")
        if content_hash(run.reference.content) != run.reference.content_hash:
            raise ReconciliationError("reference: stored reference hash differs from content.")
        verify_evidence(run.evidence.content)
        if content_hash(run.evidence.content) != run.evidence.version:
            raise ReconciliationError("evidence: stored evidence version differs from content.")
        return reference

    def compiled(self, run) -> list[ScrapedAdminArea]:
        self.verify_inputs(run)
        entities = compile_proposal(run.reference.content, run.evidence.content, run.plan, run.evidence.version)
        linked = normalize_citypopulation_entities(run.country, entities)
        # The existing linker can validate compatibility, but must never silently
        # undo explicit approved roles, identities or relations.
        differences = compare_entities(entities, linked)
        if differences:
            raise ReconciliationError(f"linker: approved plan is not stable: {json_data(differences)}")
        return entities

    def baseline(self, inputs: dict) -> list[ScrapedAdminArea]:
        allowed = {f.name for f in fields(ScrapedAdminArea)}
        rows = []
        prefix = None
        for data in inputs["areas"]:
            prefix = data["country_code"] + "_"
            values = {key: value for key, value in data.items() if key in allowed}
            parent = data["parent_id"]
            values["parent_code"] = parent[len(prefix):] if parent and parent.startswith(prefix) else parent
            most = data["most_populate_city_id"]
            values["most_populated_city_code"] = most[len(prefix):] if most and most.startswith(prefix) else most
            rows.append(ScrapedAdminArea(**values))
        return rows

    def apply(self, run_id: str, *, checkpoint=lambda: None, expected_plan_hash=None) -> dict:
        checkpoint()
        with sqlite_write_lock_if_needed() or nullcontext(), transaction.atomic():
            run = TerritorialRun.objects.select_for_update().select_related("reference", "evidence").get(pk=run_id)
            if expected_plan_hash is not None and expected_plan_hash != run.plan_hash:
                raise ReconciliationError("apply: the plan differs from the version approved in the review form.")
            if run.status == "applied":
                return run.result
            if run.status != "validated" or not run.plan_hash or content_hash(run.plan) != run.plan_hash:
                raise ReconciliationError("apply: proposal is not validated or its reviewed plan changed.")
            if content_hash(self.snapshot(run)) != run.inputs_hash:
                raise ReconciliationError("apply: reference, evidence, traditional config or current areas changed; create a new proposal.")
            entities = self.compiled(run)
            existing = {e.code: e for e in AdminArea.objects.filter(country_code=run.country)}
            preserved = []
            for entity in entities:
                old = existing.get(entity.code)
                if old:
                    if old.pk != entity.id:
                        raise ReconciliationError(f"apply {old.pk}: noncanonical existing ID; refusing to replace it.")
                    # Relationship edits on merge participants need the existing
                    # merge use case, which this strategy does not replace.
                    parent_id = f"{run.country}_{entity.parent_code}" if entity.parent_code else None
                    if old.city_merge_status and (old.level != entity.level or old.parent_id != parent_id):
                        raise ReconciliationError(f"apply {old.pk}: would change a persisted merge relationship.")
                    updates = {name: getattr(old, name) for name in (
                        "area_km2", "density", "pop_latest", "pop_latest_date", "last_census_year", "url", "data_wd")
                        if getattr(entity, name) in (None, "")}
                    entity = replace(entity, city_merge_status=old.city_merge_status, annotations=old.annotations, **updates)
                preserved.append(entity)
            checkpoint()
            repository = DjangoAdminAreaRepository()
            created, updated = repository.save_many(run.country, preserved)
            # Include retained unobserved branches in existing deterministic
            # derived rules. A scoped proposal must not clear their links.
            assignments = calculate_most_populated_assignments(repository.list_summaries(run.country))
            most_updated = repository.save_most_populated_assignments(assignments)
            checkpoint()  # cancellation here rolls back all repository writes
            run.approved_plan = run.plan
            run.status = "applied"
            run.applied_at = timezone.now()
            run.result = {"created": created, "updated": updated, "deleted": 0, "found": len(preserved),
                          "most_populated_updated": most_updated}
            run.save(update_fields=["approved_plan", "status", "applied_at", "result", "updated_at"])
            return run.result
