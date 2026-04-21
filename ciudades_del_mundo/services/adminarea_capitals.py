# ciudades_del_mundo/services/adminarea_capitals.py
from __future__ import annotations
from typing import Iterable, List, Tuple, Union
import importlib

from django.db import transaction
from django.db.models import Max

from ciudades_del_mundo.models import AdminArea

CapitalValue = Union[str, List[str]]

def _load_capital_map_from_command() -> dict:
    """
    Carga CAPITAL_MAP exclusivamente desde:
    ciudades_del_mundo.management.commands.assign_admin_capitals

    Se hace import dinámico dentro de la función para evitar import circular.
    """
    try:
        mod = importlib.import_module(
            "ciudades_del_mundo.management.commands.assign_admin_capitals"
        )
        return getattr(mod, "CAPITAL_MAP", {}) or {}
    except Exception:
        return {}

def _country_atomic_level(country_code: str) -> int | None:
    mx = (AdminArea.objects
          .filter(country_code=country_code)
          .aggregate(mx=Max("level"))["mx"])
    return int(mx) if mx is not None else None

def _descendants_at_level(seed: AdminArea, target_level: int) -> Iterable[str]:
    if seed.level == target_level:
        return [seed.id]

    frontier = [seed]
    result: set[str] = set()
    while frontier:
        cur = frontier.pop()
        if cur.level == target_level:
            result.add(cur.id)
        elif cur.level < target_level:
            frontier.extend(AdminArea.objects.filter(parent=cur))
    return result

@transaction.atomic
def assign_capitals_and_biggest_city_from_map() -> Tuple[int, int]:
    """
    - Asigna capital(es) según CAPITAL_MAP definido en assign_admin_capitals.py.
    - Recalcula most_populate_city buscando el nivel más bajo y el mayor pop_latest.
    Devuelve (num_areas_capitals_actualizadas, num_areas_biggest_actualizadas).
    """
    capital_map = _load_capital_map_from_command()
    cap_changes = 0
    biggest_changes = 0

    if not capital_map:
        return (0, 0)

    for country_code, mapping in capital_map.items():
        atomic_level = _country_atomic_level(country_code)
        if atomic_level is None:
            continue

        # 1) CAPITAL(ES) desde mapa
        for area_id, cap_value in mapping.items():
            area = AdminArea.objects.filter(id=area_id, country_code=country_code).first()
            if not area:
                continue

            cap_ids: List[str] = list(cap_value) if isinstance(cap_value, (list, tuple, set)) else [str(cap_value)]

            # valida existencia + (opcional) descendencia
            valid_cap_ids: List[str] = []
            for cid in cap_ids:
                cap = AdminArea.objects.filter(id=cid, country_code=country_code).first()
                if not cap:
                    continue
                # verifica que la capital es descendiente del área
                cur = cap
                is_desc = False
                while cur:
                    if cur.id == area.id:
                        is_desc = True
                        break
                    cur = cur.parent
                if is_desc:
                    valid_cap_ids.append(cap.id)

            # Soporte tanto para M2M 'capitals' como para FK 'capital'
            if hasattr(area, "capitals"):
                current_ids = set(area.capitals.values_list("id", flat=True))
                new_ids = set(valid_cap_ids)
                if current_ids != new_ids:
                    area.capitals.set(list(new_ids))
                    cap_changes += 1
            else:
                # Si el modelo aún usa FK 'capital', usa la primera válida
                first_id = valid_cap_ids[0] if valid_cap_ids else None
                if first_id and getattr(area, "capital_id", None) != first_id:
                    area.capital_id = first_id
                    area.save(update_fields=["capital"])
                    cap_changes += 1

        # 2) Ciudad más poblada automática para TODAS las áreas del país
        for area in AdminArea.objects.filter(country_code=country_code):
            if atomic_level is None or area.level >= atomic_level:
                continue

            atomic_ids = _descendants_at_level(area, atomic_level)
            if not atomic_ids:
                continue

            top = (AdminArea.objects
                   .filter(id__in=atomic_ids)
                   .exclude(pop_latest__isnull=True)
                   .order_by("-pop_latest")
                   .only("id")
                   .first())
            if top and area.most_populate_city_id != top.id:
                area.most_populate_city = top
                area.save(update_fields=["most_populate_city"])
                biggest_changes += 1

    return (cap_changes, biggest_changes)