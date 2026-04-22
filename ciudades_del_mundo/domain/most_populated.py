from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdminAreaSummary:
    id: str
    level: int
    parent_id: str | None
    pop_latest: int | None
    most_populate_city_id: str | None = None


@dataclass(frozen=True)
class MostPopulatedAssignment:
    area_id: str
    most_populated_id: str


def calculate_most_populated_assignments(
    areas: list[AdminAreaSummary],
    legal_subdivision_level: int | None = None,
) -> list[MostPopulatedAssignment]:
    if not areas:
        return []

    max_level = max(area.level for area in areas)
    legal_level = legal_subdivision_level if legal_subdivision_level is not None else max_level
    by_parent: dict[str | None, list[AdminAreaSummary]] = {}
    for area in areas:
        by_parent.setdefault(area.parent_id, []).append(area)

    assignments = []
    for area in areas:
        target_level = legal_level if area.level < legal_level else max_level
        if target_level <= area.level:
            continue

        top = _most_populated_descendant(area, target_level, by_parent)
        if top and area.most_populate_city_id != top.id:
            assignments.append(MostPopulatedAssignment(area_id=area.id, most_populated_id=top.id))

    return assignments


def _most_populated_descendant(
    area: AdminAreaSummary,
    target_level: int,
    by_parent: dict[str | None, list[AdminAreaSummary]],
) -> AdminAreaSummary | None:
    stack = list(by_parent.get(area.id, []))
    candidates_by_level: dict[int, list[AdminAreaSummary]] = {}
    while stack:
        node = stack.pop()
        if node.level > area.level:
            candidates_by_level.setdefault(node.level, []).append(node)
        stack.extend(by_parent.get(node.id, []))

    if not candidates_by_level:
        return None

    candidate_level = _closest_available_level(candidates_by_level.keys(), target_level)
    return _most_populated(candidates_by_level[candidate_level])


def _closest_available_level(levels, target_level: int) -> int:
    return min(levels, key=lambda level: (abs(level - target_level), level > target_level, level))


def _most_populated(candidates: list[AdminAreaSummary]) -> AdminAreaSummary | None:
    return max(
        (candidate for candidate in candidates if candidate.pop_latest is not None),
        key=lambda candidate: candidate.pop_latest,
        default=None,
    )
