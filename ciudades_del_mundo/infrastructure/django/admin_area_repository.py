from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ciudades_del_mundo.domain import (
    AdminAreaSummary,
    MostPopulatedAssignment,
    RepresentationConfig,
    RepresentationSystem,
    ScrapedAdminArea,
)
from ciudades_del_mundo.models import AdminArea


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _to_date(value):
    if value in (None, "") or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class DjangoAdminAreaRepository:
    def reset_country(self, country_code: str) -> None:
        AdminArea.objects.filter(country_code=country_code).delete()

    @transaction.atomic
    def save_many(self, country_code: str, entities: list[ScrapedAdminArea]) -> tuple[int, int]:
        created = 0
        updated = 0
        cache = {item.code: item for item in AdminArea.objects.filter(country_code=country_code)}

        for entity in sorted(entities, key=lambda item: item.level):
            parent = cache.get(entity.parent_code) if entity.parent_code else None
            obj, was_created = AdminArea.objects.update_or_create(
                id=entity.id,
                defaults={
                    "country_code": country_code,
                    "code": entity.code,
                    "name": entity.name,
                    "level": entity.level,
                    "entity_type": entity.entity_type,
                    "parent": parent,
                    "area_km2": _to_decimal(entity.area_km2),
                    "density": _to_decimal(entity.density),
                    "pop_latest": entity.pop_latest,
                    "pop_latest_date": _to_date(entity.pop_latest_date),
                    "last_census_year": entity.last_census_year,
                    "url": entity.url,
                },
            )
            cache[obj.code] = obj
            created += int(was_created)
            updated += int(not was_created)

        return created, updated

    def list_summaries(self, country_code: str) -> list[AdminAreaSummary]:
        return [
            AdminAreaSummary(
                id=area.id,
                level=area.level,
                parent_id=area.parent_id,
                pop_latest=area.pop_latest,
                most_populate_city_id=area.most_populate_city_id,
            )
            for area in AdminArea.objects.filter(country_code=country_code).only(
                "id",
                "level",
                "parent_id",
                "pop_latest",
                "most_populate_city_id",
            )
        ]

    @transaction.atomic
    def save_most_populated_assignments(self, assignments: list[MostPopulatedAssignment]) -> int:
        updated = 0
        for assignment in assignments:
            updated += AdminArea.objects.filter(pk=assignment.area_id).exclude(
                most_populate_city_id=assignment.most_populated_id
            ).update(
                most_populate_city_id=assignment.most_populated_id
            )
        return updated

    @transaction.atomic
    def save_representatives(self, country_code: str, config: RepresentationConfig) -> int:
        areas = list(
            AdminArea.objects.filter(country_code=country_code, level=config.level)
            .only("id", "code", "name", "pop_latest", "representatives")
            .order_by("code")
        )
        if not areas:
            return 0

        if config.system != RepresentationSystem.DHONDT:
            raise ValueError(f"Sistema de representación no soportado: {config.system}.")

        seats_by_id = _allocate_dhondt_representatives(areas, config)
        updated = 0
        for area in areas:
            seats = seats_by_id[area.id]
            if area.representatives != seats:
                area.representatives = seats
                area.save(update_fields=["representatives", "updated_at"])
                updated += 1
        return updated


def _allocate_dhondt_representatives(areas: list[AdminArea], config: RepresentationConfig) -> dict[str, int]:
    total = config.total_for_populations(area.pop_latest for area in areas)
    seats = {area.id: _minimum_for(area, config) for area in areas}
    maximums = {area.id: _maximum_for(area, config) for area in areas}

    fixed = sum(seats.values())
    if fixed > total:
        raise ValueError(
            f"No se pueden asignar representantes: mínimos={fixed} supera total={total}."
        )

    remaining = total - fixed
    for _ in range(remaining):
        candidate = _next_dhondt_candidate(areas, seats, maximums)
        if candidate is None:
            break
        seats[candidate.id] += 1

    return seats


def _minimum_for(area: AdminArea, config: RepresentationConfig) -> int:
    return _matched_int(area, config.min_exceptions, config.minimum)


def _maximum_for(area: AdminArea, config: RepresentationConfig) -> int | None:
    return _matched_int(area, config.max_exceptions, config.maximum)


def _matched_int(area: AdminArea, values: dict[str, int], default: int | None) -> int | None:
    for key in (area.id, area.code, area.name):
        if key in values:
            return values[key]
    normalized = {_norm(key): value for key, value in values.items()}
    for key in (area.id, area.code, area.name):
        match = normalized.get(_norm(key))
        if match is not None:
            return match
    return default


def _norm(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    return re.sub(r"\s+", " ", value).strip()


def _next_dhondt_candidate(
    areas: list[AdminArea],
    seats: dict[str, int],
    maximums: dict[str, int | None],
) -> AdminArea | None:
    best_area = None
    best_quotient = -1.0

    for area in areas:
        maximum = maximums[area.id]
        if maximum is not None and seats[area.id] >= maximum:
            continue

        quotient = max(area.pop_latest or 0, 0) / (seats[area.id] + 1)
        if quotient > best_quotient:
            best_quotient = quotient
            best_area = area

    return best_area
