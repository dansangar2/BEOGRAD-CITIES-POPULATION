"""Build ``NuevoAdminArea`` rows from SQL ``DerivedSubdivision`` TOML."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
import re
import tomllib

from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import AdminArea, DerivedSubdivision, NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services.derived_config_seeds import (
    _group_selected_names_from_seed_data,
    _safe_group_toml_key,
    _top_level_group_assignments,
)
from ciudades_del_mundo.services.derived_codes import derived_country_root_code
from ciudades_del_mundo.services.nuevo_admin_builder import (
    ORIGINAL_MUNICIPAL_LEVEL,
    create_nuevo_area_from_spec,
    refresh_nuevo_admin_most_populated,
    _add_capital_name_overrides,
    _child_city_merge_statuses,
    _label_city_preference_and_names,
    _resolve_adminarea_in_qs,
    _round_area,
)
from ciudades_del_mundo.services.source_population_indices import (
    SourcePopulationIndexConfigError,
    load_source_population_index_registry,
)


@dataclass(frozen=True)
class DerivedSubdivisionBuildResult:
    country_code: str
    root_id: str
    records: int
    built: int
    most_populated_updated: int


@dataclass
class _BuildContext:
    country_code: str
    source_population_year: int | None
    source_population_index_registry: object
    group_cache: dict[tuple[str, str], list[Any]]


def build_derived_subdivisions_for_country(
    country_code: str,
    *,
    force: bool = False,
    source_population_year: int | None = None,
) -> DerivedSubdivisionBuildResult:
    """Materialize one country's SQL ``DerivedSubdivision`` records."""
    country_code = _clean_country_code(country_code)
    if not country_code:
        raise ValueError(_("El pais fuente es obligatorio."))

    records = list(
        DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("name", "slug")
    )
    if not records:
        raise ValueError(_("No hay subdivisiones SQL para %(country)s.") % {"country": country_code})

    try:
        population_index_registry = load_source_population_index_registry()
    except SourcePopulationIndexConfigError as exc:
        raise ValueError(str(exc)) from exc

    with transaction.atomic():
        root = _ensure_country_root(country_code)
        existing = NuevoAdminArea.objects.filter(country_code=country_code).exclude(id=root.id)
        if existing.exists():
            if not force:
                raise ValueError(
                    _("%(country)s ya tiene nuevas divisiones. Usa --force para reconstruirlas.")
                    % {"country": country_code}
                )
            existing.delete()

        context = _BuildContext(
            country_code=country_code,
            source_population_year=source_population_year,
            source_population_index_registry=population_index_registry,
            group_cache={},
        )
        code_counts = Counter(_record_requested_code(record) for record in records)
        built = 0
        pending = list(records)
        while pending:
            next_pending = []
            progressed = False
            for record in pending:
                parent = _record_parent_area(record, root)
                if parent is None:
                    next_pending.append(record)
                    continue
                built += _build_record(record, parent_id=parent.id, context=context, code_counts=code_counts)
                progressed = True
            if not progressed:
                missing = ", ".join(record.slug for record in next_pending)
                raise ValueError(_("No se pudo resolver la seccion padre de: %(records)s") % {"records": missing})
            pending = next_pending
        updated = refresh_nuevo_admin_most_populated(country_code)

    return DerivedSubdivisionBuildResult(
        country_code=country_code,
        root_id=root.id,
        records=len(records),
        built=built,
        most_populated_updated=updated,
    )


def _ensure_country_root(country_code: str) -> NuevoAdminArea:
    source_root = AdminArea.objects.filter(country_code=country_code, level=0).order_by("id").first()
    root_area = source_root.area_km2 if source_root else None
    root_pop = source_root.pop_latest if source_root else None
    root_code = derived_country_root_code(country_code, source_root.code if source_root else "")
    root, _created = NuevoAdminArea.objects.update_or_create(
        id=country_code,
        defaults={
            "country_code": country_code,
            "code": root_code,
            "name": source_root.name if source_root else country_code.replace("_", " ").title(),
            "level": NuevoAdminArea.Level.COUNTRY,
            "entity_type": "Country",
            "parent": None,
            "area_km2": root_area,
            "density": _density(root_pop, root_area),
            "pop_latest": root_pop,
            "municipal_level": ORIGINAL_MUNICIPAL_LEVEL.get(country_code),
        },
    )
    return root


def _build_record(
    record: DerivedSubdivision,
    *,
    parent_id: str,
    context: _BuildContext,
    code_counts: Counter,
) -> int:
    data = _parse_record_content(record)
    data.setdefault("internal_name", record.internal_name or record.slug)
    data.setdefault("source_country_code", record.source_country_code or context.country_code)
    data.setdefault("name", record.name or record.internal_name or record.slug)
    requested_code = _record_requested_code(record, data=data)
    if requested_code and code_counts[requested_code] == 1:
        data["code"] = requested_code
    else:
        data["code"] = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    data.setdefault("entity_type", record.entity_type or data.get("generic_name") or "Subdivision")
    return _build_node(data, parent_id=parent_id, context=context, label=record.slug)


def _record_requested_code(record: DerivedSubdivision, *, data: dict[str, Any] | None = None) -> str:
    if data is None:
        try:
            data = tomllib.loads(record.content or "")
        except tomllib.TOMLDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
    return str(data.get("code") or record.code or "").strip()


def _record_parent_area(record: DerivedSubdivision, root: NuevoAdminArea) -> NuevoAdminArea | None:
    try:
        data = tomllib.loads(record.content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    parent_code = _code_piece(data.get("parent_code") or "")
    root_aliases = {_code_piece(root.code), _code_piece(root.id), _code_piece(root.country_code)}
    if not parent_code or parent_code in root_aliases:
        return root
    return NuevoAdminArea.objects.filter(country_code=root.country_code, code=parent_code).first()


def _build_node(data: dict[str, Any], *, parent_id: str, context: _BuildContext, label: str) -> int:
    parent = NuevoAdminArea.objects.get(id=parent_id)
    name = str(data.get("name") or data.get("internal_name") or label).strip()
    if not name:
        raise ValueError(_("La subdivision %(label)s no tiene nombre.") % {"label": label})
    code = _child_code(parent, str(data.get("code") or data.get("internal_name") or name).strip())
    entity_type = str(data.get("entity_type") or data.get("generic_name") or "Subdivision").strip()
    desired_level = _optional_int(data.get("level"), label=f"{label}.level")
    spec = _spec_from_node(data, default_country_code=context.country_code, context=context, label=label)
    children = data.get("children") if isinstance(data.get("children"), list) else []

    if spec:
        source_countries = _spec_country_codes(spec) or [context.country_code]
        capital_levels = {
            country: ORIGINAL_MUNICIPAL_LEVEL[country]
            for country in source_countries
            if country in ORIGINAL_MUNICIPAL_LEVEL
        }
        obj = create_nuevo_area_from_spec(
            parent_country_id=parent_id,
            new_name=name,
            include_spec=spec,
            entity_type=entity_type,
            forced_area_km2=data.get("forced_area_km2"),
            new_code=code,
            capitals=_capital_list(data),
            capital_level_by_country=capital_levels,
            auto_set_most_populated=_bool_value(data.get("auto_set_most_populated"), default=True),
            source_population_year=_node_population_year(data, context.source_population_year, label=label),
            source_population_index_registry=context.source_population_index_registry,
            allow_duplicate_source_data=True,
        )
    else:
        obj = _create_container_node(parent, name=name, code=code, entity_type=entity_type, level=desired_level)

    _apply_level(obj, desired_level)
    _apply_capital_groups(obj, data, context=context, label=label)

    built = 1
    for index, child in enumerate(children, start=1):
        if not isinstance(child, dict):
            raise ValueError(_("children de %(label)s debe contener bloques TOML validos.") % {"label": label})
        child.setdefault("source_country_code", context.country_code)
        built += _build_node(child, parent_id=obj.id, context=context, label=f"{label}.children[{index}]")

    if not spec and children:
        _refresh_container_totals(obj)
    return built


def _parse_record_content(record: DerivedSubdivision) -> dict[str, Any]:
    try:
        data = tomllib.loads(record.content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(slug)s: TOML invalido: %(error)s") % {"slug": record.slug, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(slug)s: TOML invalido.") % {"slug": record.slug})
    kind = str(data.get("kind") or "").strip()
    if kind and kind != "derived_subdivision":
        raise ValueError(_("%(slug)s: kind debe ser 'derived_subdivision'.") % {"slug": record.slug})
    return dict(data)


def _spec_from_node(data: dict[str, Any], *, default_country_code: str, context: _BuildContext, label: str) -> dict:
    spec: dict[Any, dict[str, list[Any]]] = {}
    include_levels: dict[str, set[int]] = defaultdict(set)
    for block in _blocks(data, "include", label=label):
        level = _block_level(block, label=f"{label}.include")
        country = _clean_country_code(block.get("country_code") or default_country_code)
        if _skip_missing_external_country(country, context):
            continue
        values = _block_values(block, country_code=country, context=context, label=label)
        if values:
            include_levels[country].add(level)
            spec.setdefault(level, {}).setdefault(country, []).extend(values)

    for block in _blocks(data, "subtract", label=label):
        level = _block_level(block, label=f"{label}.subtract")
        country = _clean_country_code(block.get("country_code") or default_country_code)
        if _skip_missing_external_country(country, context):
            continue
        if not any(include_level < level for include_level in include_levels.get(country, set())):
            raise ValueError(
                _(
                    "%(label)s resta nivel %(level)s de %(country)s, "
                    "pero no incluye antes una subdivision superior de ese pais."
                )
                % {"label": label, "level": level, "country": country}
            )
        values = _block_values(block, country_code=country, context=context, label=label)
        if values:
            spec.setdefault("restar", {}).setdefault(level, {}).setdefault(country, []).extend(values)
    return spec


def _skip_missing_external_country(country_code: str, context: _BuildContext) -> bool:
    return (
        country_code != context.country_code
        and not AdminArea.objects.filter(country_code=country_code).exists()
    )


def _blocks(data: dict[str, Any], key: str, *, label: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(_("%(label)s.%(key)s debe ser una lista TOML.") % {"label": label, "key": key})
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(_("%(label)s.%(key)s debe contener bloques TOML validos.") % {"label": label, "key": key})
    return value


def _block_level(block: dict[str, Any], *, label: str) -> int:
    level = _optional_int(block.get("level"), label=f"{label}.level", allow_none=False)
    if level is None or level < 0 or level > 9:
        raise ValueError(_("%(label)s debe tener un nivel entre 0 y 9.") % {"label": label})
    return level


def _block_values(block: dict[str, Any], *, country_code: str, context: _BuildContext, label: str) -> list[Any]:
    values: list[Any] = []
    values.extend(_list_value(block.get("names"), label=f"{label}.names"))
    values.extend({"id": str(value)} for value in _list_value(block.get("ids"), label=f"{label}.ids"))
    values.extend({"code": str(value)} for value in _list_value(block.get("codes"), label=f"{label}.codes"))
    for group_key in _list_value(block.get("groups"), label=f"{label}.groups"):
        values.extend(_group_names(country_code, str(group_key), context=context))
    return values


def _group_names(country_code: str, group_key: str, *, context: _BuildContext) -> list[Any]:
    normalized_key = _safe_group_toml_key(group_key)
    cache_key = (country_code, normalized_key)
    if cache_key in context.group_cache:
        return list(context.group_cache[cache_key])

    groups = SubdivisionGroup.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    if not groups.exists():
        groups = SubdivisionGroup.objects.order_by("slug")
    for group in groups:
        data = _parse_group_content(group)
        if not _group_matches(group, data, country_code=country_code, group_key=normalized_key):
            continue
        names = _group_selected_names_from_seed_data(data, internal_name=normalized_key)
        context.group_cache[cache_key] = names
        return list(names)

    raise ValueError(
        _("No existe el grupo %(group)s para %(country)s. Importa primero los TOML de /groups.")
        % {"group": normalized_key, "country": country_code}
    )


def _parse_group_content(group: SubdivisionGroup) -> dict[str, Any]:
    try:
        data = tomllib.loads(group.content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(slug)s: TOML de grupo invalido: %(error)s") % {"slug": group.slug, "error": exc}) from exc
    return data if isinstance(data, dict) else {}


def _group_matches(group: SubdivisionGroup, data: dict[str, Any], *, country_code: str, group_key: str) -> bool:
    source_country = _clean_country_code(group.source_country_code or data.get("source_country_code") or "")
    if source_country and source_country != country_code:
        return False
    candidates = {
        group.name,
        group.slug,
        group.slug.removeprefix(f"{country_code}_"),
        data.get("internal_name"),
        data.get("entry_slug"),
        data.get("slug"),
    }
    candidates.update(key for key, _values in _top_level_group_assignments(data))
    return any(_safe_group_toml_key(candidate) == group_key for candidate in candidates if candidate)


def _apply_capital_groups(obj: NuevoAdminArea, data: dict[str, Any], *, context: _BuildContext, label: str) -> None:
    raw_groups = data.get("capital_groups")
    if not raw_groups:
        return
    if not isinstance(raw_groups, list):
        raise ValueError(_("%(label)s.capital_groups debe ser una lista TOML.") % {"label": label})

    capitals = {capital.id: capital for capital in obj.capitals.all()}
    overrides = dict(obj.capital_names_by_language or {})
    territory_ids = set(obj.municipios_originales.values_list("id", flat=True))
    for index, block in enumerate(raw_groups, start=1):
        if not isinstance(block, dict):
            raise ValueError(_("capital_groups de %(label)s debe contener bloques validos.") % {"label": label})
        country = _clean_country_code(block.get("country_code") or context.country_code)
        level = _optional_int(block.get("level"), label=f"{label}.capital_groups[{index}].level")
        if level is None:
            level = ORIGINAL_MUNICIPAL_LEVEL.get(country)
        if level is None:
            raise ValueError(_("capital_groups debe indicar level para %(country)s.") % {"country": country})
        labels = []
        group_key = str(block.get("group") or block.get("group_key") or "").strip()
        if group_key:
            labels.extend(_group_names(country, group_key, context=context))
        labels.extend(_list_value(block.get("names"), label=f"{label}.capital_groups[{index}].names"))
        display_names = _capital_group_names_by_language(block)
        for raw_label in labels:
            text, preference, raw_display_names = _label_city_preference_and_names(raw_label)
            candidate = _resolve_adminarea_in_qs(
                AdminArea.objects.filter(
                    country_code=country,
                    level=level,
                    city_merge_status__in=_child_city_merge_statuses(preference),
                ),
                text,
                city_merge_status_preference=preference,
            )
            if not candidate:
                raise ValueError(
                    _("No se encontro la capital %(capital)s en %(country)s nivel %(level)s.")
                    % {"capital": text, "country": country, "level": level}
                )
            if territory_ids and candidate.id not in territory_ids:
                raise ValueError(
                    _("La capital %(capital)s no pertenece al territorio de %(area)s.")
                    % {"capital": candidate.name, "area": obj.name}
                )
            capitals[candidate.id] = candidate
            _add_capital_name_overrides(overrides, candidate, display_names or raw_display_names)

    obj.capitals.set(capitals.values())
    obj.capital_names_by_language = overrides
    obj.save(update_fields=["capital_names_by_language"])


def _capital_group_names_by_language(block: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    raw = block.get("names_by_language") or block.get("translations")
    if isinstance(raw, dict):
        for language, value in raw.items():
            text = str(value or "").strip()
            if text:
                names[str(language).strip().lower()] = text
    display_name = str(block.get("capital_name") or block.get("display_name") or "").strip()
    if display_name and "es" not in names:
        names["es"] = display_name
    return names


def _apply_level(obj: NuevoAdminArea, desired_level: int | None) -> None:
    if desired_level is None or obj.level == desired_level:
        return
    if desired_level < NuevoAdminArea.Level.COUNTRY or desired_level > NuevoAdminArea.Level.ADMIN5:
        raise ValueError(_("El nivel de %(name)s debe estar entre 0 y 5.") % {"name": obj.name})
    obj.level = desired_level
    obj.save(update_fields=["level"])


def _create_container_node(parent: NuevoAdminArea, *, name: str, code: str, entity_type: str, level: int | None) -> NuevoAdminArea:
    raw_code = _code_piece(code or _safe_slug(name))
    if parent.level == NuevoAdminArea.Level.COUNTRY:
        full_code = raw_code
    elif parent.code and raw_code.startswith(parent.code + "-"):
        full_code = raw_code
    else:
        full_code = f"{parent.code}-{raw_code}" if parent.code else raw_code
    return NuevoAdminArea.objects.create(
        id=f"{parent.country_code}-{full_code}",
        country_code=parent.country_code,
        code=full_code,
        name=name,
        level=level if level is not None else (parent.level or 0) + 1,
        entity_type=entity_type or "Subdivision",
        parent=parent,
    )


def _child_code(parent: NuevoAdminArea, raw_code: str) -> str:
    code = _code_piece(raw_code)
    if not code:
        code = _code_piece(_safe_slug(parent.name))
    parent_code = _code_piece(parent.code)
    if parent_code and code.startswith(f"{parent_code}-"):
        return code
    return f"{parent_code}-{code}" if parent_code else code


def _refresh_container_totals(obj: NuevoAdminArea) -> None:
    agg = obj.children.aggregate(total_area=Sum("area_km2"), total_pop=Sum("pop_latest"))
    total_area = _round_area(agg["total_area"])
    total_pop = agg["total_pop"]
    obj.area_km2 = total_area
    obj.pop_latest = total_pop
    obj.density = _density(total_pop, total_area)
    obj.save(update_fields=["area_km2", "pop_latest", "density"])


def _capital_list(data: dict[str, Any]) -> list[Any]:
    capitals = data.get("capitals")
    if capitals is None and data.get("capital"):
        capitals = [data["capital"]]
    return _list_value(capitals, label="capitals")


def _node_population_year(data: dict[str, Any], default_year: int | None, *, label: str) -> int | None:
    for key in ("source_population_year", "population_year", "year"):
        if key in data:
            return _optional_int(data.get(key), label=f"{label}.{key}")
    return default_year


def _spec_country_codes(spec: dict) -> list[str]:
    countries: list[str] = []
    for key, value in spec.items():
        if key == "restar":
            for subtract_value in (value or {}).values():
                if isinstance(subtract_value, dict):
                    for country in subtract_value:
                        country = _clean_country_code(country)
                        if country and country not in countries:
                            countries.append(country)
            continue
        if isinstance(value, dict):
            for country in value:
                country = _clean_country_code(country)
                if country and country not in countries:
                    countries.append(country)
    return countries


def _list_value(value: Any, *, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(_("%(label)s debe ser una lista.") % {"label": label})
    return list(value)


def _optional_int(value: Any, *, label: str, allow_none: bool = True) -> int | None:
    if value in (None, ""):
        if allow_none:
            return None
        raise ValueError(_("%(label)s debe ser numerico.") % {"label": label})
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(_("%(label)s debe ser numerico.") % {"label": label}) from exc


def _bool_value(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "si", "on"}


def _density(population: int | None, area: Decimal | float | int | None) -> Decimal | None:
    if population in (None, "") or area in (None, "", 0):
        return None
    try:
        area_decimal = Decimal(str(area))
        if area_decimal == 0:
            return None
        return Decimal(int(population)) / area_decimal
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return None


def _safe_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "subdivision"


def _code_piece(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "-", str(value or "").strip()).strip("-_")
    return text.upper()


def _clean_country_code(value: Any) -> str:
    return str(value or "").strip().lower()
