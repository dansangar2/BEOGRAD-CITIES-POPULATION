"""Helpers for reconstructing parent-child hierarchy from scraped rows."""

from __future__ import annotations

from dataclasses import replace

from ciudades_del_mundo.domain.admin_area import ScrapedAdminArea


def assign_parent_codes_by_level(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    stack: dict[int, ScrapedAdminArea] = {}
    assigned: list[ScrapedAdminArea] = []

    for entity in entities:
        parent = stack.get(entity.level - 1) if entity.level > 0 else None
        parent_code = entity.parent_code or (parent.code if parent else None)
        updated = replace(entity, parent_code=parent_code)

        stack[updated.level] = updated
        for level in [level for level in stack if level > updated.level]:
            del stack[level]

        assigned.append(updated)

    return assigned
