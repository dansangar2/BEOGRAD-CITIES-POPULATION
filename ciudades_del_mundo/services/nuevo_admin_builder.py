"""Builder service that assembles `NuevoAdminArea` from inclusion specs."""

from __future__ import annotations

import unicodedata
import re
from typing import Optional, Iterable

from django.db import transaction
from django.db.models import Sum

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea


ORIGINAL_MUNICIPAL_LEVEL: dict[str, int] = {
    "spain": 3,
    "france": 4,
    "portugal": 2,
    "andorra": 2,
    "gibraltar": 1,
    "morocco": 3,
    "italy": 3,
    "algeria": 2,
    "westernsahara": 3,
    "austria": 3,
    "hungary": 3,
    "slovakia": 3,
    "czechrep": 3,
    "croatia": 2,
    "montenegro": 1,
    "bosnia": 2,
    "slovenia": 2,
    "serbia": 2,
    "romania": 3,
    "malta": 2,
    "capeverde": 1,
    "mauritania": 3,
    "mali": 2,
    "tunisia": 2,
    "libya": 3,
    "vaticancity": 0,
    "sanmarino": 2,
    "luxembourg": 2,
    "netherlands": 3,
    "belgium": 4,
}

MAKE_CITIES = {
    "morocco": [
        {
            "city": "Tanger",
            "district_types": ["Arrondissement"],
            "new_type": "City",
            "from": ["Tanger - Assilah"],
        },
    ],
}


def _norm(value: str | None) -> str:
    """
    Normaliza texto: minúsculas, sin acentos, espacios compactados.
    Útil para comparar nombres/códigos de forma robusta.
    """
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _slugify_code(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:64]


def _resolve_adminarea_in_qs(qs, label_or_id: str) -> Optional[AdminArea]:
    """
    Busca dentro de 'qs' (queryset de AdminArea) por:
    - id exacto
    - code__iexact
    - name__iexact
    - nombre/código normalizado (sin acentos y en minúsculas)
    - y, como último recurso, name__icontains / code__icontains
    """
    if not label_or_id:
        return None

    # 1) id exacto
    obj = qs.filter(id=label_or_id).first()
    if obj:
        return obj

    # 2) code / name exactos (case-insensitive)
    obj = qs.filter(code__iexact=label_or_id).first()
    if obj:
        return obj
    obj = qs.filter(name__iexact=label_or_id).first()
    if obj:
        return obj

    # 3) búsqueda por normalización
    n = _norm(label_or_id)
    cache = list(qs.only("id", "code", "name"))
    by_code = {_norm(a.code): a for a in cache if a.code}
    by_name = {_norm(a.name): a for a in cache}

    candidate = by_code.get(n) or by_name.get(n)
    if candidate:
        return candidate

    # 4) Último recurso: icontains, priorizando nivel más detallado (level DESC)
    try:
        qs_ordered = qs.order_by("-level")
    except Exception:
        qs_ordered = qs

    candidate = qs_ordered.filter(name__icontains=label_or_id).first()
    if candidate:
        return candidate

    candidate = qs_ordered.filter(code__icontains=label_or_id).first()
    return candidate


def _lookup_many(country_code: str, level: int, labels_or_seq) -> list[AdminArea]:
    """
    Devuelve una lista de AdminArea para ese país y nivel,
    a partir de un string o una secuencia de strings.
    """
    if isinstance(labels_or_seq, (str, int)):
        labels = [labels_or_seq]
    else:
        labels = list(labels_or_seq)

    qs = AdminArea.objects.filter(country_code=country_code, level=level)
    found: list[AdminArea] = []
    missing: list[str] = []

    for label in labels:
        obj = _resolve_adminarea_in_qs(qs, str(label))
        if obj:
            found.append(obj)
        else:
            missing.append(str(label))

    if missing:
        raise ValueError(
            f"No se encontraron AdminArea para {missing} en nivel {level} "
            f"para el país '{country_code}'."
        )

    return found


def _descendants_at_level(root: AdminArea, target_level: int) -> list[AdminArea]:
    if root.level > target_level:
        raise ValueError(
            f"El área '{root.id}' está en nivel {root.level} y no se puede "
            f"proyectar a un nivel menor ({target_level})."
        )

    if root.level == target_level:
        return [root]

    current_level = root.level
    current = [root]

    while current_level < target_level and current:
        ids = [a.id for a in current]
        current = list(AdminArea.objects.filter(parent_id__in=ids))
        current_level += 1

    return current


def _to_atomic_ids(items: Iterable[AdminArea], atomic_level: int) -> set[str]:
    result: set[str] = set()

    for area in items:
        if area.level == atomic_level:
            result.add(area.id)
        elif area.level < atomic_level:
            descendants = _descendants_at_level(area, atomic_level)
            result |= {d.id for d in descendants}
        else:
            raise ValueError(
                f"No se puede convertir desde el nivel {area.level} a un nivel "
                f"más alto ({atomic_level}) para el área '{area.id}'."
            )

    return result


def _expand_to_municipal(items: Iterable[AdminArea], country_code: str) -> set[str]:
    """
    Convierte una colección de AdminArea (de cualquier nivel) en ids de municipios
    (nivel ORIGINAL_MUNICIPAL_LEVEL[country_code]).
    """
    atomic_level = ORIGINAL_MUNICIPAL_LEVEL.get(country_code)
    if atomic_level is None:
        raise ValueError(
            f"No se ha definido ORIGINAL_MUNICIPAL_LEVEL para el país origen '{country_code}'."
        )
    return _to_atomic_ids(items, atomic_level)


def _round_area(value: Decimal | None) -> Decimal | None:
    """
    Redondea el área a 2 decimales (km²) con ROUND_HALF_UP.
    Si value es None, devuelve None.
    """
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Builder principal
# ---------------------------------------------------------------------------

@transaction.atomic
def create_nuevo_area_from_spec(
    *,
    parent_country_id: str,
    new_name: str,
    include_spec: dict,
    entity_type: str | None = None,
    forced_area_km2: Decimal | float | int | None = None,
    m2m_field: str = "municipios_originales",
    new_code: str | None = None,
    capitals: list[str] | None = None,
    capital_level_by_country: dict[str, int] | None = None,
    auto_set_most_populated: bool = True,
) -> NuevoAdminArea:
    """
    - code se guarda como "code jerárquico" (path):
        - si parent es COUNTRY: code = raw_code
        - si no: code = parent.code + "-" + raw_code (sin duplicar si ya viene prefijado)
    - id se guarda como: <country_code>-<code_jerárquico>

    Ej:
      parent (Reino): id=austria_empire-LYV, code=LYV
      child  (Reino): id=austria_empire-LYV-LYV, code=LYV-LYV
    """

    parent = NuevoAdminArea.objects.select_related("parent").get(id=parent_country_id)

    parent_level = parent.level or 0
    new_level = parent_level + 1

    default_atomic = parent.municipal_level

    include_map = {k: v for k, v in include_spec.items() if k != "restar"}
    restar_raw = include_spec.get("restar", {}) or {}

    macro_ids: set[str] = set()
    mun_from_macros: set[str] = set()
    mun_extra_incluidos: set[str] = set()
    mun_restar: set[str] = set()

    def _labels_to_list(labels_raw):
        if labels_raw is None:
            return []
        if isinstance(labels_raw, (str, int)):
            return [labels_raw]
        try:
            return list(labels_raw)
        except TypeError:
            raise ValueError(f"No se puede convertir {labels_raw!r} en lista de etiquetas.")

    def _municipal_level_for(cc: str) -> int:
        lvl = ORIGINAL_MUNICIPAL_LEVEL.get(cc)
        if lvl is None:
            if default_atomic is None:
                raise ValueError(
                    f"No se ha definido ORIGINAL_MUNICIPAL_LEVEL para '{cc}' "
                    f"y el nodo padre '{parent.id}' tampoco tiene municipal_level."
                )
            return default_atomic
        return lvl

    def _parse_include_group(value: dict, lvl: int, ctx: str):
        if not isinstance(value, dict):
            raise ValueError(
                f"En '{ctx}' para nivel {lvl} se esperaba un dict "
                f"{{country_code: [labels]}}, recibido: {type(value).__name__}."
            )

        nonlocal macro_ids, mun_from_macros, mun_extra_incluidos

        for cc, labels_raw in value.items():
            labels = _labels_to_list(labels_raw)
            if not labels:
                continue

            items = _lookup_many(cc, lvl, labels)
            atomic_level = _municipal_level_for(cc)

            if lvl == atomic_level:
                mun_extra_incluidos |= _expand_to_municipal(items, cc)
            else:
                macro_ids |= {a.id for a in items}
                mun_from_macros |= _expand_to_municipal(items, cc)

    def _parse_restar_group(value: dict, lvl: int, ctx: str):
        if not isinstance(value, dict):
            raise ValueError(
                f"En '{ctx}' para nivel {lvl} se esperaba un dict "
                f"{{country_code: [labels]}}, recibido: {type(value).__name__}."
            )

        nonlocal mun_restar

        for cc, labels_raw in value.items():
            labels = _labels_to_list(labels_raw)
            if not labels:
                continue

            items = _lookup_many(cc, lvl, labels)
            mun_restar |= _expand_to_municipal(items, cc)

    for key, value in include_map.items():
        lvl = int(key)
        _parse_include_group(value, lvl, "include")

    for key, value in restar_raw.items():
        lvl = int(key)
        _parse_restar_group(value, lvl, "restar")

    mun_final_ids = (mun_from_macros | mun_extra_incluidos) - mun_restar

    if not macro_ids and not mun_final_ids:
        raise ValueError(f"La especificación para '{new_name}' no produjo ninguna unidad.")

    # ---------------------------------------------------------
    # Agregados de área / población
    # ---------------------------------------------------------
    macro_qs = AdminArea.objects.filter(id__in=macro_ids)
    agg_macro = macro_qs.aggregate(
        total_area=Sum("area_km2"),
        total_pop=Sum("pop_latest"),
    )
    area_macro = agg_macro["total_area"] or Decimal("0")
    pop_macro = agg_macro["total_pop"] or 0

    extra_qs = AdminArea.objects.filter(id__in=mun_extra_incluidos)
    restar_qs = AdminArea.objects.filter(id__in=mun_restar)

    agg_extra = extra_qs.aggregate(
        total_area=Sum("area_km2"),
        total_pop=Sum("pop_latest"),
    )
    agg_restar = restar_qs.aggregate(
        total_area=Sum("area_km2"),
        total_pop=Sum("pop_latest"),
    )

    area_extra = agg_extra["total_area"] or Decimal("0")
    pop_extra = agg_extra["total_pop"] or 0

    area_restar = agg_restar["total_area"] or Decimal("0")
    pop_restar = agg_restar["total_pop"] or 0

    total_area = area_macro + area_extra - area_restar
    total_pop = pop_macro + pop_extra - pop_restar

    if forced_area_km2 is not None:
        try:
            total_area = Decimal(str(forced_area_km2))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("forced_area_km2 debe ser convertible a Decimal")

    total_area = _round_area(total_area)

    density = None
    if total_area and total_pop is not None:
        try:
            if Decimal(total_area) != 0:
                density = Decimal(total_pop) / Decimal(total_area)
        except (InvalidOperation, ZeroDivisionError):
            density = None

    # ---------------------------------------------------------
    # Crear el NuevoAdminArea (ID y CODE jerárquicos)
    # ---------------------------------------------------------
    raw_code = (new_code or _slugify_code(new_name)).strip()

    if parent.level == NuevoAdminArea.Level.COUNTRY:
        code_val = raw_code
    else:
        if parent.code and raw_code.startswith(parent.code + "-"):
            code_val = raw_code
        else:
            code_val = f"{parent.code}-{raw_code}" if parent.code else raw_code

    new_id = f"{parent.country_code}-{code_val}"

    obj = NuevoAdminArea.objects.create(
        id=new_id,
        country_code=parent.country_code,
        code=code_val,
        name=new_name,
        level=new_level,
        entity_type=entity_type or "Subdivision",
        parent=parent,
        area_km2=total_area,
        density=density,
        pop_latest=total_pop,
    )

    # ---------------------------------------------------------
    # Vincular municipios_originales (M2M)
    # ---------------------------------------------------------
    m2m = getattr(obj, m2m_field, None)
    if m2m is None:
        raise AttributeError(f"El campo M2M '{m2m_field}' no existe en NuevoAdminArea.")

    atomic_qs = AdminArea.objects.filter(id__in=mun_final_ids) if mun_final_ids else AdminArea.objects.none()
    m2m.set(atomic_qs)

    # ---------------------------------------------------------
    # Capitales y ciudad más poblada
    # ---------------------------------------------------------
    if capitals:
        capital_objs = []
        for raw in capitals:
            label = str(raw).strip()
            if not label:
                continue

            chosen = None
            if capital_level_by_country:
                for cc, capital_level in capital_level_by_country.items():
                    if capital_level is None:
                        continue

                    candidate_qs = AdminArea.objects.filter(
                        country_code=cc,
                        level=capital_level,
                    )
                    candidate = _resolve_adminarea_in_qs(candidate_qs, label)
                    if not candidate:
                        continue

                    atomic_level = _municipal_level_for(cc)
                    if candidate.level == atomic_level and candidate.id in mun_final_ids:
                        chosen = candidate
                        break

                    if candidate.level < atomic_level:
                        descendant_ids = {
                            d.id for d in _descendants_at_level(candidate, atomic_level)
                        }
                        if descendant_ids & mun_final_ids:
                            chosen = candidate
                            break

                    if candidate.id in macro_ids:
                        chosen = candidate
                        break

            if not chosen:
                chosen = _resolve_adminarea_in_qs(atomic_qs, label)
            if not chosen:
                chosen = _resolve_adminarea_in_qs(macro_qs, label)

            if not chosen:
                raise ValueError(
                    f"La capital '{raw}' no está dentro del territorio definido para '{new_name}'."
                )

            capital_objs.append(chosen)

        obj.capitals.set(capital_objs)

    if auto_set_most_populated:
        base_qs = atomic_qs if atomic_qs.exists() else macro_qs
        top = base_qs.exclude(pop_latest__isnull=True).order_by("-pop_latest").first()
        obj.most_populate_city = top
        obj.save(update_fields=["most_populate_city"])

    return obj
