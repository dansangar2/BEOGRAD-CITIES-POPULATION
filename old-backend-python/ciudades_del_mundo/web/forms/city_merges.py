"""Hydrate and serialize configured city-unification forms."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from django.db import OperationalError, ProgrammingError
from django.urls import reverse
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.nuevo_admin_builder import ORIGINAL_MUNICIPAL_LEVEL
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups


def _config_city_builder_payload(country_code: str, data: dict, *, enabled: bool) -> dict:
    base_payload = {
        "enabled": False,
        "legal_level": "",
        "parent_level": "",
        "parent_options": [],
        "children_by_parent": {},
        "sections": [],
    }
    if not enabled:
        return base_payload
    country_code = query_parameters.normalize_country_key(country_code)
    legal_level = _config_legal_subdivision_level(country_code, data)
    if legal_level is None or legal_level <= 0:
        return base_payload
    parent_level = legal_level - 1
    try:
        child_parent_ids = set(
            str(parent_id)
            for parent_id in (
                area_selectors.visible_admin_areas()
                .filter(country_code__iexact=country_code, level=legal_level)
                .exclude(parent_id__isnull=True)
                .values_list("parent_id", flat=True)
                .distinct()
            )
            if parent_id
        )
        if not child_parent_ids:
            return base_payload
        parents = list(
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code, level=parent_level, id__in=child_parent_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return base_payload
    if not parents:
        return base_payload

    parent_lookup: dict[str, AdminArea] = {}
    parent_options = []
    for parent in parents:
        parent_id = str(parent.id)
        parent_payload = _config_city_area_payload(parent)
        parent_options.append(parent_payload)
        for key in _config_city_lookup_values(parent):
            parent_lookup.setdefault(key, parent)

    raw_cities = data.get("cities") if isinstance(data.get("cities"), list) else []
    section_parent_ids = {
        str(parent.id)
        for raw_city in raw_cities
        if isinstance(raw_city, dict)
        for parent in [_config_city_parent_from_toml(raw_city, parent_level=parent_level, parent_lookup=parent_lookup)]
        if parent
    }
    children_by_parent, child_lookup_by_parent = _config_city_children_payload_by_parent(
        country_code,
        parent_ids=section_parent_ids,
        legal_level=legal_level,
    )
    sections = _config_city_sections_from_toml(
        raw_cities,
        parent_level=parent_level,
        legal_level=legal_level,
        parent_lookup=parent_lookup,
        children_by_parent=children_by_parent,
        child_lookup_by_parent=child_lookup_by_parent,
    )

    return {
        "enabled": bool(parent_options),
        "legal_level": legal_level,
        "parent_level": parent_level,
        "country_code": country_code,
        "children_url": reverse("ciudades_del_mundo:group_source_data"),
        "parent_options": parent_options,
        "children_by_parent": children_by_parent,
        "sections": sections,
    }


def _config_city_children_payload_by_parent(
    country_code: str,
    *,
    parent_ids: set[str],
    legal_level: int,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    if not parent_ids:
        return {}, {}
    try:
        children = list(
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code, level=legal_level, parent_id__in=parent_ids)
            .only("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "pop_latest")
            .order_by("name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {}, {}
    children_by_parent: dict[str, list[dict]] = {}
    child_lookup_by_parent: dict[str, dict[str, dict]] = {}
    for child in children:
        parent_id = str(child.parent_id)
        child_payload = _config_city_area_payload(child)
        child_payload["population_text"] = _queries_groups._group_metric_text(child.pop_latest)
        children_by_parent.setdefault(parent_id, []).append(child_payload)
        lookup = child_lookup_by_parent.setdefault(parent_id, {})
        for key in _config_city_lookup_values(child):
            lookup.setdefault(key, child_payload)
    return children_by_parent, child_lookup_by_parent


def _config_legal_subdivision_level(country_code: str, data: dict) -> int | None:
    raw = data.get("LEGAL_SUBDIVISION") if isinstance(data, dict) else None
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if country_code in ORIGINAL_MUNICIPAL_LEVEL:
        return int(ORIGINAL_MUNICIPAL_LEVEL[country_code])
    return None


def _config_city_area_payload(area: AdminArea) -> dict:
    name = _queries_browser._area_display_name(area)
    entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(area.id),
        "code": str(area.code or ""),
        "name": name,
        "label": label,
        "level": int(area.level or 0),
        "type_text": entity_type or str(area.entity_type or ""),
    }


def _config_city_lookup_values(area: AdminArea) -> list[str]:
    return [
        _config_city_lookup_key(value)
        for value in (area.id, area.code, area.name, _queries_browser._area_display_name(area))
        if str(value or "").strip()
    ]


def _config_city_lookup_key(value) -> str:
    return str(value or "").strip().casefold()


def _config_city_sections_from_toml(
    raw_cities,
    *,
    parent_level: int,
    legal_level: int,
    parent_lookup: dict[str, AdminArea],
    children_by_parent: dict[str, list[dict]],
    child_lookup_by_parent: dict[str, dict[str, dict]],
) -> list[dict]:
    sections = []
    for raw_city in raw_cities or []:
        if not isinstance(raw_city, dict):
            continue
        parent = _config_city_parent_from_toml(raw_city, parent_level=parent_level, parent_lookup=parent_lookup)
        if not parent:
            continue
        parent_id = str(parent.id)
        children = children_by_parent.get(parent_id) or []
        if not children:
            continue
        selected_children = _config_city_selected_children_from_toml(
            raw_city,
            children=children,
            lookup=child_lookup_by_parent.get(parent_id, {}),
        )
        if not selected_children:
            selected_children = children
        parent_payload = _config_city_area_payload(parent)
        sections.append(
            {
                "parent_id": parent_id,
                "parent_name": parent_payload["name"],
                "parent_label": parent_payload["label"],
                "parent_code": parent_payload["code"],
                "parent_level": parent_level,
                "parent_type": str(
                    raw_city.get("parent_type")
                    or raw_city.get("parent_entity_type")
                    or parent_payload["type_text"]
                ),
                "city": str(raw_city.get("city") or parent_payload["name"]),
                "code": str(raw_city.get("id") or parent_payload["code"] or parent_id),
                "level": int(raw_city.get("level") or legal_level),
                "type": str(raw_city.get("type") or "City"),
                "selected_ids": [child["id"] for child in selected_children],
                "communes": [child["code"] or child["id"] for child in selected_children],
                "keep_communes": bool(raw_city.get("keep_communes", False)),
            }
        )
    return sections


def _config_city_parent_from_toml(raw_city: dict, *, parent_level: int, parent_lookup: dict[str, AdminArea]) -> AdminArea | None:
    raw_from = raw_city.get("from") if isinstance(raw_city.get("from"), dict) else {}
    labels = []
    for level_key, level_labels in raw_from.items():
        try:
            is_parent_level = int(level_key) == int(parent_level)
        except (TypeError, ValueError):
            is_parent_level = False
        if not is_parent_level:
            continue
        if isinstance(level_labels, (list, tuple)):
            labels.extend(level_labels)
        else:
            labels.append(level_labels)
    if not labels:
        for level_labels in raw_from.values():
            if isinstance(level_labels, (list, tuple)):
                labels.extend(level_labels)
            else:
                labels.append(level_labels)
    for label in labels:
        parent = parent_lookup.get(_config_city_lookup_key(label))
        if parent:
            return parent
    return None


def _config_city_selected_children_from_toml(raw_city: dict, *, children: list[dict], lookup: dict[str, dict]) -> list[dict]:
    selected = []
    seen = set()
    for label in raw_city.get("communes") or []:
        child = lookup.get(_config_city_lookup_key(label))
        if child and child["id"] not in seen:
            selected.append(child)
            seen.add(child["id"])
    return selected
