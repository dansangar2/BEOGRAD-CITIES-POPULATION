# ciudades_del_mundo/services/citypop_adapter.py
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Iterable, Tuple, Dict
from django.db import transaction
from ciudades_del_mundo.models import AdminArea

def _pk(country_code: str, code: str) -> str:
    return f"{country_code}_{code}"

def _recompute_density(pop_latest: int | None, area_km2: Decimal | None) -> Decimal | None:
    if pop_latest is None or area_km2 is None or area_km2 == 0:
        return None
    try:
        return (Decimal(pop_latest) / Decimal(area_km2)).quantize(Decimal("0.0001"))
    except (DivisionByZero, InvalidOperation, Exception):
        return None

def upsert_entities(country_code: str, entities: Iterable) -> Tuple[int, int]:
    created = 0
    updated = 0

    entities_sorted = sorted(entities, key=lambda e: e.level)

    cache: Dict[str, AdminArea] = {
        a.id: a for a in AdminArea.objects.filter(country_code=country_code)
    }

    with transaction.atomic():
        for e in entities_sorted:
            code = e.entity_id
            pk = _pk(country_code, code)

            parent_pk = _pk(country_code, e.parent_id) if getattr(e, "parent_id", None) else None
            parent_obj = cache.get(parent_pk) if parent_pk else None

            # --- AQUÍ: si la entidad trae "size", manda sobre area_km2 ---
            forced_size = getattr(e, "size", None)  # <- viene de tu config
            if forced_size is not None:
                area_km2 = Decimal(str(forced_size))
                density = _recompute_density(getattr(e, "pop_latest", None), area_km2)
            else:
                area_km2 = e.area_km2
                density = e.density

            obj, is_created = AdminArea.objects.update_or_create(
                id=pk,
                defaults=dict(
                    country_code=country_code,
                    code=code,
                    name=e.name,
                    level=e.level,
                    entity_type=e.entity_type,
                    parent=parent_obj,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=e.pop_latest,
                    pop_latest_date=e.pop_latest_date,
                    last_census_year=e.last_census_year,
                    url=e.url,
                ),
            )

            cache[pk] = obj
            created += int(is_created)
            updated += int(not is_created)

    return created, updated