"""Rules that merge scraped sibling entities into aggregate city entities."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from ciudades_del_mundo.domain import EntityMergeConfig, ScrapedAdminArea


def apply_entity_merges(
    entities: list[ScrapedAdminArea],
    configs: list[EntityMergeConfig],
) -> list[ScrapedAdminArea]:
    current = list(entities)
    for config in configs:
        current = _apply_entity_merge(current, config)
    return current


def _apply_entity_merge(
    entities: list[ScrapedAdminArea],
    config: EntityMergeConfig,
) -> list[ScrapedAdminArea]:
    target_types = {_norm(entity_type) for entity_type in config.entity_types}
    if not target_types:
        return entities

    groups: dict[tuple[str | None, int, str], list[ScrapedAdminArea]] = defaultdict(list)
    for entity in entities:
        if _norm(entity.entity_type) not in target_types:
            continue
        name = _base_name(entity.name, strip_numeric_suffix=config.strip_numeric_suffix)
        groups[(entity.parent_code, entity.level, _norm(name))].append(entity)

    source_codes = {entity.code for group in groups.values() for entity in group}
    transformed = []
    if config.keep_sources:
        transformed.extend(entities)
    else:
        transformed.extend(entity for entity in entities if entity.code not in source_codes)

    for group in groups.values():
        transformed.append(_merged_entity(group, config))

    return transformed


def _merged_entity(
    group: list[ScrapedAdminArea],
    config: EntityMergeConfig,
) -> ScrapedAdminArea:
    ordered = sorted(group, key=lambda entity: str(entity.code))
    first = ordered[0]
    name = _base_name(first.name, strip_numeric_suffix=config.strip_numeric_suffix)
    total_area = _sum_decimal(entity.area_km2 for entity in ordered)
    total_pop = sum(entity.pop_latest or 0 for entity in ordered)
    density = _density(total_pop, total_area)
    return replace(
        first,
        name=name,
        entity_type=config.entity_type,
        area_km2=total_area,
        density=density,
        pop_latest=total_pop,
        pop_latest_date=max((entity.pop_latest_date for entity in ordered if entity.pop_latest_date), default=None),
        last_census_year=max(
            (entity.last_census_year for entity in ordered if entity.last_census_year is not None),
            default=None,
        ),
    )


def _base_name(value: str, *, strip_numeric_suffix: bool) -> str:
    name = re.sub(r"\s+", " ", value or "").strip()
    if strip_numeric_suffix:
        name = re.sub(r"\s+\d+(?:\s*(?:&|and|-)\s*\d+)*$", "", name).strip()
    return name


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
    return Decimal(population) / area_km2


def _norm(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().casefold()
