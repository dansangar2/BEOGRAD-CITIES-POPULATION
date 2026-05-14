"""Services for allocating representatives to derived subdivisions."""

from __future__ import annotations

import re
import unicodedata

from django.db import transaction

from ciudades_del_mundo.domain import RepresentationConfig, RepresentationSystem
from ciudades_del_mundo.models import NuevoAdminArea


def representation_config_from_mapping(data: dict | None) -> RepresentationConfig | None:
    """Normalize multiple legacy shapes into a typed representation config."""
    if not data:
        return None
    if isinstance(data, tuple) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError(f"REPRESENTATION/ESCANHOS debe ser dict, recibido {type(data).__name__}.")

    normalized = dict(data)
    if "level" not in normalized and "nivel" in normalized:
        normalized["level"] = normalized["nivel"]
    if "total" not in normalized and "escanhos" in normalized:
        normalized["total"] = normalized["escanhos"]
    normalized.setdefault("system", RepresentationSystem.DHONDT)

    return RepresentationConfig.from_mapping(normalized)


@transaction.atomic
def assign_nuevo_admin_representatives(country_id: str, config: RepresentationConfig) -> int:
    """Persist representative allocation for one derived country tree."""
    root = NuevoAdminArea.objects.get(id=country_id)
    areas = list(
        NuevoAdminArea.objects.filter(country_code=root.country_code, level=config.level)
        .only("id", "code", "name", "pop_latest", "representatives")
        .order_by("code")
    )
    if not areas:
        return 0

    if config.system != RepresentationSystem.DHONDT:
        raise ValueError(f"Sistema de representacion no soportado: {config.system}.")

    seats_by_id = allocate_dhondt_representatives(areas, config)
    updated = 0
    for area in areas:
        seats = seats_by_id[area.id]
        if area.representatives != seats:
            area.representatives = seats
            area.save(update_fields=["representatives", "updated_at"])
            updated += 1
    return updated


def allocate_dhondt_representatives(areas, config: RepresentationConfig) -> dict[str, int]:
    """Compute seat allocation using the D'Hondt method plus min/max rules."""
    total = config.total_for_populations(area.pop_latest for area in areas)
    seats = {area.id: _minimum_for(area, config) for area in areas}
    maximums = {area.id: _maximum_for(area, config) for area in areas}

    fixed = sum(seats.values())
    if fixed > total:
        raise ValueError(
            f"No se pueden asignar escanos: minimos={fixed} supera total={total}."
        )

    remaining = total - fixed
    for _ in range(remaining):
        candidate = _next_dhondt_candidate(areas, seats, maximums)
        if candidate is None:
            break
        seats[candidate.id] += 1

    return seats


def _minimum_for(area, config: RepresentationConfig) -> int:
    return _matched_int(area, config.min_exceptions, config.minimum) or 0


def _maximum_for(area, config: RepresentationConfig) -> int | None:
    return _matched_int(area, config.max_exceptions, config.maximum)


def _matched_int(area, values: dict[str, int], default: int | None) -> int | None:
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


def _next_dhondt_candidate(areas, seats: dict[str, int], maximums: dict[str, int | None]):
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
