from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from ciudades_del_mundo.domain import CityConfig, ScrapedAdminArea


def apply_configured_cities(
    country_code: str,
    entities: list[ScrapedAdminArea],
    configs: list[CityConfig],
) -> list[ScrapedAdminArea]:
    current = list(entities)
    for config in configs:
        current = _apply_configured_city(country_code, current, config)
    return current


def _apply_configured_city(
    country_code: str,
    entities: list[ScrapedAdminArea],
    config: CityConfig,
) -> list[ScrapedAdminArea]:
    by_level: dict[int, list[ScrapedAdminArea]] = {}
    for entity in entities:
        by_level.setdefault(entity.level, []).append(entity)

    from_entities = _resolve_from(config, by_level)
    parent = _single_parent(config, from_entities)
    resolved_communes = _resolve_city_communes(config, entities, from_entities)
    total_area = _sum_decimal(entity.area_km2 for entity in resolved_communes)
    total_pop = sum(entity.pop_latest or 0 for entity in resolved_communes)
    density = _density(total_pop, total_area)
    city = ScrapedAdminArea(
        code=config.code,
        name=config.name,
        level=config.level,
        country_code=country_code,
        entity_type=config.entity_type,
        parent_code=parent.code if parent else None,
        area_km2=total_area,
        density=density,
        pop_latest=total_pop,
        pop_latest_date=_latest_pop_date(resolved_communes),
        last_census_year=max(
            (entity.last_census_year for entity in resolved_communes if entity.last_census_year is not None),
            default=None,
        )
    )

    target_commune_level = config.level + 1
    shifts_by_code = {
        commune.code: target_commune_level - commune.level
        for commune in resolved_communes
    }
    stale_configured_children_by_code = {
        entity.code: -1
        for entity in entities
        if entity.parent_code == config.code
        and entity.level > config.level
        and not _matches_district_type(entity, config)
    }
    by_code = {entity.code: entity for entity in entities}

    transformed = []
    for entity in entities:
        if entity.code == config.code:
            continue

        stale_shift = _shift_for_entity(entity, stale_configured_children_by_code, by_code)
        if stale_shift is not None:
            transformed.append(
                replace(
                    entity,
                    level=entity.level + stale_shift,
                    parent_code=(
                        parent.code
                        if entity.code in stale_configured_children_by_code and parent
                        else entity.parent_code
                    ),
                )
            )
            continue

        shift = _shift_for_entity(entity, shifts_by_code, by_code)
        if shift is None:
            transformed.append(entity)
            continue

        transformed.append(
            replace(
                entity,
                level=entity.level + shift,
                parent_code=(
                    config.code
                    if entity.code in shifts_by_code
                    else entity.parent_code or _inferred_shifted_parent_code(entity, shifts_by_code, by_code)
                ),
            )
        )

    transformed.append(city)
    return transformed


def _shift_for_entity(
    entity: ScrapedAdminArea,
    shifts_by_code: dict[str, int],
    by_code: dict[str, ScrapedAdminArea],
) -> int | None:
    if entity.code in shifts_by_code:
        return shifts_by_code[entity.code]

    parent_code = entity.parent_code
    seen = set()
    while parent_code and parent_code not in seen:
        seen.add(parent_code)
        shift = shifts_by_code.get(parent_code)
        if shift is not None:
            return shift
        parent = by_code.get(parent_code)
        parent_code = parent.parent_code if parent else None

    for code, shift in shifts_by_code.items():
        parent = by_code.get(code)
        if parent and entity.level > parent.level and entity.code.startswith(code):
            return shift
    return None


def _inferred_shifted_parent_code(
    entity: ScrapedAdminArea,
    shifts_by_code: dict[str, int],
    by_code: dict[str, ScrapedAdminArea],
) -> str | None:
    candidates = []
    for code in shifts_by_code:
        parent = by_code.get(code)
        if parent and entity.level > parent.level and entity.code.startswith(code):
            candidates.append(code)
    return max(candidates, key=len, default=None)


def _resolve_from(config: CityConfig, by_level: dict[int, list[ScrapedAdminArea]]) -> list[ScrapedAdminArea]:
    matches = []
    for level, labels in config.parent_from.items():
        level_entities = by_level.get(level, [])
        for label in labels:
            match = _resolve_entity(label, level_entities)
            if match:
                matches.append(match)
            else:
                raise ValueError(f"CITIES '{config.name}': parent no encontrado en nivel {level}: {label}.")
    return matches


def _single_parent(config: CityConfig, from_entities: list[ScrapedAdminArea]) -> ScrapedAdminArea | None:
    if len(from_entities) > 1:
        labels = ", ".join(entity.id for entity in from_entities)
        raise ValueError(f"CITIES '{config.name}': 'from' debe resolver a un único padre, recibido: {labels}.")
    return from_entities[0] if from_entities else None


def _resolve_city_communes(
    config: CityConfig,
    entities: list[ScrapedAdminArea],
    from_entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    children = [
        entity
        for entity in entities
        if _is_real_city_input(entity, config) and _matches_district_type(entity, config) and (
            any(entity.parent_code == parent.code and entity.level == parent.level + 1 for parent in from_entities)
            or (entity.parent_code == config.code and entity.level == config.level + 1)
        )
    ]
    if not config.communes:
        configured_children = _closest_children_of_configured_city(entities, config)
        if configured_children:
            return configured_children
        return _closest_descendants_below_parents(entities, from_entities, config)

    resolved = []
    missing = []
    for label in config.communes:
        match = _resolve_city_commune(label, children, config, from_entities)
        if match:
            resolved.append(match)
        else:
            missing.append(label)

    if missing:
        parent_labels = ", ".join(entity.id for entity in from_entities) or "sin padre"
        raise ValueError(
            f"CITIES '{config.name}': comunas no encontradas como hijas directas de {parent_labels}: "
            f"{', '.join(missing)}."
        )
    return resolved


def _closest_children_of_configured_city(
    entities: list[ScrapedAdminArea],
    config: CityConfig,
) -> list[ScrapedAdminArea]:
    descendants = [
        entity
        for entity in entities
        if _is_real_city_input(entity, config)
        and _matches_district_type(entity, config)
        and entity.parent_code == config.code
        and entity.level > config.level
    ]
    if not descendants:
        return []
    target_level = min(entity.level for entity in descendants)
    return [entity for entity in descendants if entity.level == target_level]


def _closest_descendants_below_parents(
    entities: list[ScrapedAdminArea],
    from_entities: list[ScrapedAdminArea],
    config: CityConfig,
) -> list[ScrapedAdminArea]:
    descendants = [
        entity
        for entity in entities
        if _is_real_city_input(entity, config)
        and _matches_district_type(entity, config)
        and _is_below_any_parent(entity, from_entities)
        and any(_is_descendant_of(entity, parent, entities) for parent in from_entities)
    ]
    if not descendants:
        return []
    target_level = min(entity.level for entity in descendants)
    return [entity for entity in descendants if entity.level == target_level]


def _is_real_city_input(entity: ScrapedAdminArea, config: CityConfig) -> bool:
    return entity.code != config.code and entity.id != f"{entity.country_code}_{config.code}"


def _matches_district_type(entity: ScrapedAdminArea, config: CityConfig) -> bool:
    if not config.district_types:
        return True
    entity_type = _norm(entity.entity_type)
    return entity_type in {_norm(district_type) for district_type in config.district_types}


def _is_below_any_parent(entity: ScrapedAdminArea, from_entities: list[ScrapedAdminArea]) -> bool:
    return any(entity.level > parent.level for parent in from_entities)


def _is_descendant_of(
    entity: ScrapedAdminArea,
    parent: ScrapedAdminArea,
    entities: list[ScrapedAdminArea],
) -> bool:
    by_code = {item.code: item for item in entities}
    parent_code = entity.parent_code
    seen = set()
    while parent_code and parent_code not in seen:
        if parent_code == parent.code:
            return True
        seen.add(parent_code)
        next_parent = by_code.get(parent_code)
        parent_code = next_parent.parent_code if next_parent else None
    return False


def _resolve_city_commune(
    label: str,
    children: list[ScrapedAdminArea],
    config: CityConfig,
    from_entities: list[ScrapedAdminArea],
) -> ScrapedAdminArea | None:
    matches = _matching_entities(label, children)
    if not matches:
        return None

    original_matches = [
        entity
        for entity in matches
        if any(entity.parent_code == parent.code for parent in from_entities)
    ]
    match = _closest_at_or_below_level(original_matches, config.level)
    if match:
        return match

    configured_children = [
        entity
        for entity in matches
        if entity.parent_code == config.code and entity.level == config.level + 1
    ]
    return _closest_at_or_below_level(configured_children, config.level + 1)


def _resolve_closest_entity(
    label: str,
    entities: list[ScrapedAdminArea],
    target_level: int,
) -> ScrapedAdminArea | None:
    matches = _matching_entities(label, entities)
    if not matches:
        return None
    return _closest_at_or_below_level(matches, target_level)


def _closest_at_or_below_level(
    matches: list[ScrapedAdminArea],
    target_level: int,
) -> ScrapedAdminArea | None:
    valid_matches = [entity for entity in matches if entity.level <= target_level]
    if not valid_matches:
        return None
    return max(valid_matches, key=lambda entity: entity.level)


def _resolve_entity(label: str, entities: list[ScrapedAdminArea]) -> ScrapedAdminArea | None:
    label = str(label)
    for entity in entities:
        if label in {entity.id, entity.code, entity.name}:
            return entity

    normalized = _norm(label)
    for entity in entities:
        if normalized in {_norm(entity.id), _norm(entity.code), _norm(entity.name)}:
            return entity
    return None


def _matching_entities(label: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    label = str(label)
    exact = [
        entity
        for entity in entities
        if label in {entity.id, entity.code, entity.name}
    ]
    if exact:
        return exact

    normalized = _norm(label)
    return [
        entity
        for entity in entities
        if normalized in {_norm(entity.id), _norm(entity.code), _norm(entity.name)}
    ]


def _sum_decimal(values) -> Decimal | None:
    total = Decimal("0")
    has_value = False
    for value in values:
        if value in (None, ""):
            continue
        try:
            total += Decimal(str(value))
            has_value = True
        except (InvalidOperation, ValueError):
            continue
    return total.quantize(Decimal("0.01")) if has_value else None


def _density(population: int | None, area_km2: Decimal | None) -> Decimal | None:
    if population is None or area_km2 in (None, 0):
        return None
    return Decimal(population) / Decimal(area_km2)


def _latest_pop_date(entities: list[ScrapedAdminArea]):
    values = [entity.pop_latest_date for entity in entities if entity.pop_latest_date]
    return max(values) if values else None


def _norm(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().casefold()
