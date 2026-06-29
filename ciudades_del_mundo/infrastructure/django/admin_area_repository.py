from __future__ import annotations

from contextlib import contextmanager
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
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
from ciudades_del_mundo.infrastructure.django.visual_asset_deletion import (
    delete_visual_assets_for_admin_area_ids,
)
from ciudades_del_mundo.models import AdminArea, DynamicTranslation
from ciudades_del_mundo.services.dynamic_translations import (
    FIELD_NAME,
    SUBJECT_ADMIN_AREA,
    clear_dynamic_translation_cache,
    normalize_dynamic_language_code,
)

SQLITE_SAFE_BATCH_SIZE = SQLITE_SAFE_DELETE_BATCH_SIZE
SQLITE_DEFAULT_MAX_QUERY_PARAMS = 999


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

        existing = dict(AdminArea.objects.filter(country_code=country_code).values_list("code", "id"))
        incoming_ids = {entity.id for entity in entities}
        existing_by_id = _admin_areas_by_id(incoming_ids, country_code=country_code)
        existing_ids = set(existing_by_id)

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
            "data_wd",
            "annotations",
            "updated_at",
        ]
        compare_fields = tuple(field for field in update_fields if field != "updated_at")
        created = 0
        updated = 0

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
            create_objects = [obj for obj in objects if obj.id not in existing_ids]
            if create_objects:
                AdminArea.objects.bulk_create(create_objects, batch_size=_bulk_create_batch_size(create_objects))
                created += len(create_objects)
                existing_ids.update(obj.id for obj in create_objects)
                existing_by_id.update({obj.id: obj for obj in create_objects})

            changed_by_fields: dict[tuple[str, ...], list[AdminArea]] = {}
            for obj in objects:
                if obj.id not in existing_ids:
                    continue
                changed_fields = _admin_area_changed_fields(existing_by_id[obj.id], obj, compare_fields)
                if not changed_fields:
                    continue
                update_field_names = (*changed_fields, "updated_at")
                changed_by_fields.setdefault(update_field_names, []).append(obj)

            for update_field_names, changed_objects in changed_by_fields.items():
                AdminArea.objects.bulk_update(
                    changed_objects,
                    list(update_field_names),
                    batch_size=_bulk_update_batch_size(len(update_field_names)),
                )
                updated += len(changed_objects)
            known_codes.update(entity.code for entity in level_entities)

        _save_most_populated_links(country_code, entities)
        _save_name_translations(country_code, entities)
        return created, updated

    @transaction.atomic
    def delete_missing(self, country_code: str, ids: set[str]) -> int:
        existing_ids = AdminArea.objects.filter(country_code=country_code).values_list("id", flat=True)
        missing_ids = [existing_id for existing_id in existing_ids.iterator() if existing_id not in ids]
        delete_visual_assets_for_admin_area_ids(missing_ids)
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

        areas_by_id = _admin_areas_by_id(assignment.area_id for assignment in assignments)
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
                batch_size=_bulk_update_batch_size(2),
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
                batch_size=_bulk_update_batch_size(2),
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
    def __init__(self, write_lock=None):
        self.write_lock = write_lock

    def transaction(self):
        if self.write_lock is None:
            return transaction.atomic()
        return _locked_transaction(self.write_lock)


@contextmanager
def _locked_transaction(write_lock):
    with write_lock:
        with transaction.atomic():
            yield


def _admin_area_changed(existing: AdminArea, candidate: AdminArea, fields: tuple[str, ...]) -> bool:
    return bool(_admin_area_changed_fields(existing, candidate, fields))


def _admin_area_changed_fields(existing: AdminArea, candidate: AdminArea, fields: tuple[str, ...]) -> tuple[str, ...]:
    changed = []
    for field in fields:
        if field == "parent":
            if existing.parent_id != candidate.parent_id:
                changed.append(field)
            continue
        if getattr(existing, field) != getattr(candidate, field):
            changed.append(field)
    return tuple(changed)


def _save_most_populated_links(country_code: str, entities: list[ScrapedAdminArea]) -> int:
    if not entities:
        return 0

    incoming_codes = {str(entity.code) for entity in entities}
    desired_by_id = {}
    for entity in entities:
        target_code = str(entity.most_populated_city_code or "").strip()
        desired_by_id[entity.id] = f"{country_code}_{target_code}" if target_code in incoming_codes else None

    areas_by_id = _admin_areas_by_id(desired_by_id.keys(), country_code=country_code)
    changed = []
    now = timezone.now()
    for area_id, target_id in desired_by_id.items():
        area = areas_by_id.get(area_id)
        if area is None or area.most_populate_city_id == target_id:
            continue
        area.most_populate_city_id = target_id
        area.updated_at = now
        changed.append(area)

    if changed:
        AdminArea.objects.bulk_update(
            changed,
            ["most_populate_city", "updated_at"],
            batch_size=_bulk_update_batch_size(2),
        )
    return len(changed)


def _save_name_translations(country_code: str, entities: list[ScrapedAdminArea]) -> int:
    desired = {}
    for entity in entities:
        source_name = str(entity.name or "").strip()
        for language, text in (entity.translations or {}).items():
            language = normalize_dynamic_language_code(language)
            text = str(text or "").strip()
            if not language or not text or text == source_name:
                continue
            desired[(entity.id, language)] = {
                "subject_type": SUBJECT_ADMIN_AREA,
                "subject_key": entity.id,
                "country_code": str(country_code or "").strip().lower(),
                "field": FIELD_NAME,
                "language": language,
                "source_text": source_name,
                "source_language": "es",
                "text": text,
                "source": "wikidata_label",
                "model": "",
                "prompt_version": "",
                "input_hash": "",
                "needs_review": False,
                "is_active": True,
            }
    if not desired:
        return 0

    subject_keys = sorted({key[0] for key in desired})
    languages = sorted({key[1] for key in desired})
    existing = {}
    for id_batch in _chunks(subject_keys, SQLITE_SAFE_BATCH_SIZE):
        rows = DynamicTranslation.objects.filter(
            subject_type=SUBJECT_ADMIN_AREA,
            country_code=str(country_code or "").strip().lower(),
            field=FIELD_NAME,
            subject_key__in=id_batch,
            language__in=languages,
        )
        existing.update({(row.subject_key, row.language): row for row in rows})

    now = timezone.now()
    created = []
    updated = []
    update_fields = (
        "source_text",
        "source_language",
        "text",
        "source",
        "model",
        "prompt_version",
        "input_hash",
        "needs_review",
        "is_active",
    )
    for key, values in desired.items():
        current = existing.get(key)
        if current is None:
            created.append(DynamicTranslation(created_at=now, updated_at=now, **values))
            continue
        changed = False
        for field_name in update_fields:
            value = values[field_name]
            if getattr(current, field_name) != value:
                setattr(current, field_name, value)
                changed = True
        if changed:
            current.updated_at = now
            updated.append(current)

    if created:
        DynamicTranslation.objects.bulk_create(created, batch_size=SQLITE_SAFE_BATCH_SIZE)
    if updated:
        DynamicTranslation.objects.bulk_update(
            updated,
            [*update_fields, "updated_at"],
            batch_size=_bulk_update_batch_size(len(update_fields) + 1),
        )
    if created or updated:
        clear_dynamic_translation_cache()
    return len(created) + len(updated)


def _bulk_create_batch_size(objects: list[AdminArea]) -> int:
    if not objects:
        return 1
    fields = [field for field in AdminArea._meta.local_concrete_fields if not getattr(field, "generated", False)]
    return min(SQLITE_SAFE_BATCH_SIZE, max(1, connection.ops.bulk_batch_size(fields, objects)))


def _bulk_update_batch_size(field_count: int) -> int:
    max_params = connection.features.max_query_params or SQLITE_DEFAULT_MAX_QUERY_PARAMS
    # Django bulk_update uses each object's pk in CASE/WHEN expressions and in
    # the final WHERE clause. Keep the estimate conservative for SQLite builds
    # with the classic 999-variable limit.
    params_per_object = max(1, (int(field_count) * 2) + 1)
    return min(SQLITE_SAFE_BATCH_SIZE, max(1, max_params // params_per_object))


def _admin_areas_by_id(ids, *, country_code: str = "") -> dict[str, AdminArea]:
    areas: dict[str, AdminArea] = {}
    for id_batch in _chunks((str(item) for item in ids if item), SQLITE_SAFE_BATCH_SIZE):
        query = AdminArea.objects.filter(id__in=id_batch)
        if country_code:
            query = query.filter(country_code=country_code)
        areas.update({area.id: area for area in query})
    return areas


def _chunks(values, size: int):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _normalize_data_wd(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"Q\d+", text) else ""


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
        data_wd=_normalize_data_wd(entity.data_wd),
        annotations=entity.annotations or "",
        created_at=now,
        updated_at=now,
    )
