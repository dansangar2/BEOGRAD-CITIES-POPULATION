"""Resolve partial selections of configured cities using their SQL districts."""

from django.db.models import Q

from ciudades_del_mundo.models import AdminArea


def _ancestor_ids(area):
    seen = {area.id}
    area = area.parent
    while area is not None and area.id not in seen:
        seen.add(area.id)
        yield area.id
        area = area.parent


def composite_city(area):
    """Return the city owning a source district, without guessing from names."""
    if area.city_merge_status != AdminArea.CityMergeStatus.SOURCE:
        return None
    seen = {area.id}
    parent = area.parent
    while parent is not None and parent.id not in seen:
        seen.add(parent.id)
        if parent.country_code != area.country_code:
            return None
        if parent.city_merge_status == AdminArea.CityMergeStatus.UNIFIED:
            return parent
        parent = parent.parent
    return None


def _inside(area, ancestor):
    seen = set()
    while area is not None and area.id not in seen:
        if area.id == ancestor.id:
            return True
        seen.add(area.id)
        area = area.parent
    return False


def normalize_composite_spec(spec, *, fallback_level=None):
    """Partition affected cities into disjoint districts before doing arithmetic.

    Ordinary recipes retain their existing municipal and partial-area behavior.
    Only cities with an explicitly selected source district are partitioned.
    """
    from ciudades_del_mundo.services.nuevo_admin_builder import (
        _expand_to_municipal, _lookup_many_with_preferences,
        _label_and_city_merge_preference, _labels_to_list,
        effective_source_municipal_level,
    )

    candidates = Q()
    for levels in (spec, spec.get("restar", {})):
        for level, countries in levels.items():
            if level == "restar":
                continue
            for country, values in countries.items():
                labels = [_label_and_city_merge_preference(value)[0] for value in _labels_to_list(values)]
                if labels:
                    candidates |= Q(country_code=country, level=int(level)) & (
                        Q(id__in=labels) | Q(code__in=labels) | Q(name__in=labels)
                    )
    if not candidates or not AdminArea.objects.filter(candidates, city_merge_status=AdminArea.CityMergeStatus.SOURCE).exists():
        return spec

    includes, excludes = [], []
    for operation, levels in ((includes, spec), (excludes, spec.get("restar", {}))):
        for level, countries in levels.items():
            if level == "restar":
                continue
            for country, values in countries.items():
                operation.extend(_lookup_many_with_preferences(country, int(level), values))
    cities = {}
    for area, _ in includes + excludes:
        city = composite_city(area)
        if city is not None:
            cities[city.id] = city
    if not cities:
        return spec

    units = {}
    for area, preference in includes:
        level = effective_source_municipal_level(area.country_code, fallback=fallback_level)
        if level is not None and area.level <= level:
            ids = _expand_to_municipal([area], area.country_code, preference, level)
            units.update((row.id, row) for row in AdminArea.objects.filter(id__in=ids))
        else:
            units[area.id] = area

    for city in cities.values():
        covering = [unit for unit in units.values() if _inside(city, unit)
                    or (unit.city_merge_status == AdminArea.CityMergeStatus.UNIFIED and _inside(unit, city))]
        if covering:
            for unit in covering:
                units.pop(unit.id, None)
            districts = city.children.filter(city_merge_status=AdminArea.CityMergeStatus.SOURCE)
            units.update((row.id, row) for row in districts)

    # Populate parent caches in batches; a regional selection can contain many
    # municipalities but only a small number of shared ancestors.
    known = dict(units)
    frontier = list(units.values())
    while frontier:
        missing = {area.parent_id for area in frontier if area.parent_id and area.parent_id not in known}
        parents = list(AdminArea.objects.filter(id__in=missing)) if missing else []
        known.update((area.id, area) for area in parents)
        for area in frontier:
            if area.parent_id in known:
                area.parent = known[area.parent_id]
        frontier = parents

    # A selected parent already contains its selected descendants.
    selected_ids = set(units)
    units = {key: area for key, area in units.items()
             if not selected_ids.intersection(_ancestor_ids(area))}
    remaining_subtractions = []
    for excluded, preference in excludes:
        if composite_city(excluded) is not None:
            # Split a source district only when a finer source unit is removed.
            pending = list(units.values())
            while pending:
                unit = pending.pop()
                if unit.id != excluded.id and _inside(excluded, unit):
                    children = list(unit.children.filter(city_merge_status=AdminArea.CityMergeStatus.SOURCE))
                    if children:
                        units.pop(unit.id, None)
                        units.update((row.id, row) for row in children)
                        pending.extend(children)
            units = {key: unit for key, unit in units.items() if not _inside(unit, excluded)}
        else:
            units = {key: unit for key, unit in units.items() if not _inside(unit, excluded)}
            remaining_subtractions.append(excluded)

    result = {}
    for area in units.values():
        result.setdefault(area.level, {}).setdefault(area.country_code, []).append({"id": area.id})
    for area in remaining_subtractions:
        result.setdefault("restar", {}).setdefault(area.level, {}).setdefault(area.country_code, []).append({"id": area.id})
    return result


def selected_composite_members(areas, subtract_ids=()):
    """Resolve signed group members without promoting a district to a full city."""
    if not any(composite_city(area) is not None for area in areas):
        return areas
    subtract_ids = set(subtract_ids)
    spec = {}
    for area in areas:
        target = spec.setdefault("restar", {}) if area.id in subtract_ids else spec
        target.setdefault(area.level, {}).setdefault(area.country_code, []).append({"id": area.id})
    normalized = normalize_composite_spec(spec)
    ids = {item["id"] for level, countries in normalized.items() if level != "restar"
           for values in countries.values() for item in values}
    return list(AdminArea.objects.filter(id__in=ids).select_related("parent").order_by("country_code", "level", "name", "id"))
