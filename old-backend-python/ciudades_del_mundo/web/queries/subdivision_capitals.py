"""Select and resolve capital options for derived subdivisions."""

from __future__ import annotations


from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from ciudades_del_mundo.application import export_metrics as _application_export_metrics

import re
import tomllib
import unicodedata
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web import view_support as _view_support


def _derived_subdivision_capital_queryset(country_code: str):
    country_code = query_parameters.normalize_country_key(country_code)
    queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code)
    level = _queries_subdivision_sources._derived_subdivision_group_level(country_code)
    if level is not None:
        queryset = queryset.filter(level=int(level))
    return queryset


def _derived_subdivision_capital_options(
    country_code: str,
    *,
    source_ids: set[str] | None = None,
    selected_ids=None,
    search_term: str = "",
    limit: int = 50,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code or source_ids == set():
        return []
    selected_ids = set(_normalized_derived_subdivision_capital_values(selected_ids))
    search_key = _derived_subdivision_capital_search_key(search_term)
    has_search = len(search_key) >= 2
    if not selected_ids and not has_search:
        return []
    try:
        queryset = _derived_subdivision_capital_queryset(country_code)
        if source_ids is not None:
            queryset = area_selectors.group_source_admin_areas().filter(id__in=source_ids)
        condition = Q()
        if selected_ids:
            condition |= Q(id__in=selected_ids)
        if has_search:
            if source_ids is None:
                first_letters = _derived_subdivision_capital_first_letter_filters(search_term)
                search_condition = Q()
                for first_letter in first_letters:
                    search_condition |= Q(name__istartswith=first_letter)
                condition |= search_condition
            else:
                condition |= Q(id__in=source_ids)
        rows = list(
            queryset.filter(condition)
            .only("id", "country_code", "code", "name", "level", "entity_type")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    options = []
    matched_count = 0
    seen_values = set()
    for area in rows:
        value = str(area.id)
        is_selected = value in selected_ids
        name = _queries_browser._area_display_name(area)
        if not is_selected:
            if not has_search or not _derived_subdivision_capital_name_matches(area, search_key, display_name=name):
                continue
            if matched_count >= limit:
                continue
            matched_count += 1
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append(_derived_subdivision_capital_option(area, display_name=name))
    return options


def _derived_subdivision_declared_capital_ids_for_key(
    country_code: str,
    subdivision_key: str,
) -> set[str]:
    record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(country_code, subdivision_key)
    if record is None:
        return set()
    data = _queries_subdivisions._derived_subdivision_record_toml(record)
    record_country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    if not record_country_code:
        return set()
    return _declared_capital_ids_from_data(record_country_code, data)


def _declared_capital_ids_from_data(country_code: str, data: dict) -> set[str]:
    country_code = query_parameters.normalize_country_key(country_code)
    capital_values = _derived_subdivision_capital_values(country_code, data)
    if not country_code or not capital_values:
        return set()
    capital_ids = {str(value).strip() for value in capital_values if str(value or "").strip()}
    if not capital_ids:
        return set()
    try:
        return {
            str(area_id)
            for chunk in _application_export_metrics._new_country_chunks(sorted(capital_ids), 900)
            for area_id in area_selectors.group_source_admin_areas()
            .filter(id__in=chunk)
            .values_list("id", flat=True)
        }
    except (OperationalError, ProgrammingError, ValueError):
        return set()


def _derived_subdivision_capital_search_options(
    country_code: str,
    *,
    content: str,
    selected_ids=None,
    search_term: str = "",
    limit: int = 50,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    search_key = _derived_subdivision_capital_search_key(search_term)
    selected_ids = set(_normalized_derived_subdivision_capital_values(selected_ids))
    if len(search_key) < 2:
        return _derived_subdivision_capital_options(country_code, selected_ids=selected_ids)
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        return _derived_subdivision_capital_options(country_code, selected_ids=selected_ids)
    target_level = _derived_subdivision_capital_target_level(country_code)
    include_scope = _derived_subdivision_capital_scope(
        data.get("include"),
        country_code=country_code,
        target_level=target_level,
    )
    if not _derived_subdivision_capital_scope_has_rules(include_scope):
        return []
    subtract_scope = _derived_subdivision_capital_scope(
        data.get("subtract"),
        country_code=country_code,
        target_level=target_level,
    )
    candidates = _derived_subdivision_capital_prefix_candidates(
        country_code,
        search_term=search_term,
        target_level=target_level,
    )
    seen_values = set()
    options = []
    matched_count = 0

    def collect(candidate_rows):
        nonlocal matched_count
        ancestor_map = _derived_subdivision_candidate_ancestor_ids(candidate_rows)
        for area in candidate_rows:
            if matched_count >= limit:
                break
            value = str(area.id)
            if value in seen_values:
                continue
            ancestor_ids = ancestor_map.get(value, {value})
            if not _derived_subdivision_capital_area_in_scope(area, include_scope, ancestor_ids):
                continue
            if _derived_subdivision_capital_area_in_scope(area, subtract_scope, ancestor_ids):
                continue
            name = _queries_browser._area_display_name(area)
            if not _derived_subdivision_capital_name_matches(area, search_key, display_name=name):
                continue
            options.append(_derived_subdivision_capital_option(area, display_name=name))
            seen_values.add(value)
            matched_count += 1

    collect(candidates)
    if matched_count < limit:
        collect(
            _derived_subdivision_capital_scoped_candidates(
                country_code,
                include_scope=include_scope,
                target_level=target_level,
                exclude_ids=seen_values,
            )
        )
    return options


def _derived_subdivision_capital_target_level(country_code: str) -> int:
    target_level = _queries_subdivision_sources._derived_subdivision_group_level(country_code)
    return int(target_level or 0)


def _derived_subdivision_capital_prefix_candidates(
    country_code: str,
    *,
    search_term: str,
    target_level: int,
):
    queryset = _derived_subdivision_capital_queryset(country_code)
    if target_level is not None:
        queryset = queryset.filter(level=target_level)
    first_letters = _derived_subdivision_capital_first_letter_filters(search_term)
    search_condition = Q()
    for first_letter in first_letters:
        search_condition |= Q(name__istartswith=first_letter)
    if not search_condition:
        return []
    try:
        return list(
            queryset.filter(search_condition)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_capital_scoped_candidates(
    country_code: str,
    *,
    include_scope: dict[str, set[str]],
    target_level: int,
    exclude_ids: set[str],
) -> list[AdminArea]:
    source_ids = set(include_scope.get("area_ids", set()))
    source_ids |= _derived_subdivision_descendant_ids_at_level(set(include_scope.get("parent_ids", set())), target_level)
    source_ids = {str(value) for value in source_ids if str(value or "").strip() and str(value) not in exclude_ids}
    if not source_ids:
        return []
    queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code, id__in=source_ids)
    if target_level is not None:
        queryset = queryset.filter(level=target_level)
    try:
        return list(
            queryset.only("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _derived_subdivision_capital_scope(blocks, *, country_code: str, target_level: int) -> dict[str, set[str]]:
    scope = {"area_ids": set(), "parent_ids": set()}
    if not isinstance(blocks, list):
        return scope
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or country_code)
        if block_country != country_code:
            continue
        try:
            level = int(block.get("level"))
        except (TypeError, ValueError):
            continue
        refs = _derived_subdivision_block_source_refs(block, country_code=block_country)
        areas = _derived_subdivision_source_ref_areas(block_country, level, refs)
        for area in areas:
            try:
                area_level = int(area.level or 0)
            except (TypeError, ValueError):
                continue
            area_id = str(area.id)
            if area_level == target_level:
                scope["area_ids"].add(area_id)
            elif area_level < target_level:
                scope["parent_ids"].add(area_id)
            else:
                ancestor = _derived_subdivision_ancestor_at_level(area, target_level)
                if ancestor and int(ancestor.city_merge_status or 0) in _view_support.GROUP_SOURCE_CITY_MERGE_STATUSES:
                    scope["area_ids"].add(str(ancestor.id))
    return scope


def _derived_subdivision_capital_scope_has_rules(scope: dict[str, set[str]]) -> bool:
    return bool(scope.get("area_ids") or scope.get("parent_ids"))


def _derived_subdivision_capital_area_in_scope(area: AdminArea, scope: dict[str, set[str]], ancestor_ids: set[str]) -> bool:
    if not _derived_subdivision_capital_scope_has_rules(scope):
        return False
    area_id = str(area.id)
    return area_id in scope.get("area_ids", set()) or bool(scope.get("parent_ids", set()) & ancestor_ids)


def _derived_subdivision_candidate_ancestor_ids(areas) -> dict[str, set[str]]:
    result = {str(area.id): {str(area.id)} for area in areas}
    parent_ids = {str(area.parent_id) for area in areas if getattr(area, "parent_id", None)}
    seen_parent_ids: set[str] = set()
    parent_map: dict[str, str] = {}
    while parent_ids:
        current = parent_ids - seen_parent_ids
        if not current:
            break
        seen_parent_ids |= current
        parents = list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=current)
            .only("id", "parent_id")
        )
        parent_ids = set()
        for parent in parents:
            parent_id = str(parent.id)
            parent_map[parent_id] = str(parent.parent_id or "")
            if parent.parent_id:
                parent_ids.add(str(parent.parent_id))
    for area in areas:
        value = str(area.id)
        parent_id = str(area.parent_id or "")
        seen = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            result[value].add(parent_id)
            parent_id = parent_map.get(parent_id, "")
    return result


def _derived_subdivision_capital_search_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_value).strip().casefold()


def _derived_subdivision_capital_name_matches(area: AdminArea, search_key: str, *, display_name: str = "") -> bool:
    for value in (getattr(area, "name", ""), display_name or _queries_browser._area_display_name(area)):
        for alias_key in _derived_subdivision_capital_search_alias_keys(value):
            if alias_key.startswith(search_key):
                return True
    return False


def _derived_subdivision_capital_search_alias_keys(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [text]
    parts.extend(re.split(r"\s*(?:/|\(|\)|\[|\]|;)\s*", text))
    parts.extend(re.split(r"\s+-\s+", text))
    keys = []
    seen = set()
    for part in parts:
        key = _derived_subdivision_capital_search_key(part)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _derived_subdivision_capital_option(area: AdminArea, *, display_name: str = "") -> dict:
    name = display_name or _queries_browser._area_display_name(area)
    return {
        "value": str(area.id),
        "label": name,
        "name": name,
        "code": str(area.code or "").strip(),
        "level": int(area.level or 0),
        "entity_type": str(area.entity_type or ""),
    }


def _derived_subdivision_capital_first_letter_filters(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    first = text[:1]
    normalized = _derived_subdivision_capital_search_key(first)[:1]
    filters = {first}
    accent_variants = {
        "a": "aáàâäãå",
        "c": "cç",
        "e": "eéèêë",
        "i": "iíìîï",
        "n": "nñ",
        "o": "oóòôöõ",
        "u": "uúùûü",
    }
    filters.update(accent_variants.get(normalized, normalized))
    return {item for item in filters if item}


def _derived_subdivision_capital_source_ids(country_code: str, content: str) -> set[str]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code or not str(content or "").strip():
        return set()
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        return set()
    include_ids = _derived_subdivision_blocks_source_ids(data.get("include"), default_country_code=country_code)
    subtract_ids = _derived_subdivision_blocks_source_ids(data.get("subtract"), default_country_code=country_code)
    return include_ids - subtract_ids


def _derived_subdivision_blocks_source_ids(
    blocks,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    if not isinstance(blocks, list):
        return set()
    result: set[str] = set()
    for block in blocks:
        if isinstance(block, dict):
            result |= _derived_subdivision_block_source_ids(
                block,
                default_country_code=default_country_code,
                seen=seen,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
                source_ids_cache=source_ids_cache,
            )
    return result


def _derived_subdivision_block_source_ids(
    block: dict,
    *,
    default_country_code: str,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    country_code = query_parameters.normalize_country_key(block.get("country_code") or default_country_code)
    if not country_code:
        return set()
    try:
        level = int(block.get("level"))
    except (TypeError, ValueError):
        return set()
    refs = _derived_subdivision_block_source_refs(
        block,
        country_code=country_code,
        seen=seen,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
        source_ids_cache=source_ids_cache,
    )
    areas = _derived_subdivision_source_ref_areas(country_code, level, refs)
    return _derived_subdivision_areas_to_capital_source_ids(areas, country_code=country_code)


def _derived_subdivision_block_source_refs(
    block: dict,
    *,
    country_code: str,
    expand_groups: bool = True,
    expand_derived_subdivisions: bool = True,
    seen: set[tuple[str, str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> dict[str, list[str]]:
    refs = {
        "ids": _derived_subdivision_list_values(block.get("ids")),
        "codes": _derived_subdivision_list_values(block.get("codes")),
        "names": _derived_subdivision_list_values(block.get("names")),
    }
    if not expand_groups and not expand_derived_subdivisions:
        return refs
    if expand_groups:
        for group_key in _derived_subdivision_list_values(block.get("groups")):
            group_entry = _queries_subdivision_sources._derived_subdivision_group_entry_cached(
                country_code,
                group_key,
                group_entry_cache=group_entry_cache,
                entries_cache=group_entries_cache,
            )
            if not group_entry:
                continue
            for group_block in group_entry.get("blocks") or []:
                if not isinstance(group_block, dict):
                    continue
                block_country = query_parameters.normalize_country_key(group_block.get("country_code") or country_code)
                if block_country != country_code:
                    continue
                group_refs = _derived_subdivision_group_block_refs(group_block)
                refs["ids"].extend(group_refs["ids"])
                refs["names"].extend(group_refs["names"])
    if expand_derived_subdivisions:
        for subdivision_key in _derived_subdivision_list_values(block.get("derived_subdivisions")):
            refs["ids"].extend(
                sorted(
                    _queries_subdivision_sources._derived_subdivision_source_ids_for_key(
                        country_code,
                        subdivision_key,
                        seen=seen,
                        source_ids_cache=source_ids_cache,
                        group_entry_cache=group_entry_cache,
                        group_entries_cache=group_entries_cache,
                    )
                )
            )
    return refs


def _derived_subdivision_group_block_refs(block: dict) -> dict[str, list[str]]:
    refs = {"ids": [], "names": []}
    subtract_ids = set()
    refs["names"].extend(_derived_subdivision_list_values(block.get("names")))
    for section in block.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for selected in section.get("selected") or []:
            if isinstance(selected, dict):
                selected_id = str(selected.get("id") or "").strip()
                selected_name = str(selected.get("name") or selected.get("label") or "").strip()
                if selected_id:
                    refs["ids"].append(selected_id)
                    if selected.get("operation") == "subtract":
                        subtract_ids.add(selected_id)
                elif selected_name:
                    refs["names"].append(selected_name)
            else:
                selected_name = str(selected or "").strip()
                if selected_name:
                    refs["names"].append(selected_name)
    if subtract_ids:
        from ciudades_del_mundo.services.composite_city_sources import selected_composite_members
        areas = list(AdminArea.objects.filter(id__in=refs["ids"]).select_related("parent"))
        refs["ids"] = [area.id for area in selected_composite_members(areas, subtract_ids)]
    return refs


def _derived_subdivision_source_ref_areas(country_code: str, level: int, refs: dict[str, list[str]]) -> list[AdminArea]:
    ids = {str(value).strip() for value in refs.get("ids") or [] if str(value or "").strip()}
    codes = [str(value).strip() for value in refs.get("codes") or [] if str(value or "").strip()]
    names = [str(value).strip() for value in refs.get("names") or [] if str(value or "").strip()]
    areas_by_id: dict[str, AdminArea] = {}
    base = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code)
    if ids:
        for area in base.filter(id__in=ids).only("id", "country_code", "code", "name", "level", "parent_id", "city_merge_status"):
            areas_by_id[str(area.id)] = area
    if codes or names:
        level_qs = base.filter(level=level)
        if len(codes) + len(names) <= 50:
            condition = Q()
            for code in codes:
                condition |= Q(code__iexact=code) | Q(id=code)
            for name in names:
                condition |= Q(name__iexact=name)
            rows = level_qs.filter(condition) if condition else AdminArea.objects.none()
        else:
            condition = Q()
            if codes:
                condition |= Q(code__in=codes) | Q(id__in=codes)
            if names:
                condition |= Q(name__in=names)
            rows = level_qs.filter(condition) if condition else AdminArea.objects.none()
        for area in rows.only("id", "country_code", "code", "name", "level", "parent_id", "city_merge_status"):
            areas_by_id[str(area.id)] = area
    return list(areas_by_id.values())


def _derived_subdivision_areas_to_capital_source_ids(areas: list[AdminArea], *, country_code: str) -> set[str]:
    target_level = _queries_subdivision_sources._derived_subdivision_group_level(country_code)
    target_level = int(target_level or 0)
    result: set[str] = set()
    parent_ids: set[str] = set()
    for area in areas:
        if area.city_merge_status == AdminArea.CityMergeStatus.SOURCE:
            from ciudades_del_mundo.services.composite_city_sources import composite_city
            if composite_city(area) is not None:
                result.add(str(area.id))
                continue
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            continue
        if level == target_level:
            if int(area.city_merge_status or 0) in _view_support.GROUP_SOURCE_CITY_MERGE_STATUSES:
                result.add(str(area.id))
        elif level < target_level:
            parent_ids.add(str(area.id))
        else:
            ancestor = _derived_subdivision_ancestor_at_level(area, target_level)
            if ancestor and int(ancestor.city_merge_status or 0) in _view_support.GROUP_SOURCE_CITY_MERGE_STATUSES:
                result.add(str(ancestor.id))
    result |= _derived_subdivision_descendant_ids_at_level(parent_ids, target_level)
    return result


def _derived_subdivision_descendant_ids_at_level(parent_ids: set[str], target_level: int) -> set[str]:
    result: set[str] = set()
    frontier = {str(parent_id) for parent_id in parent_ids if str(parent_id or "").strip()}
    seen: set[str] = set()
    while frontier:
        current = frontier - seen
        if not current:
            break
        seen |= current
        children = list(
            area_selectors.group_source_admin_areas()
            .filter(parent_id__in=current)
            .only("id", "level", "parent_id", "city_merge_status")
        )
        frontier = set()
        for child in children:
            try:
                level = int(child.level or 0)
            except (TypeError, ValueError):
                continue
            if level == target_level:
                result.add(str(child.id))
            elif level < target_level:
                frontier.add(str(child.id))
    return result


def _derived_subdivision_ancestor_at_level(area: AdminArea, target_level: int) -> AdminArea | None:
    parent_id = area.parent_id
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(str(parent_id))
        parent = area_selectors.group_source_admin_areas().filter(id=parent_id).only("id", "level", "parent_id", "city_merge_status").first()
        if parent is None:
            return None
        try:
            level = int(parent.level or 0)
        except (TypeError, ValueError):
            return None
        if level == target_level:
            return parent
        if level < target_level:
            return None
        parent_id = parent.parent_id
    return None


def _derived_subdivision_list_values(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalized_derived_subdivision_capital_values(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result = []
    seen = set()
    for raw_value in values:
        value = _derived_subdivision_capital_raw_value(raw_value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _derived_subdivision_capital_values_from_post(post_data) -> list[str]:
    values = post_data.getlist("capitals") if hasattr(post_data, "getlist") else []
    if not values:
        values = [post_data.get("capital")] if post_data.get("capital") not in (None, "") else []
    return _normalized_derived_subdivision_capital_values(values)


def _derived_subdivision_capital_values_from_payload(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if "capitals" in payload:
        return _normalized_derived_subdivision_capital_values(payload.get("capitals"))
    return _normalized_derived_subdivision_capital_values(payload.get("capital"))


def _derived_subdivision_capital_values(country_code: str, data: dict) -> list[str]:
    raw_values = data.get("capitals")
    if not isinstance(raw_values, list):
        raw_values = [data.get("capital")] if data.get("capital") not in (None, "") else []
    values = []
    seen = set()
    for value in _normalized_derived_subdivision_capital_values(raw_values):
        resolved = _resolve_derived_subdivision_capital_value(country_code, value) or value
        if resolved and resolved not in seen:
            seen.add(resolved)
            values.append(resolved)
    return values


def _derived_subdivision_capital_value(country_code: str, data: dict) -> str:
    values = _derived_subdivision_capital_values(country_code, data)
    return values[0] if values else ""


def _derived_subdivision_capital_raw_value(raw_value) -> str:
    if isinstance(raw_value, dict):
        for key in ("id", "code", "name", "label"):
            if raw_value.get(key) not in (None, ""):
                raw_value = raw_value.get(key)
                break
        else:
            raw_value = ""
    return str(raw_value or "").strip()


def _resolve_derived_subdivision_capital_value(country_code: str, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        queryset = _derived_subdivision_capital_queryset(country_code)
        candidate = queryset.filter(id=value).first()
        if candidate:
            return str(candidate.id)
        candidate = queryset.filter(code__iexact=value).first() or queryset.filter(name__iexact=value).first()
        if candidate:
            return str(candidate.id)
        value_key = _queries_groups._group_name_match_key(value)
        for area in queryset.only("id", "code", "name"):
            if value_key in {
                _queries_groups._group_name_match_key(area.id),
                _queries_groups._group_name_match_key(area.code),
                _queries_groups._group_name_match_key(area.name),
            }:
                return str(area.id)
    except (OperationalError, ProgrammingError, ValueError):
        return ""
    return ""
