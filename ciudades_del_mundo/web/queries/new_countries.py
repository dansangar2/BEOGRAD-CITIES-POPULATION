"""Select derived country/configuration rows and source options."""

from __future__ import annotations
from ciudades_del_mundo.ports.query_errors import DataNotFoundError

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from collections import Counter
from decimal import Decimal
from collections.abc import Iterable
import json
import re
import tomllib
from django.db import OperationalError, ProgrammingError
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, DerivedSubdivision, NuevoAdminArea
from ciudades_del_mundo.services.derived_codes import derived_code_piece, derived_country_root_code
from ciudades_del_mundo.services import new_country_entities as entity_service
from urllib.parse import urlencode
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.forms import new_countries as _forms_new_countries
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from ciudades_del_mundo.web.queries import assets as _queries_assets
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import exports as _queries_exports
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import new_country_browser as _queries_new_country_browser
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions


def _new_country_summary_payload() -> dict:
    countries = _new_country_container_rows(
        list(DerivedCountry.objects.prefetch_related("configs").order_by("name", "slug"))
    )
    population_total = sum(row.get("population") or 0 for row in countries)
    area_total = sum(row.get("area_km2") or 0 for row in countries)
    return {
        "type": "donut",
        "total": population_total,
        "countries": countries,
        "items": [
            {
                "key": row["key"],
                "label": row["label"],
                "value": row.get("population") or 0,
                "detail_url": row["detail_url"],
            }
            for row in countries
        ],
        "charts": {
            "population": {
                "type": "donut",
                "total": population_total,
                "items": [
                    {
                        "key": row["key"],
                        "label": row["label"],
                        "value": row.get("population") or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in countries
                ],
            },
            "area": {
                "type": "donut",
                "total": area_total,
                "items": [
                    {
                        "key": row["key"],
                        "label": row["label"],
                        "value": row.get("area_km2") or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in sorted(countries, key=lambda item: item.get("area_km2") or 0, reverse=True)
                    if row.get("area_km2")
                ],
            },
        },
    }


def _new_country_cards(countries: list[DerivedCountry]) -> list[dict]:
    configs_by_country: dict[str, list[DerivedCountryConfig]] = {}
    countries_by_source: dict[str, list[DerivedCountry]] = {}
    fallback_countries: list[DerivedCountry] = []
    for country in countries:
        country_configs = list(country.configs.all())
        configs_by_country[country.slug] = country_configs
        source_code = _new_country_container_source_code(country, country_configs)
        if source_code:
            countries_by_source.setdefault(source_code, []).append(country)
        else:
            fallback_countries.append(country)

    country_rows = _queries_browser._admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes = {
        query_parameters.normalize_country_key(country.get("code"))
        for country in country_rows
        if query_parameters.normalize_country_key(country.get("code"))
    }
    cards = []
    for country in country_rows:
        code = query_parameters.normalize_country_key(country.get("code"))
        if not code:
            continue
        derived_countries = countries_by_source.pop(code, [])
        container_rows = _new_country_container_rows(
            derived_countries,
            configs_by_country=configs_by_country,
            include_objects=True,
        )
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population_text": _queries_groups._group_metric_text(country.get("population")),
                "area_text": _queries_groups._group_metric_text(country.get("area_km2")),
                "flag_url": _queries_groups._stored_visual_asset_image_url(country.get("flag_asset")),
                "configs": container_rows,
                "config_count": len(container_rows),
                "new_url": reverse(
                    "ciudades_del_mundo:new_country_new_for_source",
                    kwargs={"source_country_code": code},
                ),
            }
        )

    unmatched_countries = list(fallback_countries)
    for source_code in sorted(countries_by_source):
        if source_code not in visible_country_codes:
            unmatched_countries.extend(countries_by_source[source_code])
    if unmatched_countries:
        container_rows = _new_country_container_rows(
            unmatched_countries,
            configs_by_country=configs_by_country,
            include_objects=True,
        )
        cards.append(
            {
                "key": "__other__",
                "code": "",
                "label": _("Otros paises"),
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "configs": container_rows,
                "config_count": len(container_rows),
                "new_url": reverse("ciudades_del_mundo:new_country_new"),
            }
        )
    return cards


def _new_country_container_source_code(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig] | None = None,
) -> str:
    source_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_code:
        return source_code
    for config in (configs if configs is not None else country.configs.all()):
        source_code = _new_country_config_source_code(config)
        if source_code:
            return source_code
    return ""


def _new_country_container_built_code(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig] | None = None,
) -> str:
    for config in (configs if configs is not None else country.configs.all()):
        built_code = _new_country_config_built_code(config)
        if built_code:
            return built_code
    return query_parameters.normalize_country_key(country.slug)


def _new_country_container_rows(
    countries: list[DerivedCountry],
    *,
    configs_by_country: dict[str, list[DerivedCountryConfig]] | None = None,
    include_objects: bool = False,
) -> list[dict]:
    rows = []
    for country in sorted(countries, key=lambda item: (item.name.casefold(), item.slug)):
        configs = (
            list(configs_by_country.get(country.slug, []))
            if configs_by_country is not None
            else list(country.configs.order_by("name", "slug"))
        )
        config_rows = _new_country_config_rows(configs)
        source_code = _new_country_container_source_code(country, configs)
        built_code = _new_country_container_built_code(country, configs)
        root = _queries_exports._new_country_detail_export_root(country, config_rows)
        if root is not None:
            fallback_area, fallback_population = _queries_exports._new_country_detail_export_totals(country, config_rows)
            display_name = _queries_browser._area_display_name(root)
            population_value = root.pop_latest if root.pop_latest is not None else fallback_population
            area_value = root.area_km2 if root.area_km2 is not None else fallback_area
            built_code = query_parameters.normalize_country_key(root.country_code) or built_code
            built_count = NuevoAdminArea.objects.filter(country_code=root.country_code).count()
            visual_assets = _new_country_visual_assets_for_root(root, source_code)
        else:
            area_value, population_value = _queries_exports._new_country_detail_export_totals(country, config_rows)
            display_name = country.name
            built_count = 0
            visual_assets = {}
        row = {
            "key": country.slug,
            "code": built_code,
            "label": display_name,
            "display_name": display_name,
            "built_code": built_code,
            "source_code": source_code,
            "population": _queries_new_country_browser._new_country_json_number(population_value) or 0,
            "area_km2": _application_browser_metrics._number_or_none(area_value) or 0,
            "population_text": _queries_groups._group_metric_text(population_value),
            "area_text": _queries_groups._group_metric_text(area_value),
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
            "flag_url": _queries_groups._stored_visual_asset_image_url(visual_assets.get("flag")),
            "built_count": built_count,
            "config_count": len(config_rows),
            "detail_url": (
                reverse("ciudades_del_mundo:api_new_country_container_detail", kwargs={"country_slug": country.slug})
                if config_rows or root is not None
                else ""
            ),
            "detail_api_url": (
                reverse("ciudades_del_mundo:api_new_country_container_detail", kwargs={"country_slug": country.slug})
                if config_rows or root is not None
                else ""
            ),
            "edit_url": _new_country_detail_url(country),
            "export_excel_url": _new_country_detail_export_excel_url(country),
            "view_url": (
                reverse("ciudades_del_mundo:nuevo_area_detail", kwargs={"country_id": root.id})
                if root is not None
                else ""
            ),
            "search_text": " ".join(
                [
                    country.name,
                    country.slug,
                    built_code,
                    source_code,
                    *(config.name for config in configs),
                    *(config.slug for config in configs),
                ]
            ),
        }
        if include_objects:
            row["country"] = country
            row["root"] = root
        rows.append(row)
    return rows


def _new_country_config_level_options(configs: list[DerivedCountryConfig]) -> list[dict]:
    levels = _queries_new_country_browser._new_country_config_available_levels_for_configs(configs)
    return [
        {
            "value": row["value"],
            "label": _("NV %(level)s") % {"level": row["value"]},
            "count": row["count"],
        }
        for row in levels
    ]


def _new_country_legacy_materialized_root(configs: list[DerivedCountryConfig]) -> NuevoAdminArea | None:
    """Recognize country-wide bridge recipes whose actual hierarchy is in SQL."""
    configs = [config for config in configs if _new_country_config_toml(config).get("legacy") and not _new_country_config_toml(config).get("entities")]
    if len(configs) != 1:
        return None
    config = configs[0]
    data = _new_country_config_toml(config)
    if not data.get("legacy") or data.get("entities") or data.get("children"):
        return None
    root = NuevoAdminArea.objects.filter(
        country_code=_new_country_config_built_code(config), parent__isnull=True,
    ).order_by("level", "id").first()
    if root is not None and root.children.filter(country_code=root.country_code).exists():
        return root
    return None


def _new_country_materialized_tree_id(country: DerivedCountry, area_id: str) -> str:
    return f"materialized:{country.slug}:{area_id}"


def _new_country_config_full_display_code(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    area_id = data.get("materialized_area_id")
    if area_id:
        area = entity_service.parent_area_for_country(config.country, area_id)
        if area is not None:
            return entity_service.full_area_code(area)
    root = entity_service.materialized_country_root(config.country)
    prefix = entity_service.full_area_code(root) if root else derived_code_piece(config.country.slug)
    code = _forms_new_countries._new_country_config_expected_full_code(config)
    return code if code.startswith(prefix + "-") else f"{prefix}-{code}"


def _new_country_materialized_tree_rows(
    country: DerivedCountry,
    root: NuevoAdminArea,
    *,
    parent_tree_id: str,
    selected_level: int,
) -> list[dict]:
    """Read a single persisted branch or selected level, without rebuilding data."""
    areas = NuevoAdminArea.objects.filter(country_code=root.country_code).exclude(pk=root.pk)
    parent = root
    if selected_level > 0:
        areas = areas.filter(level=selected_level)
    elif parent_tree_id:
        prefix = f"materialized:{country.slug}:"
        if not parent_tree_id.startswith(prefix):
            return []
        parent = areas.filter(pk=parent_tree_id[len(prefix):]).first()
        if parent is None:
            return []
        areas = areas.filter(parent=parent)
    else:
        areas = areas.filter(parent=root)
    areas = areas.select_related("parent").prefetch_related("capitals").annotate(
        child_count=Count("children", filter=Q(children__country_code=root.country_code)),
    ).order_by("name", "id")
    rows = []
    config_by_area = {
        _new_country_config_toml(config).get("materialized_area_id"): config
        for config in country.configs.all()
    }
    for area in areas:
        ancestor_ids = []
        ancestor = area.parent
        while ancestor is not None and ancestor.pk != root.pk:
            ancestor_ids.insert(0, _new_country_materialized_tree_id(country, ancestor.pk))
            ancestor = ancestor.parent
        density = area.density if area.density is not None else _application_browser_metrics._density(area.pop_latest, area.area_km2)
        depth = 0 if selected_level > 0 else max(0, parent.level - root.level)
        rows.append({
            "tree_id": _new_country_materialized_tree_id(country, area.id),
            "tree_parent": parent_tree_id if selected_level <= 0 else "",
            "depth": depth,
            "tree_depth_class": f"new-country-tree-depth-{min(depth, 5)}",
            "has_children": bool(area.child_count),
            "subdivision_count": area.child_count,
            "level": area.level,
            "display_code": area.code,
            "full_display_code": entity_service.full_area_code(area),
            "new_url": _new_country_config_new_url(country) + "?" + urlencode({"parent_area_id": area.pk}),
            "ancestor_tree_ids": ancestor_ids,
            "edit_url": _new_country_config_edit_url(config_by_area[area.pk]) if area.pk in config_by_area else "",
            "modal_edit_url": _new_country_config_new_url(country) + "?" + urlencode({"edit_area_id": area.pk}),
            "delete_url": _new_country_config_new_url(country) + "?" + urlencode({"delete_area_id": area.pk}),
            "parent_display_code": area.parent.code if area.parent_id != root.pk else "",
            "entity_display_name": _queries_browser._area_display_name(area),
            "official_name": _queries_new_country_browser._new_country_official_name(area.entity_type or "", _queries_browser._area_display_name(area)),
            "entity_type": area.entity_type or "",
            "entity_node": area,
            "entity_area_text": _queries_groups._group_metric_text(area.area_km2),
            "entity_population_text": _queries_groups._group_metric_text(area.pop_latest),
            "entity_density_text": _queries_groups._group_metric_text(density),
        })
    return rows


def _new_country_config_tree_request_rows(
    country: DerivedCountry,
    configs: list[DerivedCountryConfig],
    *,
    parent_tree_id: str = "",
    selected_level: int | str | None = None,
) -> list[dict]:
    parent_tree_id = str(parent_tree_id or "").strip()
    try:
        level = int(selected_level or 0)
    except (TypeError, ValueError):
        level = 0

    materialized_root = _new_country_legacy_materialized_root(configs)
    if materialized_root is not None:
        if level > 0:
            available_levels = _queries_new_country_browser._new_country_config_available_levels_for_configs(configs)
            level, _types, _filter = _queries_browser._resolve_country_level_selection(level, available_levels)
        return _new_country_materialized_tree_rows(
            country, materialized_root, parent_tree_id=parent_tree_id, selected_level=level or 0,
        )

    if level > 0:
        available_levels = _queries_new_country_browser._new_country_config_available_levels_for_configs(configs)
        level, _selected_entity_types, _selected_filter = _queries_browser._resolve_country_level_selection(
            level,
            available_levels,
        )
    if level is not None and level > 0:
        rows = _new_country_config_rows(
            configs,
            include_assigned_subdivisions=True,
            target_levels={level},
        )
        return [
            dict(row, depth=0, tree_parent="")
            for row in rows
            if int(row.get("level") or 0) == level
        ]

    if parent_tree_id:
        target_slug = _new_country_config_node_target_slug(country.slug, parent_tree_id)
        assigned_slugs = None
        source_descendant_slugs = None
        if target_slug:
            direct_child_slugs = {
                config.slug
                for config in configs
                if _new_country_config_parent_slug(config) == target_slug
            }
            assigned_slugs = {target_slug, *direct_child_slugs}
            source_descendant_slugs = {target_slug}
        rows = _new_country_config_rows(
            configs,
            include_assigned_subdivisions=True,
            assigned_subdivision_config_slugs=assigned_slugs,
            source_descendant_config_slugs=source_descendant_slugs,
        )
        parent_depth = next(
            (int(row.get("depth") or 0) for row in rows if str(row.get("tree_id") or "") == parent_tree_id),
            0,
        )
        return [
            dict(row, depth=parent_depth + 1)
            for row in rows
            if str(row.get("tree_parent") or "") == parent_tree_id
        ]

    rows = _new_country_config_rows(configs, include_assigned_subdivisions=False)
    return [row for row in rows if not str(row.get("tree_parent") or "")]


def _new_country_config_tree_row_payload(row: dict) -> dict:
    return {
        "tree_id": str(row.get("tree_id") or ""),
        "tree_parent": str(row.get("tree_parent") or ""),
        "depth": int(row.get("depth") or 0),
        "tree_depth_class": str(row.get("tree_depth_class") or ""),
        "has_children": bool(row.get("has_children")),
        "level": int(row.get("level") or 0),
        "display_code": str(row.get("display_code") or ""),
        "full_display_code": str(row.get("full_display_code") or row.get("display_code") or ""),
        "new_url": str(row.get("new_url") or ""),
        "ancestor_tree_ids": list(row.get("ancestor_tree_ids") or []),
        "parent_display_code": str(row.get("parent_display_code") or ""),
        "entity_display_name": str(row.get("entity_display_name") or ""),
        "official_name": str(row.get("official_name") or ""),
        "entity_type": str(row.get("entity_type") or ""),
        "source_level": str(row.get("source_level") or ""),
        "entity_area_text": str(row.get("entity_area_text") or "-"),
        "entity_population_text": str(row.get("entity_population_text") or "-"),
        "entity_density_text": str(row.get("entity_density_text") or "-"),
        "capital": _queries_new_country_browser._new_country_config_row_capital_names(row),
        "subdivision_count": int(row.get("subdivision_count") or 0),
        "is_config_row": bool(row.get("is_config_row")),
        "is_assigned_subdivision": bool(row.get("is_assigned_subdivision")),
        "edit_url": str(row.get("edit_url") or ""),
        "modal_edit_url": str(row.get("modal_edit_url") or row.get("edit_url") or ""),
        "clone_url": str(row.get("clone_url") or ""),
        "delete_url": str(row.get("delete_url") or ""),
    }


def _new_country_config_rows(
    configs: list[DerivedCountryConfig],
    *,
    include_assigned_subdivisions: bool = False,
    assigned_subdivision_config_slugs: set[str] | None = None,
    source_descendant_config_slugs: set[str] | None = None,
    target_levels: set[int] | None = None,
) -> list[dict]:
    rows = []
    normalized_target_levels = (
        {
            int(level)
            for level in target_levels
            if isinstance(level, int) or str(level or "").strip().isdigit()
        }
        if target_levels is not None
        else None
    )
    assigned_subdivision_config_slugs = (
        {query_parameters.normalize_country_key(slug) for slug in assigned_subdivision_config_slugs}
        if assigned_subdivision_config_slugs is not None
        else None
    )
    source_descendant_config_slugs = (
        {query_parameters.normalize_country_key(slug) for slug in source_descendant_config_slugs}
        if source_descendant_config_slugs is not None
        else None
    )
    for config in sorted(configs, key=lambda item: (item.country.name, item.name, item.slug)):
        data = _new_country_config_toml(config)
        entity = _forms_new_countries._new_country_config_first_entity(data)
        built_code = _new_country_config_built_code(config)
        source_code = _new_country_config_source_code(config)
        level = _new_country_config_level(config)
        parent_slug = _new_country_config_parent_slug(config)
        tree_id = _new_country_config_tree_id(config)
        parent_tree_id = _new_country_config_tree_parent_id(config, parent_slug)
        entity_type = str(entity.get("entity_type") or "").strip()
        selected_source_ids = _forms_new_countries._new_country_config_selected_source_ids(data, entity)
        selected_derived_subdivisions = _forms_new_countries._new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code=source_code,
        )
        assigned_subdivision_child_count = len(selected_source_ids) + len(selected_derived_subdivisions)
        has_assigned_subdivision_children = bool(assigned_subdivision_child_count)
        root = NuevoAdminArea.objects.filter(country_code=built_code, parent__isnull=True).order_by("name").first()
        entity_node = _new_country_config_materialized_entity(config, data=data)
        entity_display_name = _queries_browser._area_display_name(entity_node) if entity_node else config.name
        area_value, population_value, density_value = _new_country_config_entity_metric_values(
            data=data,
            entity_node=entity_node,
        )
        built_count = NuevoAdminArea.objects.filter(country_code=built_code).count()
        visual_assets = _new_country_visual_assets_for_root(root, source_code) if root else {}
        detail_api_url = (
            reverse("ciudades_del_mundo:api_new_country_detail", kwargs={"country_id": root.id})
            if root
            else ""
        )
        rows.append(
            {
                "config": config,
                "country": config.country,
                "built_code": built_code,
                "source_code": source_code,
                "root": root,
                "entity_node": entity_node,
                "built_count": built_count,
                "display_code": _forms_new_countries._new_country_config_expected_full_code(config, data=data) or derived_code_piece(config.slug),
                "entity_display_name": entity_display_name,
                "official_name": _queries_new_country_browser._new_country_official_name(entity_type, entity_display_name),
                "entity_type": entity_type,
                "level": level,
                "parent_slug": parent_slug,
                "entity_area_value": area_value,
                "entity_population_value": population_value,
                "entity_density_value": density_value,
                "entity_area_text": _queries_groups._group_metric_text(area_value),
                "entity_population_text": _queries_groups._group_metric_text(population_value),
                "entity_density_text": _queries_groups._group_metric_text(density_value),
                "parent_display_code": "",
                "source_level": "",
                "tree_id": tree_id,
                "tree_parent": parent_tree_id,
                "row_kind": "config",
                "sort_kind": 0,
                "sort_index": 0,
                "assigned_subdivision_child_count": assigned_subdivision_child_count,
                "has_assigned_subdivision_children": has_assigned_subdivision_children,
                "is_config_row": True,
                "is_assigned_subdivision": False,
                "display_name": _queries_browser._area_display_name(root) if root else config.country.name,
                "population_text": _queries_groups._group_metric_text(root.pop_latest if root else population_value),
                "area_text": _queries_groups._group_metric_text(root.area_km2 if root else area_value),
                "flag_url": _queries_groups._stored_visual_asset_image_url(visual_assets.get("flag")),
                "detail_api_url": detail_api_url,
                "edit_url": _new_country_config_edit_url(config),
                "new_url": _new_country_config_new_url(config.country) + "?" + urlencode({"parent_config_slug": config.slug}),
                "full_display_code": _new_country_config_full_display_code(config),
                "delete_url": _new_country_config_delete_url(config),
                "clone_url": _new_country_config_clone_url(config),
                "build_url": _new_country_config_task_url(config, "build"),
                "export_excel_url": _new_country_config_task_url(config, "export-excel"),
                "view_url": (
                    reverse("ciudades_del_mundo:nuevo_area_detail", kwargs={"country_id": root.id})
                    if root
                    else ""
                ),
                "search_text": " ".join(
                    [
                        config.country.name,
                        config.country.slug,
                        config.name,
                        config.slug,
                        built_code,
                    ]
                ),
            }
        )
        if include_assigned_subdivisions and (
            assigned_subdivision_config_slugs is None
            or query_parameters.normalize_country_key(config.slug) in assigned_subdivision_config_slugs
        ):
            rows.extend(
                _new_country_config_assigned_subdivision_rows(
                    config,
                    data=data,
                    entity=entity,
                    source_country_code=source_code,
                    parent_tree_id=tree_id,
                    parent_level=level,
                    include_derived_source_rows=(
                        source_descendant_config_slugs is None
                        or query_parameters.normalize_country_key(config.slug) in source_descendant_config_slugs
                    ),
                    target_levels=normalized_target_levels,
                    materialized_entity=entity_node,
                )
            )
    _new_country_config_apply_source_child_counts(rows)
    rows_by_id = {row.get("tree_id"): row for row in rows}

    def full_row_code(row: dict, seen=None) -> str:
        if row.get("full_display_code"):
            return row["full_display_code"]
        seen = set(seen or ())
        key = row.get("tree_id")
        if key in seen:
            return str(row.get("display_code") or "")
        seen.add(key)
        parent = rows_by_id.get(row.get("tree_parent"))
        prefix = full_row_code(parent, seen) if parent else _new_country_config_full_display_code(row["config"])
        code = derived_code_piece(row.get("display_code") or "")
        row["full_display_code"] = code if code.startswith(prefix + "-") else f"{prefix}-{code}"
        return row["full_display_code"]

    for row in rows:
        full_row_code(row)
    return _new_country_config_tree_rows(rows)


def _new_country_config_tree_id(config: DerivedCountryConfig) -> str:
    return f"config:{config.country_id}:{config.slug}"


def _new_country_config_tree_parent_id(config: DerivedCountryConfig, parent_slug: str) -> str:
    parent_slug = query_parameters.normalize_country_key(parent_slug)
    return f"config:{config.country_id}:{parent_slug}" if parent_slug else ""


def _new_country_config_node_target_slug(country_slug: str, tree_id: str) -> str:
    prefix = f"config:{country_slug}:"
    value = str(tree_id or "")
    if not value.startswith(prefix):
        return ""
    return query_parameters.normalize_country_key(value[len(prefix):].split(":", 1)[0])


def _new_country_config_assigned_subdivision_rows(
    config: DerivedCountryConfig,
    *,
    data: dict,
    entity: dict,
    source_country_code: str,
    parent_tree_id: str,
    parent_level: int,
    include_derived_source_rows: bool = True,
    target_levels: set[int] | None = None,
    materialized_entity: NuevoAdminArea | None = None,
) -> list[dict]:
    rows: list[dict] = []
    display_level = max(1, int(parent_level or 1) + 1)
    if target_levels is not None:
        clean_target_levels: set[int] = set()
        for raw_level in target_levels:
            try:
                clean_level = int(raw_level or 0)
            except (TypeError, ValueError):
                continue
            if clean_level > 0:
                clean_target_levels.add(clean_level)
        target_levels = clean_target_levels

    def wants_level(level: int) -> bool:
        return target_levels is None or int(level or 0) in target_levels

    materialized = materialized_entity
    if (materialized is not None
            and materialized.municipios_originales.filter(city_merge_status=AdminArea.CityMergeStatus.SOURCE).exists()
            and not _forms_new_countries._new_country_config_selected_derived_subdivisions(entity, fallback_country_code=source_country_code)):
        if not wants_level(display_level):
            return []
        return [
            _new_country_config_source_area_row(
                config, child, parent_tree_id=parent_tree_id, display_level=display_level,
                sort_index=index, tree_id=f"{parent_tree_id}:materialized:{child.id}",
            )
            for index, child in enumerate(materialized.children.order_by("name", "id"))
        ]

    source_ids = _forms_new_countries._new_country_config_selected_source_ids(data, entity)
    if source_ids and wants_level(display_level):
        areas = {
            area.id: area
            for area in area_selectors.group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        }
        for index, area_id in enumerate(source_ids):
            area = areas.get(area_id)
            if area is None:
                continue
            rows.append(
                _new_country_config_source_area_row(
                    config,
                    area,
                    parent_tree_id=parent_tree_id,
                    display_level=display_level,
                    sort_index=index,
                    tree_id=f"{parent_tree_id}:source:{area.id}",
                )
            )

    derived_records: list[DerivedSubdivision] = []
    derived_ref_by_key: dict[tuple[str, str], DerivedSubdivision] = {}
    derived_level_by_key: dict[tuple[str, str], int] = {}
    derived_selections = _forms_new_countries._new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    for selection in derived_selections:
        country_code = query_parameters.normalize_country_key(selection.get("country_code") or source_country_code)
        key = str(selection.get("key") or "").strip()
        if not country_code or not key:
            continue
        record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(country_code, key)
        if record is None:
            continue
        record_data = _queries_subdivisions._derived_subdivision_record_toml(record)
        record_key = _queries_subdivision_sources._derived_subdivision_source_key(record, record_data)
        lookup_key = (
            query_parameters.normalize_country_key(record_data.get("source_country_code") or record.source_country_code),
            record_key,
        )
        if lookup_key in derived_ref_by_key:
            continue
        derived_ref_by_key[lookup_key] = record
        try:
            derived_level = int(selection.get("level") or display_level)
        except (TypeError, ValueError):
            derived_level = display_level
        assigned_level = max(display_level, max(1, min(9, derived_level)))
        source_level = assigned_level + 1
        if not wants_level(assigned_level) and not (
            include_derived_source_rows and wants_level(source_level)
        ):
            continue
        derived_level_by_key[lookup_key] = assigned_level
        derived_records.append(record)
    if not derived_records:
        return rows

    option_by_key = {
        (query_parameters.normalize_country_key(option.get("country_code")), str(option.get("key") or "")): option
        for option in _new_country_derived_subdivision_options_from_records(derived_records)
    }
    record_data_by_slug = {record.slug: _queries_subdivisions._derived_subdivision_record_toml(record) for record in derived_records}
    code_counts = _new_country_derived_subdivision_code_counts(derived_records, data_by_slug=record_data_by_slug)
    materialized_rows = _queries_subdivisions._derived_subdivision_materialized_rows_for_records(
        derived_records,
        data_by_slug=record_data_by_slug,
        code_counts=code_counts,
    )
    materialized_source_areas = _new_country_config_materialized_source_area_map(materialized_rows.values())
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}
    for index, record in enumerate(derived_records, start=len(rows)):
        record_data = record_data_by_slug.get(record.slug) or {}
        record_country_code = query_parameters.normalize_country_key(record_data.get("source_country_code") or record.source_country_code)
        record_key = _queries_subdivision_sources._derived_subdivision_source_key(record, record_data)
        option = option_by_key.get((record_country_code, record_key)) or {}
        assigned_level = max(
            display_level,
            derived_level_by_key.get((record_country_code, record_key), display_level),
        )
        materialized_code = _queries_subdivisions._derived_subdivision_materialized_code(record, record_data, code_counts)
        record_area, record_population = _queries_subdivisions._derived_subdivision_metric_values(
            record,
            record_data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        record_density = _application_browser_metrics._density(record_population, record_area)
        derived_tree_id = f"{parent_tree_id}:derived:{record_country_code}:{record_key}"
        display_name = option.get("name") or _new_country_clean_created_subdivision_name(record.name)
        entity_type = option.get("type_text") or record.entity_type
        rows.append(
            {
                "config": config,
                "country": config.country,
                "source_code": record_country_code,
                "display_code": record_key,
                "entity_display_name": display_name,
                "official_name": _queries_new_country_browser._new_country_official_name(entity_type, display_name),
                "entity_type": entity_type,
                "level": assigned_level,
                "parent_slug": config.slug,
                "entity_area_value": record_area,
                "entity_population_value": record_population,
                "entity_density_value": record_density,
                "entity_area_text": option.get("area_text") or "-",
                "entity_population_text": option.get("population_text") or "-",
                "entity_density_text": option.get("density_text") or "-",
                "parent_display_code": "",
                "source_level": "",
                "tree_id": derived_tree_id,
                "tree_parent": parent_tree_id,
                "row_kind": "assigned-subdivision",
                "sort_kind": 1,
                "sort_index": index,
                "is_config_row": False,
                "is_assigned_subdivision": True,
                "root": None,
                "edit_url": "",
                "delete_url": "",
                "clone_url": "",
                "search_text": " ".join(
                    [
                        config.name,
                        config.slug,
                        record_country_code,
                        record_key,
                        record.name,
                        record.entity_type,
                        record.code,
                    ]
                ),
            }
        )
        if include_derived_source_rows and wants_level(assigned_level + 1):
            rows.extend(
                _new_country_config_derived_subdivision_source_rows(
                    config,
                    record,
                    parent_tree_id=derived_tree_id,
                    display_level=assigned_level + 1,
                    sort_index=index,
                    materialized_source_areas=materialized_source_areas.get(
                        str(
                            getattr(
                                materialized_rows.get(
                                    (record_country_code, materialized_code)
                                ),
                                "id",
                                "",
                            )
                        ),
                        [],
                    ),
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                )
            )
    return rows


def _new_country_config_source_area_row(
    config: DerivedCountryConfig,
    area: AdminArea,
    *,
    parent_tree_id: str,
    display_level: int,
    sort_index: int,
    tree_id: str,
    sort_kind: int = 1,
) -> dict:
    density_value = area.density if area.density is not None else _application_browser_metrics._density(area.pop_latest, area.area_km2)
    display_name = _new_country_clean_created_subdivision_name(
        _new_country_config_source_area_display_name(area)
    )
    entity_type = str(area.entity_type or "").strip()
    return {
        "config": config,
        "country": config.country,
        "source_code": query_parameters.normalize_country_key(area.country_code),
        "entity_node": area,
        "display_code": str(area.code or area.id),
        "entity_display_name": display_name,
        "official_name": _queries_new_country_browser._new_country_official_name(entity_type, display_name),
        "entity_type": entity_type,
        "level": display_level,
        "parent_slug": config.slug,
        "entity_area_value": area.area_km2,
        "entity_population_value": area.pop_latest,
        "entity_density_value": density_value,
        "entity_area_text": _queries_groups._group_metric_text(area.area_km2),
        "entity_population_text": _queries_groups._group_metric_text(area.pop_latest),
        "entity_density_text": _queries_groups._group_metric_text(density_value),
        "parent_display_code": "",
        "source_level": "",
        "tree_id": tree_id,
        "tree_parent": parent_tree_id,
        "row_kind": "assigned-subdivision",
        "sort_kind": sort_kind,
        "sort_index": sort_index,
        "is_config_row": False,
        "is_assigned_subdivision": True,
        "root": None,
        "edit_url": "",
        "delete_url": "",
        "clone_url": "",
        "search_text": " ".join(
            [
                config.name,
                config.slug,
                area.id,
                area.country_code,
                area.name,
                str(area.entity_type or ""),
            ]
        ),
    }


def _new_country_config_derived_subdivision_source_rows(
    config: DerivedCountryConfig,
    record: DerivedSubdivision,
    *,
    parent_tree_id: str,
    display_level: int,
    sort_index: int,
    materialized_source_areas: list[AdminArea],
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> list[dict]:
    areas = list(materialized_source_areas or [])
    if not areas:
        source_ids = _queries_subdivision_sources._derived_subdivision_source_ids_for_record(
            record,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
        if not source_ids:
            return []
        areas = list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        )
    else:
        areas = _new_country_legal_source_areas_for_display(areas)
    return [
        _new_country_config_source_area_row(
            config,
            area,
            parent_tree_id=parent_tree_id,
            display_level=display_level,
            sort_index=sort_index + index,
            tree_id=f"{parent_tree_id}:source:{area.id}",
            sort_kind=2,
        )
        for index, area in enumerate(areas)
    ]


def _new_country_legal_source_areas_for_display(areas: Iterable[AdminArea]) -> list[AdminArea]:
    original_areas = list(areas)
    source_ids = _new_country_legal_source_unit_ids_for_source_areas(original_areas)
    if not source_ids:
        return sorted(
            original_areas,
            key=lambda item: (
                str(getattr(item, "country_code", "")),
                int(getattr(item, "level", 0) or 0),
                str(getattr(item, "name", "")).casefold(),
                str(getattr(item, "id", "")),
            ),
        )
    try:
        return list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return original_areas


def _new_country_config_materialized_source_area_map(
    areas: Iterable[NuevoAdminArea],
) -> dict[str, list[AdminArea]]:
    area_ids = [str(area.id) for area in areas if getattr(area, "id", None)]
    if not area_ids:
        return {}
    try:
        through_rows = list(
            NuevoAdminArea.municipios_originales.through.objects
            .filter(nuevoadminarea_id__in=area_ids)
            .values_list("nuevoadminarea_id", "adminarea_id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    source_ids = {str(source_id) for _, source_id in through_rows if str(source_id or "").strip()}
    if not source_ids:
        return {}
    try:
        source_areas = {
            str(area.id): area
            for area in area_selectors.group_source_admin_areas()
            .filter(id__in=source_ids)
            .select_related("parent")
            .order_by("country_code", "level", "name", "id")
        }
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    by_area_id: dict[str, list[AdminArea]] = {}
    for area_id, source_id in through_rows:
        source_area = source_areas.get(str(source_id))
        if source_area is not None:
            by_area_id.setdefault(str(area_id), []).append(source_area)
    for rows in by_area_id.values():
        rows.sort(key=lambda item: (str(item.country_code), int(item.level or 0), str(item.name).casefold(), str(item.id)))
    return by_area_id


def _new_country_config_source_area_display_name(area: AdminArea) -> str:
    return _queries_browser._display_name(
        getattr(area, "name", ""),
        getattr(area, "name", ""),
        country_code=getattr(area, "country_code", ""),
    )


def _new_country_config_entity_metric_values(*, data: dict, entity_node: NuevoAdminArea | None):
    entity = _forms_new_countries._new_country_config_first_entity(data)
    stored_area, stored_population, stored_density = _new_country_config_stored_metric_values(entity)
    area_value = entity_node.area_km2 if entity_node and entity_node.area_km2 is not None else stored_area
    population_value = (
        entity_node.pop_latest
        if entity_node and entity_node.pop_latest is not None
        else stored_population
    )
    density_value = entity_node.density if entity_node and entity_node.density is not None else stored_density
    if density_value is None:
        density_value = _application_browser_metrics._density(population_value, area_value)
    return area_value, population_value, density_value


def _new_country_config_tree_rows(rows: list[dict]) -> list[dict]:
    row_by_key = {str(row.get("tree_id") or ""): row for row in rows if str(row.get("tree_id") or "")}
    children_by_key: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for row in rows:
        key = str(row.get("tree_id") or "")
        parent_key = str(row.get("tree_parent") or "")
        if parent_key and parent_key in row_by_key and parent_key != key:
            children_by_key.setdefault(parent_key, []).append(row)
        else:
            row["tree_parent"] = ""
            row["parent_slug"] = "" if row.get("is_config_row") else row.get("parent_slug", "")
            roots.append(row)

    ordered: list[dict] = []
    seen: set[str] = set()

    def sort_key(row: dict) -> tuple[int, int, int, str, str]:
        return (
            int(row.get("sort_kind") or 0),
            int(row.get("level") or 1),
            int(row.get("sort_index") or 0),
            str(row.get("entity_display_name") or getattr(row.get("config"), "name", "") or "").casefold(),
            str(row.get("tree_id") or ""),
        )

    def append_tree(row: dict, depth: int) -> None:
        key = str(row.get("tree_id") or "")
        if key in seen:
            return
        seen.add(key)
        children = sorted(children_by_key.get(key, []), key=sort_key)
        row["depth"] = depth
        row["tree_depth_class"] = f"new-country-tree-depth-{min(depth, 5)}"
        row["has_children"] = bool(children) or bool(row.get("has_assigned_subdivision_children"))
        if "subdivision_count" not in row:
            row["subdivision_count"] = len(children) or int(row.get("assigned_subdivision_child_count") or 0)
        ordered.append(row)
        for child in children:
            append_tree(child, depth + 1)

    for row in sorted(roots, key=sort_key):
        append_tree(row, 0)
    for row in sorted(rows, key=sort_key):
        append_tree(row, 0)
    return ordered


def _new_country_config_apply_source_child_counts(rows: list[dict]) -> None:
    source_ids = [
        str(row["entity_node"].id)
        for row in rows
        if isinstance(row.get("entity_node"), AdminArea)
    ]
    if not source_ids:
        return
    counts = {
        str(item["parent_id"]): int(item["total"] or 0)
        for item in area_selectors.visible_admin_areas()
        .filter(parent_id__in=source_ids)
        .values("parent_id")
        .annotate(total=Count("id"))
    }
    for row in rows:
        entity_node = row.get("entity_node")
        if isinstance(entity_node, AdminArea):
            row["source_child_count"] = counts.get(str(entity_node.id), 0)


def _new_country_config_source_code(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    return query_parameters.normalize_country_key(
        data.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )


def _new_country_config_built_code(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    return query_parameters.normalize_country_key(data.get("derived_country_code") or config.derived_country_code or config.slug)


def _new_country_config_toml(config: DerivedCountryConfig) -> dict:
    try:
        data = tomllib.loads(config.content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _new_country_config_node_detail_url(country_slug: str, tree_id: str) -> str:
    return reverse(
        "ciudades_del_mundo:api_new_country_config_node_detail",
        kwargs={"country_slug": country_slug, "tree_id": tree_id},
    )


def _new_country_source_code_for_built_code(built_code: str) -> str:
    built_code = query_parameters.normalize_country_key(built_code)
    if not built_code:
        return ""
    for config in DerivedCountryConfig.objects.select_related("country").all():
        if _new_country_config_built_code(config) == built_code:
            return _new_country_config_source_code(config)
    return ""


def _new_country_visual_assets_for_root(root: NuevoAdminArea, source_country_code: str = "") -> dict:
    candidates = [
        ("admin_area", str(root.id)),
        ("country", str(root.country_code)),
    ]
    source_country_code = query_parameters.normalize_country_key(source_country_code)
    if source_country_code:
        candidates.append(("country", source_country_code))
    for entity_type, entity_key in candidates:
        assets = _queries_assets._visual_assets_payload(entity_type, entity_key, include_fallbacks=False)
        if _queries_assets._visual_assets_have_display_image(assets):
            return assets
    return {}


def _normalize_new_country_code(value: str) -> str:
    try:
        return _forms_recipes._normalize_recipe_slug(value)
    except ValueError as exc:
        raise ValueError(
            _("El codigo debe empezar por una letra y usar mayusculas sin acentos, numeros o guion bajo, sin espacios.")
        ) from exc


def _new_country_entity_code_from_form(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", text):
        raise ValueError(
            _("El codigo debe empezar por una letra y usar mayusculas sin acentos, numeros, guion o guion bajo, sin espacios.")
        )
    return text.upper()


def _new_country_slug_from_entity_code(value: str) -> str:
    return _normalize_new_country_code(str(value or "").replace("-", "_"))


def _new_country_created_entity_queryset(
    country: DerivedCountry,
    search_term: str,
    *,
    current_config: DerivedCountryConfig | None = None,
):
    queryset = country.configs.order_by("name", "slug").only(
        "id",
        "country",
        "slug",
        "name",
        "source_country_code",
        "derived_country_code",
        "content",
    )
    excluded_slugs: set[str] = set()
    if current_config is not None:
        excluded_slugs.add(current_config.slug)
        excluded_slugs.update(_new_country_config_parent_chain(current_config))
    if excluded_slugs:
        queryset = queryset.exclude(slug__in=excluded_slugs)
    search_term = str(search_term or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(slug__icontains=search_term)
            | Q(name__icontains=search_term)
            | Q(source_country_code__icontains=search_term)
            | Q(derived_country_code__icontains=search_term)
            | Q(content__icontains=search_term)
        )
    return queryset


def _new_country_created_entity_options_from_records(records: list[DerivedCountryConfig]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for config in records:
        data = _new_country_config_toml(config)
        entity = _forms_new_countries._new_country_config_first_entity(data)
        entity_type = str(entity.get("entity_type") or "").strip()
        level = _new_country_config_level(config)
        materialized = _new_country_config_materialized_entity(config, data=data)
        area_value, population_value, density_value = _new_country_config_entity_metric_values(
            data=data,
            entity_node=materialized,
        )
        area_text = _queries_groups._group_metric_text(area_value)
        population_text = _queries_groups._group_metric_text(population_value)
        density_text = _queries_groups._group_metric_text(density_value)
        options.append(
            {
                "value": config.slug,
                "slug": config.slug,
                "name": str(entity.get("name") or config.name or config.slug).strip(),
                "type_text": entity_type or _("Sin tipo"),
                "level": level,
                "area_text": area_text,
                "population_text": population_text,
                "density_text": density_text,
                "search_text": " ".join(
                    [
                        config.slug,
                        config.name,
                        str(entity.get("name") or ""),
                        entity_type,
                        str(level),
                        area_text,
                        population_text,
                        density_text,
                    ]
                ),
            }
        )
    return options


def _new_country_config_stored_metric_values(entity: dict) -> tuple[object, object, object]:
    area_value = _new_country_entity_number(entity, "area_km2", "area")
    population_value = _new_country_entity_number(entity, "pop_latest", "population")
    density_value = _new_country_entity_number(entity, "density")
    return area_value, population_value, density_value


def _new_country_entity_number(entity: dict, *keys: str):
    if not isinstance(entity, dict):
        return None
    for key in keys:
        if key not in entity:
            continue
        number = _application_browser_metrics._number_or_none(entity.get(key))
        if number is not None:
            return number
    return None


def _new_country_config_preview_metric_values(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
) -> tuple[object, object]:
    cache_key = (str(config.country_id or ""), config.slug)
    seen = set(seen or set())
    if cache_key in seen:
        return None, None
    seen.add(cache_key)
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _forms_new_countries._new_country_config_first_entity(data)
    try:
        source_ids = _new_country_config_preview_source_ids(config, data=data)
    except (OperationalError, ProgrammingError, ValueError):
        source_ids = set()
    if source_ids and not entity.get("manual_template"):
        try:
            aggregate = area_selectors.group_source_admin_areas().filter(id__in=source_ids).aggregate(
                area=Sum("area_km2"),
                population=Sum("pop_latest"),
            )
        except (OperationalError, ProgrammingError, ValueError):
            return None, None
        return aggregate.get("area"), aggregate.get("population")

    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    area_value, population_value = _new_country_config_own_derived_subdivision_metric_values(config, data=data)
    parent_slug = query_parameters.normalize_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        child_area, child_population = _new_country_config_preview_metric_values(child, seen=seen)
        area_value = _queries_browser._add_optional_metric(area_value, child_area)
        population_value = _queries_browser._add_optional_metric(population_value, child_population)
    seen.remove(cache_key)
    if entity.get("manual_template"):
        if "manual_area" in entity:
            area_value = Decimal(str(entity["manual_area"]))
        population_value = entity.get("manual_population", population_value)
    return area_value, population_value


def _new_country_config_own_derived_subdivision_metric_values(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> tuple[object, object]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _forms_new_countries._new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = query_parameters.normalize_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    selections = _forms_new_countries._new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    if not selections:
        return None, None
    records = []
    seen_records = set()
    for item in selections:
        record = _queries_subdivision_sources._derived_subdivision_source_record_for_key(item["country_code"], item["key"])
        if record is None or record.pk in seen_records:
            continue
        seen_records.add(record.pk)
        records.append(record)
    if not records:
        return None, None
    data_by_slug = {record.slug: _queries_subdivisions._derived_subdivision_record_toml(record) for record in records}
    code_counts = _new_country_derived_subdivision_code_counts(records, data_by_slug=data_by_slug)
    materialized_rows = _queries_subdivisions._derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    area_value = None
    population_value = None
    for record in records:
        record_area, record_population = _queries_subdivisions._derived_subdivision_metric_values(
            record,
            data_by_slug.get(record.slug) or {},
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_value = _queries_browser._add_optional_metric(area_value, record_area)
        population_value = _queries_browser._add_optional_metric(population_value, record_population)
    return area_value, population_value


def _new_country_config_preview_source_ids(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
    source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
    group_entry_cache: dict[tuple[str, str], dict | None] | None = None,
    group_entries_cache: dict[str, list[dict]] | None = None,
    config_source_ids_cache: dict[tuple[str, str], set[str]] | None = None,
) -> set[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    cache_key = (str(config.country_id or ""), config.slug)
    config_source_ids_cache = config_source_ids_cache if config_source_ids_cache is not None else {}
    if cache_key in config_source_ids_cache:
        return set(config_source_ids_cache[cache_key])
    seen = set(seen or set())
    if cache_key in seen:
        return set()
    seen.add(cache_key)
    source_ids_cache = source_ids_cache if source_ids_cache is not None else {}
    group_entry_cache = group_entry_cache if group_entry_cache is not None else {}
    group_entries_cache = group_entries_cache if group_entries_cache is not None else {}

    entity = _forms_new_countries._new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    default_country_code = query_parameters.normalize_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    include_ids = _queries_subdivision_capitals._derived_subdivision_blocks_source_ids(
        entity.get("include"),
        default_country_code=default_country_code,
        seen=set(),
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    subtract_ids = _queries_subdivision_capitals._derived_subdivision_blocks_source_ids(
        entity.get("subtract"),
        default_country_code=default_country_code,
        seen=set(),
        source_ids_cache=source_ids_cache,
        group_entry_cache=group_entry_cache,
        group_entries_cache=group_entries_cache,
    )
    result = include_ids - subtract_ids
    if not result:
        result = _new_country_config_derived_subdivision_source_ids(
            entity,
            default_country_code=default_country_code,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    if not result:
        result = _new_country_config_selection_source_ids(
            selection,
            default_country_code=default_country_code,
        )

    parent_slug = query_parameters.normalize_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        result.update(
            _new_country_config_preview_source_ids(
                child,
                seen=seen,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
                config_source_ids_cache=config_source_ids_cache,
            )
        )
    seen.remove(cache_key)
    config_source_ids_cache[cache_key] = set(result)
    return result


def _new_country_config_derived_subdivision_source_ids(
    entity: dict,
    *,
    default_country_code: str,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> set[str]:
    result: set[str] = set()
    for item in _forms_new_countries._new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=default_country_code,
    ):
        result.update(
            _queries_subdivision_sources._derived_subdivision_source_ids_for_key(
                item["country_code"],
                item["key"],
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
        )
    return result


def _new_country_config_selection_source_ids(selection: dict, *, default_country_code: str) -> set[str]:
    if not isinstance(selection, dict):
        return set()
    include_ids: list[str] = []
    subtract_ids: list[str] = []
    items = selection.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("value") or "").strip()
            if not item_id:
                continue
            operation = str(item.get("operation") or "add").strip().lower()
            if operation == "subtract":
                subtract_ids.append(item_id)
            else:
                include_ids.append(item_id)
    else:
        include_ids = _forms_new_countries._new_country_config_text_values(selection.get("include_ids"))
        subtract_ids = _forms_new_countries._new_country_config_text_values(selection.get("subtract_ids"))
    return _new_country_config_admin_area_ids_to_source_ids(
        include_ids,
        default_country_code=default_country_code,
    ) - _new_country_config_admin_area_ids_to_source_ids(
        subtract_ids,
        default_country_code=default_country_code,
    )


def _new_country_config_admin_area_ids_to_source_ids(
    area_ids: list[str],
    *,
    default_country_code: str,
) -> set[str]:
    ids = {str(value).strip() for value in area_ids if str(value or "").strip()}
    if not ids:
        return set()
    try:
        areas = list(
            area_selectors.group_source_admin_areas()
            .filter(id__in=ids)
            .only("id", "country_code", "level", "parent_id", "city_merge_status")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return set()
    areas_by_country: dict[str, list[AdminArea]] = {}
    default_country_code = query_parameters.normalize_country_key(default_country_code)
    for area in areas:
        country_code = query_parameters.normalize_country_key(area.country_code or default_country_code)
        if country_code:
            areas_by_country.setdefault(country_code, []).append(area)
    return _new_country_legal_source_unit_ids_for_source_areas_by_country(areas_by_country)


def _new_country_legal_source_unit_ids_for_source_areas(areas: Iterable[AdminArea]) -> set[str]:
    areas_by_country: dict[str, list[AdminArea]] = {}
    for area in areas:
        country_code = query_parameters.normalize_country_key(getattr(area, "country_code", ""))
        if country_code:
            areas_by_country.setdefault(country_code, []).append(area)
    return _new_country_legal_source_unit_ids_for_source_areas_by_country(areas_by_country)


def _new_country_legal_source_unit_ids_for_source_areas_by_country(
    areas_by_country: dict[str, list[AdminArea]],
) -> set[str]:
    result: set[str] = set()
    for country_code, country_areas in areas_by_country.items():
        result.update(_queries_subdivision_capitals._derived_subdivision_areas_to_capital_source_ids(country_areas, country_code=country_code))
    return result


def _new_country_config_materialized_entity(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> NuevoAdminArea | None:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _forms_new_countries._new_country_config_first_entity(data)
    built_code = _new_country_config_built_code(config)
    code_candidates = _new_country_config_materialized_code_candidates(config, data=data)
    if built_code and code_candidates:
        match = (
            NuevoAdminArea.objects.filter(country_code=built_code, code__in=code_candidates)
            .exclude(parent__isnull=True)
            .order_by("level", "id")
            .first()
        )
        if match:
            return match
    name = str(entity.get("name") or config.name or "").strip()
    if not built_code or not name:
        return None
    return (
        NuevoAdminArea.objects.filter(country_code=built_code, name__iexact=name)
        .exclude(parent__isnull=True)
        .order_by("level", "id")
        .first()
    )


def _new_country_config_materialized_code_candidates(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
) -> list[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    entity = _forms_new_countries._new_country_config_first_entity(data)
    candidates = []

    def add(value) -> None:
        code = derived_code_piece(value)
        if code and code not in candidates:
            candidates.append(code)

    local_full_code = _forms_new_countries._new_country_config_expected_full_code(config, data=data)
    add(local_full_code)
    built_code = _new_country_config_built_code(config)
    root_code = derived_country_root_code(built_code, data.get("root_code") or data.get("code") or built_code)
    if root_code and local_full_code and not local_full_code.startswith(f"{root_code}-"):
        add(f"{root_code}-{local_full_code}")
    add(entity.get("code"))
    add(config.slug)
    return candidates


def _new_country_detail_url(country: DerivedCountry) -> str:
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_detail",
            kwargs={"source_country_code": source_country_code, "country_slug": country.slug},
        )
    return reverse("ciudades_del_mundo:new_country_detail_legacy", kwargs={"country_slug": country.slug})


def _new_country_source_kwargs(country: DerivedCountry) -> dict[str, str]:
    return {
        "source_country_code": query_parameters.normalize_country_key(country.source_country_code),
        "country_slug": country.slug,
    }


def _new_country_config_new_url(country: DerivedCountry) -> str:
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse("ciudades_del_mundo:new_country_config_new", kwargs=_new_country_source_kwargs(country))
    return reverse("ciudades_del_mundo:new_country_config_new_legacy", kwargs={"country_slug": country.slug})


def _new_country_detail_export_excel_url(country: DerivedCountry) -> str:
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse("ciudades_del_mundo:new_country_detail_export_excel", kwargs=_new_country_source_kwargs(country))
    return reverse("ciudades_del_mundo:new_country_detail_export_excel_legacy", kwargs={"country_slug": country.slug})


def _new_country_config_edit_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_edit",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_edit_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_delete_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_delete",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_delete_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_clone_url(config: DerivedCountryConfig) -> str:
    country = config.country
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:new_country_config_clone",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug},
        )
    return reverse(
        "ciudades_del_mundo:new_country_config_clone_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug},
    )


def _new_country_config_task_url(config: DerivedCountryConfig, action: str) -> str:
    country = config.country
    source_country_code = query_parameters.normalize_country_key(country.source_country_code)
    if source_country_code:
        return reverse(
            "ciudades_del_mundo:start_new_country_config_task",
            kwargs={**_new_country_source_kwargs(country), "config_slug": config.slug, "action": action},
        )
    return reverse(
        "ciudades_del_mundo:start_new_country_config_task_legacy",
        kwargs={"country_slug": country.slug, "config_slug": config.slug, "action": action},
    )


def _new_country_config_parent_options(
    country: DerivedCountry,
    current_config: DerivedCountryConfig | None = None,
) -> list[dict[str, object]]:
    excluded = {current_config.slug} if current_config else set()
    if current_config:
        excluded.update(_new_country_config_descendant_slugs(country, current_config.slug))
    options: list[dict[str, object]] = [
        {"value": "", "label": _("%(name)s (Nivel 0)") % {"name": country.name}, "level": 0}
    ]
    for config in country.configs.order_by("name", "slug"):
        if config.slug in excluded:
            continue
        level = _new_country_config_level(config)
        options.append(
            {
                "value": config.slug,
                "label": _("%(name)s (Nivel %(level)s)") % {"name": config.name, "level": level},
                "level": level,
            }
        )
    return options


def _new_country_parent_level(country: DerivedCountry, parent_config_slug: str) -> int:
    parent_config_slug = query_parameters.normalize_country_key(parent_config_slug)
    if not parent_config_slug:
        return 0
    try:
        return _new_country_config_level(country.configs.get(slug=parent_config_slug))
    except DerivedCountryConfig.DoesNotExist:
        return 0


def _new_country_config_level(config: DerivedCountryConfig) -> int:
    data = _new_country_config_toml(config)
    for entity in data.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        try:
            level = int(entity.get("level"))
        except (TypeError, ValueError):
            level = 0
        if level > 0:
            return level
    parent_slug = _new_country_config_parent_slug(config)
    if parent_slug:
        return _new_country_parent_level(config.country, parent_slug) + 1
    return 1


def _new_country_config_parent_slug(config: DerivedCountryConfig) -> str:
    data = _new_country_config_toml(config)
    for entity in data.get("entities") or []:
        if isinstance(entity, dict):
            parent_slug = query_parameters.normalize_country_key(entity.get("parent_config_slug") or entity.get("parent"))
            if parent_slug:
                return parent_slug
    return query_parameters.normalize_country_key(data.get("parent_config_slug") or data.get("parent"))


def _new_country_config_parent_chain(config: DerivedCountryConfig) -> set[str]:
    chain: set[str] = set()
    parent_slug = _new_country_config_parent_slug(config)
    while parent_slug and parent_slug not in chain:
        chain.add(parent_slug)
        parent = config.country.configs.filter(slug=parent_slug).first()
        if parent is None:
            break
        parent_slug = _new_country_config_parent_slug(parent)
    return chain


def _new_country_config_descendant_slugs(country: DerivedCountry, config_slug: str) -> set[str]:
    descendants: set[str] = set()
    changed = True
    while changed:
        changed = False
        for config in country.configs.all():
            if config.slug in descendants:
                continue
            parent_slug = _new_country_config_parent_slug(config)
            if parent_slug == config_slug or parent_slug in descendants:
                descendants.add(config.slug)
                changed = True
    return descendants


def _new_country_selected_derived_subdivisions_from_selection_json(
    selection_json: str,
    *,
    fallback_country_code: str,
) -> list[dict[str, str]]:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(selection, dict):
        return []
    return _new_country_normalized_derived_subdivision_selections(
        selection.get("selected_derived_subdivisions")
        or selection.get("selected_subdivision_keys")
        or selection.get("derived_subdivisions")
        or [],
        fallback_country_code=fallback_country_code,
    )


def _new_country_selected_subdivision_keys_from_selection_json(
    selection_json: str,
    *,
    fallback_country_code: str = "",
) -> list[str]:
    return [
        _new_country_derived_subdivision_selection_value(item["country_code"], item["key"])
        for item in _new_country_selected_derived_subdivisions_from_selection_json(
            selection_json,
            fallback_country_code=fallback_country_code,
        )
    ]


def _new_country_derived_subdivision_selection_value(country_code: str, subdivision_key: str) -> str:
    return _queries_subdivision_sources._derived_subdivision_record_source_item_id(country_code, subdivision_key)


def _new_country_normalized_derived_subdivision_selections(
    values,
    *,
    fallback_country_code: str,
) -> list[dict[str, str | int]]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result: list[dict[str, str | int]] = []
    seen: dict[tuple[str, str], dict[str, str | int]] = {}
    fallback_country_code = query_parameters.normalize_country_key(fallback_country_code)
    for value in values:
        country_code = ""
        subdivision_key = ""
        selection_level: int | None = None
        if isinstance(value, dict):
            encoded_id = str(value.get("id") or value.get("value") or "").strip()
            encoded_country, encoded_key = _new_country_split_derived_subdivision_value(encoded_id)
            country_code = query_parameters.normalize_country_key(
                value.get("country_code") or value.get("source_country_code") or encoded_country or fallback_country_code
            )
            subdivision_key = str(
                value.get("key")
                or value.get("derived_subdivision_key")
                or value.get("internal_name")
                or encoded_key
                or value.get("code")
                or ""
            ).strip()
            try:
                raw_level = int(value.get("level") or 0)
            except (TypeError, ValueError):
                raw_level = 0
            if raw_level > 0:
                selection_level = max(1, min(9, raw_level))
        else:
            encoded_country, encoded_key = _new_country_split_derived_subdivision_value(str(value or "").strip())
            country_code = query_parameters.normalize_country_key(encoded_country or fallback_country_code)
            subdivision_key = encoded_key or str(value or "").strip()
        try:
            subdivision_key = _queries_groups._group_internal_name(subdivision_key)
        except ValueError:
            continue
        marker = (country_code, subdivision_key.casefold())
        if not country_code:
            continue
        if marker in seen:
            if selection_level is not None and "level" not in seen[marker]:
                seen[marker]["level"] = selection_level
            continue
        entry: dict[str, str | int] = {"country_code": country_code, "key": subdivision_key}
        if selection_level is not None:
            entry["level"] = selection_level
        seen[marker] = entry
        result.append(entry)
    return result


def _new_country_split_derived_subdivision_value(value: str) -> tuple[str, str]:
    value = str(value or "").strip()
    if not value.startswith("derived-subdivision::"):
        return "", value
    parts = value.split("::", 2)
    if len(parts) < 3:
        return "", ""
    return query_parameters.normalize_country_key(parts[1]), parts[2]


def _new_country_normalized_subdivision_keys(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result = []
    seen = set()
    for value in values:
        key = str(value or "").strip()
        marker = key.casefold()
        if not key or marker in seen:
            continue
        seen.add(marker)
        result.append(key)
    return result


def _new_country_capital_source_ids(
    country_code: str,
    selected_subdivision_keys: list[str] | None = None,
    *,
    country: DerivedCountry | None = None,
    selected_source_ids=None,
    selected_derived_subdivisions=None,
    selected_child_config_slugs=None,
    require_selection: bool = False,
) -> set[str] | None:
    selections = _new_country_normalized_derived_subdivision_selections(
        selected_derived_subdivisions if selected_derived_subdivisions is not None else selected_subdivision_keys,
        fallback_country_code=country_code,
    )
    child_config_slugs = _forms_new_countries._new_country_normalized_config_slugs(selected_child_config_slugs)
    selected_source_ids = _new_country_normalized_subdivision_keys(selected_source_ids)
    has_explicit_scope = bool(selections or child_config_slugs or selected_source_ids)
    if not has_explicit_scope:
        return set() if require_selection else None
    source_ids: set[str] = set()
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    group_entries_cache: dict[str, list[dict]] = {}
    source_ids.update(
        _new_country_config_admin_area_ids_to_source_ids(
            selected_source_ids,
            default_country_code=country_code,
        )
    )
    for item in selections:
        item_country_code = item["country_code"]
        item_key = item["key"]
        source_ids.update(
            _queries_subdivision_sources._derived_subdivision_source_ids_for_key(
                item_country_code,
                item_key,
                source_ids_cache=source_ids_cache,
                group_entry_cache=group_entry_cache,
                group_entries_cache=group_entries_cache,
            )
        )
        source_ids.update(_queries_subdivision_capitals._derived_subdivision_declared_capital_ids_for_key(item_country_code, item_key))
    source_ids.update(
        _new_country_child_config_source_ids(
            country,
            child_config_slugs,
            source_ids_cache=source_ids_cache,
            group_entry_cache=group_entry_cache,
            group_entries_cache=group_entries_cache,
        )
    )
    return source_ids


def _new_country_config_capital_options_payload(
    country_code: str,
    *,
    country: DerivedCountry | None = None,
    selected_ids=None,
    selected_source_ids=None,
    selected_subdivision_keys=None,
    selected_derived_subdivisions=None,
    selected_child_config_slugs=None,
    search_term: str = "",
    use_subdivisions: bool = False,
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    source_ids = _new_country_capital_source_ids(
        country_code,
        _new_country_normalized_subdivision_keys(selected_subdivision_keys),
        country=country,
        selected_source_ids=selected_source_ids,
        selected_derived_subdivisions=selected_derived_subdivisions,
        selected_child_config_slugs=selected_child_config_slugs,
        require_selection=bool(use_subdivisions),
    )
    return _queries_subdivision_capitals._derived_subdivision_capital_options(
        country_code,
        source_ids=source_ids,
        selected_ids=selected_ids,
        search_term=search_term,
    )


def _new_country_child_config_source_ids(
    country: DerivedCountry | None,
    selected_child_config_slugs,
    *,
    source_ids_cache: dict[tuple[str, str], set[str]],
    group_entry_cache: dict[tuple[str, str], dict | None],
    group_entries_cache: dict[str, list[dict]],
) -> set[str]:
    if country is None:
        return set()
    slugs = _forms_new_countries._new_country_normalized_config_slugs(selected_child_config_slugs)
    if not slugs:
        return set()
    try:
        configs = {
            config.slug: config
            for config in country.configs.filter(slug__in=slugs).select_related("country")
        }
    except (OperationalError, ProgrammingError, ValueError):
        return set()
    result: set[str] = set()
    config_source_ids_cache: dict[tuple[str, str], set[str]] = {}
    for slug in slugs:
        config = configs.get(slug)
        if config is None:
            continue
        try:
            result.update(
                _new_country_config_preview_source_ids(
                    config,
                    source_ids_cache=source_ids_cache,
                    group_entry_cache=group_entry_cache,
                    group_entries_cache=group_entries_cache,
                    config_source_ids_cache=config_source_ids_cache,
                )
            )
            result.update(_new_country_config_declared_capital_source_ids(config))
        except (OperationalError, ProgrammingError, ValueError):
            continue
    return result


def _new_country_config_declared_capital_source_ids(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[tuple[str, str]] | None = None,
) -> set[str]:
    data = data if isinstance(data, dict) else _new_country_config_toml(config)
    cache_key = (str(config.country_id or ""), config.slug)
    seen = set(seen or set())
    if cache_key in seen:
        return set()
    seen.add(cache_key)

    entity = _forms_new_countries._new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = query_parameters.normalize_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    result = _queries_subdivision_capitals._declared_capital_ids_from_data(source_country_code, entity)
    for item in _forms_new_countries._new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    ):
        result.update(
            _queries_subdivision_capitals._derived_subdivision_declared_capital_ids_for_key(
                item["country_code"],
                item["key"],
            )
        )

    parent_slug = query_parameters.normalize_country_key(config.slug)
    for child in config.country.configs.order_by("slug"):
        if child.slug == config.slug or _new_country_config_parent_slug(child) != parent_slug:
            continue
        result.update(_new_country_config_declared_capital_source_ids(child, seen=seen))
    seen.remove(cache_key)
    return result


def _new_country_derived_subdivision_options(source_country_code: str) -> list[dict[str, str]]:
    source_country_code = query_parameters.normalize_country_key(source_country_code)
    try:
        records = list(_new_country_derived_subdivision_queryset(""))
    except (OperationalError, ProgrammingError):
        return []
    return _new_country_derived_subdivision_options_from_records(records)


def _new_country_created_subdivision_country_options() -> list[dict[str, str]]:
    try:
        country_codes = list(
            DerivedSubdivision.objects.exclude(source_country_code="")
            .values_list("source_country_code", flat=True)
            .distinct()
            .order_by("source_country_code")
        )
    except (OperationalError, ProgrammingError):
        return []
    options = []
    seen = set()
    for raw_code in country_codes:
        code = query_parameters.normalize_country_key(raw_code)
        if not code or code in seen:
            continue
        seen.add(code)
        options.append({"value": code, "label": _queries_browser._source_country_display_label(code)})
    return options


def _new_country_derived_subdivision_queryset(search_term: str, *, country_code: str = ""):
    queryset = DerivedSubdivision.objects.order_by("source_country_code", "name", "slug").only(
        "slug",
        "internal_name",
        "name",
        "code",
        "entity_type",
        "source_country_code",
        "content",
    )
    country_code = query_parameters.normalize_country_key(country_code)
    if country_code:
        queryset = queryset.filter(source_country_code__iexact=country_code)
    search_term = str(search_term or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(slug__icontains=search_term)
            | Q(internal_name__icontains=search_term)
            | Q(name__icontains=search_term)
            | Q(code__icontains=search_term)
            | Q(entity_type__icontains=search_term)
            | Q(source_country_code__icontains=search_term)
            | Q(content__icontains=search_term)
        )
    return queryset


def _new_country_compact_subdivision_options(records):
    return [
        {"name": record.name, "country_code": record.source_country_code,
         "key": _queries_subdivision_sources._derived_subdivision_source_key(record, _queries_subdivisions._derived_subdivision_record_toml(record))}
        for record in records
    ]


def _new_country_derived_subdivision_options_from_records(
    records: list[DerivedSubdivision],
) -> list[dict[str, str]]:
    data_by_slug = {record.slug: _queries_subdivisions._derived_subdivision_record_toml(record) for record in records}
    code_counts = _new_country_derived_subdivision_code_counts(records, data_by_slug=data_by_slug)
    materialized_rows = _queries_subdivisions._derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    options = []
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        area_value, population_value = _queries_subdivisions._derived_subdivision_metric_values(
            record,
            data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_text = _queries_groups._group_metric_text(area_value)
        population_text = _queries_groups._group_metric_text(population_value)
        density_text = _queries_groups._group_metric_text(_application_browser_metrics._density(population_value, area_value))
        try:
            level = _forms_subdivisions._derived_subdivision_level(data.get("level") or 1)
        except ValueError:
            level = 1
        entity_type = str(data.get("entity_type") or record.entity_type or "").strip()
        type_text = entity_type or _("Sin tipo")
        record_country_code = query_parameters.normalize_country_key(record.source_country_code)
        record_key = _queries_subdivision_sources._derived_subdivision_source_key(record, data)
        country_label = _queries_browser._source_country_display_label(record_country_code)
        clean_name = _new_country_clean_created_subdivision_name(record.name)
        selection_name = str(record.name or "").strip() or clean_name or str(record.internal_name or record.slug).strip()
        content_search_text = _new_country_derived_subdivision_content_search_text(data)
        options.append(
            {
                "value": _new_country_derived_subdivision_selection_value(record_country_code, record_key),
                "country_code": record_country_code,
                "country_label": country_label,
                "key": record_key,
                "label": _new_country_derived_subdivision_label(record),
                "name": clean_name,
                "selection_name": selection_name,
                "code": str(record.code or record.internal_name or record.slug),
                "entity_type": entity_type,
                "level": level,
                "type_text": type_text,
                "area_text": area_text,
                "population_text": population_text,
                "density_text": density_text,
                "search_text": " ".join(
                    [
                        record.slug,
                        record.internal_name,
                        record.name,
                        clean_name,
                        selection_name,
                        record.entity_type,
                        record.code,
                        record.source_country_code,
                        country_label,
                        entity_type,
                        str(level),
                        type_text,
                        area_text,
                        population_text,
                        density_text,
                        content_search_text,
                    ]
                ),
            }
        )
    return options


def _new_country_source_area_options_from_records(records: list[AdminArea]) -> list[dict[str, str]]:
    options = []
    country_labels: dict[str, str] = {}
    for area in records:
        country_code = query_parameters.normalize_country_key(area.country_code)
        if country_code not in country_labels:
            country_labels[country_code] = _queries_browser._source_country_display_label(country_code)
        name = _queries_browser._area_display_name(area)
        entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=country_code)
        parent = getattr(area, "parent", None)
        parent_label = _queries_browser._area_display_name(parent) if parent else ""
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append(
            {
                "value": str(area.id),
                "id": str(area.id),
                "country_code": country_code,
                "country_label": country_labels[country_code],
                "name": name,
                "label": label,
                "code": str(area.code or ""),
                "level": int(area.level or 0),
                "type_text": entity_type,
                "entity_type": entity_type,
                "parent_id": str(area.parent_id or ""),
                "parent_label": parent_label,
            }
        )
    return options


def _new_country_derived_subdivision_content_search_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    values: list[str] = []

    def add(value) -> None:
        if value in (None, ""):
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
            return
        text = str(value or "").strip()
        if text:
            values.append(text)

    for key in ("internal_name", "name", "code", "entity_type", "parent_code", "capitals"):
        add(data.get(key))
    for section_name in ("include", "subtract"):
        blocks = data.get(section_name) if isinstance(data.get(section_name), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("country_code", "names", "ids", "codes", "groups", "derived_subdivisions", "expressions"):
                add(block.get(key))
    return " ".join(values)


def _new_country_derived_subdivision_code_counts(
    records: list[DerivedSubdivision],
    *,
    data_by_slug: dict[str, dict],
) -> Counter:
    country_codes = {
        query_parameters.normalize_country_key((data_by_slug.get(record.slug) or {}).get("source_country_code") or record.source_country_code)
        for record in records
    }
    country_codes = {country_code for country_code in country_codes if country_code}
    if not country_codes:
        return Counter()
    try:
        count_records = list(
            DerivedSubdivision.objects.filter(source_country_code__in=country_codes).only(
                "slug",
                "internal_name",
                "code",
                "source_country_code",
                "content",
            )
        )
    except (OperationalError, ProgrammingError):
        count_records = records
    count_data_by_slug = {
        record.slug: data_by_slug.get(record.slug) or _queries_subdivisions._derived_subdivision_record_toml(record)
        for record in count_records
    }
    return Counter(
        _queries_subdivisions._derived_subdivision_requested_code_count_key(record, count_data_by_slug.get(record.slug) or {})
        for record in count_records
    )


def _new_country_derived_subdivision_label(row: DerivedSubdivision) -> str:
    code = str(row.code or row.internal_name or row.slug).strip()
    suffix = f" - {code}" if code else ""
    name = _new_country_clean_created_subdivision_name(row.name) or str(row.internal_name or row.slug).strip()
    return f"{name}{suffix}"


def _new_country_clean_created_subdivision_name(value: str | None) -> str:
    original = str(value or "").strip()
    if not original:
        return ""
    cleaned = re.sub(r"\s*\([^)]*\)", "", original).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned or original


def _default_derived_country_toml(
    *,
    country: DerivedCountry,
    slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
) -> str:
    source_country_code = str(source_country_code or "").strip().lower()
    return "\n".join(
        [
            "schema_version = 1",
            'kind = "derived_country_config"',
            f"country = {_queries_browser._toml_string(country.slug)}",
            f"slug = {_queries_browser._toml_string(slug)}",
            f"name = {_queries_browser._toml_string(name)}",
            f"source_country_code = {_queries_browser._toml_string(source_country_code)}",
            f"derived_country_code = {_queries_browser._toml_string(derived_country_code)}",
            "groups = []",
            "",
            "[selection]",
            f"source_country_code = {_queries_browser._toml_string(source_country_code)}",
            "include_ids = []",
            "subtract_ids = []",
            "include_codes = []",
            "subtract_codes = []",
            "group_slugs = []",
            "",
        ]
    )


def get_derived_country_config(country: DerivedCountry, slug: str) -> DerivedCountryConfig:
    try:
        return country.configs.get(slug=_forms_recipes._normalize_recipe_slug(slug))
    except DerivedCountryConfig.DoesNotExist as exc:
        raise DataNotFoundError(_("No existe la configuracion '%(slug)s'.") % {"slug": slug}) from exc


def get_derived_country(slug: str) -> DerivedCountry:
    try:
        return DerivedCountry.objects.get(slug=_forms_recipes._normalize_recipe_slug(slug))
    except DerivedCountry.DoesNotExist as exc:
        raise DataNotFoundError(_("No existe el pais nuevo '%(slug)s'.") % {"slug": slug}) from exc


def new_country_materialized_scope(config: DerivedCountryConfig) -> dict:
    country_code = _new_country_config_built_code(config)
    code_candidates = _new_country_config_materialized_code_candidates(config)
    materialized = _new_country_config_materialized_entity(config) if country_code and code_candidates else None
    return {"country_code": country_code, "code_candidates": code_candidates,
            "materialized_id": materialized.id if materialized is not None else None}
