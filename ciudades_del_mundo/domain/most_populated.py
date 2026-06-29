"""Domain logic to derive the most populated descendant for each area."""

from __future__ import annotations

from dataclasses import dataclass

from .admin_area import CITY_MERGE_NONE, CITY_MERGE_UNIFIED


@dataclass(frozen=True)
class AdminAreaSummary:
    id: str
    level: int
    parent_id: str | None
    pop_latest: int | None
    city_merge_status: int = CITY_MERGE_NONE
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

    # Kept in the signature for callers/config compatibility. Scraped
    # AdminArea selection now targets the highest available descendant level.
    _ = legal_subdivision_level
    max_level = max(area.level for area in areas)
    by_parent: dict[str | None, list[AdminAreaSummary]] = {}
    for area in areas:
        if area.city_merge_status in {CITY_MERGE_NONE, CITY_MERGE_UNIFIED}:
            by_parent.setdefault(area.parent_id, []).append(area)

    descendant_index = _descendant_index(areas, by_parent)

    assignments = []
    for area in areas:
        target_level = max_level
        if target_level <= area.level:
            continue

        top = _most_populated_descendant(area, target_level, descendant_index)
        if top and area.most_populate_city_id != top.id:
            assignments.append(MostPopulatedAssignment(area_id=area.id, most_populated_id=top.id))

    return assignments


@dataclass(frozen=True)
class _DescendantIndexEntry:
    levels: frozenset[int]
    top_by_level: dict[int, AdminAreaSummary]


def _descendant_index(
    areas: list[AdminAreaSummary],
    by_parent: dict[str | None, list[AdminAreaSummary]],
) -> dict[str, _DescendantIndexEntry]:
    """Return available descendant levels and top populated rows per level.

    The previous implementation traversed each area's full subtree separately.
    Large countries repeat the same work thousands of times. This bottom-up
    index keeps the same level-selection semantics while visiting each edge a
    bounded number of times.
    """
    result: dict[str, _DescendantIndexEntry] = {}
    for area in sorted(areas, key=lambda item: int(item.level), reverse=True):
        levels: set[int] = set()
        top_by_level: dict[int, AdminAreaSummary] = {}
        for child in reversed(by_parent.get(area.id, [])):
            if child.id == area.id:
                continue
            if int(child.level) > int(area.level):
                levels.add(int(child.level))
                _set_most_populated(top_by_level, int(child.level), child)
            child_entry = result.get(child.id)
            if child_entry is None:
                continue
            for level in child_entry.levels:
                if int(level) <= int(area.level):
                    continue
                levels.add(int(level))
                top = child_entry.top_by_level.get(int(level))
                if top is not None:
                    _set_most_populated(top_by_level, int(level), top)
        result[area.id] = _DescendantIndexEntry(frozenset(levels), top_by_level)
    return result


def _most_populated_descendant(
    area: AdminAreaSummary,
    target_level: int,
    descendant_index: dict[str, _DescendantIndexEntry],
) -> AdminAreaSummary | None:
    entry = descendant_index.get(area.id)
    if entry is None or not entry.levels:
        return None

    candidate_level = _closest_available_level(entry.levels, target_level)
    return entry.top_by_level.get(candidate_level)


def _closest_available_level(levels, target_level: int) -> int:
    return min(levels, key=lambda level: (abs(level - target_level), level > target_level, level))


def _set_most_populated(
    top_by_level: dict[int, AdminAreaSummary],
    level: int,
    candidate: AdminAreaSummary,
) -> None:
    if candidate.pop_latest is None:
        return
    current = top_by_level.get(level)
    if current is None or (candidate.pop_latest or 0) > (current.pop_latest or 0):
        top_by_level[level] = candidate
