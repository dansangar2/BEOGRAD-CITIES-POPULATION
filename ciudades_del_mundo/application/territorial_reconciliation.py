"""Deterministic territorial plan compilation and entity comparison."""
from dataclasses import replace
from decimal import Decimal

from ciudades_del_mundo.domain.admin_area import ScrapedAdminArea
from ciudades_del_mundo.domain.most_populated import AdminAreaSummary, calculate_most_populated_assignments
from ciudades_del_mundo.domain.territorial_reconciliation import (
    EvidenceRow, ReconciliationError, parse_plan, parse_reference, validate_plan,
)
from ciudades_del_mundo.ports.territorial_reconciliation import TerritorialProposalProvider


def compile_proposal(reference_data: dict, evidence: dict, plan_data: dict, version: str) -> list[ScrapedAdminArea]:
    entities = validate_plan(parse_plan(plan_data), parse_reference(reference_data), evidence_rows(evidence), version)
    summaries = [AdminAreaSummary(e.id, e.level, f"{e.country_code}_{e.parent_code}" if e.parent_code else None,
                                 e.pop_latest, e.city_merge_status) for e in entities]
    assignments = {a.area_id: a.most_populated_id for a in calculate_most_populated_assignments(summaries)}
    codes = {e.id: e.code for e in entities}
    return [replace(e, most_populated_city_code=codes.get(assignments.get(e.id))) for e in entities]


def evidence_rows(document: dict) -> tuple[EvidenceRow, ...]:
    try:
        return tuple(EvidenceRow(r["id"], r["page"], r["block"], ScrapedAdminArea(**r["entity"]))
                     for r in document["rows"])
    except (KeyError, TypeError) as exc:
        raise ReconciliationError(f"evidence: invalid source row: {exc}") from exc


def propose(provider: TerritorialProposalProvider, reference_data: dict, evidence: dict,
            evidence_version: str, *, checkpoint=lambda: None, max_calls: int = 6,
            max_payload_chars: int = 500_000, deadline_seconds: int = 300,
            on_response=lambda response: None, on_call=lambda: None) -> dict:
    """Compatibility entrypoint; AI orchestration lives in application.ai."""
    from ciudades_del_mundo.application.ai.territorial import propose as propose_with_ai
    return propose_with_ai(provider, reference_data, evidence, evidence_version,
        checkpoint=checkpoint, max_calls=max_calls, max_payload_chars=max_payload_chars,
        deadline_seconds=deadline_seconds, on_response=on_response, on_call=on_call)


COMPARISON_FIELDS = ("name", "level", "parent_code", "entity_type", "raw_entity_type", "pop_latest",
    "pop_latest_date", "last_census_year", "area_km2", "density", "data_wd", "city_merge_status",
    "most_populated_city_code")


def compare_entities(expected: list[ScrapedAdminArea], observed: list[ScrapedAdminArea]) -> list[dict]:
    """Entity-level differences, including metrics and repetitions; no count proxy."""
    left = {e.id: e for e in expected}
    right = {e.id: e for e in observed}
    if len(left) != len(expected) or len(right) != len(observed):
        raise ReconciliationError("comparison: duplicate persisted identity.")
    differences = []
    for key in sorted(set(left) | set(right)):
        if key not in left or key not in right:
            differences.append({"id": key, "cause": "extra" if key not in left else "missing"})
            continue
        for field in COMPARISON_FIELDS:
            a, b = getattr(left[key], field), getattr(right[key], field)
            if field in ("area_km2", "density"):
                a = Decimal(str(a)).normalize() if a is not None else None
                b = Decimal(str(b)).normalize() if b is not None else None
            # JSON snapshots serialize Decimal/date values, preserve semantics.
            if (None if a is None else str(a)) != (None if b is None else str(b)):
                differences.append({"id": key, "cause": field, "expected": a, "observed": b})
    return differences
