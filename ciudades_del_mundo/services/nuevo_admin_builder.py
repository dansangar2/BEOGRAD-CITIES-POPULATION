"""Builder service that assembles ``NuevoAdminArea`` from inclusion specs.

Recipe nodes describe source areas to include and optionally subtract. This
service resolves those source selectors, expands them to each country's
municipal level, aggregates area/population, and persists the derived node.
"""

from __future__ import annotations

import unicodedata
import re
from typing import Optional, Iterable
from urllib.parse import unquote, urlparse

from django.db import transaction
from django.db.models import Sum

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
from ciudades_del_mundo.services.source_population_indices import SourcePopulationIndexRegistry


# ---------------------------------------------------------------------------
# Source expansion settings
# ---------------------------------------------------------------------------

ORIGINAL_MUNICIPAL_LEVEL: dict[str, int] = {
    "spain": 3,
    "france": 4,
    "portugal": 2,
    "andorra": 2,
    "gibraltar": 1,
    "puertorico": 3,
    "equatorialguinea": 2,
    "morocco": 3,
    "cuba": 2,
    "italy": 3,
    "algeria": 2,
    "westernsahara": 3,
    "austria": 4,
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
    "mali": 3,
    "tunisia": 2,
    "libya": 2,
    "sudan": 2,
    "southsudan": 2,
    "mexico": 2,
    "usa": 3,
    "vaticancity": 0,
    "sanmarino": 2,
    "luxembourg": 2,
    "netherlands": 3,
    "belgium": 4,
    "canada": 3,
    "guatemala": 2,
    "elsalvador": 3,
    "belize": 1,
    "honduras": 2,
    "nicaragua": 2,
    "costarica": 3,
    "panama": 3,
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


# ---------------------------------------------------------------------------
# City merge status preferences
# ---------------------------------------------------------------------------

DEFAULT_CITY_MERGE_STATUS_PREFERENCE = (
    AdminArea.CityMergeStatus.UNIFIED,
    AdminArea.CityMergeStatus.NONE,
    AdminArea.CityMergeStatus.SOURCE,
)

MOST_POPULATED_CITY_MERGE_STATUSES = (
    int(AdminArea.CityMergeStatus.NONE),
    int(AdminArea.CityMergeStatus.UNIFIED),
)

_CITY_MERGE_STATUS_ALIASES = {
    "0": AdminArea.CityMergeStatus.NONE,
    "none": AdminArea.CityMergeStatus.NONE,
    "normal": AdminArea.CityMergeStatus.NONE,
    "no unificada": AdminArea.CityMergeStatus.NONE,
    "1": AdminArea.CityMergeStatus.UNIFIED,
    "unified": AdminArea.CityMergeStatus.UNIFIED,
    "unificada": AdminArea.CityMergeStatus.UNIFIED,
    "ciudad unificada": AdminArea.CityMergeStatus.UNIFIED,
    "2": AdminArea.CityMergeStatus.SOURCE,
    "source": AdminArea.CityMergeStatus.SOURCE,
    "fuente": AdminArea.CityMergeStatus.SOURCE,
    "fuente de ciudad unificada": AdminArea.CityMergeStatus.SOURCE,
}


# ---------------------------------------------------------------------------
# Text, code and merge-status helpers
# ---------------------------------------------------------------------------

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


def _coerce_city_merge_status(value) -> int:
    if isinstance(value, AdminArea.CityMergeStatus):
        return int(value)
    if isinstance(value, int):
        return value
    normalized = _norm(str(value))
    if normalized in _CITY_MERGE_STATUS_ALIASES:
        return int(_CITY_MERGE_STATUS_ALIASES[normalized])
    return int(value)


def _city_merge_status_preference(value=None) -> tuple[int, ...]:
    if value is None:
        preferred = [int(status) for status in DEFAULT_CITY_MERGE_STATUS_PREFERENCE]
    elif isinstance(value, (str, int, AdminArea.CityMergeStatus)):
        preferred = [_coerce_city_merge_status(value)]
    else:
        preferred = [_coerce_city_merge_status(item) for item in value]

    result: list[int] = []
    for status in preferred + [
        int(AdminArea.CityMergeStatus.NONE),
        int(AdminArea.CityMergeStatus.UNIFIED),
        int(AdminArea.CityMergeStatus.SOURCE),
    ]:
        status = int(status)
        if status not in result:
            result.append(status)
    return tuple(result)


# ---------------------------------------------------------------------------
# Source label preference helpers
# ---------------------------------------------------------------------------

def _preferred_adminarea(
    candidates: Iterable[AdminArea],
    city_merge_status_preference=None,
) -> Optional[AdminArea]:
    ordered = _preferred_adminareas(candidates, city_merge_status_preference)
    return ordered[0] if ordered else None


def _preferred_adminareas(
    candidates: Iterable[AdminArea],
    city_merge_status_preference=None,
) -> list[AdminArea]:
    unique = {candidate.id: candidate for candidate in candidates}
    if not unique:
        return []

    status_order = {
        status: index
        for index, status in enumerate(_city_merge_status_preference(city_merge_status_preference))
    }
    fallback_order = len(status_order)
    return sorted(
        unique.values(),
        key=lambda area: (
            status_order.get(int(area.city_merge_status or 0), fallback_order),
            -(area.level or 0),
            area.id,
        ),
    )


def _label_and_city_merge_preference(raw_label, default_preference=None) -> tuple[str, tuple[int, ...]]:
    preference = default_preference
    label = raw_label

    if isinstance(raw_label, dict):
        for key in ("label", "name", "id", "code"):
            if key in raw_label and raw_label[key] not in (None, ""):
                label = raw_label[key]
                break
        for key in ("city_merge_status", "prefer_city_merge_status", "merge_status"):
            if key in raw_label and raw_label[key] not in (None, ""):
                preference = raw_label[key]
                break
    else:
        parent_label, child_label = _split_parent_label(raw_label)
        if parent_label and child_label:
            label = child_label

    if label in (None, ""):
        raise ValueError(f"Etiqueta AdminArea invalida: {raw_label!r}.")

    return str(label), _city_merge_status_preference(preference)


def _split_parent_label(raw_label) -> tuple[str | None, str | None]:
    if not isinstance(raw_label, str) or "|" not in raw_label:
        return None, None
    parent_label, child_label = raw_label.split("|", 1)
    parent_label = parent_label.strip()
    child_label = child_label.strip()
    if not parent_label or not child_label:
        return None, None
    return parent_label, child_label


def _label_parent(raw_label) -> str | None:
    if not isinstance(raw_label, dict):
        parent_label, _child_label = _split_parent_label(raw_label)
        return parent_label
    for key in ("parent", "parent_label", "parent_name", "state", "admin1"):
        value = raw_label.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _label_lookup_value(label_or_id: str) -> str:
    value = str(label_or_id or "").strip()
    if "|" not in value:
        return value
    _parent_label, raw_name = value.split("|", 1)
    return raw_name.strip() or value


# ---------------------------------------------------------------------------
# Capital display-name helpers
# ---------------------------------------------------------------------------

def _language_code(value) -> str:
    normalized = _norm(str(value))
    aliases = {
        "es": "es",
        "spa": "es",
        "spanish": "es",
        "espanol": "es",
        "castellano": "es",
    }
    return aliases.get(normalized, normalized)


def _capital_names_by_language(raw_label) -> dict[str, str]:
    if not isinstance(raw_label, dict):
        return {}

    names: dict[str, str] = {}
    names_raw = None
    for key in ("names_by_language", "names", "translations", "nombres"):
        if key in raw_label:
            names_raw = raw_label[key]
            break

    if names_raw is not None:
        if not isinstance(names_raw, dict):
            raise ValueError(
                "Los nombres de capital por idioma deben ser un dict "
                "{idioma: nombre}."
            )
        for language, name in names_raw.items():
            if name not in (None, ""):
                names[_language_code(language)] = str(name)

    for key in ("es", "spa", "spanish", "espanol", "español", "castellano"):
        if key in raw_label and raw_label[key] not in (None, ""):
            names[_language_code(key)] = str(raw_label[key])

    return names


def _label_city_preference_and_names(
    raw_label,
    default_preference=None,
) -> tuple[str, tuple[int, ...], dict[str, str]]:
    label, preference = _label_and_city_merge_preference(raw_label, default_preference)
    return label, preference, _capital_names_by_language(raw_label)


def _add_capital_name_overrides(
    target: dict[str, dict[str, str]],
    capital: AdminArea,
    names_by_language: dict[str, str],
) -> None:
    for language, name in names_by_language.items():
        if name:
            target.setdefault(language, {})[capital.id] = name


# ---------------------------------------------------------------------------
# Duplicate detection helpers
# ---------------------------------------------------------------------------

def _remember_duplicate(
    seen: dict[str, str],
    duplicates: dict[str, list[str]],
    key: str,
    origin: str,
) -> None:
    if key in seen:
        origins = duplicates.setdefault(key, [seen[key]])
        if origin not in origins:
            origins.append(origin)
        return
    seen[key] = origin


def _duplicate_origin(ctx: str, level: int, country_code: str, area: AdminArea) -> str:
    return f"{ctx} nivel {level}, pais '{country_code}', {area.name} ({area.id})"


def _raise_duplicate_source_data(
    new_name: str,
    source_duplicates: dict[str, list[str]],
    municipal_duplicates: dict[str, list[str]],
) -> None:
    if not source_duplicates and not municipal_duplicates:
        return

    lines = [f"Datos repetidos en la especificacion de '{new_name}'."]
    _append_duplicate_lines(lines, "Entidades repetidas", source_duplicates)
    _append_duplicate_lines(
        lines,
        "Municipios repetidos tras expandir entidades en el mismo nivel",
        municipal_duplicates,
    )
    raise ValueError("\n".join(lines))


def _append_duplicate_lines(
    lines: list[str],
    title: str,
    duplicates: dict[str, list[str]],
    *,
    limit: int = 30,
) -> None:
    if not duplicates:
        return

    lines.append(f"{title}:")
    items = sorted(duplicates.items())
    for key, origins in items[:limit]:
        lines.append(f"- {key}: " + " | ".join(origins))
    if len(items) > limit:
        lines.append(f"- ... y {len(items) - limit} repetido(s) mas.")


# ---------------------------------------------------------------------------
# Source area lookup helpers
# ---------------------------------------------------------------------------

def _labels_to_list(labels_raw):
    if labels_raw is None:
        return []
    if isinstance(labels_raw, (str, int)) or _is_label_selector(labels_raw):
        return [labels_raw]
    try:
        return list(labels_raw)
    except TypeError:
        raise ValueError(f"No se puede convertir {labels_raw!r} en lista de etiquetas.")


def _is_label_selector(value) -> bool:
    return isinstance(value, dict) and bool(
        {
            "label",
            "name",
            "id",
            "code",
            "city_merge_status",
            "prefer_city_merge_status",
            "merge_status",
            "parent",
            "parent_label",
            "parent_name",
            "state",
            "admin1",
        }
        & set(value)
    )


def _resolve_adminarea_in_qs(
    qs,
    label_or_id: str,
    city_merge_status_preference=None,
) -> Optional[AdminArea]:
    """
    Busca dentro de 'qs' (queryset de AdminArea) por:
    - id exacto
    - code__iexact
    - name__iexact
    - nombre/código normalizado (sin acentos y en minúsculas)
    - y, como último recurso, name__icontains / code__icontains
    """
    lookup_value = _label_lookup_value(label_or_id)
    if not lookup_value:
        return None

    # 1) id exacto
    obj = qs.filter(id=lookup_value).first()
    if obj:
        return obj

    # 2) code / name exactos (case-insensitive)
    obj = _preferred_adminarea(
        list(qs.filter(code__iexact=lookup_value)) + list(qs.filter(name__iexact=lookup_value)),
        city_merge_status_preference,
    )
    if obj is not None:
        return obj

    # 3) búsqueda por normalización
    n = _norm(lookup_value)
    cache = list(qs.only("id", "code", "name", "level", "city_merge_status"))
    candidate = _preferred_adminarea(
        [
            area
            for area in cache
            if n in {_norm(area.id), _norm(area.code), _norm(area.name)}
        ],
        city_merge_status_preference,
    )
    if candidate:
        return candidate

    # 4) Último recurso: icontains, priorizando nivel más detallado (level DESC)
    try:
        qs_ordered = qs.order_by("-level")
    except Exception:
        qs_ordered = qs

    candidate = _preferred_adminarea(
        qs_ordered.filter(name__icontains=lookup_value),
        city_merge_status_preference,
    )
    if candidate:
        return candidate

    return _preferred_adminarea(
        qs_ordered.filter(code__icontains=lookup_value),
        city_merge_status_preference,
    )


def _lookup_many(
    country_code: str,
    level: int,
    labels_or_seq,
    city_merge_status_preference=None,
) -> list[AdminArea]:
    """
    Devuelve una lista de AdminArea para ese país y nivel,
    a partir de un string o una secuencia de strings.
    """
    return [
        area
        for area, _preference in _lookup_many_with_preferences(
            country_code,
            level,
            labels_or_seq,
            city_merge_status_preference,
        )
    ]


def _lookup_many_with_preferences(
    country_code: str,
    level: int,
    labels_or_seq,
    city_merge_status_preference=None,
) -> list[tuple[AdminArea, tuple[int, ...]]]:
    labels = _labels_to_list(labels_or_seq)

    qs = AdminArea.objects.filter(country_code=country_code, level=level)
    found: list[tuple[AdminArea, tuple[int, ...]]] = []
    missing: list[str] = []

    for raw_label in labels:
        label, preference = _label_and_city_merge_preference(
            raw_label,
            city_merge_status_preference,
        )
        lookup_qs = qs
        parent_label = _label_parent(raw_label)
        if parent_label:
            parent = _resolve_adminarea_in_qs(
                AdminArea.objects.filter(country_code=country_code, level__lt=level),
                parent_label,
                city_merge_status_preference=preference,
            )
            if not parent:
                missing.append(f"{label} bajo {parent_label}")
                continue
            lookup_qs = lookup_qs.filter(parent_id=parent.id)
        lookup_qs = lookup_qs.filter(
            city_merge_status__in=_child_city_merge_statuses(preference)
        )

        obj = _resolve_adminarea_in_qs(
            lookup_qs,
            label,
            city_merge_status_preference=preference,
        )
        if obj:
            found.append((obj, preference))
        else:
            missing.append(label)

    if missing:
        raise ValueError(
            f"No se encontraron AdminArea para {missing} en nivel {level} "
            f"para el país '{country_code}'."
        )

    return found


# ---------------------------------------------------------------------------
# Source expansion helpers
# ---------------------------------------------------------------------------

def _child_city_merge_statuses(city_merge_status_preference=None) -> tuple[int, ...]:
    primary = _city_merge_status_preference(city_merge_status_preference)[0]
    if primary == int(AdminArea.CityMergeStatus.SOURCE):
        return (
            int(AdminArea.CityMergeStatus.NONE),
            int(AdminArea.CityMergeStatus.SOURCE),
        )
    return (
        int(AdminArea.CityMergeStatus.NONE),
        int(AdminArea.CityMergeStatus.UNIFIED),
    )


def _descendants_at_level(
    root: AdminArea,
    target_level: int,
    city_merge_status_preference=None,
) -> list[AdminArea]:
    if root.level > target_level:
        raise ValueError(
            f"El área '{root.id}' está en nivel {root.level} y no se puede "
            f"proyectar a un nivel menor ({target_level})."
        )

    if root.level == target_level:
        return [root]

    result: list[AdminArea] = []
    seen: set[str] = set()
    frontier = [root]

    while frontier:
        ids = [a.id for a in frontier]
        children = list(
            AdminArea.objects
            .filter(
                parent_id__in=ids,
                city_merge_status__in=_child_city_merge_statuses(city_merge_status_preference),
            )
        )
        frontier = []

        for child in children:
            if child.level == target_level:
                if child.id not in seen:
                    result.append(child)
                    seen.add(child.id)
            elif child.level is not None and child.level < target_level:
                frontier.append(child)

    if not result and root.level == 0:
        result = list(
            AdminArea.objects.filter(
                country_code=root.country_code,
                level=target_level,
                city_merge_status__in=_child_city_merge_statuses(city_merge_status_preference),
            )
        )
        seen = {a.id for a in result}

    if not result and root.code and str(root.code).isdigit():
        for child in AdminArea.objects.filter(
            country_code=root.country_code,
            level=target_level,
            code__startswith=str(root.code),
            parent_id__isnull=True,
            city_merge_status__in=_child_city_merge_statuses(city_merge_status_preference),
        ):
            if child.id not in seen:
                result.append(child)
                seen.add(child.id)

    return result


def _to_atomic_ids(
    items: Iterable[AdminArea],
    atomic_level: int,
    city_merge_status_preference=None,
) -> set[str]:
    result: set[str] = set()

    for area in items:
        if area.level == atomic_level:
            if int(area.city_merge_status or 0) in _child_city_merge_statuses(
                city_merge_status_preference
            ):
                result.add(area.id)
        elif area.level < atomic_level:
            descendants = _descendants_at_level(
                area,
                atomic_level,
                city_merge_status_preference,
            )
            result |= {d.id for d in descendants}
        else:
            raise ValueError(
                f"No se puede convertir desde el nivel {area.level} a un nivel "
                f"más alto ({atomic_level}) para el área '{area.id}'."
            )

    return result


def _ancestor_at_level(area: AdminArea, target_level: int) -> AdminArea | None:
    if area.level == target_level:
        return area
    if area.level < target_level:
        return None

    parent_id = area.parent_id
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = AdminArea.objects.filter(id=parent_id).first()
        if parent is None:
            return None
        if parent.level == target_level:
            return parent
        if parent.level is not None and parent.level < target_level:
            return None
        parent_id = parent.parent_id

    return None


def _expand_to_municipal(
    items: Iterable[AdminArea],
    country_code: str,
    city_merge_status_preference=None,
) -> set[str]:
    """
    Convierte una colección de AdminArea (de cualquier nivel) en ids de municipios
    (nivel ORIGINAL_MUNICIPAL_LEVEL[country_code]).
    """
    atomic_level = ORIGINAL_MUNICIPAL_LEVEL.get(country_code)
    if atomic_level is None:
        raise ValueError(
            f"No se ha definido ORIGINAL_MUNICIPAL_LEVEL para el país origen '{country_code}'."
        )
    return _to_atomic_ids(items, atomic_level, city_merge_status_preference)


def _area_inside_sources(
    candidate: AdminArea,
    macro_ids: set[str],
    municipal_ids: set[str],
    macro_areas: Iterable[AdminArea],
) -> bool:
    if candidate.id in macro_ids or candidate.id in municipal_ids:
        return True

    parent_id = candidate.parent_id
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        if parent_id in macro_ids or parent_id in municipal_ids:
            return True
        seen.add(parent_id)
        parent_id = (
            AdminArea.objects
            .filter(id=parent_id)
            .values_list("parent_id", flat=True)
            .first()
        )

    candidate_code = str(candidate.code or "")
    if not candidate_code:
        return False

    for macro in macro_areas:
        macro_code = str(macro.code or "")
        if (
            macro.country_code == candidate.country_code
            and macro_code
            and macro_code.isdigit()
            and candidate_code.startswith(macro_code)
        ):
            return True

    if _area_inside_usa_city_url(candidate, municipal_ids):
        return True

    return False


# ---------------------------------------------------------------------------
# URL fallback matching
# ---------------------------------------------------------------------------

def _url_slug_norm(value: str | None) -> str:
    value = _norm((value or "").replace("_", " ").replace("-", " "))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _url_path_segments(url: str | None) -> list[str]:
    if not url:
        return []
    path = unquote(urlparse(url).path or "")
    return [segment for segment in path.strip("/").split("/") if segment]


def _url_leaf_name(url: str | None) -> str:
    segments = _url_path_segments(url)
    if not segments:
        return ""
    leaf = segments[-1]
    if "__" in leaf:
        leaf = leaf.split("__", 1)[1]
    return _url_slug_norm(leaf)


def _area_url_names(area: AdminArea) -> set[str]:
    values = {_url_slug_norm(area.name), _url_leaf_name(area.url)}
    return {value for value in values if value}


def _contains_url_name(context: str, value: str) -> bool:
    if not context or not value:
        return False
    return f" {value} " in f" {context} "


def _area_inside_usa_city_url(candidate: AdminArea, municipal_ids: set[str]) -> bool:
    """
    CityPopulation leaves some multi-county US cities without parent_id.
    Their URL still carries state and county context, e.g.
    /usa/texas/bexar_medina/4865000__san_antonio/.
    """
    if candidate.country_code != "usa" or not municipal_ids:
        return False

    segments = _url_path_segments(candidate.url)
    lowered_segments = [segment.lower() for segment in segments]
    try:
        usa_index = lowered_segments.index("usa")
    except ValueError:
        return False

    if len(segments) <= usa_index + 3:
        return False
    if lowered_segments[usa_index + 1] == "admin":
        return False

    state_context = _url_slug_norm(segments[usa_index + 1])
    county_context = _url_slug_norm(segments[usa_index + 2])
    if not state_context or not county_context:
        return False

    for source in (
        AdminArea.objects
        .filter(id__in=municipal_ids, country_code="usa")
        .select_related("parent", "parent__parent")
        .only(
            "id", "name", "url", "level",
            "parent__id", "parent__name", "parent__code", "parent__url", "parent__level",
            "parent__parent__id", "parent__parent__name", "parent__parent__code",
            "parent__parent__url", "parent__parent__level",
        )
    ):
        parent = source.parent
        if parent is None:
            continue

        if parent.level == 1:
            state = parent
            county = source
        else:
            state = parent.parent
            county = parent

        if state is None:
            continue

        state_names = _area_url_names(state) | {_url_slug_norm(state.code)}
        if state_context not in state_names:
            continue

        if any(_contains_url_name(county_context, value) for value in _area_url_names(county)):
            return True

    return False


def _usa_orphan_city_ids_inside(municipal_ids: set[str]) -> set[str]:
    """Return parentless US level-3 cities whose URL context matches sources."""
    contexts = _usa_source_url_contexts(municipal_ids)
    if not contexts:
        return set()

    result: set[str] = set()
    candidates = (
        AdminArea.objects
        .filter(country_code="usa", level=3, parent_id__isnull=True)
        .exclude(url__isnull=True)
        .only("id", "url")
    )
    for candidate in candidates:
        parsed = _usa_city_url_context(candidate.url)
        if parsed is None:
            continue
        state_context, county_context = parsed
        for state_names, county_names in contexts:
            if state_context not in state_names:
                continue
            if any(_contains_url_name(county_context, county_name) for county_name in county_names):
                result.add(candidate.id)
                break

    return result


def _usa_source_url_contexts(municipal_ids: set[str]) -> list[tuple[set[str], set[str]]]:
    if not municipal_ids:
        return []

    contexts: list[tuple[set[str], set[str]]] = []
    for source in (
        AdminArea.objects
        .filter(id__in=municipal_ids, country_code="usa")
        .select_related("parent", "parent__parent")
        .only(
            "id", "name", "url", "level",
            "parent__id", "parent__name", "parent__code", "parent__url", "parent__level",
            "parent__parent__id", "parent__parent__name", "parent__parent__code",
            "parent__parent__url", "parent__parent__level",
        )
    ):
        parent = source.parent
        if parent is None:
            continue

        if parent.level == 1:
            state = parent
            county = source
        else:
            state = parent.parent
            county = parent

        if state is None:
            continue

        state_names = _area_url_names(state) | {_url_slug_norm(state.code)}
        county_names = _area_url_names(county)
        if state_names and county_names:
            contexts.append((state_names, county_names))

    return contexts


def _usa_city_url_context(url: str | None) -> tuple[str, str] | None:
    segments = _url_path_segments(url)
    lowered_segments = [segment.lower() for segment in segments]
    try:
        usa_index = lowered_segments.index("usa")
    except ValueError:
        return None

    if len(segments) <= usa_index + 3:
        return None
    if lowered_segments[usa_index + 1] == "admin":
        return None

    state_context = _url_slug_norm(segments[usa_index + 1])
    county_context = _url_slug_norm(segments[usa_index + 2])
    if not state_context or not county_context:
        return None
    return state_context, county_context


def _resolve_adminarea_inside_sources(
    qs,
    label_or_id: str,
    macro_ids: set[str],
    municipal_ids: set[str],
    macro_areas: Iterable[AdminArea],
    city_merge_status_preference=None,
) -> Optional[AdminArea]:
    candidate = _resolve_adminarea_in_qs(
        qs,
        label_or_id,
        city_merge_status_preference=city_merge_status_preference,
    )
    if candidate and _area_inside_sources(candidate, macro_ids, municipal_ids, macro_areas):
        return candidate

    lookup_value = _label_lookup_value(label_or_id)
    if not lookup_value:
        return None

    normalized = _norm(lookup_value)
    normalized_candidates = [
        area
        for area in qs.only("id", "code", "name", "level", "city_merge_status", "parent_id", "country_code", "url")
        if normalized in {_norm(area.id), _norm(area.code), _norm(area.name)}
    ]
    candidate_groups = [
        normalized_candidates,
        qs.filter(name__icontains=lookup_value),
        qs.filter(code__icontains=lookup_value),
    ]
    for candidates in candidate_groups:
        for area in _preferred_adminareas(candidates, city_merge_status_preference):
            if _area_inside_sources(area, macro_ids, municipal_ids, macro_areas):
                return area

    return None


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

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


def _sum_source_population(
    qs,
    *,
    source_population_year: int | None,
    source_population_index_registry: SourcePopulationIndexRegistry | None,
    fallback_multiplier_areas: Iterable[AdminArea] = (),
) -> int:
    """Sum source populations, applying historical multipliers when active.

    Without an active build year or registry this is a normal database sum. If
    a registry is active, every source row is loaded so its country/id/code/name
    can be matched against ``source_population_indices.toml``.
    """
    if (
        source_population_year is None
        or source_population_index_registry is None
        or source_population_index_registry.is_empty
    ):
        return qs.aggregate(total_pop=Sum("pop_latest"))["total_pop"] or 0

    fallback_by_id = {area.id: area for area in fallback_multiplier_areas}
    total = Decimal("0")
    for area in qs.only("id", "country_code", "code", "name", "parent_id", "pop_latest"):
        if area.pop_latest is None:
            continue
        multiplier = _source_population_multiplier(
            area,
            source_population_year=source_population_year,
            source_population_index_registry=source_population_index_registry,
            fallback_by_id=fallback_by_id,
        )
        total += Decimal(max(area.pop_latest, 0)) * multiplier

    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _source_population_multiplier(
    area: AdminArea,
    *,
    source_population_year: int,
    source_population_index_registry: SourcePopulationIndexRegistry,
    fallback_by_id: dict[str, AdminArea],
) -> Decimal:
    """Return the multiplier for ``area``, inheriting from known ancestors.

    Sub-municipal ``restar`` rows can be descendants of a macro area whose
    multiplier was configured instead of the individual row. ``fallback_by_id``
    carries those macro areas so subtraction uses the same historical scale as
    the source area it is being removed from.
    """
    direct = source_population_index_registry.multiplier_for_area(
        area,
        source_population_year,
    )
    if direct is not None:
        return direct

    parent_id = area.parent_id
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        fallback = fallback_by_id.get(parent_id)
        if fallback is not None:
            inherited = source_population_index_registry.multiplier_for_area(
                fallback,
                source_population_year,
            )
            if inherited is not None:
                return inherited
            parent_id = fallback.parent_id
            continue

        parent = (
            AdminArea.objects
            .only("id", "country_code", "code", "name", "parent_id")
            .filter(id=parent_id)
            .first()
        )
        if parent is None:
            break

        inherited = source_population_index_registry.multiplier_for_area(
            parent,
            source_population_year,
        )
        if inherited is not None:
            return inherited
        parent_id = parent.parent_id

    return Decimal("1")


# ---------------------------------------------------------------------------
# Most-populated refresh
# ---------------------------------------------------------------------------

def _top_populated(candidates: Iterable[AdminArea]) -> AdminArea | None:
    top = None
    for candidate in candidates:
        if candidate.pop_latest is None:
            continue
        if int(candidate.city_merge_status or 0) not in MOST_POPULATED_CITY_MERGE_STATUSES:
            continue
        if top is None or (
            candidate.pop_latest,
            -_city_merge_status_preference().index(int(candidate.city_merge_status or 0)),
        ) > (
            top.pop_latest,
            -_city_merge_status_preference().index(int(top.city_merge_status or 0)),
        ):
            top = candidate
    return top


@transaction.atomic
def refresh_nuevo_admin_most_populated(country_id: str) -> int:
    """
    Recalcula la ciudad mas poblada de NuevoAdminArea de abajo arriba.

    Las hojas toman el mayor municipio original. Los contenedores toman el mayor
    candidato entre sus propios municipios originales y los resultados ya
    calculados de sus hijos.
    """
    areas = list(
        NuevoAdminArea.objects
        .filter(country_code=country_id)
        .prefetch_related("municipios_originales")
        .order_by("-level")
    )
    children_by_parent: dict[str | None, list[NuevoAdminArea]] = {}
    for area in areas:
        children_by_parent.setdefault(area.parent_id, []).append(area)

    top_by_area_id: dict[str, AdminArea] = {}
    updated = 0

    for area in areas:
        candidates = list(area.municipios_originales.all())
        for child in children_by_parent.get(area.id, []):
            child_top = top_by_area_id.get(child.id)
            if child_top is not None:
                candidates.append(child_top)

        top = _top_populated(candidates)
        if top is not None:
            top_by_area_id[area.id] = top

        top_id = top.id if top else None
        if area.most_populate_city_id != top_id:
            area.most_populate_city = top
            area.save(update_fields=["most_populate_city"])
            updated += 1

    return updated


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
    city_merge_status_preference=None,
    source_population_year: int | None = None,
    source_population_index_registry: SourcePopulationIndexRegistry | None = None,
    allow_duplicate_source_data: bool = False,
) -> NuevoAdminArea:
    """Create or update one derived area from a canonical source ``spec``.

    ``include_spec`` is grouped by source level and country. Levels above the
    municipal level are expanded to municipal source ids; levels below it are
    treated as partial exceptions. ``restar`` subtracts source rows from the
    final area/population aggregate.

    Persisted ``code`` is the hierarchical path. Children below level 1 get the
    parent code prepended unless the recipe already supplied that prefix. The
    object id is always ``<derived_country_code>-<hierarchical_code>``.
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
    # Unidades por debajo del nivel municipal: sirven para excepciones parciales.
    sub_extra_incluidos: dict[str, str | None] = {}
    sub_restar: dict[str, str | None] = {}
    source_seen: dict[str, str] = {}
    source_duplicates: dict[str, list[str]] = {}
    municipal_seen: dict[str, str] = {}
    municipal_duplicates: dict[str, list[str]] = {}

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

        nonlocal macro_ids, mun_from_macros, mun_extra_incluidos, sub_extra_incluidos

        for cc, labels_raw in value.items():
            labels = _labels_to_list(labels_raw)
            if not labels:
                continue

            lookup_items = _lookup_many_with_preferences(
                cc,
                lvl,
                labels,
                city_merge_status_preference,
            )
            items = [area for area, _preference in lookup_items]
            atomic_level = _municipal_level_for(cc)

            if lvl > atomic_level:
                for area, _preference in lookup_items:
                    origin = _duplicate_origin(ctx, lvl, cc, area)
                    _remember_duplicate(
                        source_seen,
                        source_duplicates,
                        f"{ctx} nivel {lvl} entidad {area.name} ({area.id})",
                        origin,
                    )
                    ancestor = _ancestor_at_level(area, atomic_level)
                    sub_extra_incluidos[area.id] = ancestor.id if ancestor else None
            elif lvl == atomic_level:
                for area, preference in lookup_items:
                    origin = _duplicate_origin(ctx, lvl, cc, area)
                    _remember_duplicate(
                        source_seen,
                        source_duplicates,
                        f"{ctx} nivel {lvl} entidad {area.name} ({area.id})",
                        origin,
                    )
                    expanded_ids = _expand_to_municipal([area], cc, preference)
                    for municipal_id in expanded_ids:
                        _remember_duplicate(
                            municipal_seen,
                            municipal_duplicates,
                            f"{ctx} nivel {lvl} municipio {municipal_id}",
                            origin,
                        )
                    mun_extra_incluidos |= expanded_ids
            else:
                macro_ids |= {a.id for a in items}
                for area, preference in lookup_items:
                    origin = _duplicate_origin(ctx, lvl, cc, area)
                    _remember_duplicate(
                        source_seen,
                        source_duplicates,
                        f"{ctx} nivel {lvl} entidad {area.name} ({area.id})",
                        origin,
                    )
                    expanded_ids = _expand_to_municipal([area], cc, preference)
                    for municipal_id in expanded_ids:
                        _remember_duplicate(
                            municipal_seen,
                            municipal_duplicates,
                            f"{ctx} nivel {lvl} municipio {municipal_id}",
                            origin,
                        )
                    mun_from_macros |= expanded_ids

    def _parse_restar_group(value: dict, lvl: int, ctx: str):
        if not isinstance(value, dict):
            raise ValueError(
                f"En '{ctx}' para nivel {lvl} se esperaba un dict "
                f"{{country_code: [labels]}}, recibido: {type(value).__name__}."
            )

        nonlocal mun_restar, sub_restar

        for cc, labels_raw in value.items():
            labels = _labels_to_list(labels_raw)
            if not labels:
                continue

            lookup_items = _lookup_many_with_preferences(
                cc,
                lvl,
                labels,
                city_merge_status_preference,
            )
            for area, preference in lookup_items:
                origin = _duplicate_origin(ctx, lvl, cc, area)
                _remember_duplicate(
                    source_seen,
                    source_duplicates,
                    f"{ctx} nivel {lvl} entidad {area.name} ({area.id})",
                    origin,
                )
                atomic_level = _municipal_level_for(cc)
                if lvl > atomic_level:
                    ancestor = _ancestor_at_level(area, atomic_level)
                    sub_restar[area.id] = ancestor.id if ancestor else None
                    continue

                expanded_ids = _expand_to_municipal([area], cc, preference)
                for municipal_id in expanded_ids:
                    _remember_duplicate(
                        municipal_seen,
                        municipal_duplicates,
                        f"{ctx} nivel {lvl} municipio {municipal_id}",
                        origin,
                    )
                mun_restar |= expanded_ids

    for key, value in include_map.items():
        lvl = int(key)
        _parse_include_group(value, lvl, "include")

    for key, value in restar_raw.items():
        lvl = int(key)
        _parse_restar_group(value, lvl, "restar")

    if not allow_duplicate_source_data:
        _raise_duplicate_source_data(new_name, source_duplicates, municipal_duplicates)

    mun_incluidos = mun_from_macros | mun_extra_incluidos
    sub_extra_final_ids = {
        sub_id
        for sub_id, ancestor_id in sub_extra_incluidos.items()
        if ancestor_id is None or ancestor_id not in mun_incluidos or ancestor_id in mun_restar
    }
    sub_restar_final_ids = {
        sub_id
        for sub_id, ancestor_id in sub_restar.items()
        if ancestor_id is None or (ancestor_id in mun_incluidos and ancestor_id not in mun_restar)
    }
    mun_final_ids = mun_incluidos - mun_restar

    if not macro_ids and not mun_final_ids and not sub_extra_final_ids:
        raise ValueError(f"La especificación para '{new_name}' no produjo ninguna unidad.")

    # ---------------------------------------------------------
    # Agregados de área / población
    # ---------------------------------------------------------
    macro_qs = AdminArea.objects.filter(id__in=macro_ids)
    macro_areas = list(
        macro_qs.only("id", "country_code", "code", "name", "parent_id", "pop_latest")
    )

    source_unit_ids = mun_final_ids | sub_extra_final_ids
    source_unit_ids |= _usa_orphan_city_ids_inside(source_unit_ids)
    source_units_qs = (
        AdminArea.objects.filter(id__in=source_unit_ids)
        if source_unit_ids
        else macro_qs
    )

    agg_source = source_units_qs.aggregate(total_area=Sum("area_km2"))
    area_source = agg_source["total_area"] or Decimal("0")
    pop_source = _sum_source_population(
        source_units_qs,
        source_population_year=source_population_year,
        source_population_index_registry=source_population_index_registry,
    )

    restar_qs = AdminArea.objects.filter(id__in=sub_restar_final_ids)
    agg_restar = restar_qs.aggregate(total_area=Sum("area_km2"))

    area_restar = agg_restar["total_area"] or Decimal("0")
    pop_restar = _sum_source_population(
        restar_qs,
        source_population_year=source_population_year,
        source_population_index_registry=source_population_index_registry,
        fallback_multiplier_areas=macro_areas,
    )

    total_area = area_source - area_restar
    total_pop = pop_source - pop_restar

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
    m2m.set(source_units_qs)

    # ---------------------------------------------------------
    # Capitales y ciudad más poblada
    # ---------------------------------------------------------
    if capitals:
        capital_objs = []
        capital_name_overrides: dict[str, dict[str, str]] = {}
        for raw in capitals:
            label, capital_preference, names_by_language = _label_city_preference_and_names(
                raw,
                city_merge_status_preference,
            )
            label = label.strip()
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
                        city_merge_status__in=_child_city_merge_statuses(capital_preference),
                    )
                    candidate = _resolve_adminarea_in_qs(
                        candidate_qs,
                        label,
                        city_merge_status_preference=capital_preference,
                    )
                    if not candidate:
                        continue

                    atomic_level = _municipal_level_for(cc)
                    if candidate.id in source_unit_ids:
                        chosen = candidate
                        break

                    if candidate.level < atomic_level:
                        descendant_ids = {
                            d.id for d in _descendants_at_level(
                                candidate,
                                atomic_level,
                                capital_preference,
                            )
                        }
                        if descendant_ids & mun_final_ids:
                            chosen = candidate
                            break

                    if candidate.id in macro_ids:
                        chosen = candidate
                        break

            if not chosen:
                chosen = _resolve_adminarea_in_qs(
                    source_units_qs,
                    label,
                    city_merge_status_preference=capital_preference,
                )
            if not chosen:
                chosen = _resolve_adminarea_in_qs(
                    macro_qs,
                    label,
                    city_merge_status_preference=capital_preference,
                )
            if not chosen:
                territory_country_codes = set(
                    AdminArea.objects
                    .filter(id__in=macro_ids | source_unit_ids)
                    .values_list("country_code", flat=True)
                )
                for cc in territory_country_codes:
                    candidate = _resolve_adminarea_inside_sources(
                        AdminArea.objects.filter(
                            country_code=cc,
                            city_merge_status__in=_child_city_merge_statuses(capital_preference),
                        ).order_by("-level"),
                        label,
                        city_merge_status_preference=capital_preference,
                        macro_ids=macro_ids,
                        municipal_ids=source_unit_ids,
                        macro_areas=macro_areas,
                    )
                    if candidate:
                        chosen = candidate
                        break

            if not chosen:
                raise ValueError(
                    f"La capital '{label}' no está dentro del territorio definido para '{new_name}'."
                )

            capital_objs.append(chosen)
            _add_capital_name_overrides(
                capital_name_overrides,
                chosen,
                names_by_language,
            )

        obj.capitals.set(capital_objs)
        obj.capital_names_by_language = capital_name_overrides
        obj.save(update_fields=["capital_names_by_language"])

    if auto_set_most_populated:
        top = (
            source_units_qs
            .filter(city_merge_status__in=MOST_POPULATED_CITY_MERGE_STATUSES)
            .exclude(pop_latest__isnull=True)
            .order_by("-pop_latest")
            .first()
        )
        obj.most_populate_city = top
        obj.save(update_fields=["most_populate_city"])

    return obj
