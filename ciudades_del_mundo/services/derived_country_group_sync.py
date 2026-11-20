"""Synchronize derived-country group moves across sibling configs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.utils.translation import gettext as _
import tomllib

from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    DerivedSubdivision,
    SubdivisionGroup,
)
from ciudades_del_mundo.services.derived_config_seeds import (
    _clean_country_code,
    _group_selected_names_from_seed_data,
    _safe_group_toml_key,
    _toml_array,
    _toml_string,
    _top_level_group_assignments,
)

AUTO_SUBTRACT_MARKER = "auto_generated"
AUTO_SUBTRACT_SOURCE = "auto_source_config_slug"


@dataclass(frozen=True)
class GroupRef:
    country_code: str
    level: int
    group_key: str


@dataclass(frozen=True)
class GroupUse:
    ref: GroupRef
    config_slug: str


@transaction.atomic
def sync_automatic_group_subtractions(
    country: DerivedCountry,
    *,
    previous_contents: dict[str, str] | None = None,
) -> list[str]:
    """Apply automatic source-owner subtractions for moved groups.

    A group included in one config represents a moved chunk of source territory.
    If the group's member areas belong under another sibling config's direct
    source include, that owner config gets an automatic ``entities.subtract``
    block. Only blocks tagged with ``auto_generated = true`` are removed by this
    sync, so manual subtractions survive when the move is undone.
    """
    configs = list(country.configs.select_for_update().order_by("slug"))
    if not configs:
        return []

    subtract_blocks_by_slug: dict[str, list[tuple[dict[str, Any], str]]] = {}
    include_uses: list[GroupUse] = []
    include_owners: dict[tuple[str, str], list[str]] = {}
    owner_by_source_area: dict[str, set[str]] = {}
    subdivision_cache: dict[tuple[str, str], tuple[dict[str, Any], str] | None] = {}

    for config in configs:
        data = _parse_config_content(config)
        default_country = _config_source_country(config, data)
        include_blocks, subtract_blocks = _resolved_config_blocks(
            data,
            default_country=default_country,
            subdivision_cache=subdivision_cache,
            label=config.full_slug,
        )
        subtract_blocks_by_slug[config.slug] = subtract_blocks
        for block, block_default_country in include_blocks:
            country_code = _block_country(block, block_default_country)
            level = _block_level(block)
            if not country_code or level is None:
                continue
            for group_key in _block_group_keys(block):
                ref = GroupRef(country_code=country_code, level=level, group_key=group_key)
                include_uses.append(GroupUse(ref=ref, config_slug=config.slug))
                include_owners.setdefault((country_code, group_key), []).append(config.slug)
            for area in _direct_block_areas(block, country_code=country_code, level=level):
                owner_by_source_area.setdefault(str(area.id), set()).add(config.slug)

    _validate_unique_group_includes(include_owners, country)

    desired_by_target: dict[str, dict[GroupRef, str]] = {}
    group_area_cache: dict[GroupRef, list[AdminArea]] = {}
    for use in include_uses:
        member_areas = group_area_cache.setdefault(use.ref, _group_member_areas(use.ref))
        for area in member_areas:
            for owner_slug in _source_owner_slugs(area, owner_by_source_area):
                if owner_slug == use.config_slug:
                    continue
                desired_by_target.setdefault(owner_slug, {})[use.ref] = use.config_slug

    previous_manual_refs_by_slug = _previous_manual_refs_by_slug(
        country,
        previous_contents or {},
        subdivision_cache=subdivision_cache,
    )

    changed_slugs: list[str] = []
    for config in configs:
        manual_subtract_refs = _manual_subtract_group_refs(subtract_blocks_by_slug.get(config.slug, []))
        desired_sources = desired_by_target.get(config.slug, {})
        desired_refs = set(desired_sources)
        locked_manual_refs = previous_manual_refs_by_slug.get(config.slug, set()).intersection(desired_refs)
        new_content = _replace_auto_subtract_blocks(
            config.content,
            desired_refs=desired_refs,
            desired_sources=desired_sources,
            manual_subtract_refs=manual_subtract_refs,
            locked_manual_refs=locked_manual_refs,
        )
        if new_content != config.content:
            _validate_config_content(new_content, config=config)
            config.content = new_content
            config.save(update_fields=["content", "updated_at"])
            changed_slugs.append(config.slug)
    return changed_slugs


def _validate_unique_group_includes(
    include_owners: dict[tuple[str, str], list[str]],
    country: DerivedCountry,
) -> None:
    for (country_code, group_key), slugs in sorted(include_owners.items()):
        if len(slugs) <= 1:
            continue
        owners = ", ".join(sorted(slugs))
        raise ValueError(
            _(
                "El grupo %(group)s de %(country)s ya esta asignado en este pais nuevo "
                "a %(owners)s. Quita esa asignacion antes de usarlo en otra entidad."
            )
            % {
                "group": group_key,
                "country": country_code or country.slug,
                "owners": owners,
            }
        )


def _parse_config_content(config: DerivedCountryConfig) -> dict[str, Any]:
    try:
        data = tomllib.loads(config.content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            _("%(config)s: TOML invalido: %(error)s")
            % {"config": config.full_slug, "error": exc}
        ) from exc
    return data if isinstance(data, dict) else {}


def _validate_config_content(content: str, *, config: DerivedCountryConfig) -> None:
    try:
        tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            _("%(config)s: no se pudo sincronizar la resta automatica: %(error)s")
            % {"config": config.full_slug, "error": exc}
        ) from exc


def _entities(data: dict[str, Any]) -> list[dict[str, Any]]:
    entities = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(entities, list):
        return []
    return [entity for entity in entities if isinstance(entity, dict)]


def _resolved_config_blocks(
    data: dict[str, Any],
    *,
    default_country: str,
    subdivision_cache: dict[tuple[str, str], tuple[dict[str, Any], str] | None],
    label: str,
) -> tuple[list[tuple[dict[str, Any], str]], list[tuple[dict[str, Any], str]]]:
    include_blocks: list[tuple[dict[str, Any], str]] = []
    subtract_blocks: list[tuple[dict[str, Any], str]] = []
    for entity in _entities(data):
        _collect_resolved_blocks(
            entity,
            default_country=default_country,
            include_blocks=include_blocks,
            subtract_blocks=subtract_blocks,
            subdivision_cache=subdivision_cache,
            stack=set(),
            label=label,
        )
    return include_blocks, subtract_blocks


def _collect_resolved_blocks(
    node: dict[str, Any],
    *,
    default_country: str,
    include_blocks: list[tuple[dict[str, Any], str]],
    subtract_blocks: list[tuple[dict[str, Any], str]],
    subdivision_cache: dict[tuple[str, str], tuple[dict[str, Any], str] | None],
    stack: set[tuple[str, str]],
    label: str,
) -> None:
    node_default_country = _clean_country_code(node.get("source_country_code") or default_country)
    for block in _blocks(node, "include"):
        include_blocks.append((block, node_default_country))
        block_country = _block_country(block, node_default_country)
        for subdivision_key in _block_derived_subdivision_keys(block):
            cache_key = (block_country, subdivision_key)
            if cache_key in stack:
                raise ValueError(
                    _("%(label)s tiene una referencia circular a la subdivision %(subdivision)s.")
                    % {"label": label, "subdivision": subdivision_key}
                )
            resolved = _derived_subdivision_data(
                block_country,
                subdivision_key,
                cache=subdivision_cache,
            )
            if resolved is None:
                continue
            subdivision_data, subdivision_country = resolved
            _collect_resolved_blocks(
                subdivision_data,
                default_country=subdivision_country,
                include_blocks=include_blocks,
                subtract_blocks=subtract_blocks,
                subdivision_cache=subdivision_cache,
                stack={*stack, cache_key},
                label=label,
            )
    for block in _blocks(node, "subtract"):
        subtract_blocks.append((block, node_default_country))
    for child in node.get("children") if isinstance(node.get("children"), list) else []:
        if not isinstance(child, dict):
            continue
        _collect_resolved_blocks(
            child,
            default_country=node_default_country,
            include_blocks=include_blocks,
            subtract_blocks=subtract_blocks,
            subdivision_cache=subdivision_cache,
            stack=set(stack),
            label=label,
        )


def _block_derived_subdivision_keys(block: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in _list_value(block.get("derived_subdivisions")):
        key = _safe_group_toml_key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _derived_subdivision_data(
    country_code: str,
    subdivision_key: str,
    *,
    cache: dict[tuple[str, str], tuple[dict[str, Any], str] | None],
) -> tuple[dict[str, Any], str] | None:
    country_code = _clean_country_code(country_code)
    subdivision_key = _safe_group_toml_key(subdivision_key)
    cache_key = (country_code, subdivision_key)
    if cache_key in cache:
        return cache[cache_key]
    try:
        records = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
        for record in records:
            candidates = {
                record.internal_name,
                record.slug,
                record.slug.removeprefix(f"{country_code}_"),
            }
            if not any(
                _safe_group_toml_key(candidate) == subdivision_key
                for candidate in candidates
                if str(candidate or "").strip()
            ):
                continue
            data = _parse_derived_subdivision_content(record)
            resolved_country = _clean_country_code(
                data.get("source_country_code") or record.source_country_code or country_code
            )
            cache[cache_key] = (data, resolved_country)
            return cache[cache_key]
    except (OperationalError, ProgrammingError):
        cache[cache_key] = None
        return None
    cache[cache_key] = None
    return None


def _parse_derived_subdivision_content(record: DerivedSubdivision) -> dict[str, Any]:
    try:
        data = tomllib.loads(record.content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            _("%(config)s: TOML invalido: %(error)s")
            % {"config": record.slug, "error": exc}
        ) from exc
    return data if isinstance(data, dict) else {}


def _config_source_country(config: DerivedCountryConfig, data: dict[str, Any]) -> str:
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    return _clean_country_code(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )


def _blocks(entity: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = entity.get(key) if isinstance(entity, dict) else None
    if not isinstance(value, list):
        return []
    return [block for block in value if isinstance(block, dict)]


def _block_country(block: dict[str, Any], default_country: str) -> str:
    return _clean_country_code(block.get("country_code") or default_country)


def _block_level(block: dict[str, Any]) -> int | None:
    try:
        level = int(block.get("level"))
    except (TypeError, ValueError):
        return None
    return level if level >= 0 else None


def _list_value(value) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _block_group_keys(block: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in _list_value(block.get("groups")):
        key = _safe_group_toml_key(value)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _direct_block_areas(block: dict[str, Any], *, country_code: str, level: int) -> list[AdminArea]:
    ids = set(_list_value(block.get("ids")))
    codes = set(_list_value(block.get("codes")))
    names = set(_list_value(block.get("names")))
    if not (ids or codes or names):
        return []
    try:
        queryset = AdminArea.objects.filter(country_code__iexact=country_code, level=level)
        condition = Q()
        if ids:
            condition |= Q(id__in=ids)
        if codes:
            condition |= Q(code__in=codes)
        if names:
            condition |= Q(name__in=names)
        return list(queryset.filter(condition).select_related("parent"))
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _manual_subtract_group_refs(
    blocks: list[tuple[dict[str, Any], str]],
) -> set[GroupRef]:
    refs: set[GroupRef] = set()
    for block, default_country in blocks:
        if _is_auto_subtract_block(block):
            continue
        country_code = _block_country(block, default_country)
        level = _block_level(block)
        if not country_code or level is None:
            continue
        refs.update(GroupRef(country_code, level, group_key) for group_key in _block_group_keys(block))
    return refs


def _previous_manual_refs_by_slug(
    country: DerivedCountry,
    previous_contents: dict[str, str],
    *,
    subdivision_cache: dict[tuple[str, str], tuple[dict[str, Any], str] | None],
) -> dict[str, set[GroupRef]]:
    result: dict[str, set[GroupRef]] = {}
    for slug, content in previous_contents.items():
        config = country.configs.filter(slug=slug).first()
        if config is None:
            continue
        try:
            data = tomllib.loads(str(content or ""))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(
                _("%(config)s: TOML invalido: %(error)s")
                % {"config": config.full_slug, "error": exc}
            ) from exc
        default_country = _config_source_country(config, data)
        _include_blocks, subtract_blocks = _resolved_config_blocks(
            data,
            default_country=default_country,
            subdivision_cache=subdivision_cache,
            label=config.full_slug,
        )
        result[slug] = _manual_subtract_group_refs(subtract_blocks)
    return result


def _is_auto_subtract_block(block: dict[str, Any]) -> bool:
    return str(block.get(AUTO_SUBTRACT_MARKER) or "").strip().lower() in {"1", "true", "yes", "on"}


def _group_member_areas(ref: GroupRef) -> list[AdminArea]:
    names, ids = _group_member_names_and_ids(ref.country_code, ref.group_key)
    if not names and not ids:
        return []
    normalized_names = {name.casefold() for name in names}
    normalized_ids = {str(value) for value in ids}
    try:
        candidates = list(
            AdminArea.objects.filter(country_code__iexact=ref.country_code, level=ref.level)
            .select_related("parent", "parent__parent", "parent__parent__parent", "parent__parent__parent__parent")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    result: list[AdminArea] = []
    seen: set[str] = set()
    for area in candidates:
        area_id = str(area.id)
        keys = {
            area_id,
            str(area.code or ""),
            str(area.name or "").casefold(),
        }
        if normalized_ids.isdisjoint(keys) and normalized_names.isdisjoint(keys):
            continue
        if area_id in seen:
            continue
        seen.add(area_id)
        result.append(area)
    return result


def _group_member_names_and_ids(country_code: str, group_key: str) -> tuple[list[str], list[str]]:
    try:
        groups = SubdivisionGroup.objects.filter(source_country_code__iexact=country_code).order_by("slug")
        if not groups.exists():
            groups = SubdivisionGroup.objects.order_by("slug")
        for group in groups:
            data = _parse_group_content(group)
            if not _group_matches(group, data, country_code=country_code, group_key=group_key):
                continue
            internal_name = _safe_group_toml_key(
                data.get("internal_name") or _single_group_assignment_name(data) or group.name or group.slug
            )
            return (
                _group_selected_names_from_seed_data(data, internal_name=internal_name),
                _group_selected_ids_from_seed_data(data),
            )
    except (OperationalError, ProgrammingError, ValueError):
        return [], []
    return [], []


def _parse_group_content(group: SubdivisionGroup) -> dict[str, Any]:
    try:
        data = tomllib.loads(group.content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _group_matches(group: SubdivisionGroup, data: dict[str, Any], *, country_code: str, group_key: str) -> bool:
    source_country = _clean_country_code(group.source_country_code or data.get("source_country_code") or "")
    if source_country and source_country != country_code:
        return False
    candidates = {
        group.name,
        group.slug,
        group.slug.removeprefix(f"{country_code}_"),
        data.get("internal_name"),
        data.get("entry_slug"),
        data.get("slug"),
    }
    candidates.update(key for key, _values in _top_level_group_assignments(data))
    return any(_safe_group_toml_key(candidate) == group_key for candidate in candidates if candidate)


def _single_group_assignment_name(data: dict[str, Any]) -> str:
    assignments = _top_level_group_assignments(data)
    return assignments[0][0] if len(assignments) == 1 else ""


def _group_selected_ids_from_seed_data(data: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def add_many(values) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)

    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    country_groups_sources = []
    country_groups = selection.get("country_groups") if isinstance(selection, dict) else None
    if isinstance(country_groups, list):
        country_groups_sources.append(country_groups)
    root_country_groups = data.get("country_groups") if isinstance(data.get("country_groups"), list) else None
    if isinstance(root_country_groups, list):
        country_groups_sources.append(root_country_groups)
    for country_groups in country_groups_sources:
        for block in country_groups:
            if not isinstance(block, dict):
                continue
            sections = block.get("sections") or []
            if not isinstance(sections, list):
                continue
            for section in sections:
                if not isinstance(section, dict):
                    continue
                add_many(section.get("selected_ids") or [])
                selected = section.get("selected") or []
                if isinstance(selected, list):
                    add_many([item.get("id") for item in selected if isinstance(item, dict)])
    return result


def _source_owner_slugs(area: AdminArea, owner_by_source_area: dict[str, set[str]]) -> set[str]:
    owners: set[str] = set()
    current: AdminArea | None = area
    while current is not None:
        owners.update(owner_by_source_area.get(str(current.id), set()))
        current = current.parent
    return owners


def _replace_auto_subtract_blocks(
    content: str,
    *,
    desired_refs: set[GroupRef],
    desired_sources: dict[GroupRef, str],
    manual_subtract_refs: set[GroupRef],
    locked_manual_refs: set[GroupRef],
) -> str:
    cleaned = _remove_auto_subtract_blocks(content)
    restored_manual_refs = locked_manual_refs.difference(manual_subtract_refs)
    auto_refs = desired_refs.difference(manual_subtract_refs, locked_manual_refs)
    manual_blocks = [
        _manual_subtract_block(ref)
        for ref in sorted(restored_manual_refs, key=_group_ref_sort_key)
    ]
    auto_blocks = [
        _auto_subtract_block(ref, source_slug=desired_sources.get(ref, ""))
        for ref in sorted(auto_refs, key=_group_ref_sort_key)
    ]
    appended_blocks = [*manual_blocks, *auto_blocks]
    if not appended_blocks:
        return cleaned
    base = cleaned.rstrip()
    return (base + "\n\n" if base else "") + "\n\n".join(appended_blocks) + "\n"


def _remove_auto_subtract_blocks(content: str) -> str:
    lines = str(content or "").splitlines()
    result: list[str] = []
    block: list[str] = []
    block_header = ""

    def flush_block() -> None:
        nonlocal block, block_header
        if not block:
            return
        if not (_is_entities_subtract_header(block_header) and _block_text_is_auto(block)):
            result.extend(block)
        block = []
        block_header = ""

    for line in lines:
        if line.strip().startswith("["):
            flush_block()
            block_header = line.strip()
            block = [line]
            continue
        if block:
            block.append(line)
        else:
            result.append(line)
    flush_block()
    while result and not result[-1].strip():
        result.pop()
    return "\n".join(result) + ("\n" if result else "")


def _is_entities_subtract_header(header: str) -> bool:
    return header.strip() == "[[entities.subtract]]"


def _block_text_is_auto(block: list[str]) -> bool:
    marker_re = re.compile(rf"^\s*{re.escape(AUTO_SUBTRACT_MARKER)}\s*=\s*true\s*(#.*)?$", re.IGNORECASE)
    return any(marker_re.match(line) for line in block)


def _manual_subtract_block(ref: GroupRef) -> str:
    return "\n".join(
        [
            "[[entities.subtract]]",
            f"country_code = {_toml_string(ref.country_code)}",
            f"level = {ref.level}",
            f"groups = {_toml_array([ref.group_key])}",
        ]
    )


def _auto_subtract_block(ref: GroupRef, *, source_slug: str) -> str:
    lines = [
        "[[entities.subtract]]",
        f"country_code = {_toml_string(ref.country_code)}",
        f"level = {ref.level}",
        f"groups = {_toml_array([ref.group_key])}",
        f"{AUTO_SUBTRACT_MARKER} = true",
    ]
    if source_slug:
        lines.append(f"{AUTO_SUBTRACT_SOURCE} = {_toml_string(source_slug)}")
    return "\n".join(lines)


def _group_ref_sort_key(ref: GroupRef) -> tuple[str, int, str]:
    return (ref.country_code, ref.level, ref.group_key)
