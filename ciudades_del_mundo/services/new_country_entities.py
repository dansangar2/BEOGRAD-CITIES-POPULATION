"""Incremental entity creation alongside existing, materialized country trees."""
from __future__ import annotations

import tomllib

from django.db import transaction
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea
from django.db.models import Q
from ciudades_del_mundo.services.derived_codes import derived_code_piece
from ciudades_del_mundo.services.derived_config_seeds import _render_new_country_seed
from ciudades_del_mundo.services.derived_subdivision_builder import (
    _BuildContext, _build_generated_children, _build_node, _child_code_for_context, _refresh_container_totals, _spec_from_node, _source_unit_areas_from_spec,
)
from ciudades_del_mundo.services.nuevo_admin_builder import create_nuevo_area_from_spec
from ciudades_del_mundo.services.source_population_indices import load_source_population_index_registry


def is_country_bridge(config: DerivedCountryConfig) -> bool:
    try:
        data = tomllib.loads(config.content or '')
    except tomllib.TOMLDecodeError:
        return False
    return bool(data.get('legacy')) and not data.get('entities') and not data.get('children')


def materialized_country_root(country: DerivedCountry) -> NuevoAdminArea | None:
    codes = []
    for config in country.configs.all():
        if config.derived_country_code and config.derived_country_code not in codes:
            codes.append(config.derived_country_code)
    if country.slug not in codes:
        codes.append(country.slug)
    for code in codes:
        root = NuevoAdminArea.objects.filter(country_code=code, parent__isnull=True).order_by('level', 'id').first()
        if root is not None:
            return root
    return None


def parent_area_for_country(country: DerivedCountry, area_id: str) -> NuevoAdminArea | None:
    if not area_id:
        return None
    root = materialized_country_root(country)
    parent = NuevoAdminArea.objects.filter(pk=area_id, country_code=root.country_code).first() if root else None
    if parent is None:
        raise ValueError(_("La entidad padre no pertenece a este país."))
    return parent


def full_area_code(area: NuevoAdminArea) -> str:
    chain = []
    seen = set()
    current = area
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        chain.append(current)
        current = current.parent
    full = ''
    previous = None
    for current in reversed(chain):
        code = derived_code_piece(current.code or current.pk)
        if not full:
            full = code
        elif code.startswith(full + '-'):
            full = code
        else:
            parent_code = derived_code_piece(previous.code) if previous else ''
            local = code[len(parent_code) + 1:] if parent_code and code.startswith(parent_code + '-') else code
            full = f'{full}-{local}'
        previous = current
    return full


def config_for_area(country: DerivedCountry, area: NuevoAdminArea) -> DerivedCountryConfig | None:
    for config in country.configs.all():
        if tomllib.loads(config.content or '').get('materialized_area_id') == area.pk:
            return config
    return None


def _move_saved_branch(area: NuevoAdminArea, parent: NuevoAdminArea, local_code: str, delta: int, context: _BuildContext) -> list[NuevoAdminArea]:
    children = list(area.children.filter(country_code=area.country_code))
    previous_code = area.code
    code = _child_code_for_context(parent, local_code, context)
    if NuevoAdminArea.objects.filter(country_code=area.country_code, code=code).exclude(pk=area.pk).exists():
        raise ValueError(_("Ya existe una configuracion '%(slug)s' para este pais.") % {'slug': code})
    area.parent = parent
    area.code = code
    area.level += delta
    area.save(update_fields=['parent', 'code', 'level'])
    moved = [area]
    for child in children:
        local = child.code[len(previous_code) + 1:] if child.code.startswith(previous_code + '-') else child.code
        moved.extend(_move_saved_branch(child, area, local, delta, context))
    return moved


def delete_saved_entity(country: DerivedCountry, area: NuevoAdminArea) -> None:
    """Remove one node and promote its children while keeping their IDs."""
    parent = area.parent
    if parent is None:
        raise ValueError(_("La entidad padre no pertenece a este país."))
    context = _BuildContext(
        country_code=country.source_country_code, source_population_year=None,
        source_population_index_registry=None, group_cache={},
        derived_subdivision_cache={}, derived_subdivision_stack=set(), root_child_uses_local_code=True,
    )
    owners = {}
    for config in country.configs.all():
        data = tomllib.loads(config.content or "")
        if data.get("materialized_area_id"):
            owners[data["materialized_area_id"]] = (config, data)
    for child in area.children.filter(country_code=area.country_code):
        local = child.code.removeprefix(area.code + "-")
        for moved in _move_saved_branch(child, parent, local, parent.level + 1 - child.level, context):
            if moved.pk not in owners:
                continue
            config, data = owners[moved.pk]
            parent_config = owners.get(moved.parent_id)
            parent_slug = parent_config[0].slug if parent_config else ""
            data.update(parent_area_id=moved.parent_id, parent_config_slug=parent_slug)
            data["entities"][0].update(level=moved.level, parent_config_slug=parent_slug)
            config.content = _render_new_country_seed(data)
            config.save(update_fields=["content", "updated_at"])
    area.delete()
    _refresh_aggregate_ancestors(country, parent)


def _refresh_aggregate_ancestors(country: DerivedCountry, current: NuevoAdminArea | None) -> None:
    while current is not None:
        owner = config_for_area(country, current)
        data = tomllib.loads(owner.content) if owner else {}
        if (owner is None and not current.municipios_originales.exists()) or data.get('aggregate_children'):
            _refresh_container_totals(current, manual=next(iter(data.get("entities", [])), None))
        current = current.parent


def branch_source_ids(area, *, include_self=True):
    """Collect distinct sources across the saved branch, including foreign sources."""
    pending = [area.pk]
    seen = {area.pk}
    nodes = [area.pk] if include_self else []
    while pending:
        pending = [pk for pk in NuevoAdminArea.objects.filter(
            country_code=area.country_code, parent_id__in=pending,
        ).values_list("pk", flat=True) if pk not in seen]
        seen.update(pending)
        nodes.extend(pending)
    return set(NuevoAdminArea.objects.filter(pk__in=nodes).exclude(
        municipios_originales=None,
    ).values_list("municipios_originales", flat=True))


def update_saved_entity(country, area, entity, *, replace_sources, replace_capitals=False):
    """Edit the saved node in place without changing its ID or descendants."""
    name, entity_type = entity["name"], entity["entity_type"]
    if entity.get("manual_template"):
        _refresh_container_totals(area, manual=entity)
    child_sources = branch_source_ids(area, include_self=False) if not entity.get("manual_template") else set()
    if replace_sources or child_sources:
        context = _BuildContext(
            country_code=country.source_country_code, source_population_year=None,
            source_population_index_registry=load_source_population_index_registry(),
            group_cache={}, derived_subdivision_cache={}, derived_subdivision_stack=set(),
        )
        spec = (_spec_from_node(entity, default_country_code=country.source_country_code, context=context, label=area.pk)
                if replace_sources else {})
        inherited = child_sources if replace_sources else branch_source_ids(area)
        for source in AdminArea.objects.filter(pk__in=inherited).values("pk", "country_code", "level"):
            spec.setdefault(source["level"], {}).setdefault(source["country_code"], []).append({"id": source["pk"]})
        if spec:
            create_nuevo_area_from_spec(
                parent_country_id=area.parent_id, new_name=name, entity_type=entity_type,
                new_code=area.code, include_spec=spec,
                source_population_index_registry=context.source_population_index_registry,
                allow_duplicate_source_data=True, existing_area=area,
            )
        else:
            area.municipios_originales.clear()
            _refresh_container_totals(area)
        if replace_sources and not area.children.exists() and not entity.get("manual_template"):
            _build_generated_children(area, entity, context=context, label=area.pk)
    if replace_capitals:
        capitals = []
        allowed = capital_candidate_ids(country, entity, area, include_current=True)
        source_codes = set(area.municipios_originales.values_list("country_code", flat=True))
        source_codes.add(country.source_country_code)
        for value in entity.get("capitals", []):
            candidates = AdminArea.objects.filter(country_code__in=source_codes)
            capital = AdminArea.objects.filter(pk=value, pk__in=allowed).first()
            if capital is None:
                matches = list(candidates.filter(Q(name__iexact=value) | Q(code__iexact=value))[:2])
                if len(matches) != 1:
                    raise ValueError(_("La seleccion contiene subdivisiones que no pertenecen al pais fuente."))
                capital = matches[0]
            capitals.append(capital)
        area.capitals.set(capitals)
        area.capital_names_by_language = {}
        area.save(update_fields=["capital_names_by_language"])
    area.name = name
    area.entity_type = entity_type
    area.save(update_fields=["name", "entity_type", "updated_at"])
    if replace_sources or child_sources or entity.get("manual_template"):
        _refresh_aggregate_ancestors(country, area.parent)


def capital_candidate_ids(country, entity, area=None, *, include_current=False):
    context = _BuildContext(
        country_code=country.source_country_code, source_population_year=None,
        source_population_index_registry=None, group_cache={},
        derived_subdivision_cache={}, derived_subdivision_stack=set(),
    )
    spec = _spec_from_node(entity, default_country_code=country.source_country_code, context=context, label=country.slug)
    result = {unit.pk for unit in _source_unit_areas_from_spec(spec, context=context)} if spec else set()
    if area is None:
        return result
    parents = [area.pk]
    seen = {area.pk}
    descendants = []
    while parents:
        parents = [pk for pk in NuevoAdminArea.objects.filter(
            country_code=area.country_code, parent_id__in=parents,
        ).values_list("pk", flat=True) if pk not in seen]
        seen.update(parents)
        descendants.extend(parents)
    nodes = NuevoAdminArea.objects.filter(pk__in=([area.pk] if include_current else []) + descendants)
    result.update(nodes.exclude(municipios_originales=None).values_list("municipios_originales", flat=True))
    result.update(nodes.exclude(capitals=None).values_list("capitals", flat=True))
    return result


@transaction.atomic
def materialize_created_entity(config: DerivedCountryConfig, parent: NuevoAdminArea) -> NuevoAdminArea:
    """Build only the new node; preserve the country's saved IDs and branches."""
    data = tomllib.loads(config.content)
    entity = dict(data['entities'][0])
    context = _BuildContext(
        country_code=config.source_country_code, source_population_year=None,
        source_population_index_registry=load_source_population_index_registry(),
        group_cache={}, derived_subdivision_cache={}, derived_subdivision_stack=set(),
        root_child_uses_local_code=True,
    )
    code = _child_code_for_context(parent, str(entity.get('code') or config.slug), context)
    if NuevoAdminArea.objects.filter(country_code=parent.country_code, code=code).exists():
        raise ValueError(_("Ya existe una configuracion '%(slug)s' para este pais.") % {'slug': code})
    _build_node(entity, parent_id=parent.pk, context=context, label=config.full_slug)
    area = NuevoAdminArea.objects.get(country_code=parent.country_code, parent=parent, code=code)
    data.update(materialized_area_id=area.pk, parent_area_id=parent.pk,
                aggregate_children=not bool(entity.get('include')))
    config.content = _render_new_country_seed(data)
    config.save(update_fields=['content', 'updated_at'])
    previous_parents = []
    for child_config in config.country.configs.exclude(pk=config.pk):
        child_data = tomllib.loads(child_config.content or '')
        if child_data.get('parent_config_slug') != config.slug or not child_data.get('materialized_area_id'):
            continue
        child = parent_area_for_country(config.country, child_data['materialized_area_id'])
        if child.parent_id == area.pk:
            continue
        previous_parents.append(child.parent)
        local_code = child_data['entities'][0]['code']
        _move_saved_branch(child, area, local_code, area.level + 1 - child.level, context)
        child_data['parent_area_id'] = area.pk
        child_config.content = _render_new_country_seed(child_data)
        child_config.save(update_fields=['content', 'updated_at'])
    if data['aggregate_children'] and area.children.exists():
        _refresh_container_totals(area, manual=entity)
    # Explicitly selected source data stays authoritative. Containers sum children.
    for previous in previous_parents:
        _refresh_aggregate_ancestors(config.country, previous)
    _refresh_aggregate_ancestors(config.country, parent)
    return area
