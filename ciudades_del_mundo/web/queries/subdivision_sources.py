"""Resolve SQL source selections and group membership for subdivisions."""

from __future__ import annotations


from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

import json
import tomllib
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedSubdivision, SubdivisionGroup
from ciudades_del_mundo.services.nuevo_admin_builder import effective_source_municipal_level
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions


def _derived_subdivision_default_source_blocks(country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    return [{"country_code": country_code, "names": [], "sections": []}] if country_code else []


def _derived_subdivision_default_source_items(country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    return [{"country_code": country_code, "items": []}] if country_code else []


def _derived_subdivision_visual_source_items_from_content(
    content: str,
    table_name: str,
    *,
    fallback_country_code: str,
    group_entries_cache: dict[str, list[dict]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> list[dict]:
    data = _forms_subdivisions._derived_subdivision_toml_data(content)
    source_blocks = data.get(table_name) if isinstance(data.get(table_name), list) else []
    scoped_municipal_ids_by_country = (
        _derived_subdivision_include_municipal_scope_by_country(
            data,
            fallback_country_code=fallback_country_code,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        if table_name == "subtract"
        else {}
    )
    visual_by_country: dict[str, dict] = {}
    seen_ids: dict[str, set[str]] = {}
    for block in source_blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        raw_level = block.get("level")
        if raw_level in (None, ""):
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level < 0:
            continue
        refs = _queries_subdivision_capitals._derived_subdivision_block_source_refs(
            block,
            country_code=block_country,
            expand_groups=False,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        areas = _queries_subdivision_capitals._derived_subdivision_source_ref_areas(block_country, level, refs)
        country_block = visual_by_country.setdefault(block_country, {"country_code": block_country, "items": []})
        country_seen = seen_ids.setdefault(block_country, set())
        parent_by_id, _ = _derived_subdivision_group_parent_maps(areas)
        for area in sorted(areas, key=lambda item: (int(item.level or 0), _queries_browser._area_display_name(item), str(item.id))):
            area_id = str(area.id)
            if area_id in country_seen:
                continue
            country_seen.add(area_id)
            country_block["items"].append(
                _derived_subdivision_source_item_payload(
                    area,
                    ancestor_ids=_area_ancestor_ids_from_parent_map(area_id, parent_by_id),
                )
            )
        for group_key in _queries_subdivision_capitals._derived_subdivision_list_values(block.get("groups")):
            group_item = _derived_subdivision_group_source_item_for_key(
                block_country,
                group_key,
                level=level,
                scoped_municipal_ids=scoped_municipal_ids_by_country.get(block_country),
                entries_cache=group_entries_cache,
            )
            if not group_item:
                continue
            group_item_id = str(group_item.get("id") or group_item.get("value") or "").strip()
            if not group_item_id or group_item_id in country_seen:
                continue
            country_seen.add(group_item_id)
            country_block["items"].append(group_item)
        for subdivision_key in _queries_subdivision_capitals._derived_subdivision_list_values(block.get("derived_subdivisions")):
            subdivision_item = _derived_subdivision_record_source_item_for_key(
                block_country,
                subdivision_key,
                level=level,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
            if not subdivision_item:
                continue
            subdivision_item_id = str(subdivision_item.get("id") or subdivision_item.get("value") or "").strip()
            if not subdivision_item_id or subdivision_item_id in country_seen:
                continue
            country_seen.add(subdivision_item_id)
            country_block["items"].append(subdivision_item)
    return _derived_subdivision_source_items_with_default(
        list(visual_by_country.values()),
        fallback_country_code=fallback_country_code,
    )


def _derived_subdivision_include_municipal_scope_by_country(
    data: dict,
    *,
    fallback_country_code: str,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, set[str]]:
    include_blocks = data.get("include") if isinstance(data.get("include"), list) else []
    result: dict[str, set[str]] = {}
    for block in include_blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        raw_level = block.get("level")
        if raw_level in (None, ""):
            continue
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level < 0:
            continue
        refs = _queries_subdivision_capitals._derived_subdivision_block_source_refs(
            block,
            country_code=block_country,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            source_ids_cache=source_ids_cache,
        )
        areas = _queries_subdivision_capitals._derived_subdivision_source_ref_areas(block_country, level, refs)
        result.setdefault(block_country, set()).update(
            _queries_subdivision_capitals._derived_subdivision_areas_to_capital_source_ids(areas, country_code=block_country)
        )
    return result


def _derived_subdivision_source_item_payload(area: AdminArea, *, ancestor_ids: list[str] | None = None) -> dict:
    name = _queries_browser._area_display_name(area)
    entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    metadata = {} if ancestor_ids is not None else _queries_groups._group_admin_area_metadata(str(area.id))
    return {
        "id": str(area.id),
        "value": str(area.id),
        "country_code": query_parameters.normalize_country_key(area.country_code),
        "code": str(area.code or ""),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
        "entity_type": entity_type,
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": ancestor_ids if ancestor_ids is not None else metadata.get("ancestor_ids") or [],
    }


def _area_ancestor_ids_from_parent_map(area_id: str, parent_by_id: dict[str, str]) -> list[str]:
    ancestor_ids = []
    parent_id = parent_by_id.get(str(area_id), "")
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        ancestor_ids.append(parent_id)
        parent_id = parent_by_id.get(parent_id, "")
    return ancestor_ids


def _derived_subdivision_group_source_item_id(country_code: str, group_key: str) -> str:
    return f"group::{query_parameters.normalize_country_key(country_code)}::{_queries_groups._group_internal_name(group_key)}"


def _derived_subdivision_record_source_item_id(country_code: str, subdivision_key: str) -> str:
    return f"derived-subdivision::{query_parameters.normalize_country_key(country_code)}::{_queries_groups._group_internal_name(subdivision_key)}"


def _derived_subdivision_group_entries_cached(
    country_code: str,
    entries_cache: dict[str, list[dict]] | None = None,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    if entries_cache is None:
        return _queries_groups._subdivision_group_entries_for_country(_queries_groups._subdivision_groups_for_country(country_code), country_code)
    if country_code not in entries_cache:
        entries_cache[country_code] = _queries_groups._subdivision_group_entries_for_country(
            _queries_groups._subdivision_groups_for_country(country_code),
            country_code,
        )
    return entries_cache[country_code]


def _derived_subdivision_group_entry_cached(
    country_code: str,
    group_key: str,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return None
    try:
        entry_slug = _queries_groups._group_entry_slug(group_key)
    except ValueError:
        return None
    if group_entry_cache is None:
        if entries_cache is not None:
            for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache):
                if _derived_subdivision_group_entry_matches(entry, entry_slug):
                    return entry
        return _queries_groups._group_entry_for_country(country_code, entry_slug, hydrate=False)
    cache_key = (country_code, entry_slug)
    if cache_key not in group_entry_cache:
        group_entry = None
        if entries_cache is not None:
            for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache):
                if _derived_subdivision_group_entry_matches(entry, entry_slug):
                    group_entry = entry
                    break
        if group_entry is None:
            group_entry = _queries_groups._group_entry_for_country(country_code, entry_slug, hydrate=False)
        group_entry_cache[cache_key] = group_entry
    return group_entry_cache[cache_key]


def _source_item_levels(items) -> list[int]:
    levels: set[int] = set()
    for item in items or []:
        raw_level = item.get("level") if isinstance(item, dict) else item
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if level > 0:
            levels.add(level)
    return sorted(levels)


def _derived_subdivision_group_source_item_for_key(
    country_code: str,
    group_key: str,
    *,
    level: int | None = None,
    scoped_municipal_ids: set[str] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    try:
        normalized_key = _queries_groups._group_internal_name(group_key)
    except ValueError:
        return None
    options = _derived_subdivision_group_source_options(
        country_code,
        level=level,
        group_key=normalized_key,
        scoped_municipal_ids=scoped_municipal_ids,
        entries_cache=entries_cache,
    )
    if options:
        return options[0]
    level = int(level or _derived_subdivision_group_level(country_code) or 1)
    label = f"{normalized_key} ({_('Grupo')})"
    return {
        "id": _derived_subdivision_group_source_item_id(country_code, normalized_key),
        "value": _derived_subdivision_group_source_item_id(country_code, normalized_key),
        "source_kind": "group",
        "item_type": "group",
        "country_code": country_code,
        "code": normalized_key,
        "name": normalized_key,
        "label": label,
        "level": level,
        "entity_type": str(_("Grupo")),
        "parent_id": "",
        "ancestor_ids": [],
        "group_key": normalized_key,
        "group_slug": _queries_groups._group_entry_slug(normalized_key),
        "member_names": [],
        "member_ids": [],
        "member_count": 0,
        "member_text": "",
        "description": "",
    }


def _derived_subdivision_group_source_options(
    country_code: str,
    *,
    level: str | int | None = None,
    levels: list[str | int] | tuple[str | int, ...] | set[str | int] | None = None,
    parent_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    group_key: str = "",
    direct_parent_only: bool = False,
    scoped_municipal_ids: set[str] | None = None,
    entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    try:
        level_int = int(level) if level not in (None, "") else None
    except (TypeError, ValueError):
        return []
    requested_levels = {level_int} if level_int is not None else _source_item_levels(
        {"level": raw_level} for raw_level in (levels or [])
    )
    parent_id_set = {str(parent_id).strip() for parent_id in (parent_ids or []) if str(parent_id or "").strip()}
    scoped_municipal_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    lookup_key = str(group_key or "").strip()
    entries = [
        entry
        for entry in _derived_subdivision_group_entries_cached(country_code, entries_cache)
        if not lookup_key or _derived_subdivision_group_entry_matches(entry, lookup_key)
    ]
    if not entries:
        return []
    entry_refs = [
        _derived_subdivision_group_member_refs(entry, country_code)
        for entry in entries
    ]
    target_levels = requested_levels or {int(_derived_subdivision_group_level(country_code) or 1)}
    if parent_id_set and not scoped_municipal_ids:
        for target_level in target_levels:
            scoped_municipal_ids.update(
                _queries_subdivision_capitals._derived_subdivision_descendant_ids_at_level(parent_id_set, int(target_level or 0))
            )
    area_by_id, area_by_key = _derived_subdivision_group_member_area_indexes(
        country_code,
        member_ids=[
            item_id
            for refs in entry_refs
            for item_id in refs["member_ids"]
        ],
        member_names=[
            name
            for refs in entry_refs
            for name in refs["member_names"]
            if _queries_groups._group_name_match_key(name) not in refs["selected_name_keys"]
        ],
        target_levels=target_levels,
    )
    parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(area_by_id.values())

    options = []
    seen: set[str] = set()
    for entry, refs in zip(entries, entry_refs):
        member_areas = _derived_subdivision_group_resolved_member_areas(
            refs,
            area_by_id=area_by_id,
            area_by_key=area_by_key,
            scoped_municipal_ids=scoped_municipal_ids,
        )
        resolved_level = _derived_subdivision_group_item_level(country_code, member_areas)
        parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
            member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
        )
        if requested_levels and resolved_level not in requested_levels:
            continue
        if parent_id_set and not _derived_subdivision_group_item_matches_parent_ids(
            {"parent_id": parent_id, "ancestor_ids": ancestor_ids},
            parent_id_set,
            direct_parent_only=direct_parent_only,
        ):
            continue
        item = _derived_subdivision_group_source_item_payload(
            entry,
            country_code,
            member_names=refs["member_names"],
            member_areas=member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
            resolved_level=resolved_level,
            resolved_parent_id=parent_id,
            resolved_ancestor_ids=ancestor_ids,
            include_member_names=include_member_names,
        )
        if not item:
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        options.append(item)
    return sorted(
        options,
        key=lambda item: (
            int(item.get("level") or 0),
            str(item.get("label") or item.get("name") or item.get("group_key") or ""),
            str(item.get("id") or ""),
        ),
    )


def _derived_subdivision_group_member_refs(entry: dict, country_code: str) -> dict:
    member_ids: list[str] = []
    subtract_ids: set[str] = set()
    member_names: list[str] = []
    selected_name_keys: set[str] = set()
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    country_code = query_parameters.normalize_country_key(country_code)
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for name in _queries_groups._group_block_selected_names(block):
            name = str(name or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                member_names.append(name)
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                selected_name = str(selected.get("name") or selected.get("label") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
                selected_name_key = _queries_groups._group_name_match_key(selected_name)
                if selected_name_key:
                    selected_name_keys.add(selected_name_key)
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    member_ids.append(selected_id)
                if selected_id and isinstance(selected, dict) and selected.get("operation") == "subtract":
                    subtract_ids.add(selected_id)
    return {
        "member_ids": member_ids,
        "subtract_ids": subtract_ids,
        "member_names": member_names,
        "selected_name_keys": selected_name_keys,
    }


def _derived_subdivision_group_member_area_indexes(
    country_code: str,
    *,
    member_ids: list[str],
    member_names: list[str],
    target_levels: set[int] | None,
) -> tuple[dict[str, AdminArea], dict[str, list[AdminArea]]]:
    country_code = query_parameters.normalize_country_key(country_code)
    clean_ids = sorted({str(item_id).strip() for item_id in member_ids if str(item_id or "").strip()})
    clean_names = [str(name).strip() for name in member_names if str(name or "").strip()]
    name_keys = {_queries_groups._group_name_match_key(name) for name in clean_names if _queries_groups._group_name_match_key(name)}
    area_by_id: dict[str, AdminArea] = {}
    candidates_by_key: dict[str, list[AdminArea]] = {}
    try:
        if clean_ids:
            for area in (
                area_selectors.group_source_admin_areas()
                .filter(country_code__iexact=country_code, id__in=clean_ids)
                .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            ):
                area_by_id[str(area.id)] = area
        if name_keys:
            queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code).exclude(level=0)
            clean_target_levels = {int(value) for value in (target_levels or set()) if int(value or 0) > 0}
            if clean_target_levels:
                queryset = queryset.filter(level__in=clean_target_levels)
            if len(name_keys) <= 250:
                condition = Q()
                for name in clean_names:
                    condition |= Q(name__iexact=name) | Q(code__iexact=name) | Q(id=name)
                queryset = queryset.filter(condition) if condition else AdminArea.objects.none()
            rows = list(
                queryset.only(
                    "id",
                    "country_code",
                    "code",
                    "name",
                    "level",
                    "entity_type",
                    "parent_id",
                    "city_merge_status",
                )
            )
        else:
            rows = []
    except (OperationalError, ProgrammingError, ValueError):
        return area_by_id, candidates_by_key
    child_counts = _queries_browser._admin_area_child_counts([str(area.id) for area in rows], group_source_only=True)
    for area in sorted(rows, key=lambda item: _queries_groups._group_area_name_candidate_rank(item, child_counts)):
        for lookup_value in (area.name, area.code, area.id):
            key = _queries_groups._group_name_match_key(lookup_value)
            if key and key in name_keys:
                candidates_by_key.setdefault(key, []).append(area)
                area_by_id.setdefault(str(area.id), area)
    return area_by_id, candidates_by_key


def _derived_subdivision_group_resolved_member_areas(
    refs: dict,
    *,
    area_by_id: dict[str, AdminArea],
    area_by_key: dict[str, list[AdminArea]],
    scoped_municipal_ids: set[str] | None = None,
) -> list[AdminArea]:
    result: list[AdminArea] = []
    seen_ids: set[str] = set()
    selected_name_keys = refs.get("selected_name_keys") or set()
    scoped_municipal_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    for item_id in refs.get("member_ids") or []:
        area = area_by_id.get(str(item_id))
        if area and str(area.id) not in seen_ids:
            result.append(area)
            seen_ids.add(str(area.id))
    for name in refs.get("member_names") or []:
        key = _queries_groups._group_name_match_key(name)
        if key in selected_name_keys:
            continue
        candidates = area_by_key.get(key) or []
        area = _derived_subdivision_group_candidate_in_scope(candidates, scoped_municipal_ids)
        if area and str(area.id) not in seen_ids:
            result.append(area)
            seen_ids.add(str(area.id))
    from ciudades_del_mundo.services.composite_city_sources import selected_composite_members
    return selected_composite_members(result, refs.get("subtract_ids") or ())


def _derived_subdivision_group_candidate_in_scope(
    candidates: list[AdminArea],
    scoped_municipal_ids: set[str],
) -> AdminArea | None:
    if not candidates:
        return None
    if not scoped_municipal_ids:
        return candidates[0]
    for candidate in candidates:
        if _derived_subdivision_area_matches_municipal_scope(candidate, scoped_municipal_ids):
            return candidate
    return candidates[0]


def _derived_subdivision_area_matches_municipal_scope(area: AdminArea, scoped_municipal_ids: set[str]) -> bool:
    if not scoped_municipal_ids:
        return False
    if str(area.id) in scoped_municipal_ids:
        return True
    return bool(
        _queries_subdivision_capitals._derived_subdivision_areas_to_capital_source_ids([area], country_code=area.country_code)
        & scoped_municipal_ids
    )


def _derived_subdivision_group_parent_maps(member_areas) -> tuple[dict[str, str], dict[str, int]]:
    parent_by_id: dict[str, str] = {}
    level_by_id: dict[str, int] = {}
    frontier = set()
    for area in member_areas:
        area_id = str(area.id)
        parent_by_id[area_id] = str(area.parent_id or "")
        level_by_id[area_id] = int(area.level or 0)
        if area.parent_id:
            frontier.add(str(area.parent_id))
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        try:
            rows = list(area_selectors.visible_admin_areas().filter(id__in=current).values("id", "parent_id", "level"))
        except (OperationalError, ProgrammingError, ValueError):
            break
        frontier = set()
        for row in rows:
            area_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_by_id[area_id] = parent_id
            level_by_id[area_id] = int(row.get("level") or 0)
            if parent_id and parent_id not in seen:
                frontier.add(parent_id)
    return parent_by_id, level_by_id


def _admin_area_parent_maps_for_ids(area_ids: set[str]) -> tuple[dict[str, str], dict[str, int]]:
    parent_by_id: dict[str, str] = {}
    level_by_id: dict[str, int] = {}
    frontier = {str(area_id).strip() for area_id in area_ids if str(area_id or "").strip()}
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        try:
            rows = list(area_selectors.visible_admin_areas().filter(id__in=current).values("id", "parent_id", "level"))
        except (OperationalError, ProgrammingError, ValueError):
            break
        frontier = set()
        for row in rows:
            area_id = str(row["id"])
            parent_id = str(row.get("parent_id") or "")
            parent_by_id[area_id] = parent_id
            level_by_id[area_id] = int(row.get("level") or 0)
            if parent_id and parent_id not in seen:
                frontier.add(parent_id)
    return parent_by_id, level_by_id


def _admin_area_common_scope_for_ids(
    area_ids: set[str],
    *,
    parent_by_id: dict[str, str],
    level_by_id: dict[str, int],
) -> tuple[str, list[str]]:
    clean_ids = {str(area_id).strip() for area_id in area_ids if str(area_id or "").strip()}
    if not clean_ids:
        return "", []
    common_ids: set[str] | None = None
    for area_id in clean_ids:
        area_ancestor_ids = set()
        parent_id = parent_by_id.get(area_id, "")
        guard = 0
        while parent_id and guard < 20:
            area_ancestor_ids.add(parent_id)
            parent_id = parent_by_id.get(parent_id, "")
            guard += 1
        common_ids = area_ancestor_ids if common_ids is None else common_ids & area_ancestor_ids
    if not common_ids:
        return "", []
    parent_id = sorted(common_ids, key=lambda candidate: (level_by_id.get(candidate, -1), candidate), reverse=True)[0]
    ancestor_ids = sorted(
        (candidate for candidate in common_ids if candidate != parent_id),
        key=lambda candidate: (level_by_id.get(candidate, -1), candidate),
        reverse=True,
    )
    return parent_id, ancestor_ids


def _derived_subdivision_group_entry_matches(entry: dict, group_key: str) -> bool:
    candidates = [
        entry.get("internal_name"),
        entry.get("slug"),
        entry.get("record_slug"),
    ]
    try:
        lookup_internal = _queries_groups._group_internal_name(group_key)
    except ValueError:
        lookup_internal = ""
    try:
        lookup_slug = _queries_groups._group_entry_slug(group_key)
    except ValueError:
        lookup_slug = ""
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if lookup_internal:
            try:
                if _queries_groups._group_internal_name(text) == lookup_internal:
                    return True
            except ValueError:
                pass
        if lookup_slug:
            try:
                if _queries_groups._group_entry_slug(text) == lookup_slug:
                    return True
            except ValueError:
                pass
    return False


def _derived_subdivision_group_source_item_payload(
    entry: dict,
    country_code: str,
    *,
    member_names: list[str] | None = None,
    member_areas: list[AdminArea] | None = None,
    parent_by_id: dict[str, str] | None = None,
    level_by_id: dict[str, int] | None = None,
    resolved_level: int | None = None,
    resolved_parent_id: str | None = None,
    resolved_ancestor_ids: list[str] | None = None,
    include_member_names: bool = True,
) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    try:
        group_key = _queries_groups._group_internal_name(entry.get("internal_name") or entry.get("slug") or "")
    except ValueError:
        return None
    group_slug = _queries_groups._group_entry_slug(entry.get("slug") or group_key)
    if member_names is None:
        member_names = _derived_subdivision_group_member_names(entry, country_code)
    if member_areas is None:
        member_areas = _derived_subdivision_group_member_areas(entry, country_code, member_names)
    if parent_by_id is None or level_by_id is None:
        parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    level = int(resolved_level or _derived_subdivision_group_item_level(country_code, member_areas))
    if resolved_parent_id is None or resolved_ancestor_ids is None:
        parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
            member_areas,
            parent_by_id=parent_by_id,
            level_by_id=level_by_id,
        )
    else:
        parent_id = str(resolved_parent_id or "")
        ancestor_ids = resolved_ancestor_ids
    member_ids = [str(area.id) for area in member_areas]
    member_labels = [_derived_subdivision_group_member_label(area) for area in member_areas[:30]]
    resolved_name_keys = {_queries_groups._group_name_match_key(area.name) for area in member_areas}
    unresolved_names = [name for name in member_names if _queries_groups._group_name_match_key(name) not in resolved_name_keys]
    display_members = member_labels + [name for name in unresolved_names if name not in member_labels]
    member_text = ", ".join(display_members)
    if len(member_areas) > len(member_labels):
        member_text = f"{member_text}, +{len(member_areas) - len(member_labels)}" if member_text else f"+{len(member_areas) - len(member_labels)}"
    record = entry.get("record")
    description = str(getattr(record, "description", "") or "").strip()
    name = str(entry.get("name") or group_key).strip()
    label = f"{name} ({_('Grupo')})"
    return {
        "id": _derived_subdivision_group_source_item_id(country_code, group_key),
        "value": _derived_subdivision_group_source_item_id(country_code, group_key),
        "source_kind": "group",
        "item_type": "group",
        "country_code": country_code,
        "code": group_key,
        "name": name,
        "label": label,
        "level": level,
        "entity_type": str(_("Grupo")),
        "parent_id": parent_id,
        "ancestor_ids": ancestor_ids,
        "group_key": group_key,
        "group_slug": group_slug,
        "member_names": member_names if include_member_names else [],
        "member_ids": member_ids,
        "member_count": len(member_names),
        "member_text": member_text,
        "description": description,
    }


def _derived_subdivision_group_member_label(area: AdminArea) -> str:
    name = str(area.name or area.code or area.id)
    entity_type = str(area.entity_type or "")
    return f"{name} ({entity_type})" if entity_type else name


def _derived_subdivision_group_member_names(entry: dict, country_code: str) -> list[str]:
    names = []
    seen: set[str] = set()
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for name in _queries_groups._group_block_selected_names(block):
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _derived_subdivision_group_member_areas(entry: dict, country_code: str, member_names: list[str]) -> list[AdminArea]:
    country_code = query_parameters.normalize_country_key(country_code)
    member_ids: list[str] = []
    seen_ids: set[str] = set()
    selected_name_keys: set[str] = set()
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                selected_name = str(selected.get("name") or selected.get("label") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
                selected_name_key = _queries_groups._group_name_match_key(selected_name)
                if selected_name_key:
                    selected_name_keys.add(selected_name_key)
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    member_ids.append(selected_id)
    resolved_ids = list(member_ids)
    unresolved_member_names = [name for name in member_names if _queries_groups._group_name_match_key(name) not in selected_name_keys]
    if unresolved_member_names:
        resolved = _queries_groups._group_sections_for_flat_names(country_code, unresolved_member_names)
        for section in resolved.get("sections") or []:
            for selected in section.get("selected") or []:
                selected_id = str(selected.get("id") or "").strip() if isinstance(selected, dict) else ""
                if selected_id and selected_id not in seen_ids:
                    seen_ids.add(selected_id)
                    resolved_ids.append(selected_id)
    if not resolved_ids:
        return []
    try:
        rows = list(
            area_selectors.group_source_admin_areas()
            .filter(country_code__iexact=country_code, id__in=resolved_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    area_by_id = {str(area.id): area for area in rows}
    from ciudades_del_mundo.services.composite_city_sources import selected_composite_members
    refs = _derived_subdivision_group_member_refs(entry, country_code)
    return selected_composite_members([area_by_id[item_id] for item_id in resolved_ids if item_id in area_by_id],
                                      refs.get("subtract_ids") or ())


def _derived_subdivision_group_item_level(country_code: str, member_areas: list[AdminArea]) -> int:
    levels = {int(area.level or 0) for area in member_areas if int(area.level or 0) > 0}
    if len(levels) == 1:
        return next(iter(levels))
    return int(_derived_subdivision_group_level(country_code) or 1)


def _derived_subdivision_group_common_scope(
    member_areas: list[AdminArea],
    *,
    parent_by_id: dict[str, str] | None = None,
    level_by_id: dict[str, int] | None = None,
) -> tuple[str, list[str]]:
    if not member_areas:
        return "", []
    if parent_by_id is None or level_by_id is None:
        parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    common_ids: set[str] | None = None
    for area in member_areas:
        area_ancestor_ids = set()
        parent_id = str(area.parent_id or "")
        guard = 0
        while parent_id and guard < 20:
            area_ancestor_ids.add(parent_id)
            parent_id = parent_by_id.get(parent_id, "")
            guard += 1
        common_ids = area_ancestor_ids if common_ids is None else common_ids & area_ancestor_ids
    if not common_ids:
        return "", []
    parent_id = sorted(common_ids, key=lambda area_id: (level_by_id.get(area_id, -1), area_id), reverse=True)[0]
    ancestor_ids = sorted(
        (area_id for area_id in common_ids if area_id != parent_id),
        key=lambda area_id: (level_by_id.get(area_id, -1), area_id),
        reverse=True,
    )
    return parent_id, ancestor_ids


def _derived_subdivision_group_item_matches_parent_ids(item: dict, parent_ids: set[str], *, direct_parent_only: bool = False) -> bool:
    if direct_parent_only:
        return str(item.get("parent_id") or "").strip() in parent_ids
    scope_ids = {str(item.get("parent_id") or "").strip()}
    scope_ids.update(str(value).strip() for value in item.get("ancestor_ids") or [] if str(value or "").strip())
    return bool(scope_ids & parent_ids)


def _source_items_with_group_options(items: list[dict], group_options: list[dict]) -> list[dict]:
    result = list(items or [])
    seen = {str(item.get("id") or item.get("value") or "") for item in result if isinstance(item, dict)}
    for option in group_options or []:
        option_id = str(option.get("id") or option.get("value") or "").strip()
        if option_id and option_id not in seen:
            result.append(option)
            seen.add(option_id)
    return result


def _derived_subdivision_source_record_for_key(country_code: str, subdivision_key: str) -> DerivedSubdivision | None:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return None
    try:
        internal_name = _queries_groups._group_internal_name(subdivision_key)
    except ValueError:
        return None
    try:
        entry_slug = _queries_groups._group_entry_slug(internal_name)
    except ValueError:
        entry_slug = ""
    queryset = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    if entry_slug:
        record = queryset.filter(slug=_queries_subdivisions._derived_subdivision_record_slug(country_code, entry_slug)).first()
        if record:
            return record
        record = queryset.filter(slug=entry_slug).first()
        if record:
            return record
    return queryset.filter(internal_name__iexact=internal_name).first()


def _derived_subdivision_source_key(record: DerivedSubdivision, data: dict | None = None) -> str:
    data = data if isinstance(data, dict) else _forms_subdivisions._derived_subdivision_toml_data(record.content)
    for candidate in (
        data.get("internal_name"),
        record.internal_name,
        _queries_subdivisions._derived_subdivision_entry_slug(record),
        record.slug,
    ):
        try:
            return _queries_groups._group_internal_name(str(candidate or ""))
        except ValueError:
            continue
    return _queries_groups._group_internal_name(record.slug)


def _derived_subdivision_source_level(record: DerivedSubdivision, data: dict | None = None) -> int:
    data = data if isinstance(data, dict) else _forms_subdivisions._derived_subdivision_toml_data(record.content)
    try:
        return _forms_subdivisions._derived_subdivision_level(data.get("level") or 1)
    except ValueError:
        return 1


def _derived_subdivision_source_ids_for_key(
    country_code: str,
    subdivision_key: str,
    *,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    record = _derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        return set()
    return _derived_subdivision_source_ids_for_record(
        record,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )


def _derived_subdivision_source_ids_for_record(
    record: DerivedSubdivision,
    *,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    data = _forms_subdivisions._derived_subdivision_toml_data(record.content)
    country_code = query_parameters.normalize_country_key(record.source_country_code or data.get("source_country_code") or "")
    if not country_code:
        return set()
    try:
        record_key = _derived_subdivision_source_key(record, data)
    except ValueError:
        return set()
    stack = set(seen or set())
    cache_key = (country_code, record_key)
    if source_ids_cache is not None and cache_key in source_ids_cache:
        return set(source_ids_cache[cache_key])
    if cache_key in stack:
        return set()
    stack.add(cache_key)
    source_ids = _derived_subdivision_node_source_ids(
        data,
        default_country_code=country_code,
        seen=stack,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    if source_ids_cache is not None:
        source_ids_cache[cache_key] = set(source_ids)
    return source_ids


def _derived_subdivision_node_source_ids(
    data: dict,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> set[str]:
    if not isinstance(data, dict):
        return set()
    include_ids = _queries_subdivision_capitals._derived_subdivision_blocks_source_ids(
        data.get("include"),
        default_country_code=default_country_code,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    subtract_ids = _queries_subdivision_capitals._derived_subdivision_blocks_source_ids(
        data.get("subtract"),
        default_country_code=default_country_code,
        seen=seen,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    result = include_ids - subtract_ids
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if isinstance(child, dict):
            result.update(
                _derived_subdivision_node_source_ids(
                    child,
                    default_country_code=default_country_code,
                    seen=seen,
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                )
            )
    return result


def _derived_subdivision_record_source_areas(
    record: DerivedSubdivision,
    *,
    source_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> list[AdminArea]:
    if source_ids is None:
        source_ids = _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    if not source_ids:
        return []
    try:
        return list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=source_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            .order_by("level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_record_source_item_for_key(
    country_code: str,
    subdivision_key: str,
    *,
    level: int | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    record = _derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        try:
            normalized_key = _queries_groups._group_internal_name(subdivision_key)
        except ValueError:
            return None
        level = int(level or 1)
        label = f"{normalized_key} ({_('Subdivisiones creadas')}, {_('Nivel')} {level})"
        item_id = _derived_subdivision_record_source_item_id(country_code, normalized_key)
        return {
            "id": item_id,
            "value": item_id,
            "source_kind": "derived_subdivision",
            "item_type": "derived_subdivision",
            "country_code": country_code,
            "code": normalized_key,
            "name": normalized_key,
            "label": label,
            "level": level,
            "entity_type": str(_("Subdivisiones creadas")),
            "parent_id": "",
            "ancestor_ids": [],
            "derived_subdivision_key": normalized_key,
            "derived_subdivision_slug": _queries_groups._group_entry_slug(normalized_key),
            "member_names": [],
            "member_ids": [],
            "member_count": 0,
            "member_text": "",
            "description": "",
        }
    return _derived_subdivision_record_source_item_payload(
        record,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )


def _derived_subdivision_record_source_item_payload(
    record: DerivedSubdivision,
    *,
    source_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
    compact_parent_id: str = "",
    compact_ancestor_ids: list[str] | None = None,
) -> dict | None:
    country_code = query_parameters.normalize_country_key(record.source_country_code)
    data = _forms_subdivisions._derived_subdivision_toml_data(record.content)
    if not country_code:
        country_code = query_parameters.normalize_country_key(data.get("source_country_code") or "")
    if not country_code:
        return None
    try:
        subdivision_key = _derived_subdivision_source_key(record, data)
    except ValueError:
        return None
    level = _derived_subdivision_source_level(record, data)
    if not include_member_names and source_ids is not None:
        member_ids = sorted(str(item_id).strip() for item_id in source_ids if str(item_id or "").strip())
        if not member_ids:
            return None
        parent_id = str(compact_parent_id or "").strip()
        ancestor_ids = [str(value) for value in (compact_ancestor_ids or []) if str(value or "").strip()]
        if not parent_id:
            parent_by_id, level_by_id = _admin_area_parent_maps_for_ids(set(member_ids))
            parent_id, ancestor_ids = _admin_area_common_scope_for_ids(
                set(member_ids),
                parent_by_id=parent_by_id,
                level_by_id=level_by_id,
            )
        try:
            preview_areas = list(
                area_selectors.group_source_admin_areas()
                .filter(id__in=member_ids)
                .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
                .order_by("level", "name", "id")[:30]
            )
        except (OperationalError, ProgrammingError, ValueError):
            preview_areas = []
        member_labels = [_derived_subdivision_group_member_label(area) for area in preview_areas]
        member_text = ", ".join(member_labels)
        if len(member_ids) > len(member_labels):
            member_text = f"{member_text}, +{len(member_ids) - len(member_labels)}" if member_text else f"+{len(member_ids) - len(member_labels)}"
        name = str(data.get("name") or record.name or subdivision_key).strip()
        entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
        if entity_type:
            label = f"{name} ({entity_type}, {_('Nivel')} {level})"
        else:
            label = f"{name} ({_('Nivel')} {level})"
        item_id = _derived_subdivision_record_source_item_id(country_code, subdivision_key)
        return {
            "id": item_id,
            "value": item_id,
            "source_kind": "derived_subdivision",
            "item_type": "derived_subdivision",
            "country_code": country_code,
            "code": subdivision_key,
            "name": name,
            "label": label,
            "level": level,
            "entity_type": str(_("Subdivisiones creadas")),
            "parent_id": parent_id,
            "ancestor_ids": ancestor_ids,
            "derived_subdivision_key": subdivision_key,
            "derived_subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(record),
            "member_names": [],
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "member_text": member_text,
            "description": str(record.description or "").strip(),
        }
    member_areas = _derived_subdivision_record_source_areas(
        record,
        source_ids=source_ids,
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    if not member_areas:
        return None
    parent_by_id, level_by_id = _derived_subdivision_group_parent_maps(member_areas)
    parent_id, ancestor_ids = _derived_subdivision_group_common_scope(
        member_areas,
        parent_by_id=parent_by_id,
        level_by_id=level_by_id,
    )
    member_ids = [str(area.id) for area in member_areas]
    member_labels = [_derived_subdivision_group_member_label(area) for area in member_areas[:30]]
    display_members = member_labels
    member_text = ", ".join(display_members)
    if len(member_areas) > len(display_members):
        member_text = f"{member_text}, +{len(member_areas) - len(display_members)}" if member_text else f"+{len(member_areas) - len(display_members)}"
    name = str(data.get("name") or record.name or subdivision_key).strip()
    entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
    if entity_type:
        label = f"{name} ({entity_type}, {_('Nivel')} {level})"
    else:
        label = f"{name} ({_('Nivel')} {level})"
    item_id = _derived_subdivision_record_source_item_id(country_code, subdivision_key)
    return {
        "id": item_id,
        "value": item_id,
        "source_kind": "derived_subdivision",
        "item_type": "derived_subdivision",
        "country_code": country_code,
        "code": subdivision_key,
        "name": name,
        "label": label,
        "level": level,
        "entity_type": str(_("Subdivisiones creadas")),
        "parent_id": parent_id,
        "ancestor_ids": ancestor_ids,
        "derived_subdivision_key": subdivision_key,
        "derived_subdivision_slug": _queries_subdivisions._derived_subdivision_entry_slug(record),
        "member_names": member_labels if include_member_names else [],
        "member_ids": member_ids,
        "member_count": len(member_ids),
        "member_text": member_text,
        "description": str(record.description or "").strip(),
    }


def _derived_subdivision_source_options(
    country_code: str,
    *,
    level: str | int | None = None,
    levels: list[str | int] | tuple[str | int, ...] | set[str | int] | None = None,
    parent_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    current_level: str | int | None = None,
    current_subdivision: str = "",
    current_subdivision_country: str = "",
    scoped_municipal_ids: set[str] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    include_member_names: bool = True,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    try:
        minimum_level = int(level) if level not in (None, "") else None
    except (TypeError, ValueError):
        minimum_level = None
    if minimum_level is None:
        requested_levels = _source_item_levels({"level": raw_level} for raw_level in (levels or []))
        minimum_level = min(requested_levels) if requested_levels else None
    try:
        current_level_int = int(current_level) if current_level not in (None, "") else None
    except (TypeError, ValueError):
        current_level_int = None
    try:
        current_key = _queries_groups._group_internal_name(current_subdivision) if str(current_subdivision or "").strip() else ""
    except ValueError:
        current_key = ""
    current_subdivision_country = query_parameters.normalize_country_key(current_subdivision_country)
    parent_id_set = {str(parent_id).strip() for parent_id in (parent_ids or []) if str(parent_id or "").strip()}
    compact_parent_id = next(iter(parent_id_set)) if len(parent_id_set) == 1 else ""
    scope_ids = {
        str(item_id).strip()
        for item_id in (scoped_municipal_ids or set())
        if str(item_id or "").strip()
    }
    if parent_id_set and not scope_ids:
        scope_ids = _derived_subdivision_municipal_scope_ids_for_parents(country_code, parent_id_set)
    try:
        records = list(
            DerivedSubdivision.objects
            .filter(source_country_code__iexact=country_code)
            .only("slug", "internal_name", "name", "source_country_code", "entity_type", "code", "description", "content")
            .order_by("name", "slug")
        )
    except (OperationalError, ProgrammingError):
        return []
    options = []
    seen_ids: set[str] = set()
    for record in records:
        data = _forms_subdivisions._derived_subdivision_toml_data(record.content)
        try:
            record_key = _derived_subdivision_source_key(record, data)
        except ValueError:
            continue
        if current_key and (not current_subdivision_country or current_subdivision_country == country_code) and record_key == current_key:
            continue
        record_level = _derived_subdivision_source_level(record, data)
        if current_level_int is not None and record_level <= current_level_int:
            continue
        if minimum_level is not None and record_level < minimum_level:
            continue
        source_ids = _derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
        if parent_id_set:
            if not source_ids or not (source_ids & scope_ids):
                continue
        item = _derived_subdivision_record_source_item_payload(
            record,
            source_ids=source_ids,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
            include_member_names=include_member_names,
            compact_parent_id=compact_parent_id if not include_member_names else "",
        )
        if not item:
            continue
        item_id = str(item.get("id") or item.get("value") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        options.append(item)
        seen_ids.add(item_id)
    return sorted(
        options,
        key=lambda item: (
            int(item.get("level") or 0),
            str(item.get("label") or item.get("name") or ""),
            str(item.get("id") or ""),
        ),
    )


def _derived_subdivision_municipal_scope_ids_for_parents(country_code: str, parent_ids: set[str]) -> set[str]:
    target_level = _queries_subdivision_capitals._derived_subdivision_capital_target_level(country_code)
    if target_level is None:
        return set()
    parent_ids = {str(parent_id).strip() for parent_id in parent_ids if str(parent_id or "").strip()}
    result: set[str] = set()
    if not parent_ids:
        return result
    try:
        parents = list(area_selectors.group_source_admin_areas().filter(id__in=parent_ids).only("id", "level", "country_code", "city_merge_status"))
    except (OperationalError, ProgrammingError, ValueError):
        parents = []
    lower_parent_ids: set[str] = set()
    for parent in parents:
        try:
            level = int(parent.level or 0)
        except (TypeError, ValueError):
            continue
        if level == target_level:
            result.add(str(parent.id))
        elif level < target_level:
            lower_parent_ids.add(str(parent.id))
        else:
            ancestor = _queries_subdivision_capitals._derived_subdivision_ancestor_at_level(parent, target_level)
            if ancestor:
                result.add(str(ancestor.id))
    result |= _queries_subdivision_capitals._derived_subdivision_descendant_ids_at_level(lower_parent_ids, target_level)
    return result


def _derived_subdivision_source_items_with_default(items: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = query_parameters.normalize_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    seen_ids: dict[str, set[str]] = {}
    for block in items:
        if not isinstance(block, dict):
            continue
        country_code = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "items": []})
        country_seen = seen_ids.setdefault(country_code, set())
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("value") or "").strip()
            if not item_id or item_id in country_seen:
                continue
            normalized = dict(item)
            normalized["id"] = item_id
            normalized["value"] = item_id
            normalized["country_code"] = query_parameters.normalize_country_key(normalized.get("country_code") or country_code)
            grouped_block["items"].append(normalized)
            country_seen.add(item_id)
    if fallback_country_code and fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "items": []}
    ordered_codes = ([fallback_country_code] if fallback_country_code in grouped else []) + sorted(
        code for code in grouped if code != fallback_country_code
    )
    return [grouped[code] for code in ordered_codes]


def _derived_subdivision_visual_source_blocks_from_content(
    content: str,
    table_name: str,
    *,
    fallback_country_code: str,
) -> list[dict]:
    data = _forms_subdivisions._derived_subdivision_toml_data(content)
    source_blocks = data.get(table_name) if isinstance(data.get(table_name), list) else []
    visual_by_country: dict[str, dict] = {}
    for block in source_blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        try:
            level = int(block.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        refs = _queries_subdivision_capitals._derived_subdivision_block_source_refs(block, country_code=block_country) if level else {"ids": [], "codes": [], "names": []}
        areas = _queries_subdivision_capitals._derived_subdivision_source_ref_areas(block_country, level, refs) if level else []
        if not areas:
            continue
        country_block = visual_by_country.setdefault(block_country, {"country_code": block_country, "names": [], "sections": []})
        for section in _derived_subdivision_visual_sections_from_areas(areas):
            _derived_subdivision_merge_visual_section(country_block, section)
    return _derived_subdivision_source_blocks_with_default(
        list(visual_by_country.values()),
        fallback_country_code=fallback_country_code,
    )


def _derived_subdivision_visual_sections_from_areas(areas: list[AdminArea]) -> list[dict]:
    area_ids = [str(area.id) for area in areas if str(getattr(area, "id", "") or "").strip()]
    if not area_ids:
        return []
    try:
        hydrated = list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=area_ids)
            .select_related("parent")
            .only(
                "id",
                "country_code",
                "code",
                "name",
                "level",
                "entity_type",
                "parent_id",
                "parent__id",
                "parent__country_code",
                "parent__code",
                "parent__name",
                "parent__level",
                "parent__entity_type",
                "parent__parent_id",
            )
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    sections_by_parent: dict[str, dict] = {}
    selected_seen: dict[str, set[str]] = {}
    for area in sorted(hydrated, key=lambda item: (str(item.parent_id or ""), int(item.level or 0), _queries_browser._area_display_name(item), str(item.id))):
        parent = area.parent
        if parent is None:
            continue
        parent_id = str(parent.id)
        section = sections_by_parent.setdefault(parent_id, _queries_groups._group_section_payload_from_area(parent))
        section_seen = selected_seen.setdefault(parent_id, set())
        area_id = str(area.id)
        if area_id in section_seen:
            continue
        section_seen.add(area_id)
        section["selected"].append(_derived_subdivision_visual_selected_area_payload(area))
    return list(sections_by_parent.values())


def _derived_subdivision_visual_selected_area_payload(area: AdminArea) -> dict:
    name = _queries_browser._area_display_name(area)
    label = name
    entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
    if entity_type:
        label = f"{label} ({entity_type})"
    return {
        "id": str(area.id),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
    }


def _derived_subdivision_merge_visual_section(country_block: dict, section: dict) -> None:
    if not isinstance(section, dict):
        return
    section_id = str(section.get("area_id") or "").strip()
    if not section_id:
        return
    sections = country_block.setdefault("sections", [])
    existing = next((item for item in sections if str(item.get("area_id") or "") == section_id), None)
    if existing is None:
        sections.append(section)
        return
    seen = {str(item.get("id") or item.get("name") or "") for item in existing.get("selected") or [] if isinstance(item, dict)}
    for selected in section.get("selected") or []:
        if not isinstance(selected, dict):
            continue
        selected_key = str(selected.get("id") or selected.get("name") or "").strip()
        if selected_key and selected_key not in seen:
            existing.setdefault("selected", []).append(selected)
            seen.add(selected_key)


def _derived_subdivision_source_blocks_with_default(blocks: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = query_parameters.normalize_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        country_code = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "names": [], "sections": []})
        grouped_block["names"].extend(str(name).strip() for name in (block.get("names") or []) if str(name or "").strip())
        for section in block.get("sections") or []:
            _derived_subdivision_merge_visual_section(grouped_block, section)
    if fallback_country_code and fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "names": [], "sections": []}
    ordered_codes = ([fallback_country_code] if fallback_country_code in grouped else []) + sorted(
        code for code in grouped if code != fallback_country_code
    )
    return [grouped[code] for code in ordered_codes]


def _derived_subdivision_content_with_visual_source_blocks(
    content: str,
    *,
    include_blocks_json,
    subtract_blocks_json,
    country_code: str,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data["include"] = _derived_subdivision_toml_blocks_from_visual_source_json(
        include_blocks_json,
        fallback_country_code=country_code,
    )
    data["subtract"] = _derived_subdivision_toml_blocks_from_visual_source_json(
        subtract_blocks_json,
        fallback_country_code=country_code,
    )
    return _forms_subdivisions._render_derived_subdivision_toml_data(data)


def _derived_subdivision_content_with_visual_source_ids(
    content: str,
    *,
    include_ids_json,
    subtract_ids_json,
    country_code: str,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data["include"] = _derived_subdivision_toml_blocks_from_visual_source_ids(
        include_ids_json,
        fallback_country_code=country_code,
    )
    data["subtract"] = _derived_subdivision_toml_blocks_from_visual_source_ids(
        subtract_ids_json,
        fallback_country_code=country_code,
    )
    return _forms_subdivisions._render_derived_subdivision_toml_data(data)


def _derived_subdivision_toml_blocks_from_visual_source_ids(value, *, fallback_country_code: str) -> list[dict]:
    raw_items = _derived_subdivision_raw_source_items_from_json(value)
    requested_ids = []
    seen_requested_ids = set()
    for item in raw_items:
        if _derived_subdivision_raw_source_item_group_key(item):
            continue
        if _derived_subdivision_raw_source_item_subdivision_key(item):
            continue
        item_id = str(item.get("id") or item.get("value") or "").strip()
        if item_id and item_id not in seen_requested_ids:
            requested_ids.append(item_id)
            seen_requested_ids.add(item_id)
    if requested_ids:
        try:
            rows = list(
                area_selectors.group_source_admin_areas()
                .filter(id__in=requested_ids)
                .values("id", "country_code", "level")
            )
        except (OperationalError, ProgrammingError, ValueError):
            rows = []
    else:
        rows = []
    area_by_id = {str(row["id"]): row for row in rows}
    result: dict[tuple[str, int], dict] = {}
    seen_values: dict[tuple[str, int, str], set[str]] = {}

    def toml_block_for(block_country: str, level: int) -> dict:
        key = (block_country, level)
        return result.setdefault(
            key,
            {
                "country_code": block_country,
                "level": level,
                "names": [],
                "ids": [],
                "codes": [],
                "groups": [],
                "derived_subdivisions": [],
                "expressions": [],
            },
        )

    for item in raw_items:
        group_key = _derived_subdivision_raw_source_item_group_key(item)
        if group_key:
            block_country = _derived_subdivision_raw_source_item_country(item, fallback_country_code=fallback_country_code)
            try:
                level = int(item.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if not level:
                level = _derived_subdivision_group_level(block_country)
            if not block_country or not level:
                continue
            toml_block = toml_block_for(block_country, int(level))
            seen = seen_values.setdefault((block_country, int(level), "groups"), set())
            if group_key not in seen:
                toml_block["groups"].append(group_key)
                seen.add(group_key)
            continue

        subdivision_key = _derived_subdivision_raw_source_item_subdivision_key(item)
        if subdivision_key:
            block_country = _derived_subdivision_raw_source_item_country(item, fallback_country_code=fallback_country_code)
            try:
                level = int(item.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if not block_country or not level:
                continue
            toml_block = toml_block_for(block_country, int(level))
            seen = seen_values.setdefault((block_country, int(level), "derived_subdivisions"), set())
            if subdivision_key not in seen:
                toml_block["derived_subdivisions"].append(subdivision_key)
                seen.add(subdivision_key)
            continue

        item_id = str(item.get("id") or item.get("value") or "").strip()
        row = area_by_id.get(item_id)
        if not row:
            continue
        block_country = query_parameters.normalize_country_key(row.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level < 0:
            continue
        toml_block = toml_block_for(block_country, level)
        seen = seen_values.setdefault((block_country, level, "ids"), set())
        if item_id not in seen:
            toml_block["ids"].append(item_id)
            seen.add(item_id)
    fallback_country_code = query_parameters.normalize_country_key(fallback_country_code)
    return sorted(
        result.values(),
        key=lambda block: (
            0 if block.get("country_code") == fallback_country_code else 1,
            str(block.get("country_code") or ""),
            int(block.get("level") or 0),
        ),
    )


def _derived_subdivision_raw_source_item_country(item: dict, *, fallback_country_code: str) -> str:
    country_code = query_parameters.normalize_country_key(item.get("country_code") or fallback_country_code)
    item_id = str(item.get("id") or item.get("value") or "").strip()
    if item_id.startswith("group::") or item_id.startswith("derived-subdivision::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            country_code = query_parameters.normalize_country_key(parts[1] or country_code)
    return country_code


def _derived_subdivision_raw_source_item_group_key(item: dict) -> str:
    item_id = str(item.get("id") or item.get("value") or "").strip()
    source_kind = str(item.get("source_kind") or item.get("item_type") or "").strip().lower()
    is_group = source_kind in {"group", "subdivision_group"} or item_id.startswith("group::")
    if not is_group:
        return ""
    group_key = str(item.get("group_key") or item.get("internal_name") or item.get("code") or "").strip()
    if not group_key and item_id.startswith("group::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            group_key = parts[2]
    try:
        return _queries_groups._group_internal_name(group_key)
    except ValueError:
        return ""


def _derived_subdivision_raw_source_item_subdivision_key(item: dict) -> str:
    item_id = str(item.get("id") or item.get("value") or "").strip()
    source_kind = str(item.get("source_kind") or item.get("item_type") or "").strip().lower()
    is_subdivision = source_kind in {"derived_subdivision", "derived-subdivision"} or item_id.startswith("derived-subdivision::")
    if not is_subdivision:
        return ""
    subdivision_key = str(
        item.get("derived_subdivision_key")
        or item.get("internal_name")
        or item.get("code")
        or ""
    ).strip()
    if not subdivision_key and item_id.startswith("derived-subdivision::"):
        parts = item_id.split("::", 2)
        if len(parts) >= 3:
            subdivision_key = parts[2]
    try:
        return _queries_groups._group_internal_name(subdivision_key)
    except ValueError:
        return ""


def _derived_subdivision_toml_blocks_from_visual_source_json(value, *, fallback_country_code: str) -> list[dict]:
    raw_blocks = _derived_subdivision_raw_source_blocks_from_json(value)
    selected_ids = {
        str(selected.get("id") or "").strip()
        for block in raw_blocks
        if isinstance(block, dict)
        for section in block.get("sections") or []
        if isinstance(section, dict)
        for selected in section.get("selected") or []
        if (
            isinstance(selected, dict)
            and str(selected.get("id") or "").strip()
            and not _derived_subdivision_raw_source_item_group_key(selected)
            and not _derived_subdivision_raw_source_item_subdivision_key(selected)
        )
    }
    area_level_map = _derived_subdivision_selected_area_levels(selected_ids)
    result: dict[tuple[str, int], dict] = {}
    seen_values: dict[tuple[str, int, str], set[str]] = {}
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not block_country:
            continue
        for section in block.get("sections") or []:
            if not isinstance(section, dict):
                continue
            try:
                section_level = int(section.get("level") or 0)
            except (TypeError, ValueError):
                section_level = 0
            selected_values = section.get("selected") if isinstance(section.get("selected"), list) else []
            for selected in selected_values:
                if not isinstance(selected, dict):
                    continue
                selected_id = str(selected.get("id") or "").strip()
                selected_name = str(selected.get("name") or selected.get("label") or "").strip()
                try:
                    selected_level = int(selected.get("level") or 0)
                except (TypeError, ValueError):
                    selected_level = 0
                subdivision_key = _derived_subdivision_raw_source_item_subdivision_key(selected)
                if subdivision_key:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "derived_subdivisions")
                    seen = seen_values.setdefault(seen_key, set())
                    if subdivision_key not in seen:
                        toml_block["derived_subdivisions"].append(subdivision_key)
                        seen.add(subdivision_key)
                    continue
                group_key = _derived_subdivision_raw_source_item_group_key(selected)
                if group_key:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "groups")
                    seen = seen_values.setdefault(seen_key, set())
                    if group_key not in seen:
                        toml_block["groups"].append(group_key)
                        seen.add(group_key)
                    continue
                if selected_id:
                    selected_level = (
                        area_level_map[selected_id]
                        if selected_id in area_level_map
                        else selected_level or (section_level + 1 if section_level else 0)
                    )
                    if selected_level < 0:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "ids")
                    seen = seen_values.setdefault(seen_key, set())
                    if selected_id not in seen:
                        toml_block["ids"].append(selected_id)
                        seen.add(selected_id)
                elif selected_name:
                    selected_level = selected_level or (section_level + 1 if section_level else 0)
                    if not selected_level:
                        continue
                    toml_block = result.setdefault(
                        (block_country, selected_level),
                        {
                            "country_code": block_country,
                            "level": selected_level,
                            "names": [],
                            "ids": [],
                            "codes": [],
                            "groups": [],
                            "derived_subdivisions": [],
                            "expressions": [],
                        },
                    )
                    seen_key = (block_country, selected_level, "names")
                    seen = seen_values.setdefault(seen_key, set())
                    if selected_name not in seen:
                        toml_block["names"].append(selected_name)
                        seen.add(selected_name)
    return [result[key] for key in sorted(result)]


def _derived_subdivision_raw_source_blocks_from_json(value) -> list[dict]:
    if isinstance(value, str):
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    else:
        payload = value
    if payload in (None, ""):
        return []
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    return [item for item in payload if isinstance(item, dict)]


def _derived_subdivision_raw_source_items_from_json(value) -> list[dict]:
    if isinstance(value, str):
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    else:
        payload = value
    if payload in (None, ""):
        return []
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    items = []
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            country_code = query_parameters.normalize_country_key(entry.get("country_code") or "")
            for item in entry.get("items") or []:
                if isinstance(item, dict):
                    normalized = dict(item)
                    if country_code and not normalized.get("country_code"):
                        normalized["country_code"] = country_code
                    items.append(normalized)
                else:
                    item_id = str(item or "").strip()
                    if item_id:
                        items.append({"id": item_id, "country_code": country_code})
        elif isinstance(entry, dict):
            items.append(entry)
        else:
            item_id = str(entry or "").strip()
            if item_id:
                items.append({"id": item_id})
    return items


def _derived_subdivision_selected_area_levels(area_ids: set[str]) -> dict[str, int]:
    if not area_ids:
        return {}
    try:
        rows = list(area_selectors.group_source_admin_areas().filter(id__in=area_ids).values("id", "level"))
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    return {str(row["id"]): int(row.get("level") or 0) for row in rows}


def _derived_subdivision_group_country_sections(current_country_code: str) -> list[dict]:
    current_country_code = query_parameters.normalize_country_key(current_country_code)
    country_codes = {current_country_code}
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        country_codes.update(_queries_groups._subdivision_group_record_country_codes(group))
    sections = []
    for country_code in sorted(code for code in country_codes if code):
        entries = _queries_groups._subdivision_group_entries_for_country(_queries_groups._subdivision_groups_for_country(country_code), country_code)
        if not entries:
            continue
        sections.append(
            {
                "country_code": country_code,
                "country_label": _queries_browser._display_name("", country_code, country_code=country_code),
                "default_group_level": _derived_subdivision_group_level(country_code),
                "groups": entries,
            }
        )
    return sections


def _derived_subdivision_group_level(country_code: str) -> int:
    country_code = query_parameters.normalize_country_key(country_code)
    configured_level = effective_source_municipal_level(country_code)
    if configured_level is not None:
        return int(configured_level)
    try:
        level = (
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .order_by("-level")
            .values_list("level", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        level = None
    return int(level or 1)
