"""Services for allocating representatives to derived subdivisions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import re
import unicodedata

from django.db import transaction

from ciudades_del_mundo.domain import RepresentationConfig, RepresentationSystem
from ciudades_del_mundo.models import NuevoAdminArea


@dataclass(frozen=True)
class RepresentationArea:
    id: str
    code: str
    name: str
    pop_latest: int


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
        .only(
            "id",
            "code",
            "name",
            "pop_latest",
            "representatives",
            "population_index",
            "province_status",
            "depends_on_id",
        )
        .order_by("code")
    )
    if not areas:
        return 0

    if config.system != RepresentationSystem.DHONDT:
        raise ValueError(f"Sistema de representacion no soportado: {config.system}.")

    seats_by_id = allocate_dhondt_representatives_for_nuevo_admin(
        root.country_code,
        areas,
        config,
    )
    updated = 0
    for area in areas:
        seats = seats_by_id.get(area.id, 0)
        if area.representatives != seats:
            area.representatives = seats
            area.save(update_fields=["representatives", "updated_at"])
            updated += 1
    return updated


def allocate_dhondt_representatives_for_nuevo_admin(
    country_code: str,
    areas: list[NuevoAdminArea],
    config: RepresentationConfig,
) -> dict[str, int]:
    """Allocate seats with NuevoAdminArea population indexes and province status."""
    all_areas = list(
        NuevoAdminArea.objects
        .filter(country_code=country_code)
        .only("id", "parent_id", "population_index", "province_status")
    )
    effective_indexes = _effective_population_indexes(all_areas)
    territory_flags = _territory_flags(all_areas)

    areas_by_id = {area.id: area for area in areas}
    owner_by_area_id: dict[str, str | None] = {}
    population_by_owner_id: dict[str, int] = defaultdict(int)

    for area in areas:
        if territory_flags.get(area.id, False):
            owner_by_area_id[area.id] = None
            continue

        owner = _representation_owner(area, areas_by_id, territory_flags)
        owner_by_area_id[area.id] = owner.id
        population_by_owner_id[owner.id] += _scaled_population(
            area.pop_latest,
            effective_indexes.get(area.id, Decimal("1")),
        )

    representation_units = [
        RepresentationArea(
            id=area.id,
            code=area.code,
            name=area.name,
            pop_latest=population_by_owner_id.get(area.id, 0),
        )
        for area in areas
        if owner_by_area_id.get(area.id) == area.id
    ]

    seats_by_owner_id = allocate_dhondt_representatives(representation_units, config)
    return {
        area.id: seats_by_owner_id.get(area.id, 0)
        if owner_by_area_id.get(area.id) == area.id
        else 0
        for area in areas
    }


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


def _effective_population_indexes(areas: list[NuevoAdminArea]) -> dict[str, Decimal]:
    areas_by_id = {area.id: area for area in areas}
    cache: dict[str, Decimal] = {}

    def resolve(area: NuevoAdminArea) -> Decimal:
        if area.id in cache:
            return cache[area.id]

        parent_factor = Decimal("1")
        if area.parent_id:
            parent = areas_by_id.get(area.parent_id)
            if parent is not None:
                parent_factor = resolve(parent)

        own_factor = area.population_index if area.population_index is not None else Decimal("1")
        cache[area.id] = parent_factor * Decimal(own_factor)
        return cache[area.id]

    for area in areas:
        resolve(area)

    return cache


def _territory_flags(areas: list[NuevoAdminArea]) -> dict[str, bool]:
    areas_by_id = {area.id: area for area in areas}
    cache: dict[str, bool] = {}

    def resolve(area: NuevoAdminArea) -> bool:
        if area.id in cache:
            return cache[area.id]

        is_territory = area.province_status == NuevoAdminArea.ProvinceStatus.TERRITORY
        if not is_territory and area.parent_id:
            parent = areas_by_id.get(area.parent_id)
            if parent is not None:
                is_territory = resolve(parent)

        cache[area.id] = is_territory
        return is_territory

    for area in areas:
        resolve(area)

    return cache


def _representation_owner(
    area: NuevoAdminArea,
    areas_by_id: dict[str, NuevoAdminArea],
    territory_flags: dict[str, bool],
) -> NuevoAdminArea:
    current = area
    seen: set[str] = set()

    while current.province_status == NuevoAdminArea.ProvinceStatus.DEPENDENCY:
        if not current.depends_on_id:
            raise ValueError(
                f"La dependencia '{current.name}' debe indicar de que provincia depende."
            )
        if current.id in seen:
            raise ValueError(f"Dependencia circular detectada en '{area.name}'.")
        seen.add(current.id)

        owner = areas_by_id.get(current.depends_on_id)
        if owner is None:
            raise ValueError(
                f"La dependencia '{current.name}' apunta a '{current.depends_on_id}', "
                f"que no existe en el nivel de representacion."
            )
        if territory_flags.get(owner.id, False):
            raise ValueError(
                f"La dependencia '{current.name}' no puede depender del territorio '{owner.name}'."
            )
        current = owner

    return current


def _scaled_population(population: int | None, population_index: Decimal) -> int:
    if population is None:
        return 0
    value = Decimal(max(population, 0)) * population_index
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
