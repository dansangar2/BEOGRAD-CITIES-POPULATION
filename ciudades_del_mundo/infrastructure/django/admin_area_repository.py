from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ciudades_del_mundo.domain import ScrapedAdminArea
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
