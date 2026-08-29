"""Build ``NuevoAdminArea`` rows from SQL ``DerivedSubdivision`` TOML."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
import re
import tomllib

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
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
    create_nuevo_area_from_spec,
    effective_source_municipal_level,
    refresh_nuevo_admin_most_populated,
    _add_capital_name_overrides,
    _ancestor_at_level,
    _child_city_merge_statuses,
    _expand_to_municipal,
    _label_city_preference_and_names,
    _lookup_many_with_preferences,
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
    errors: tuple[str, ...] = ()


@dataclass
class _BuildContext:
    country_code: str
    source_population_year: int | None
    source_population_index_registry: object
    group_cache: dict[tuple[str, str], list[Any]]
    derived_subdivision_cache: dict[tuple[str, str], list[tuple[int, str, dict[str, str]]]]
    derived_subdivision_stack: set[tuple[str, str]]
    root_child_uses_local_code: bool = False


def build_derived_subdivisions_for_country(
    country_code: str,
    *,
    force: bool = False,
    source_population_year: int | None = None,
    continue_on_error: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
) -> DerivedSubdivisionBuildResult:
    """Materialize one country's SQL ``DerivedSubdivision`` records."""
    country_code = _clean_country_code(country_code)
    if not country_code:
        raise ValueError(_("El pais fuente es obligatorio."))

    all_records = list(
        DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("name", "slug")
    )
    records = _select_records(all_records, country_code=country_code, slugs=slugs)
    if not records:
        raise ValueError(_("No hay subdivisiones SQL para %(country)s.") % {"country": country_code})

    try:
        population_index_registry = load_source_population_index_registry()
    except SourcePopulationIndexConfigError as exc:
        raise ValueError(str(exc)) from exc

    with transaction.atomic():
        root = _ensure_country_root(country_code)
        partial_build = bool(_clean_slug_filters(slugs))
        if not partial_build:
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
            derived_subdivision_cache={},
            derived_subdivision_stack=set(),
        )
        code_counts = Counter(_record_requested_code(record) for record in all_records)
        built = 0
        errors: list[str] = []
        pending = list(records)
        while pending:
            next_pending = []
            progressed = False
            for record in pending:
                parent = _record_parent_area(record, root)
                if parent is None:
                    next_pending.append(record)
                    continue
                try:
                    with transaction.atomic():
                        if partial_build:
                            _delete_existing_record_area(
                                record,
                                parent=parent,
                                context=context,
                                code_counts=code_counts,
                                force=force,
                            )
                        built += _build_record(record, parent_id=parent.id, context=context, code_counts=code_counts)
                except ValueError as exc:
                    if not continue_on_error:
                        raise
                    errors.append(f"{record.slug}: {exc}")
                progressed = True
            if not progressed:
                missing = ", ".join(record.slug for record in next_pending)
                if continue_on_error:
                    errors.append(_("No se pudo resolver la seccion padre de: %(records)s") % {"records": missing})
                    break
                raise ValueError(_("No se pudo resolver la seccion padre de: %(records)s") % {"records": missing})
            pending = next_pending
        updated = refresh_nuevo_admin_most_populated(country_code)

    return DerivedSubdivisionBuildResult(
        country_code=country_code,
        root_id=root.id,
        records=len(records),
        built=built,
        most_populated_updated=updated,
        errors=tuple(errors),
    )


def _select_records(
    records: list[DerivedSubdivision],
    *,
    country_code: str,
    slugs: list[str] | tuple[str, ...] | None,
) -> list[DerivedSubdivision]:
    filters = _clean_slug_filters(slugs)
    if not filters:
        return records

    selected: list[DerivedSubdivision] = []
    matched: set[str] = set()
    for record in records:
        keys = _record_selector_keys(record, country_code=country_code)
        if not keys & filters:
            continue
        selected.append(record)
        matched.update(keys & filters)

    missing = [slug for slug in filters if slug not in matched]
    if missing:
        raise ValueError(
            _("No existen subdivisiones SQL para %(country)s: %(slugs)s")
            % {"country": country_code, "slugs": ", ".join(sorted(missing))}
        )
    return selected


def _clean_slug_filters(slugs: list[str] | tuple[str, ...] | None) -> set[str]:
    return {
        str(value).strip().casefold()
        for value in (slugs or [])
        if str(value or "").strip()
    }


def _record_selector_keys(record: DerivedSubdivision, *, country_code: str) -> set[str]:
    slug = str(record.slug or "").strip()
    internal_name = str(record.internal_name or "").strip()
    country_prefix = f"{country_code}_"
    keys = {slug, internal_name}
    if slug.casefold().startswith(country_prefix.casefold()):
        keys.add(slug[len(country_prefix) :])
    return {key.casefold() for key in keys if key}


def _delete_existing_record_area(
    record: DerivedSubdivision,
    *,
    parent: NuevoAdminArea,
    context: _BuildContext,
    code_counts: Counter,
    force: bool,
) -> None:
    target = _record_materialized_area(record, parent=parent, context=context, code_counts=code_counts)
    if target is None:
        return
    if not force:
        raise ValueError(
            _("%(record)s ya esta construida. Usa --force para reconstruirla.")
            % {"record": record.slug}
        )
    _delete_existing_area_subtree(target)


def _delete_existing_area_subtree(target: NuevoAdminArea) -> None:
    area_ids = _existing_area_subtree_ids(target)
    if area_ids:
        NuevoAdminArea.objects.filter(id__in=area_ids).delete()


def _existing_area_subtree_ids(target: NuevoAdminArea) -> set[str]:
    area_ids = {str(target.id)}
    frontier = {str(target.id)}
    while frontier:
        children = set(
            NuevoAdminArea.objects.filter(
                country_code=target.country_code,
                parent_id__in=frontier,
            ).values_list("id", flat=True)
        )
        frontier = {str(child_id) for child_id in children if str(child_id) not in area_ids}
        area_ids.update(frontier)

    code_prefix = f"{str(target.code or '').strip()}-"
    if code_prefix != "-":
        area_ids.update(
            str(area_id)
            for area_id in NuevoAdminArea.objects.filter(
                country_code=target.country_code,
                code__startswith=code_prefix,
            ).values_list("id", flat=True)
        )
    return area_ids


def _record_materialized_area(
    record: DerivedSubdivision,
    *,
    parent: NuevoAdminArea,
    context: _BuildContext,
    code_counts: Counter,
) -> NuevoAdminArea | None:
    data = _parse_record_content(record)
    requested_code = _record_requested_code(record, data=data)
    if requested_code and code_counts[requested_code] == 1:
        raw_code = requested_code
    else:
        raw_code = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    code = _child_code_for_context(parent, raw_code, context)
    return NuevoAdminArea.objects.filter(country_code=parent.country_code, code=code).first()


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
            "municipal_level": effective_source_municipal_level(country_code),
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
    code = _child_code_for_context(parent, str(data.get("code") or data.get("internal_name") or name).strip(), context)
    entity_type = str(data.get("entity_type") or data.get("generic_name") or "Subdivision").strip()
    desired_level = _optional_int(data.get("level"), label=f"{label}.level")
    spec = _spec_from_node(data, default_country_code=context.country_code, context=context, label=label)
    children = data.get("children") if isinstance(data.get("children"), list) else []
    folded_self_child = False
    if not spec:
        folded_data = _fold_self_child_wrapper(data)
        if folded_data is not data:
            folded_self_child = True
            data = folded_data
            name = str(data.get("name") or data.get("internal_name") or label).strip()
            code = _child_code_for_context(
                parent,
                str(data.get("code") or data.get("internal_name") or name).strip(),
                context,
            )
            entity_type = str(data.get("entity_type") or data.get("generic_name") or "Subdivision").strip()
            desired_level = _optional_int(data.get("level"), label=f"{label}.level")
            spec = _spec_from_node(data, default_country_code=context.country_code, context=context, label=label)
            children = data.get("children") if isinstance(data.get("children"), list) else []

    if spec:
        source_countries = _spec_country_codes(spec) or [context.country_code]
        capital_levels = {
            country: int(level)
            for country in source_countries
            for level in [effective_source_municipal_level(country)]
            if level is not None
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
    if not children:
        built += _build_generated_children(obj, data, context=context, label=label)
    for index, child in enumerate(children, start=1):
        if not isinstance(child, dict):
            raise ValueError(_("children de %(label)s debe contener bloques TOML validos.") % {"label": label})
        child.setdefault("source_country_code", context.country_code)
        built += _build_node(child, parent_id=obj.id, context=context, label=f"{label}.children[{index}]")

    if spec and folded_self_child and children:
        _refresh_node_totals_with_own_sources_and_children(obj)
    if not spec and children:
        _refresh_container_totals(obj)
    return built


def _fold_self_child_wrapper(data: dict[str, Any]) -> dict[str, Any]:
    children = data.get("children") if isinstance(data.get("children"), list) else []
    if not children:
        return data
    mirror_index = next(
        (index for index, child in enumerate(children) if isinstance(child, dict) and _is_self_child_wrapper(data, child)),
        None,
    )
    if mirror_index is None:
        return data
    child = children[mirror_index]

    merged = dict(data)
    merged.pop("children", None)
    for key, value in child.items():
        if key == "children":
            continue
        if key in _SELF_CHILD_IDENTITY_KEYS and str(merged.get(key) or "").strip():
            continue
        if value in (None, "") and key in merged:
            continue
        merged[key] = value
    if data.get("internal_name") and not str(merged.get("internal_name") or "").strip():
        merged["internal_name"] = data["internal_name"]
    remaining_children = [
        item
        for index, item in enumerate(children)
        if index != mirror_index
    ]
    child_children = child.get("children") if isinstance(child.get("children"), list) else []
    remaining_children = list(child_children) + remaining_children
    if remaining_children:
        merged["children"] = remaining_children
    return merged


def _is_self_child_wrapper(parent_data: dict[str, Any], child_data: dict[str, Any]) -> bool:
    parent_code = _code_piece(parent_data.get("code") or parent_data.get("internal_name") or parent_data.get("name"))
    child_code = _code_piece(child_data.get("code") or child_data.get("internal_name") or child_data.get("name"))
    parent_name = _identity_text(parent_data.get("name") or parent_data.get("internal_name"))
    child_name = _identity_text(child_data.get("name") or child_data.get("internal_name"))
    same_name = bool(parent_name and child_name and parent_name == child_name)
    same_code = bool(parent_code and child_code and parent_code == child_code)
    if parent_name and child_name and not same_name:
        return False
    if not same_name and parent_code and child_code and not same_code:
        return False

    parent_level = _int_or_none(parent_data.get("level"))
    child_level = _int_or_none(child_data.get("level"))
    if parent_level is not None and child_level is not None and parent_level != child_level:
        return False

    parent_type = _identity_text(parent_data.get("entity_type") or parent_data.get("generic_name"))
    child_type = _identity_text(child_data.get("entity_type") or child_data.get("generic_name"))
    if parent_type and child_type and parent_type != child_type:
        return False
    return same_name or same_code


_SELF_CHILD_IDENTITY_KEYS = {
    "internal_name",
    "name",
    "code",
    "entity_type",
    "generic_name",
    "level",
    "parent_code",
    "source_country_code",
}


def _identity_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _refresh_node_totals_with_own_sources_and_children(obj: NuevoAdminArea) -> None:
    children = list(obj.children.prefetch_related("municipios_originales"))
    if not children:
        return
    total_area = Decimal(str(obj.area_km2 or 0))
    total_pop = obj.pop_latest
    source_ids = {str(area_id) for area_id in obj.municipios_originales.values_list("id", flat=True)}
    for child in children:
        total_area += Decimal(str(child.area_km2 or 0))
        if child.pop_latest is not None:
            total_pop = int(child.pop_latest) if total_pop is None else int(total_pop) + int(child.pop_latest)
        source_ids.update(str(area_id) for area_id in child.municipios_originales.values_list("id", flat=True))
    total_area = _round_area(total_area)
    obj.area_km2 = total_area
    obj.pop_latest = total_pop
    obj.density = _density(total_pop, total_area)
    obj.save(update_fields=["area_km2", "pop_latest", "density"])
    if source_ids:
        obj.municipios_originales.add(*source_ids)


def _build_generated_children(
    obj: NuevoAdminArea,
    data: dict[str, Any],
    *,
    context: _BuildContext,
    label: str,
) -> int:
    if "use_selected_entities_as_children" not in data:
        return 0
    if _bool_value(data.get("use_selected_entities_as_children"), default=False):
        return _build_selected_entity_children(obj, data, context=context, label=label)
    return _build_source_unit_children(obj, context=context, label=label)


def _build_source_unit_children(obj: NuevoAdminArea, *, context: _BuildContext, label: str) -> int:
    units = list(
        obj.municipios_originales
        .only("id", "country_code", "code", "name", "level", "entity_type", "raw_entity_type")
        .order_by("country_code", "level", "name", "id")
    )
    if not units:
        return 0
    used_codes = {str(code or "") for code in obj.children.values_list("code", flat=True)}
    built = 0
    for unit in units:
        try:
            level = int(unit.level or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        raw_code = _unique_child_code_piece(
            obj,
            _source_area_child_code(unit, context=context),
            used_codes,
            fallback=unit.id,
        )
        child = create_nuevo_area_from_spec(
            parent_country_id=obj.id,
            new_name=str(unit.name or unit.id).strip(),
            include_spec={level: {_clean_country_code(unit.country_code): [{"id": str(unit.id)}]}},
            entity_type=str(unit.entity_type or unit.raw_entity_type or "Subdivision").strip(),
            new_code=raw_code,
            capitals=[],
            auto_set_most_populated=True,
            source_population_year=context.source_population_year,
            source_population_index_registry=context.source_population_index_registry,
            allow_duplicate_source_data=True,
        )
        child_level = int(obj.level or 0) + 1
        _apply_level(child, child_level)
        built += 1
    return built


def _build_selected_entity_children(
    obj: NuevoAdminArea,
    data: dict[str, Any],
    *,
    context: _BuildContext,
    label: str,
) -> int:
    built = 0
    seen: set[tuple[str, str, str]] = set()
    for index, block in enumerate(_blocks(data, "include", label=label), start=1):
        level = _block_level(block, label=f"{label}.include[{index}]")
        country = _clean_country_code(block.get("country_code") or data.get("source_country_code") or context.country_code)
        if _skip_missing_external_country(country, context):
            continue
        area_values = _block_direct_area_values(block, label=f"{label}.include[{index}]")
        if area_values:
            for area, _preference in _lookup_many_with_preferences(country, level, area_values):
                source_key = ("area", _clean_country_code(area.country_code), str(area.id))
                if source_key in seen:
                    continue
                seen.add(source_key)
                built += _build_selected_area_child(obj, area, context=context)
        for group_key in _list_value(block.get("groups"), label=f"{label}.include[{index}].groups"):
            normalized_group_key = _group_internal_name(str(group_key))
            source_key = ("group", country, f"{level}:{normalized_group_key}")
            if source_key in seen:
                continue
            seen.add(source_key)
            built += _build_selected_group_child(
                obj,
                country,
                level,
                normalized_group_key,
                context=context,
            )
        for subdivision_key in _list_value(block.get("derived_subdivisions"), label=f"{label}.include[{index}].derived_subdivisions"):
            normalized_subdivision_key = _group_internal_name(str(subdivision_key))
            source_key = ("derived_subdivision", country, normalized_subdivision_key)
            if source_key in seen:
                continue
            seen.add(source_key)
            built += _build_selected_derived_subdivision_child(
                obj,
                country,
                normalized_subdivision_key,
                context=context,
                assigned_level=level,
                label=f"{label}.include[{index}]",
            )
    return built


def _build_selected_area_child(
    parent: NuevoAdminArea,
    area: AdminArea,
    *,
    context: _BuildContext,
) -> int:
    try:
        level = int(area.level or 0)
    except (TypeError, ValueError):
        return 0
    if level <= 0:
        return 0
    used_codes = {str(code or "") for code in parent.children.values_list("code", flat=True)}
    raw_code = _unique_child_code_piece(
        parent,
        _source_area_child_code(area, context=context),
        used_codes,
        fallback=area.id,
    )
    child = create_nuevo_area_from_spec(
        parent_country_id=parent.id,
        new_name=str(area.name or area.id).strip(),
        include_spec={level: {_clean_country_code(area.country_code): [{"id": str(area.id)}]}},
        entity_type=str(area.entity_type or area.raw_entity_type or "Subdivision").strip(),
        new_code=raw_code,
        capitals=[],
        auto_set_most_populated=True,
        source_population_year=context.source_population_year,
        source_population_index_registry=context.source_population_index_registry,
        allow_duplicate_source_data=True,
    )
    _apply_level(child, int(parent.level or 0) + 1)
    return 1


def _build_selected_group_child(
    parent: NuevoAdminArea,
    country: str,
    level: int,
    group_key: str,
    *,
    context: _BuildContext,
) -> int:
    group_key = _group_internal_name(group_key)
    values = _group_names(country, group_key, context=context)
    if not values:
        return 0
    used_codes = {str(code or "") for code in parent.children.values_list("code", flat=True)}
    raw_code = _unique_child_code_piece(
        parent,
        _code_piece(f"{country}_{group_key}") if country != context.country_code else _code_piece(group_key),
        used_codes,
        fallback=group_key,
    )
    child = create_nuevo_area_from_spec(
        parent_country_id=parent.id,
        new_name=group_key,
        include_spec={level: {country: values}},
        entity_type=str(_("Grupo")),
        new_code=raw_code,
        capitals=[],
        auto_set_most_populated=True,
        source_population_year=context.source_population_year,
        source_population_index_registry=context.source_population_index_registry,
        allow_duplicate_source_data=True,
    )
    _apply_level(child, int(parent.level or 0) + 1)
    return 1


def _build_selected_derived_subdivision_child(
    parent: NuevoAdminArea,
    country: str,
    subdivision_key: str,
    *,
    context: _BuildContext,
    assigned_level: int | None = None,
    label: str,
) -> int:
    record = _derived_subdivision_record_for_key(country, subdivision_key)
    if record is None:
        raise ValueError(
            _("No existe la subdivision %(subdivision)s para %(country)s.")
            % {"subdivision": subdivision_key, "country": country}
        )
    child_data = _parse_record_content(record)
    record_country = _clean_country_code(record.source_country_code or child_data.get("source_country_code") or country)
    record_key = _derived_subdivision_record_key(record, child_data)
    cache_key = (record_country, record_key)
    if cache_key in context.derived_subdivision_stack:
        raise ValueError(
            _("%(label)s tiene una referencia circular a la subdivision %(subdivision)s.")
            % {"label": label, "subdivision": record_key}
        )
    child_data.setdefault("internal_name", record.internal_name or record.slug)
    child_data.setdefault("source_country_code", record_country)
    child_data.setdefault("name", record.name or record.internal_name or record.slug)
    child_data["code"] = _derived_subdivision_child_code(parent, record, child_data, record_key)
    child_data["parent_code"] = parent.code
    if assigned_level is not None:
        child_data["level"] = max(1, min(5, int(assigned_level)))
    child_data.setdefault("entity_type", record.entity_type or child_data.get("generic_name") or "Subdivision")
    child_data.setdefault("use_selected_entities_as_children", False)
    context.derived_subdivision_stack.add(cache_key)
    try:
        return _build_node(
            child_data,
            parent_id=parent.id,
            context=context,
            label=f"{label}.derived_subdivisions[{record_key}]",
        )
    finally:
        context.derived_subdivision_stack.discard(cache_key)


def _source_area_child_code(area: AdminArea, *, context: _BuildContext) -> str:
    source_country = _clean_country_code(area.country_code)
    raw = str(area.code or area.id or area.name).strip()
    if source_country and source_country != context.country_code:
        raw = f"{source_country}_{raw}"
    return _code_piece(raw)


def _derived_subdivision_child_code(
    parent: NuevoAdminArea,
    record: DerivedSubdivision,
    data: dict[str, Any],
    record_key: str,
) -> str:
    code = _code_piece(data.get("code") or record.code or record_key)
    parent_code = _code_piece(parent.code)
    if parent_code and code.startswith(f"{parent_code}-"):
        return code
    record_country = _clean_country_code(record.source_country_code or data.get("source_country_code") or "")
    record_parent_code = _code_piece(data.get("parent_code") or "")
    if not record_parent_code and record_country:
        record_parent_code = _code_piece(derived_country_root_code(record_country, record_country))
    if record_parent_code and code.startswith(f"{record_parent_code}-"):
        return code[len(record_parent_code) + 1 :]
    return code or _code_piece(record_key)


def _unique_child_code_piece(parent: NuevoAdminArea, raw_code: str, used_codes: set[str], *, fallback: str) -> str:
    raw_code = _code_piece(raw_code or fallback)
    full_code = _child_code(parent, raw_code)
    if full_code not in used_codes:
        used_codes.add(full_code)
        return raw_code
    fallback_piece = _code_piece(fallback) or "ITEM"
    for index in range(2, 1000):
        candidate = _code_piece(f"{raw_code}-{fallback_piece}-{index}")
        full_code = _child_code(parent, candidate)
        if full_code not in used_codes:
            used_codes.add(full_code)
            return candidate
    raise ValueError(_("No se pudo generar un codigo unico para %(parent)s.") % {"parent": parent.name})


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
        for subdivision_key in _list_value(block.get("derived_subdivisions"), label=f"{label}.derived_subdivisions"):
            for unit_level, unit_country, unit_value in _derived_subdivision_source_unit_values(
                country,
                str(subdivision_key),
                context=context,
                label=label,
            ):
                include_levels[unit_country].add(level)
                include_levels[unit_country].add(unit_level)
                spec.setdefault(unit_level, {}).setdefault(unit_country, []).append(unit_value)

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
        for subdivision_key in _list_value(block.get("derived_subdivisions"), label=f"{label}.derived_subdivisions"):
            for unit_level, unit_country, unit_value in _derived_subdivision_source_unit_values(
                country,
                str(subdivision_key),
                context=context,
                label=label,
            ):
                spec.setdefault("restar", {}).setdefault(unit_level, {}).setdefault(unit_country, []).append(unit_value)
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


def _block_direct_area_values(block: dict[str, Any], *, label: str) -> list[Any]:
    values: list[Any] = []
    values.extend(_list_value(block.get("names"), label=f"{label}.names"))
    values.extend({"id": str(value)} for value in _list_value(block.get("ids"), label=f"{label}.ids"))
    values.extend({"code": str(value)} for value in _list_value(block.get("codes"), label=f"{label}.codes"))
    return values


def _derived_subdivision_source_unit_values(
    country_code: str,
    subdivision_key: str,
    *,
    context: _BuildContext,
    label: str,
) -> list[tuple[int, str, dict[str, str]]]:
    country_code = _clean_country_code(country_code)
    record = _derived_subdivision_record_for_key(country_code, subdivision_key)
    if record is None:
        raise ValueError(
            _("No existe la subdivision %(subdivision)s para %(country)s.")
            % {"subdivision": subdivision_key, "country": country_code}
        )
    data = _parse_record_content(record)
    record_country = _clean_country_code(record.source_country_code or data.get("source_country_code") or country_code)
    record_key = _derived_subdivision_record_key(record, data)
    cache_key = (record_country, record_key)
    if cache_key in context.derived_subdivision_cache:
        return list(context.derived_subdivision_cache[cache_key])
    if cache_key in context.derived_subdivision_stack:
        raise ValueError(
            _("%(label)s tiene una referencia circular a la subdivision %(subdivision)s.")
            % {"label": label, "subdivision": record_key}
        )
    context.derived_subdivision_stack.add(cache_key)
    try:
        data.setdefault("internal_name", record.internal_name or record.slug)
        data.setdefault("source_country_code", record_country)
        data.setdefault("name", record.name or record.internal_name or record.slug)
        values = _node_source_unit_values(
            data,
            default_country_code=record_country,
            context=context,
            label=f"{label}.{record_key}",
        )
        context.derived_subdivision_cache[cache_key] = list(values)
        return list(values)
    finally:
        context.derived_subdivision_stack.discard(cache_key)


def _node_source_unit_values(
    data: dict[str, Any],
    *,
    default_country_code: str,
    context: _BuildContext,
    label: str,
) -> list[tuple[int, str, dict[str, str]]]:
    if not isinstance(data, dict):
        return []
    values = _source_unit_values_from_spec(
        _spec_from_node(data, default_country_code=default_country_code, context=context, label=label),
        context=context,
    )
    for index, child in enumerate(data.get("children") if isinstance(data.get("children"), list) else [], start=1):
        if isinstance(child, dict):
            child_country = _clean_country_code(child.get("source_country_code") or default_country_code)
            values.extend(
                _node_source_unit_values(
                    child,
                    default_country_code=child_country,
                    context=context,
                    label=f"{label}.children[{index}]",
                )
            )
    return _dedupe_source_unit_values(values)


def _source_unit_values_from_spec(
    spec: dict,
    *,
    context: _BuildContext,
) -> list[tuple[int, str, dict[str, str]]]:
    if not spec:
        return []
    areas = _source_unit_areas_from_spec(spec, context=context)
    return [
        (int(area.level or 0), _clean_country_code(area.country_code), {"id": str(area.id)})
        for area in sorted(areas, key=lambda item: (_clean_country_code(item.country_code), int(item.level or 0), item.name, item.id))
        if area.id
    ]


def _source_unit_areas_from_spec(spec: dict, *, context: _BuildContext) -> list[AdminArea]:
    include_map = {key: value for key, value in (spec or {}).items() if key != "restar"}
    restar_raw = (spec or {}).get("restar", {}) or {}
    mun_from_macros: set[str] = set()
    mun_extra_incluidos: set[str] = set()
    mun_restar: set[str] = set()
    sub_extra_incluidos: dict[str, str | None] = {}
    sub_restar: dict[str, str | None] = {}

    def municipal_level_for(country: str) -> int:
        level = effective_source_municipal_level(country)
        if level is None:
            level = _deepest_source_level(country)
        if level is None:
            raise ValueError(_("No se ha definido nivel municipal para '%(country)s'.") % {"country": country})
        return int(level)

    def parse_include_group(value: dict, source_level: int) -> None:
        for country, labels_raw in value.items():
            country = _clean_country_code(country)
            labels = _labels_to_list(labels_raw)
            if not country or not labels:
                continue
            atomic_level = municipal_level_for(country)
            lookup_items = _lookup_many_with_preferences(country, source_level, labels)
            if source_level > atomic_level:
                for area, _preference in lookup_items:
                    ancestor = _ancestor_at_level(area, atomic_level)
                    if atomic_level == 0 and ancestor is not None:
                        mun_extra_incluidos.add(ancestor.id)
                        continue
                    sub_extra_incluidos[area.id] = ancestor.id if ancestor else None
            elif source_level == atomic_level:
                for area, preference in lookup_items:
                    mun_extra_incluidos.update(
                        _expand_to_municipal([area], country, preference, atomic_level)
                    )
            else:
                for area, preference in lookup_items:
                    mun_from_macros.update(
                        _expand_to_municipal([area], country, preference, atomic_level)
                    )

    def parse_restar_group(value: dict, source_level: int) -> None:
        for country, labels_raw in value.items():
            country = _clean_country_code(country)
            labels = _labels_to_list(labels_raw)
            if not country or not labels:
                continue
            atomic_level = municipal_level_for(country)
            included_municipal_ids = mun_from_macros | mun_extra_incluidos
            lookup_items = _lookup_many_with_preferences(
                country,
                source_level,
                labels,
                scoped_municipal_ids=included_municipal_ids,
                scoped_atomic_level=atomic_level,
            )
            for area, preference in lookup_items:
                if source_level > atomic_level:
                    ancestor = _ancestor_at_level(area, atomic_level)
                    if atomic_level == 0 and ancestor is not None:
                        mun_restar.add(ancestor.id)
                        continue
                    sub_restar[area.id] = ancestor.id if ancestor else None
                else:
                    mun_restar.update(_expand_to_municipal([area], country, preference, atomic_level))

    for key, value in include_map.items():
        parse_include_group(value, int(key))
    for key, value in restar_raw.items():
        parse_restar_group(value, int(key))

    mun_incluidos = mun_from_macros | mun_extra_incluidos
    sub_extra_final_ids = {
        sub_id
        for sub_id, ancestor_id in sub_extra_incluidos.items()
        if sub_id not in sub_restar
        and (ancestor_id is None or ancestor_id not in mun_incluidos or ancestor_id in mun_restar)
    }
    source_unit_ids = (mun_incluidos - mun_restar) | sub_extra_final_ids
    if not source_unit_ids:
        return []
    return list(AdminArea.objects.filter(id__in=source_unit_ids).order_by("country_code", "level", "name", "id"))


def _dedupe_source_unit_values(values: list[tuple[int, str, dict[str, str]]]) -> list[tuple[int, str, dict[str, str]]]:
    result: list[tuple[int, str, dict[str, str]]] = []
    seen: set[tuple[int, str, str]] = set()
    for level, country, value in values:
        item_id = str((value or {}).get("id") or "").strip()
        key = (int(level or 0), _clean_country_code(country), item_id)
        if not item_id or key in seen:
            continue
        seen.add(key)
        result.append((key[0], key[1], {"id": item_id}))
    return result


def _derived_subdivision_record_for_key(country_code: str, subdivision_key: str) -> DerivedSubdivision | None:
    country_code = _clean_country_code(country_code)
    try:
        internal_name = _group_internal_name(subdivision_key)
    except ValueError:
        return None
    entry_slug = _safe_slug(internal_name).replace("-", "_")
    queryset = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    for slug in (f"{country_code}_{entry_slug}", entry_slug):
        record = queryset.filter(slug=slug).first()
        if record:
            return record
    return queryset.filter(internal_name__iexact=internal_name).first()


def _derived_subdivision_record_key(record: DerivedSubdivision, data: dict[str, Any] | None = None) -> str:
    data = data if isinstance(data, dict) else {}
    for candidate in (
        data.get("internal_name"),
        record.internal_name,
        record.slug.removeprefix(f"{_clean_country_code(record.source_country_code)}_"),
        record.slug,
    ):
        try:
            return _group_internal_name(str(candidate or ""))
        except ValueError:
            continue
    return _group_internal_name(record.slug)


def _deepest_source_level(country_code: str) -> int | None:
    try:
        level = (
            AdminArea.objects
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .order_by("-level")
            .values_list("level", flat=True)
            .first()
        )
    except (OperationalError, ProgrammingError):
        level = None
    return int(level) if level is not None else None


def _labels_to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


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
            level = effective_source_municipal_level(country)
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


def _child_code_for_context(parent: NuevoAdminArea, raw_code: str, context: _BuildContext) -> str:
    code = _code_piece(raw_code)
    if not code:
        code = _code_piece(_safe_slug(parent.name))
    if (
        context.root_child_uses_local_code
        and parent.parent_id is None
        and int(parent.level or 0) == NuevoAdminArea.Level.COUNTRY
    ):
        return code
    return _child_code(parent, code)


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


def _group_internal_name(value: Any) -> str:
    return _safe_group_toml_key(value)


def _code_piece(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "-", str(value or "").strip()).strip("-_")
    return text.upper()


def _clean_country_code(value: Any) -> str:
    return str(value or "").strip().lower()
