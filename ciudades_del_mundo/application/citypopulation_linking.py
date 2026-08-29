"""Cross-page linker for modular CityPopulation scraping.

CityPopulation often repeats the same administrative row in two consecutive
blocks: one page persists it as the lower section of the previous branch and the
next page uses it as the upper section for its children.  This module collapses
those equivalent rows only when they represent the **same level**, then rewires
children to the kept row.

Matching priority is intentionally simple and documented:
1. ``data_wd`` (Wikidata QID) when both rows have it.
2. CityPopulation internal ``code`` when QID is missing.

Rows at different levels are never collapsed.  That keeps explicit repetitions,
like the Brussels district duplicated at LV2 and LV3, intact.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Iterable
from urllib.parse import unquote, urlparse

from ciudades_del_mundo.domain import ScrapedAdminArea


def normalize_citypopulation_entities(
    country_code: str,
    entities: Iterable[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    """Return deterministic, linked entities ready for repository saving."""
    scoped = [entity for entity in entities if entity.country_code == country_code]
    scoped = _align_repeated_block_levels(scoped)
    scoped = _attach_parentless_same_qid_children(scoped)
    scoped = _normalize_child_levels(scoped)
    scoped = _drop_context_country_code_rows(country_code, scoped)
    scoped = _normalize_country_parent_aliases(country_code, scoped)
    scoped = _disambiguate_conflicting_codes(scoped)
    scoped = _attach_parentless_disambiguated_code_children(scoped)
    scoped = _apply_forced_parent_levels(scoped)
    scoped = _collapse_equivalent_rows(scoped)
    scoped = _collapse_equivalent_rows(scoped)
    scoped = _attach_rootless_country_children(country_code, scoped)
    scoped = _repair_explicit_repeated_roots(country_code, scoped)
    scoped = _attach_sum_to_root_entities(country_code, scoped)
    scoped = _apply_forced_parent_levels(scoped)
    scoped = _apply_parent_name_hints(scoped)
    scoped = _rewire_parent_codes(scoped)
    scoped = _normalize_child_levels(scoped)
    scoped = _collapse_equivalent_rows(scoped)
    scoped = _drop_cross_level_shortcuts(scoped)
    scoped = _attach_parentless_disambiguated_code_children(scoped)
    scoped = _repair_self_parent_links(country_code, scoped)
    scoped = _roll_up_sum_to_root_metrics(country_code, scoped)
    scoped = _fill_country_root_metrics(country_code, scoped)
    return scoped


def unlinked_entities(country_code: str, entities: Iterable[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Rows with a parent_code that does not resolve to another scraped row."""
    scoped = [entity for entity in entities if entity.country_code == country_code]
    by_code = {entity.code for entity in scoped}
    root = _preferred_country_root(country_code, scoped)
    return [
        entity
        for entity in scoped
        if (
            entity.parent_code
            and entity.parent_code not in by_code
            or (
                not entity.parent_code
                and root is not None
                and str(entity.code) != str(root.code)
                and int(entity.level) > int(root.level)
            )
        )
    ]


def apply_runtime_config_extensions(config, entities: Iterable[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Apply optional SQL/TOML post-processing hints attached by composition roots."""
    scoped = list(entities)
    scoped = _apply_runtime_synthetic_entities(config, scoped)
    scoped = _apply_runtime_parent_overrides(config, scoped)
    scoped = _apply_runtime_root_metric_sources(config, scoped)
    return scoped


def _drop_context_country_code_rows(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Remove page-context rows that reuse the config slug below the real root."""
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities

    kept: list[ScrapedAdminArea] = []
    active_context_alias: str | None = None
    active_context_level: int | None = None
    for entity in entities:
        if str(entity.code).casefold() == str(country_code).casefold() and entity is not root and int(entity.level) > 0:
            replacement = _context_country_row_replacement(entity, entities)
            if replacement is not None:
                active_context_alias = str(replacement.code)
                active_context_level = int(entity.level)
            elif str(root.code).casefold() != str(entity.code).casefold():
                active_context_alias = str(root.code)
                active_context_level = int(entity.level)
            else:
                active_context_alias = None
                active_context_level = None
            continue
        if (
            active_context_alias
            and active_context_level is not None
            and str(entity.parent_code or "").casefold() == str(country_code).casefold()
            and int(entity.level) > active_context_level
        ):
            entity = replace(entity, parent_code=active_context_alias)
        elif active_context_alias and not entity.parent_code and active_context_level is not None and int(entity.level) <= active_context_level:
            active_context_alias = None
            active_context_level = None
        kept.append(entity)
    return kept


def _context_country_row_replacement(
    entity: ScrapedAdminArea,
    entities: list[ScrapedAdminArea],
) -> ScrapedAdminArea | None:
    if _qid(entity.data_wd):
        return None
    candidates = [
        candidate
        for candidate in entities
        if candidate is not entity
        and int(candidate.level) == int(entity.level)
        and str(candidate.code) != str(entity.code)
        and _identity_name(candidate.name) == _identity_name(entity.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: _duplicate_score(candidate, entities))


def _normalize_country_parent_aliases(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Rewrite parent references to the configured slug when the real root has another code."""
    root = _preferred_country_root(country_code, entities)
    if root is None or str(root.code).casefold() == str(country_code).casefold():
        return entities
    has_country_code_row = any(
        str(entity.code).casefold() == str(country_code).casefold() and int(entity.level) == 0
        for entity in entities
    )
    if has_country_code_row:
        return entities
    return [
        replace(entity, parent_code=root.code)
        if str(entity.parent_code or "").casefold() == str(country_code).casefold()
        else entity
        for entity in entities
    ]


def _disambiguate_conflicting_codes(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Keep CityPopulation code reuse from merging unrelated rows.

    Most CityPopulation ids are stable within a country, but some pages reuse a
    short code for a different level or branch.  France is a common shape:
    region ``163`` and an arrondissement ``163`` can both appear in one scrape.
    The repository only allows one ``country_code + code`` row, so incompatible
    later occurrences receive a deterministic parent-scoped code while children
    parsed after that occurrence follow the new alias.
    """
    owners_by_raw_code: dict[str, list[ScrapedAdminArea]] = {}
    active_parent_alias: dict[str, str] = {}
    used_codes: set[str] = set()
    updated: list[ScrapedAdminArea] = []

    for entity in entities:
        raw_code = str(entity.code)
        parent_code = _resolve_replacement(entity.parent_code, active_parent_alias)
        entity = replace(entity, parent_code=parent_code) if parent_code != entity.parent_code else entity

        owners = owners_by_raw_code.get(raw_code, [])
        compatible = next((owner for owner in owners if _same_scraped_identity(owner, entity)), None)
        if compatible is not None:
            if str(compatible.code) != raw_code:
                entity = replace(entity, code=str(compatible.code))
            active_parent_alias[raw_code] = str(entity.code)
            used_codes.add(str(entity.code))
            updated.append(entity)
            continue

        if owners:
            new_code = _conflict_code(entity, used_codes)
            entity = replace(entity, code=new_code)
            active_parent_alias[raw_code] = new_code
        else:
            active_parent_alias[raw_code] = raw_code

        owners_by_raw_code.setdefault(raw_code, []).append(entity)
        used_codes.add(str(entity.code))
        updated.append(entity)
    return updated


def _attach_parentless_disambiguated_code_children(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Use parent-scoped conflict codes as a last-resort parent hint.

    ``_disambiguate_conflicting_codes`` creates ids such as ``17_174`` when a
    CityPopulation row with raw code ``174`` must be kept separate under parent
    ``17``.  Later duplicate/linking passes can leave that row parentless while
    keeping the conflict-safe code.  The scoped code itself is then the most
    reliable generic hint: attach ``17_174`` below ``17`` and shift its subtree.
    """
    by_code = {str(entity.code): entity for entity in entities}
    children_by_parent: dict[str, list[ScrapedAdminArea]] = {}
    for entity in entities:
        if entity.parent_code:
            children_by_parent.setdefault(str(entity.parent_code), []).append(entity)

    parent_by_code: dict[str, str] = {}
    delta_by_code: dict[str, int] = {}
    for entity in entities:
        if entity.parent_code or int(entity.level) == 0:
            continue
        parent = _disambiguated_code_parent(entity, by_code)
        if parent is None:
            continue
        code = str(entity.code)
        parent_by_code[code] = str(parent.code)
        delta = int(parent.level) + 1 - int(entity.level)
        stack = [entity]
        while stack:
            current = stack.pop()
            current_code = str(current.code)
            if current_code in delta_by_code:
                continue
            delta_by_code[current_code] = delta
            stack.extend(children_by_parent.get(current_code, ()))

    if not parent_by_code:
        return entities

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        code = str(entity.code)
        delta = delta_by_code.get(code, 0)
        parent_code = parent_by_code.get(code, entity.parent_code)
        if delta or parent_code != entity.parent_code:
            updated.append(replace(entity, level=int(entity.level) + delta, parent_code=parent_code))
        else:
            updated.append(entity)
    return updated


def _disambiguated_code_parent(
    entity: ScrapedAdminArea,
    by_code: dict[str, ScrapedAdminArea],
) -> ScrapedAdminArea | None:
    code = str(entity.code or "")
    if "_" not in code:
        return None
    parts = [part for part in code.split("_") if part]
    if len(parts) < 2:
        return None
    for index in range(len(parts) - 1, 0, -1):
        parent_code = "_".join(parts[:index])
        if str(parent_code).casefold() == str(entity.country_code).casefold():
            continue
        parent = by_code.get(parent_code)
        if parent is None or parent is entity:
            continue
        if _parent_chain_reaches(parent, entity, by_code):
            continue
        return parent
    return None


def _same_scraped_identity(first: ScrapedAdminArea, second: ScrapedAdminArea) -> bool:
    first_qid = _qid(first.data_wd)
    second_qid = _qid(second.data_wd)
    if first_qid and second_qid:
        return first_qid == second_qid
    if int(first.level) == int(second.level):
        return True
    first_name = _identity_name(first.name)
    second_name = _identity_name(second.name)
    return bool(first_name and second_name and first_name == second_name)


def _conflict_code(entity: ScrapedAdminArea, used_codes: set[str]) -> str:
    raw = str(entity.code)
    parent = str(entity.parent_code or "").strip()
    base = f"{parent}_{raw}" if parent else f"{raw}__l{int(entity.level)}"
    candidate = base
    index = 2
    while candidate in used_codes:
        candidate = f"{base}__dup{index}"
        index += 1
    return candidate


def _attach_parentless_same_qid_children(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    previous_by_wd: dict[str, ScrapedAdminArea] = {}
    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        wd = _qid(entity.data_wd)
        anchor = previous_by_wd.get(wd) if wd else None
        if (
            anchor is not None
            and not entity.parent_code
            and int(entity.level) <= int(anchor.level)
            and _identity_name(entity.name) != _identity_name(anchor.name)
        ):
            entity = replace(entity, parent_code=anchor.code, level=int(anchor.level) + 1)
        updated.append(entity)
        if wd:
            previous_by_wd.setdefault(wd, entity)
    return updated


def _apply_runtime_synthetic_entities(config, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    specs = tuple(getattr(config, "runtime_synthetic_entities", ()) or ())
    if not specs:
        return entities
    country_code = str(getattr(config, "country_code", "") or "")
    by_code = {str(entity.code): entity for entity in entities}
    updated = [entity for entity in entities if str(entity.code) not in {str(spec.get("code")) for spec in specs}]

    for spec in specs:
        code = str(spec.get("code") or "").strip()
        name = str(spec.get("name") or code).strip()
        if not code or not name:
            continue
        metric_source = by_code.get(str(spec.get("copy_metrics_from") or ""))
        metric_sources = [by_code[item] for item in spec.get("metric_source_codes", ()) or () if str(item) in by_code]
        if spec.get("metric_source_parent_code"):
            metric_sources.extend(
                entity for entity in entities if str(entity.parent_code or "") == str(spec["metric_source_parent_code"])
            )
        if spec.get("metric_source_level") is not None:
            metric_sources.extend(entity for entity in entities if int(entity.level) == int(spec["metric_source_level"]))

        area = _decimal_or_none(spec.get("area_km2"))
        population = _int_or_none(spec.get("pop_latest"))
        pop_date = spec.get("pop_latest_date")
        if metric_source is not None:
            area = area if area is not None else _decimal_or_none(metric_source.area_km2)
            population = population if population is not None else _int_or_none(metric_source.pop_latest)
            pop_date = pop_date or metric_source.pop_latest_date
        if metric_sources:
            area = area if area is not None else _sum_decimals(source.area_km2 for source in metric_sources)
            population = population if population is not None else _sum_ints(source.pop_latest for source in metric_sources)
            pop_date = pop_date or next((source.pop_latest_date for source in metric_sources if source.pop_latest_date), None)
        density = None
        if population is not None and area not in (None, 0):
            density = Decimal(population) / Decimal(area)
        updated.append(
            ScrapedAdminArea(
                code=code,
                name=name,
                level=int(spec.get("level", 0)),
                country_code=country_code,
                entity_type=spec.get("entity_type"),
                raw_entity_type=spec.get("raw_entity_type") or spec.get("entity_type"),
                parent_code=spec.get("parent_code"),
                area_km2=area,
                density=density,
                pop_latest=population,
                pop_latest_date=pop_date,
                url=spec.get("url"),
            )
        )
        by_code[code] = updated[-1]
    return updated


def _apply_runtime_parent_overrides(config, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    overrides = tuple(getattr(config, "runtime_parent_overrides", ()) or ())
    if not overrides:
        return entities
    updated = entities
    for override in overrides:
        updated = [
            _apply_parent_override(entity, override)
            if _matches_parent_override(entity, override)
            else entity
            for entity in updated
        ]
    return updated


def _apply_parent_override(entity: ScrapedAdminArea, override: dict) -> ScrapedAdminArea:
    values = {}
    if "parent_code" in override:
        values["parent_code"] = override.get("parent_code")
    if "level" in override:
        values["level"] = int(override["level"])
    return replace(entity, **values) if values else entity


def _matches_parent_override(entity: ScrapedAdminArea, override: dict) -> bool:
    if str(entity.code) in {str(code) for code in override.get("exclude_codes", ())}:
        return False
    codes = {str(code) for code in override.get("codes", ())}
    names = {_norm_name(name) for name in override.get("names", ())}
    levels = {int(level) for level in override.get("match_levels", ())}
    if codes and str(entity.code) in codes:
        return True
    if names and _norm_name(entity.name) in names:
        return True
    if levels and int(entity.level) in levels:
        return True
    return not codes and not names and not levels


def _apply_runtime_root_metric_sources(config, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    sources = tuple(getattr(config, "runtime_root_metric_sources", ()) or ())
    if not sources:
        return entities
    country_code = str(getattr(config, "country_code", "") or "")
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities
    matched = [entity for entity in entities if any(_matches_metric_source(entity, source) for source in sources)]
    if not matched:
        return entities
    population_addition = _sum_ints(entity.pop_latest for entity in matched) or 0
    area_addition = _sum_decimals(entity.area_km2 for entity in matched) or Decimal("0")
    population = int(root.pop_latest or 0) + population_addition if population_addition else root.pop_latest
    area = (_decimal_or_none(root.area_km2) or Decimal("0")) + area_addition if area_addition else root.area_km2
    density = root.density
    if population is not None and area not in (None, 0):
        density = Decimal(population) / Decimal(area)
    filled = replace(root, pop_latest=population, area_km2=area, density=density)
    return [filled if entity is root else entity for entity in entities]


def _matches_metric_source(entity: ScrapedAdminArea, source: dict) -> bool:
    code = source.get("code")
    name = source.get("name")
    path = source.get("path")
    if code and str(entity.code) == str(code):
        return True
    if name and _norm_name(entity.name) == _norm_name(name):
        return True
    if path and str(path).strip("/") in str(entity.url or ""):
        return True
    return False


def _apply_forced_parent_levels(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    by_level: dict[int, list[ScrapedAdminArea]] = {}
    for entity in entities:
        by_level.setdefault(int(entity.level), []).append(entity)
    indexes_by_level = {level: _forced_parent_index(rows) for level, rows in by_level.items()}
    scope_tokens_cache: dict[int, set[str]] = {}

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        target_level = _forced_parent_level(entity.annotations)
        if target_level is None:
            updated.append(_strip_forced_parent_level_marker(entity))
            continue
        if not _should_apply_forced_parent(entity, target_level):
            updated.append(_strip_forced_parent_level_marker(entity))
            continue
        parent = _best_forced_parent(
            entity,
            indexes_by_level.get(target_level, _empty_forced_parent_index()),
            scope_tokens_cache=scope_tokens_cache,
        )
        if parent is None:
            updated.append(_strip_forced_parent_level_marker(entity))
            continue
        updated.append(
            _strip_forced_parent_level_marker(
                replace(entity, parent_code=parent.code, level=max(int(entity.level), int(parent.level) + 1))
            )
        )
    return updated


def _should_apply_forced_parent(entity: ScrapedAdminArea, target_level: int) -> bool:
    level = int(entity.level)
    section = _annotation_section(entity.annotations)
    if level == 0 or section == "infosection":
        return False
    if level > target_level:
        return True
    if section == "cities":
        return True
    return False


def _best_forced_parent(
    entity: ScrapedAdminArea,
    candidates: Iterable[ScrapedAdminArea] | dict[str, object],
    *,
    scope_tokens_cache: dict[int, set[str]] | None = None,
) -> ScrapedAdminArea | None:
    index = candidates if isinstance(candidates, dict) else _forced_parent_index(candidates)
    entity_code = str(entity.code or "")
    current_parent_code = str(entity.parent_code or "")
    for length in index["prefix_lengths"]:
        if int(length) >= len(entity_code):
            continue
        for candidate in index["prefix"].get(entity_code[: int(length)], ()):
            if _valid_forced_parent_candidate(entity, candidate) and _prefix_parent_scope_matches(
                entity,
                candidate,
                scope_tokens_cache=scope_tokens_cache,
            ):
                return candidate
    entity_qid = _qid(entity.data_wd)
    if entity_qid:
        qid_matches = [
            candidate
            for candidate in index["qid"].get(entity_qid, ())
            if _valid_forced_parent_candidate(entity, candidate)
        ]
        if qid_matches:
            return max(qid_matches, key=lambda candidate: _forced_parent_score(entity, candidate))
    entity_name = _identity_name(entity.name) or _norm_name(entity.name)
    if entity_name:
        name_matches = [
            candidate
            for candidate in index["name"].get(entity_name, ())
            if _valid_forced_parent_candidate(entity, candidate)
        ]
        if name_matches:
            return max(name_matches, key=lambda candidate: _forced_parent_score(entity, candidate))
    scope_matches = []
    seen_scope_ids: set[int] = set()
    for token in _url_scope_tokens_cached(entity, scope_tokens_cache):
        for candidate in index["scope_name"].get(token, ()):
            candidate_id = id(candidate)
            if candidate_id in seen_scope_ids or not _valid_forced_parent_candidate(entity, candidate):
                continue
            seen_scope_ids.add(candidate_id)
            scope_matches.append(candidate)
    if scope_matches:
        return max(scope_matches, key=lambda candidate: _forced_parent_score(entity, candidate))
    current = index["code"].get(current_parent_code)
    if current is not None and not _valid_forced_parent_candidate(entity, current):
        return None
    return current


def _forced_parent_index(candidates: Iterable[ScrapedAdminArea]) -> dict[str, object]:
    index = _empty_forced_parent_index()
    safe_code_cache: dict[str, bool] = {}
    for candidate in candidates:
        code = str(candidate.code or "")
        if code:
            index["code"].setdefault(code, candidate)
            if _prefix_safe_code_cached(code, safe_code_cache):
                index["prefix"].setdefault(code, []).append(candidate)
        qid = _qid(candidate.data_wd)
        if qid:
            index["qid"].setdefault(qid, []).append(candidate)
        name_keys = {
            key
            for key in (_identity_name(candidate.name), _norm_name(candidate.name))
            if key
        }
        for name_key in name_keys:
            index["name"].setdefault(name_key, []).append(candidate)
        scope_name = _identity_name(candidate.name) or _norm_name(candidate.name)
        if scope_name:
            index["scope_name"].setdefault(scope_name, []).append(candidate)
    index["prefix_lengths"] = tuple(sorted({len(code) for code in index["prefix"]}, reverse=True))
    return index


def _empty_forced_parent_index() -> dict[str, object]:
    return {
        "code": {},
        "prefix": {},
        "prefix_lengths": (),
        "qid": {},
        "name": {},
        "scope_name": {},
    }


def _valid_forced_parent_candidate(entity: ScrapedAdminArea, candidate: ScrapedAdminArea) -> bool:
    return candidate is not entity and str(candidate.code) != str(entity.code or "")


def _forced_parent_score(entity: ScrapedAdminArea, candidate: ScrapedAdminArea) -> tuple[int, int, int]:
    candidate_code = str(candidate.code or "")
    entity_code = str(entity.code or "")
    prefix = 1 if candidate_code and entity_code.startswith(candidate_code) else 0
    same_qid = 1 if _qid(entity.data_wd) and _qid(entity.data_wd) == _qid(candidate.data_wd) else 0
    return (prefix, same_qid, len(candidate_code))


def _forced_parent_level(annotations: str | None) -> int | None:
    match = re.search(r"Forced parent level:\s*(\d+)", str(annotations or ""))
    return int(match.group(1)) if match else None


def _apply_parent_name_hints(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    by_level_name: dict[tuple[int, str], list[ScrapedAdminArea]] = {}
    for entity in entities:
        key = (int(entity.level), _norm_name(entity.name))
        if key[1]:
            by_level_name.setdefault(key, []).append(entity)

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        hint = _parent_name_hint(entity.annotations)
        if not hint:
            updated.append(entity)
            continue
        candidates = by_level_name.get((int(entity.level) - 1, _norm_name(hint)), [])
        if not candidates:
            updated.append(entity)
            continue
        scoped = [
            candidate
            for candidate in candidates
            if entity.parent_code and str(candidate.parent_code) == str(entity.parent_code)
        ]
        if len(scoped) == 1:
            updated.append(replace(entity, parent_code=scoped[0].code))
            continue
        if len(candidates) == 1:
            updated.append(replace(entity, parent_code=candidates[0].code))
            continue
        updated.append(entity)
    return updated


def _parent_name_hint(annotations: str | None) -> str:
    match = re.search(r"CityPopulation parent hint:\s*([^;]+)", str(annotations or ""))
    return match.group(1).strip() if match else ""


def _forced_highest_level(annotations: str | None) -> int | None:
    match = re.search(r"Forced highest level:\s*(\d+)", str(annotations or ""))
    return int(match.group(1)) if match else None


def _strip_forced_highest_level_marker(entity: ScrapedAdminArea) -> ScrapedAdminArea:
    annotations = re.sub(r"(?:^|;\s*)Forced highest level:\s*\d+", "", str(entity.annotations or "")).strip()
    annotations = re.sub(r"^;\s*|\s*;\s*$", "", annotations)
    return replace(entity, annotations=annotations or None)


def _strip_forced_parent_level_marker(entity: ScrapedAdminArea) -> ScrapedAdminArea:
    annotations = re.sub(r"(?:^|;\s*)Forced parent level:\s*\d+", "", str(entity.annotations or "")).strip()
    annotations = re.sub(r"^;\s*|\s*;\s*$", "", annotations)
    return replace(entity, annotations=annotations or None)



def _align_repeated_block_levels(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Lift repeated block anchors to the level where they first appeared.

    CityPopulation block pages normally repeat the parent layer of the
    previous page.  Example for Belgium:

    * block 1: ``Belgium(LV0) -> Region(LV1) -> Province(LV2)``
    * block 2: ``Province -> District``

    The second block is parsed in isolation, so its first row may start again
    at LV0.  If that first row has the same Wikidata id or CityPopulation code
    as an already-scraped entity, it is not a new root: it is the same branch
    anchor and must inherit the previous absolute level.  Descendants in that
    row group are shifted by the same delta before duplicate collapse rewires
    them to the kept parent.

    Explicit user duplications are not aligned; they intentionally keep the
    same entity on consecutive levels, e.g. the Brussels exception.
    """

    aligned: list[ScrapedAdminArea] = []
    previous_by_wd: dict[str, ScrapedAdminArea] = {}
    previous_by_code: dict[str, ScrapedAdminArea] = {}
    active_shift: tuple[int, int] | None = None

    for entity in entities:
        original_level = int(entity.level)
        if _forced_highest_level(entity.annotations) is not None:
            active_shift = None
        anchor = _previous_same_identity(entity, previous_by_wd, previous_by_code)
        if active_shift and original_level <= active_shift[0]:
            active_shift = None
        if active_shift and not entity.parent_code and _is_block_anchor(entity) and anchor is None:
            active_shift = None

        delta = active_shift[1] if active_shift else 0
        shifted = replace(entity, level=original_level + delta) if delta else entity

        if (
            anchor is not None
            and _is_block_anchor(entity)
            and not _is_explicit_repeat(entity)
            and _forced_highest_level(entity.annotations) is None
            and int(anchor.level) != int(shifted.level)
        ):
            delta = int(anchor.level) - original_level
            shifted = replace(entity, level=int(anchor.level))
            active_shift = (original_level, delta)

        shifted = _strip_forced_highest_level_marker(shifted)
        aligned.append(shifted)
        wd = _qid(shifted.data_wd)
        if wd:
            previous_by_wd.setdefault(wd, shifted)
        previous_by_code.setdefault(str(shifted.code), shifted)

    return aligned


def _previous_same_identity(
    entity: ScrapedAdminArea,
    previous_by_wd: dict[str, ScrapedAdminArea],
    previous_by_code: dict[str, ScrapedAdminArea],
) -> ScrapedAdminArea | None:
    wd = _qid(entity.data_wd)
    if wd and wd in previous_by_wd:
        return previous_by_wd[wd]
    candidate = previous_by_code.get(str(entity.code))
    if (
        candidate is not None
        and int(candidate.level) == 0
        and str(candidate.code).casefold() == str(entity.country_code).casefold()
        and _identity_name(candidate.name) != _identity_name(entity.name)
    ):
        return None
    return candidate


def _is_block_anchor(entity: ScrapedAdminArea) -> bool:
    section = _annotation_section(entity.annotations)
    return section in {"infosection", "major_subdivision", "minor_subdivision"}


def _is_explicit_repeat(entity: ScrapedAdminArea) -> bool:
    normalized = _norm_name(entity.annotations)
    return "duplicacion explicita" in normalized or "duplicaci n expl cita" in normalized


def _annotation_section(annotations: str | None) -> str:
    text = str(annotations or "")
    marker = "CityPopulation section:"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split(";", 1)[0].strip().casefold()


def _attach_rootless_country_children(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Attach ordinary level-0 branches below the real country root.

    Some countries are configured with one page that only stores the country
    infosection and later pages that intentionally disable ``infosection``.
    Those later pages are parsed in isolation, so their first stored section can
    start at LV0 even though, globally, it is a child of the country.

    France is the canonical example:

    * ``cities`` stores only ``France`` as LV0.
    * ``reg/admin`` stores regions/departments, with no infosection.

    The regions must therefore become ``France -> Region`` and the complete
    branch below each region must be shifted one level down.  ``sum_to_root``
    pages are handled by ``_attach_sum_to_root_entities`` and are not changed
    here.  If the row is already one level below the country, it is only
    attached to the root without shifting; this covers ordinary L1 rows from a
    later block whose page did not persist an infosection.
    """
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities

    root_level = int(root.level)
    candidates = [
        entity
        for entity in entities
        if not entity.parent_code
        and str(entity.code) != str(root.code)
        and int(entity.level) <= root_level + 1
        and not entity.contributes_to_root
        and _annotation_section(entity.annotations) in {"major_subdivision", "minor_subdivision"}
    ]
    if not candidates:
        return entities

    children_by_parent: dict[str, list[ScrapedAdminArea]] = {}
    for entity in entities:
        if entity.parent_code:
            children_by_parent.setdefault(str(entity.parent_code), []).append(entity)

    shift_by_code: dict[str, int] = {}
    candidate_codes = {str(entity.code) for entity in candidates}
    for candidate in candidates:
        delta = root_level + 1 - int(candidate.level)
        stack = [candidate]
        while stack:
            current = stack.pop()
            code = str(current.code)
            if code in shift_by_code:
                continue
            shift_by_code[code] = delta
            stack.extend(children_by_parent.get(code, ()))

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        code = str(entity.code)
        delta = shift_by_code.get(code)
        if delta is None:
            updated.append(entity)
            continue
        parent_code = root.code if code in candidate_codes else entity.parent_code
        updated.append(replace(entity, level=int(entity.level) + delta, parent_code=parent_code))
    return updated


def _repair_explicit_repeated_roots(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities

    children_by_parent: dict[str, list[ScrapedAdminArea]] = {}
    for entity in entities:
        if entity.parent_code:
            children_by_parent.setdefault(str(entity.parent_code), []).append(entity)

    repeated = [
        entity
        for entity in entities
        if _is_explicit_repeat(entity)
        and entity.parent_code
        and _norm_name(entity.name) == _norm_name((_entity_by_code(entities, entity.parent_code) or entity).name)
    ]
    if not repeated:
        return entities

    shift_by_code: dict[str, int] = {}
    parent_by_code: dict[str, str] = {}
    type_by_code: dict[str, str] = {}

    for repeated_root in repeated:
        parent = _entity_by_code(entities, repeated_root.parent_code)
        if parent is None:
            continue
        if int(parent.level) == 1 and int(repeated_root.level) == 2:
            type_by_code[str(parent.code)] = parent.entity_type or "Region [overseas]"
            type_by_code[str(repeated_root.code)] = repeated_root.entity_type or "Department [overseas]"

        for sibling in entities:
            if sibling is repeated_root or sibling is parent:
                continue
            if str(sibling.parent_code or "") != str(root.code):
                continue
            if int(sibling.level) != int(repeated_root.level):
                continue
            parent_by_code[str(sibling.code)] = str(repeated_root.code)
            shift_by_code[str(sibling.code)] = 1
            stack = list(children_by_parent.get(str(sibling.code), ()))
            while stack:
                child = stack.pop()
                code = str(child.code)
                if code in shift_by_code:
                    continue
                shift_by_code[code] = 1
                stack.extend(children_by_parent.get(code, ()))

    if not shift_by_code and not type_by_code:
        return entities

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        code = str(entity.code)
        level = int(entity.level) + shift_by_code.get(code, 0)
        parent_code = parent_by_code.get(code, entity.parent_code)
        entity_type = type_by_code.get(code, entity.entity_type)
        raw_entity_type = type_by_code.get(code, entity.raw_entity_type)
        if level != entity.level or parent_code != entity.parent_code or entity_type != entity.entity_type:
            updated.append(replace(entity, level=level, parent_code=parent_code, entity_type=entity_type, raw_entity_type=raw_entity_type))
        else:
            updated.append(entity)
    return updated


def _attach_sum_to_root_entities(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Attach rootless ``sum_to_root`` rows to the level-0 country row.

    The TOML/form option means: "this page belongs to the country total".
    It must not create a country-child-of-itself situation.  Therefore only
    marked rows without an explicit/forced parent are changed, and the canonical
    country root itself is never rewired.
    """
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        if (
            entity.contributes_to_root
            and not entity.parent_code
            and str(entity.code) != str(root.code)
        ):
            updated.append(replace(entity, parent_code=root.code))
        else:
            updated.append(entity)
    return updated


def _roll_up_sum_to_root_metrics(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Add population/area from ``sum_to_root`` branches to their ancestors.

    Only the top row of every marked branch contributes.  Descendants are also
    marked by the parser because they belong to the same page, but adding all of
    them would double-count municipalities below a department/region.
    """
    if not any(entity.contributes_to_root for entity in entities):
        return entities

    by_code = {str(entity.code): entity for entity in entities}
    contributor_codes = _top_sum_to_root_codes(entities, by_code)
    if not contributor_codes:
        return entities

    additions: dict[str, dict[str, Decimal | int]] = {}
    for code in contributor_codes:
        entity = by_code.get(code)
        parent_code = str(entity.parent_code) if entity and entity.parent_code else ""
        seen_parents: set[str] = set()
        while parent_code and parent_code not in seen_parents:
            seen_parents.add(parent_code)
            parent = by_code.get(parent_code)
            if parent is None or parent.code == entity.code:
                break
            bucket = additions.setdefault(str(parent.code), {"population": 0, "area": Decimal("0")})
            population = _int_or_none(entity.pop_latest)
            area = _decimal_or_none(entity.area_km2)
            if population is not None:
                bucket["population"] = int(bucket["population"]) + population
            if area is not None:
                bucket["area"] = Decimal(bucket["area"]) + area
            parent_code = str(parent.parent_code) if parent.parent_code else ""

    if not additions:
        return entities

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        addition = additions.get(str(entity.code))
        if not addition:
            updated.append(entity)
            continue
        population = entity.pop_latest
        area = _decimal_or_none(entity.area_km2)
        if addition["population"]:
            population = int(population or 0) + int(addition["population"])
        if addition["area"]:
            area = (area or Decimal("0")) + Decimal(addition["area"])
        density = entity.density
        if population is not None and area not in (None, 0):
            density = Decimal(population) / Decimal(area)
        updated.append(replace(entity, pop_latest=population, area_km2=area, density=density))
    return updated


def _top_sum_to_root_codes(
    entities: list[ScrapedAdminArea],
    by_code: dict[str, ScrapedAdminArea],
) -> set[str]:
    marked = {str(entity.code) for entity in entities if entity.contributes_to_root}
    top: set[str] = set()
    for entity in entities:
        code = str(entity.code)
        if code not in marked:
            continue
        parent_code = str(entity.parent_code) if entity.parent_code else ""
        has_marked_ancestor = False
        seen: set[str] = set()
        while parent_code and parent_code not in seen:
            seen.add(parent_code)
            if parent_code in marked:
                has_marked_ancestor = True
                break
            parent = by_code.get(parent_code)
            parent_code = str(parent.parent_code) if parent and parent.parent_code else ""
        if not has_marked_ancestor:
            top.add(code)
    return top


def _preferred_country_root(country_code: str, entities: list[ScrapedAdminArea]) -> ScrapedAdminArea | None:
    roots = [entity for entity in entities if _is_country_root(entity)]
    if not roots:
        return None
    exact = [entity for entity in roots if str(entity.code).casefold() == str(country_code).casefold()]
    if exact:
        return exact[0]
    infosection = [entity for entity in roots if _annotation_section(entity.annotations) == "infosection"]
    if infosection:
        return infosection[0]
    return roots[0]


def _int_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

def _collapse_equivalent_rows(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    kept: list[ScrapedAdminArea] = []
    replacements: dict[str, str] = {}
    key_owner: dict[tuple[str, object], ScrapedAdminArea] = {}
    kept_index_by_owner: dict[int, int] = {}
    keys_by_owner: dict[int, set[tuple[str, object]]] = {}
    duplicate_keys_cache: dict[int, tuple[tuple[str, object], ...]] = {}
    duplicate_score_cache: dict[int, tuple[int, int, int, int]] = {}
    by_code = _by_code_first(entities)

    def duplicate_keys(entity: ScrapedAdminArea) -> tuple[tuple[str, object], ...]:
        return duplicate_keys_cache.setdefault(id(entity), _duplicate_keys(entity))

    def duplicate_score(entity: ScrapedAdminArea) -> tuple[int, int, int, int]:
        return duplicate_score_cache.setdefault(id(entity), _duplicate_score(entity, by_code))

    def register_owner(entity: ScrapedAdminArea) -> None:
        owner_id = id(entity)
        owner_keys = keys_by_owner.setdefault(owner_id, set())
        for key in duplicate_keys(entity):
            if key in key_owner:
                continue
            key_owner[key] = entity
            owner_keys.add(key)

    def replace_owner(old: ScrapedAdminArea, new: ScrapedAdminArea) -> None:
        old_id = id(old)
        new_id = id(new)
        kept_index = kept_index_by_owner.pop(old_id, None)
        if kept_index is not None:
            kept[kept_index] = new
            kept_index_by_owner[new_id] = kept_index
        for key in keys_by_owner.pop(old_id, set()):
            if key_owner.get(key) is old:
                key_owner[key] = new
                keys_by_owner.setdefault(new_id, set()).add(key)
        register_owner(new)

    for entity in entities:
        duplicate = _first_duplicate_for(entity, key_owner, duplicate_keys=duplicate_keys(entity))
        if duplicate is None:
            kept_index_by_owner[id(entity)] = len(kept)
            kept.append(entity)
            register_owner(entity)
            continue

        preferred = entity if duplicate_score(entity) > duplicate_score(duplicate) else duplicate
        preferred_was_duplicate = preferred is duplicate
        discarded = entity if preferred is duplicate else duplicate
        preferred = _merge_duplicate_parent(preferred, discarded, by_code)
        replacements[str(discarded.code)] = str(preferred.code)
        if preferred_was_duplicate:
            if preferred is not duplicate:
                replace_owner(duplicate, preferred)
            continue

        replace_owner(duplicate, preferred)

    if not replacements:
        return kept
    return [replace(entity, parent_code=_resolve_replacement(entity.parent_code, replacements)) for entity in kept]


def _merge_duplicate_parent(
    preferred: ScrapedAdminArea,
    discarded: ScrapedAdminArea,
    entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea],
) -> ScrapedAdminArea:
    """Preserve the best valid parent when equivalent rows are collapsed."""
    discarded_parent = str(discarded.parent_code or "").strip()
    if not discarded_parent:
        return preferred
    if not _duplicate_parent_is_valid(discarded_parent, preferred, entities):
        return preferred

    preferred_parent = str(preferred.parent_code or "").strip()
    if not _duplicate_parent_is_valid(preferred_parent, preferred, entities):
        return replace(preferred, parent_code=discarded_parent)

    if _parent_quality(discarded, entities) > _parent_quality(preferred, entities):
        return replace(preferred, parent_code=discarded_parent)
    return preferred


def _duplicate_parent_is_valid(
    parent_code: str,
    child: ScrapedAdminArea,
    entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea],
) -> bool:
    if not parent_code or parent_code == str(child.code):
        return False
    parent = _entity_by_code(entities, parent_code)
    if parent is None:
        return False
    return int(parent.level) < int(child.level)


def _drop_cross_level_shortcuts(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    by_wd: dict[str, list[ScrapedAdminArea]] = {}
    for entity in entities:
        wd = _qid(entity.data_wd)
        if wd:
            by_wd.setdefault(wd, []).append(entity)
    if not by_wd:
        return entities

    by_code = {str(entity.code): entity for entity in entities}
    replacements: dict[str, str] = {}
    dropped: set[str] = set()
    for rows in by_wd.values():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda item: int(item.level))
        for shallow in ordered:
            if _is_explicit_repeat(shallow) or str(shallow.code) in dropped:
                continue
            for deep in reversed(ordered):
                if deep is shallow or int(deep.level) <= int(shallow.level):
                    continue
                if _is_explicit_repeat(deep):
                    continue
                if _same_identity_parent_chain(shallow, deep, by_code):
                    continue
                if _code_prefix_related(shallow, deep):
                    continue
                deep_quality = _parent_quality(deep, by_code)
                shallow_quality = _parent_quality(shallow, by_code)
                if deep_quality < shallow_quality:
                    continue
                if deep_quality == shallow_quality and not _parents_equivalent(shallow, deep, by_code):
                    continue
                replacements[str(shallow.code)] = str(deep.code)
                dropped.add(str(shallow.code))
                break

    if not replacements:
        return entities
    kept = [entity for entity in entities if str(entity.code) not in dropped]
    return [replace(entity, parent_code=_resolve_replacement(entity.parent_code, replacements)) for entity in kept]


def _same_identity_parent_chain(
    shallow: ScrapedAdminArea,
    deep: ScrapedAdminArea,
    by_code: dict[str, ScrapedAdminArea],
) -> bool:
    parent_code = str(deep.parent_code) if deep.parent_code else ""
    seen: set[str] = set()
    while parent_code and parent_code not in seen:
        if parent_code == str(shallow.code):
            return True
        seen.add(parent_code)
        parent = by_code.get(parent_code)
        parent_code = str(parent.parent_code) if parent and parent.parent_code else ""
    return False


def _code_prefix_related(first: ScrapedAdminArea, second: ScrapedAdminArea) -> bool:
    first_code = str(first.code or "")
    second_code = str(second.code or "")
    return bool(first_code and second_code and (first_code.startswith(second_code) or second_code.startswith(first_code)))


def _parents_equivalent(
    first: ScrapedAdminArea,
    second: ScrapedAdminArea,
    by_code: dict[str, ScrapedAdminArea],
) -> bool:
    first_parent = by_code.get(str(first.parent_code)) if first.parent_code else None
    second_parent = by_code.get(str(second.parent_code)) if second.parent_code else None
    if first_parent is None or second_parent is None:
        return False
    first_wd = _qid(first_parent.data_wd)
    second_wd = _qid(second_parent.data_wd)
    if first_wd and second_wd and first_wd == second_wd:
        return True
    return _norm_name(first_parent.name) == _norm_name(second_parent.name)


def _first_duplicate_for(
    entity: ScrapedAdminArea,
    key_owner: dict[tuple[str, object], ScrapedAdminArea],
    *,
    duplicate_keys: tuple[tuple[str, object], ...] | None = None,
) -> ScrapedAdminArea | None:
    for key in (duplicate_keys if duplicate_keys is not None else _duplicate_keys(entity)):
        duplicate = key_owner.get(key)
        if duplicate is not None:
            if str(duplicate.code) != str(entity.code) and _code_prefix_related(duplicate, entity):
                continue
            return duplicate
    return None


def _register_duplicate_keys(
    entity: ScrapedAdminArea,
    key_owner: dict[tuple[str, object], ScrapedAdminArea],
) -> None:
    for key in _duplicate_keys(entity):
        key_owner.setdefault(key, entity)


def _duplicate_keys(entity: ScrapedAdminArea) -> tuple[tuple[str, object], ...]:
    level = int(entity.level)
    keys: list[tuple[str, object]] = [(("code", str(entity.code), level))]
    wd = _qid(entity.data_wd)
    if wd:
        keys.append(("wd", wd, level))
    parent = str(entity.parent_code or "")
    name = _norm_name(entity.name)
    if name and parent:
        keys.append(("name-parent", name, level, parent))
    identity_name = _identity_name(entity.name)
    if identity_name and parent and _is_block_anchor(entity):
        scope = _url_primary_scope_token(entity)
        if scope:
            keys.append(("identity-name-scope", identity_name, level, scope))
    elif identity_name and not parent:
        scope = _url_primary_scope_token(entity)
        if scope:
            keys.append(("identity-name-scope", identity_name, level, scope))
        else:
            keys.append(("identity-name", identity_name, level))
    return tuple(keys)


def _preferred_duplicate(
    current: ScrapedAdminArea,
    candidate: ScrapedAdminArea,
    entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea],
) -> ScrapedAdminArea:
    current_score = _duplicate_score(current, entities)
    candidate_score = _duplicate_score(candidate, entities)
    return candidate if candidate_score > current_score else current


def _duplicate_score(
    entity: ScrapedAdminArea,
    entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea],
) -> tuple[int, int, int, int]:
    parent_quality = _parent_quality(entity, entities)
    has_qid = 1 if _qid(entity.data_wd) else 0
    code_precision = len(str(entity.code or ""))
    url_specificity = len(str(entity.url or ""))
    return (parent_quality, has_qid, code_precision, url_specificity)


def _parent_quality(entity: ScrapedAdminArea, entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea]) -> int:
    parent = _entity_by_code(entities, str(entity.parent_code) if entity.parent_code else None)
    if parent is None:
        return 0
    gap = int(entity.level) - int(parent.level)
    if gap == 1:
        return 4
    if gap > 1:
        return 2
    return 1


def _rewire_parent_codes(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Repair parent links with the most specific CityPopulation code prefix.

    CityPopulation does not always expose a useful ``data-wd`` for locality
    rows.  In those cases the stable relation is encoded in the internal code:

    * Italy commune ``015171`` -> locality ``01517110002``.
    * Spain municipality ``03082`` -> locality ``03082000202``.

    The parser can initially attach those rows to the visible upper table
    parent, for example the province ``015``.  This step keeps Wikidata/code
    deduplication untouched, but rewires city/locality rows to the longest
    already-scraped prefix and moves them exactly one level below that prefix.
    """
    codes = {str(entity.code) for entity in entities}
    by_code = _by_code_first(entities)
    prefix_index = _prefix_parent_index(entities)
    safe_code_cache: dict[str, bool] = {}
    scope_tokens_cache: dict[int, set[str]] = {}

    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        parent_code = str(entity.parent_code) if entity.parent_code else None
        prefix_parent = _best_prefix_parent(
            entity,
            prefix_index,
            safe_code_cache=safe_code_cache,
            scope_tokens_cache=scope_tokens_cache,
        )
        if prefix_parent is not None:
            current_parent = by_code.get(parent_code) if parent_code else None
            expected_parent = str(prefix_parent.code)
            expected_level = int(prefix_parent.level) + 1
            if _should_use_prefix_parent(
                entity,
                current_parent,
                prefix_parent,
                scope_tokens_cache=scope_tokens_cache,
            ):
                updated.append(replace(entity, parent_code=expected_parent, level=expected_level))
                continue
        if parent_code and parent_code not in codes:
            # No guesswork: keep the unresolved parent visible for diagnostics.
            updated.append(entity)
        else:
            updated.append(entity)
    return updated


def _repair_self_parent_links(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    """Replace impossible self-parent links with the safest generic parent.

    Duplicate collapse and cross-level shortcut removal can expose rows whose
    parent code is their own CityPopulation code.  Keep the repair generic:
    first use the same prefix-based parent rule as locality rewiring, then fall
    back to the country root only when no better scoped parent exists.
    """
    root = _preferred_country_root(country_code, entities)
    prefix_index = _prefix_parent_index(entities)
    safe_code_cache: dict[str, bool] = {}
    scope_tokens_cache: dict[int, set[str]] = {}
    updated: list[ScrapedAdminArea] = []
    for entity in entities:
        if str(entity.parent_code or "") != str(entity.code):
            updated.append(entity)
            continue
        prefix_parent = _best_prefix_parent(
            entity,
            prefix_index,
            safe_code_cache=safe_code_cache,
            scope_tokens_cache=scope_tokens_cache,
        )
        if prefix_parent is not None:
            updated.append(
                replace(
                    entity,
                    parent_code=prefix_parent.code,
                    level=int(prefix_parent.level) + 1,
                )
            )
            continue
        if root is not None and str(root.code) != str(entity.code):
            updated.append(replace(entity, parent_code=root.code, level=max(int(entity.level), int(root.level) + 1)))
            continue
        updated.append(replace(entity, parent_code=None))
    return updated


def _normalize_child_levels(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    changed = True
    updated = list(entities)
    remaining_passes = min(len(updated) + 1, 12)
    while changed and remaining_passes > 0:
        remaining_passes -= 1
        changed = False
        next_entities: list[ScrapedAdminArea] = []
        by_code = _by_code_prefer_lowest(updated)
        for entity in updated:
            parent = by_code.get(str(entity.parent_code)) if entity.parent_code else None
            if parent is entity or (parent is not None and str(parent.code) == str(entity.code)):
                next_entities.append(entity)
                continue
            if parent is not None and str(parent.parent_code or "") == str(parent.code):
                next_entities.append(entity)
                continue
            if parent is not None and _parent_chain_reaches(parent, entity, by_code):
                next_entities.append(entity)
                continue
            if parent is not None and int(entity.level) <= int(parent.level):
                entity = replace(entity, level=int(parent.level) + 1)
                changed = True
            next_entities.append(entity)
        updated = next_entities
    return updated


def _by_code_prefer_lowest(entities: list[ScrapedAdminArea]) -> dict[str, ScrapedAdminArea]:
    by_code: dict[str, ScrapedAdminArea] = {}
    for entity in entities:
        code = str(entity.code)
        current = by_code.get(code)
        if current is None or int(entity.level) < int(current.level):
            by_code[code] = entity
    return by_code


def _by_code_first(entities: list[ScrapedAdminArea]) -> dict[str, ScrapedAdminArea]:
    by_code: dict[str, ScrapedAdminArea] = {}
    for entity in entities:
        by_code.setdefault(str(entity.code), entity)
    return by_code


def _parent_chain_reaches(
    start: ScrapedAdminArea,
    target: ScrapedAdminArea,
    by_code: dict[str, ScrapedAdminArea],
) -> bool:
    current = start
    seen: set[str] = set()
    target_code = str(target.code)
    while current is not None:
        code = str(current.code)
        if code == target_code:
            return True
        if code in seen or not current.parent_code:
            return False
        seen.add(code)
        current = by_code.get(str(current.parent_code))
    return False


def _should_use_prefix_parent(
    entity: ScrapedAdminArea,
    current_parent: ScrapedAdminArea | None,
    prefix_parent: ScrapedAdminArea,
    *,
    scope_tokens_cache: dict[int, set[str]] | None = None,
) -> bool:
    if str(entity.parent_code or "") == str(prefix_parent.code) and int(entity.level) == int(prefix_parent.level) + 1:
        return False
    if current_parent is None:
        return True
    if str(entity.parent_code or "") == str(entity.code):
        return True
    current_gap = int(entity.level) - int(current_parent.level)
    prefix_gap = int(entity.level) - int(prefix_parent.level)
    current_code = str(current_parent.code or "")
    entity_code = str(entity.code or "")
    prefix_code = str(prefix_parent.code or "")
    current_is_prefix = bool(current_code and entity_code.startswith(current_code))
    prefix_is_prefix = bool(prefix_code and entity_code.startswith(prefix_code))
    if current_gap <= 0:
        return True
    if current_gap == 1:
        if (
            int(prefix_parent.level) <= int(current_parent.level)
            and _parent_name_in_child_url_scope(entity, current_parent, scope_tokens_cache=scope_tokens_cache)
        ):
            return False
        if (
            prefix_is_prefix
            and not current_is_prefix
            and int(prefix_parent.level) >= int(current_parent.level)
        ):
            return True
        return int(prefix_parent.level) >= int(current_parent.level) and len(str(prefix_parent.code)) > len(str(current_parent.code))
    if prefix_gap == 1 and int(prefix_parent.level) >= int(current_parent.level):
        return True
    if int(prefix_parent.level) > int(current_parent.level) and _parent_quality(entity, [current_parent]) < 4:
        return True
    return False


def _parent_name_in_child_url_scope(
    entity: ScrapedAdminArea,
    parent: ScrapedAdminArea,
    *,
    scope_tokens_cache: dict[int, set[str]] | None = None,
) -> bool:
    parent_name = _identity_name(parent.name) or _norm_name(parent.name)
    if not parent_name:
        return False
    return parent_name in _url_scope_tokens_cached(entity, scope_tokens_cache)


def _entity_by_code(
    entities: list[ScrapedAdminArea] | dict[str, ScrapedAdminArea],
    code: str | None,
) -> ScrapedAdminArea | None:
    if not code:
        return None
    wanted = str(code)
    if isinstance(entities, dict):
        return entities.get(wanted)
    for entity in entities:
        if str(entity.code) == wanted:
            return entity
    return None


def _prefix_parent_index(entities: list[ScrapedAdminArea]) -> dict[int, dict[str, list[ScrapedAdminArea]]]:
    """Index possible code-prefix parents by code length, longest first."""
    by_length: dict[int, dict[str, list[ScrapedAdminArea]]] = {}
    safe_code_cache: dict[str, bool] = {}
    for entity in entities:
        code = str(entity.code or "")
        if not code or _is_explicit_repeat(entity) or not _prefix_safe_code_cached(code, safe_code_cache):
            continue
        by_length.setdefault(len(code), {}).setdefault(code, []).append(entity)
    for by_code in by_length.values():
        for items in by_code.values():
            items.sort(key=lambda item: int(item.level), reverse=True)
    return dict(sorted(by_length.items(), reverse=True))


def _best_prefix_parent(
    entity: ScrapedAdminArea,
    prefix_index: dict[int, dict[str, list[ScrapedAdminArea]]],
    *,
    safe_code_cache: dict[str, bool] | None = None,
    scope_tokens_cache: dict[int, set[str]] | None = None,
) -> ScrapedAdminArea | None:
    """Return the longest valid code-prefix parent for city/locality rows.

    This is intentionally limited to rows parsed as the ``cities`` section,
    because administrative codes are not globally prefix-safe.  Example:
    Italian province ``015`` starts with ``01`` but its real parent is
    Lombardia ``03``, so prefix repair must not run on admin rows.
    """
    if _is_explicit_repeat(entity) or int(entity.level) == 0 or _annotation_section(entity.annotations) == "infosection":
        return None

    code = str(entity.code or "")
    if len(code) < 3 or not _prefix_safe_code_cached(code, safe_code_cache):
        return None

    for length, candidates_by_code in prefix_index.items():
        if length >= len(code):
            continue
        for candidate in candidates_by_code.get(code[:length], ()):
            candidate_code = str(candidate.code or "")
            if not candidate_code or candidate_code == code:
                continue
            if not _prefix_safe_code_cached(candidate_code, safe_code_cache):
                continue
            if not code.startswith(candidate_code):
                continue
            if not _prefix_parent_scope_matches(entity, candidate, scope_tokens_cache=scope_tokens_cache):
                continue
            return candidate
    return None


def _prefix_safe_code_cached(code: str, cache: dict[str, bool] | None = None) -> bool:
    if cache is None:
        return _prefix_safe_code(code)
    text = str(code or "").strip()
    if text not in cache:
        cache[text] = _prefix_safe_code(text)
    return cache[text]


def _prefix_safe_code(code: str) -> bool:
    text = str(code or "").strip()
    return bool(text) and sum(1 for char in text if char.isdigit()) >= max(2, len(text) // 2)


def _prefix_parent_scope_matches(
    entity: ScrapedAdminArea,
    candidate: ScrapedAdminArea,
    *,
    scope_tokens_cache: dict[int, set[str]] | None = None,
) -> bool:
    entity_tokens = _url_scope_tokens_cached(entity, scope_tokens_cache)
    candidate_tokens = _url_scope_tokens_cached(candidate, scope_tokens_cache)
    if entity_tokens and not candidate_tokens:
        return not _is_block_anchor(candidate)
    if entity_tokens and candidate_tokens:
        return bool(entity_tokens & candidate_tokens)
    return True


def _url_scope_tokens_cached(entity: ScrapedAdminArea, cache: dict[int, set[str]] | None = None) -> set[str]:
    if cache is None:
        return _url_scope_tokens(entity)
    owner_id = id(entity)
    if owner_id not in cache:
        cache[owner_id] = _url_scope_tokens(entity)
    return cache[owner_id]


def _url_scope_tokens(entity: ScrapedAdminArea) -> set[str]:
    return set(_url_scope_token_list(entity))


def _url_primary_scope_token(entity: ScrapedAdminArea) -> str:
    tokens = _url_scope_token_list(entity)
    return tokens[0] if tokens else ""


def _url_scope_token_list(entity: ScrapedAdminArea) -> tuple[str, ...]:
    url = str(entity.url or "").strip()
    if not url:
        return ()
    path = unquote(urlparse(url).path or "")
    if not path or path == "/":
        return ()
    raw_tokens = re.split(r"[^0-9A-Za-zÀ-ÿ]+", path)
    generic = {
        "admin",
        "cities",
        "city",
        "countries",
        "country",
        "en",
        "fr",
        "localities",
        "places",
        "reg",
        _norm_name(entity.country_code),
    }
    tokens: list[str] = []
    for segment in path.split("/"):
        normalized_segment = _norm_name(segment)
        if (
            len(normalized_segment) >= 3
            and normalized_segment not in generic
            and not normalized_segment.replace(" ", "").isdigit()
            and not normalized_segment.split()[0].isdigit()
            and normalized_segment not in tokens
        ):
            tokens.append(normalized_segment)
    for token in raw_tokens:
        normalized = _norm_name(token)
        if len(normalized) < 3 or normalized in generic or normalized.isdigit():
            continue
        if normalized not in tokens:
            tokens.append(normalized)
    return tuple(tokens)


def _resolve_replacement(code: str | None, replacements: dict[str, str]) -> str | None:
    if not code:
        return code
    seen = set()
    current = code
    while current in replacements and current not in seen:
        seen.add(current)
        current = replacements[current]
    return current


def _merge_missing_fields(target: ScrapedAdminArea, source: ScrapedAdminArea) -> None:
    # Dataclasses are frozen; this function documents the merge policy but does
    # not mutate target.  The first row wins because it appears earlier in the
    # configured block order.  Children are rewired to that first row.
    return None


def _fill_country_root_metrics(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    root = _preferred_country_root(country_code, entities)
    if root is None:
        return entities
    if root.pop_latest is not None and root.area_km2 is not None:
        return entities
    child_level = min(
        (entity.level for entity in entities if entity.parent_code == root.code and entity.level > root.level),
        default=None,
    )
    if child_level is None:
        return entities
    children = [entity for entity in entities if entity.parent_code == root.code and entity.level == child_level]
    if not children:
        return entities
    area = root.area_km2 if root.area_km2 is not None else _sum_decimals(child.area_km2 for child in children)
    population = root.pop_latest if root.pop_latest is not None else _sum_ints(child.pop_latest for child in children)
    density = root.density
    if density is None and population is not None and area not in (None, 0):
        density = Decimal(population) / Decimal(area)
    filled = replace(root, area_km2=area, pop_latest=population, density=density)
    return [filled if entity is root else entity for entity in entities]



def _is_country_root(entity: ScrapedAdminArea) -> bool:
    if int(entity.level) != 0 or entity.parent_code:
        return False
    if _annotation_section(entity.annotations) == "infosection":
        return True
    if str(entity.code).casefold() == str(entity.country_code).casefold():
        return True
    return True

def _sum_decimals(values) -> Decimal | None:
    total = Decimal("0")
    found = False
    for value in values:
        if value in (None, ""):
            continue
        try:
            total += Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        found = True
    return total if found else None


def _sum_ints(values) -> int | None:
    total = 0
    found = False
    for value in values:
        if value in (None, ""):
            continue
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
        found = True
    return total if found else None


def _norm_name(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _identity_name(value) -> str:
    generic = {
        "admin",
        "administrative",
        "capital",
        "city",
        "commune",
        "de",
        "del",
        "department",
        "district",
        "municipality",
        "of",
        "province",
        "region",
        "regi",
        "regio",
        "région",
        "the",
    }
    tokens = [token for token in _norm_name(value).split() if token not in generic]
    return " ".join(tokens)


def _qid(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return text if text.startswith("Q") and text[1:].isdigit() else ""
