from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from ciudades_del_mundo.domain import (
    AdminAreaSummary,
    MostPopulatedAssignment,
    RepresentationConfig,
    RepresentationSystem,
    ScrapedAdminArea,
)
from ciudades_del_mundo.infrastructure.django.admin_area_deletion import (
    SQLITE_SAFE_DELETE_BATCH_SIZE,
    delete_admin_area_country,
    delete_admin_area_ids,
)
from ciudades_del_mundo.models import AdminArea

SQLITE_SAFE_BATCH_SIZE = SQLITE_SAFE_DELETE_BATCH_SIZE


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
        delete_admin_area_country(country_code, batch_size=SQLITE_SAFE_BATCH_SIZE)

    @transaction.atomic
    def save_many(self, country_code: str, entities: list[ScrapedAdminArea]) -> tuple[int, int]:
        if not entities:
            return 0, 0

        existing = dict(
            AdminArea.objects.filter(country_code=country_code).values_list("code", "id")
        )
        incoming_ids = {entity.id for entity in entities}
        existing_ids = set(existing.values())
        created = sum(1 for entity_id in incoming_ids if entity_id not in existing_ids)
        updated = len(incoming_ids) - created

        known_codes = set(existing)
        update_fields = [
            "country_code",
            "code",
            "name",
            "level",
            "city_merge_status",
            "entity_type",
            "raw_entity_type",
            "parent",
            "area_km2",
            "density",
            "pop_latest",
            "pop_latest_date",
            "last_census_year",
            "url",
            "updated_at",
        ]

        for level in sorted({entity.level for entity in entities}):
            level_entities = [entity for entity in entities if entity.level == level]
            objects = [
                _admin_area_from_entity(
                    country_code=country_code,
                    entity=entity,
                    known_codes=known_codes,
                )
                for entity in level_entities
            ]
            AdminArea.objects.bulk_create(
                objects,
                batch_size=SQLITE_SAFE_BATCH_SIZE,
                update_conflicts=True,
                update_fields=update_fields,
                unique_fields=["id"],
            )
            known_codes.update(entity.code for entity in level_entities)

        return created, updated

    @transaction.atomic
    def delete_missing(self, country_code: str, ids: set[str]) -> int:
        existing_ids = AdminArea.objects.filter(country_code=country_code).values_list("id", flat=True)
        missing_ids = [existing_id for existing_id in existing_ids.iterator() if existing_id not in ids]
        return delete_admin_area_ids(
            missing_ids,
            batch_size=SQLITE_SAFE_BATCH_SIZE,
            country_code=country_code,
        )

    def list_summaries(self, country_code: str) -> list[AdminAreaSummary]:
        return [
            AdminAreaSummary(
                id=area.id,
                level=area.level,
                parent_id=area.parent_id,
                pop_latest=area.pop_latest,
                city_merge_status=area.city_merge_status,
                most_populate_city_id=area.most_populate_city_id,
            )
            for area in AdminArea.objects.filter(country_code=country_code).only(
                "id",
                "level",
                "parent_id",
                "pop_latest",
                "city_merge_status",
                "most_populate_city_id",
            )
        ]

    @transaction.atomic
    def save_most_populated_assignments(self, assignments: list[MostPopulatedAssignment]) -> int:
        if not assignments:
            return 0

        areas_by_id = AdminArea.objects.in_bulk([assignment.area_id for assignment in assignments])
        changed = []
        now = timezone.now()
        for assignment in assignments:
            area = areas_by_id.get(assignment.area_id)
            if area is None or area.most_populate_city_id == assignment.most_populated_id:
                continue
            area.most_populate_city_id = assignment.most_populated_id
            area.updated_at = now
            changed.append(area)

        if changed:
            AdminArea.objects.bulk_update(
                changed,
                ["most_populate_city", "updated_at"],
                batch_size=SQLITE_SAFE_BATCH_SIZE,
            )
        return len(changed)

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
        changed = []
        now = timezone.now()
        for area in areas:
            seats = seats_by_id[area.id]
            if area.representatives != seats:
                area.representatives = seats
                area.updated_at = now
                changed.append(area)

        if changed:
            AdminArea.objects.bulk_update(
                changed,
                ["representatives", "updated_at"],
                batch_size=SQLITE_SAFE_BATCH_SIZE,
            )
        return len(changed)


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


class DjangoUnitOfWork:
    def transaction(self):
        return transaction.atomic()


def _admin_area_from_entity(
    *,
    country_code: str,
    entity: ScrapedAdminArea,
    known_codes: set[str],
) -> AdminArea:
    now = timezone.now()
    parent_id = f"{country_code}_{entity.parent_code}" if entity.parent_code in known_codes else None
    return AdminArea(
        id=entity.id,
        country_code=country_code,
        code=entity.code,
        name=entity.name,
        level=entity.level,
        city_merge_status=entity.city_merge_status,
        entity_type=entity.entity_type,
        raw_entity_type=entity.raw_entity_type or entity.entity_type or "",
        parent_id=parent_id,
        area_km2=_to_decimal(entity.area_km2),
        density=_to_decimal(entity.density),
        pop_latest=entity.pop_latest,
        pop_latest_date=_to_date(entity.pop_latest_date),
        last_census_year=entity.last_census_year,
        url=entity.url,
        created_at=now,
        updated_at=now,
    )
