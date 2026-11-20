"""Select source areas and build dashboard/country-browser payloads."""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.wikidata_countries import wikidata_country_id as _wikidata_country_id
from ciudades_del_mundo.application.group_references import replace_group_references

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

import ast
import json
import re
import tomllib
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError, connection
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, DerivedSubdivision, NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services.dynamic_translations import (
    dynamic_area_name, dynamic_country_name, dynamic_country_names, dynamic_entity_type_label,
)
from ciudades_del_mundo.web.spain_translations import normalize_language_code, spain_entity_type, spain_name
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.forms import new_countries as _forms_new_countries
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from ciudades_del_mundo.web.queries import assets as _queries_assets
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web.queries import nuevo_areas as _queries_nuevo_areas
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web import view_support as _view_support


def _country_summary_payload(*, detail_route: str, include_visual_assets: bool = False) -> dict:
    countries = _admin_root_population_rows(
        detail_route=detail_route,
        include_visual_assets=include_visual_assets,
    )
    population_total = sum(row["population"] for row in countries)
    area_total = sum(row["area_km2"] or 0 for row in countries)
    return {
        "type": "donut",
        "total": population_total,
        "countries": countries,
        "items": [
            {
                "key": row["code"],
                "label": row["label"],
                "value": row["population"],
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
                        "key": row["code"],
                        "label": row["label"],
                        "value": row["population"],
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
                        "key": row["code"],
                        "label": row["label"],
                        "value": row["area_km2"] or 0,
                        "detail_url": row["detail_url"],
                    }
                    for row in sorted(countries, key=lambda item: item["area_km2"] or 0, reverse=True)
                    if row["area_km2"]
                ],
            },
        },
    }


def _derived_summary_payload() -> dict:
    derived = list(
        NuevoAdminArea.objects.values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("country_code")
    )
    _apply_country_labels(derived, _queries_nuevo_areas._nuevo_country_label_map())
    return {
        "type": "bar",
        "items": _bar_rows(derived, "display_label", "population", limit=10),
    }


def _division_row_code_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _pending_derived_subdivision_rows_by_country(
    country_codes: set[str] | None = None,
    *,
    exclude_hrefs: set[str] | None = None,
) -> dict[str, list[dict]]:
    normalized_codes = {
        query_parameters.normalize_country_key(code)
        for code in (country_codes or set())
        if query_parameters.normalize_country_key(code)
    }
    if not normalized_codes:
        return {}
    exclude_hrefs = {str(href) for href in (exclude_hrefs or set()) if str(href)}
    by_country: dict[str, list[dict]] = {}
    try:
        records = list(
            DerivedSubdivision.objects.filter(source_country_code__in=normalized_codes).order_by(
                "source_country_code",
                "slug",
            )
        )
    except (OperationalError, ProgrammingError):
        return {}
    for record in records:
        source_code = query_parameters.normalize_country_key(record.source_country_code)
        if not source_code:
            continue
        data = _queries_subdivisions._derived_subdivision_record_toml(record)
        entry_slug = _queries_subdivisions._derived_subdivision_entry_slug(record)
        href = reverse(
            "ciudades_del_mundo:derived_subdivision_edit",
            kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
        )
        if href in exclude_hrefs:
            continue
        metadata = _queries_subdivisions._derived_subdivision_edit_metadata(record, data, href=href)
        try:
            level = int(metadata.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        type_text = str(metadata.get("type_text") or "-").strip() or "-"
        name = str(metadata.get("name") or record.name or record.internal_name or record.slug).strip() or "-"
        type_level_text = type_text
        area_value, population_value = _queries_subdivisions._derived_subdivision_preview_metric_values(source_code, record.content)
        area_text = _queries_groups._group_metric_text(area_value)
        population_text = _queries_groups._group_metric_text(population_value)
        search_text = " ".join(
            str(value or "")
            for value in (
                record.slug,
                record.internal_name,
                record.name,
                record.entity_type,
                record.code,
                name,
                type_text,
                level,
                area_text,
                population_text,
            )
        )
        by_country.setdefault(source_code, []).append(
            {
                "id": record.slug,
                "name": name,
                "level": level,
                "type_text": type_text,
                "type_level_text": type_level_text,
                "area_text": area_text,
                "population_text": population_text,
                "search_text": search_text,
                "href": href,
                "clone_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_clone",
                    kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
                ),
                "delete_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_delete",
                    kwargs={"country_code": source_code, "subdivision_slug": entry_slug},
                ),
            }
        )
    return by_country


def _legacy_division_spec_country_codes_from_node(node) -> set[str]:
    codes: set[str] = set()
    if isinstance(node, ast.List):
        for item in node.elts:
            codes.update(_legacy_division_spec_country_codes_from_node(item))
        return codes
    if not isinstance(node, ast.Dict):
        return codes
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "spec":
            codes.update(_legacy_spec_value_country_codes(value))
        elif isinstance(key, ast.Constant) and key.value == "source":
            maybe_code = _literal_string_node(value)
            if maybe_code:
                codes.add(maybe_code)
    return codes


def _legacy_spec_value_country_codes(node) -> set[str]:
    codes: set[str] = set()
    if not isinstance(node, ast.Dict):
        return codes
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            codes.add(key.value)
            continue
        if isinstance(value, ast.Dict):
            for inner_key in value.keys:
                if isinstance(inner_key, ast.Constant) and isinstance(inner_key.value, str):
                    codes.add(inner_key.value)
    return codes


def _literal_string_node(node) -> str:
    return str(node.value or "").strip() if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _country_group_record_slug(country_code: str, group_slug: str) -> str:
    return _forms_recipes._normalize_recipe_slug(f"{query_parameters.normalize_country_key(country_code)}_{_queries_groups._group_entry_slug(group_slug)}")


def derived_subdivision_clone_values(record: DerivedSubdivision) -> dict:
    data = _forms_subdivisions._derived_subdivision_toml_data(record.content)
    source_country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    clone_internal_name, clone_code, clone_slug = _queries_subdivisions._derived_subdivision_clone_internal_and_code(record, data)
    clone_name = _forms_new_countries._clone_display_name(record.name)
    content = _forms_new_countries._set_toml_string_field_in_content(record.content, field="internal_name", value=clone_internal_name)
    content = _forms_new_countries._set_toml_string_field_in_content(content, field="source_country_code", value=source_country_code)
    content = _forms_new_countries._set_toml_string_field_in_content(content, field="name", value=clone_name)
    content = _forms_new_countries._set_toml_string_field_in_content(content, field="code", value=clone_code)
    _validate_plain_toml(content, expected_kind="derived_subdivision")
    return dict(
        slug=clone_slug,
        internal_name=clone_internal_name,
        name=clone_name,
        source_country_code=source_country_code,
        entity_type=str(data.get("entity_type") or record.entity_type or "").strip(),
        code=clone_code,
        description=record.description,
        content=content.rstrip() + "\n",
    )


def _validate_derived_subdivision_toml(content: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("TOML invalido."))
    kind = str(data.get("kind") or "").strip()
    if kind and kind != "derived_subdivision":
        raise ValueError(_("El TOML debe usar kind = 'derived_subdivision'."))
    for key in ("include", "subtract"):
        blocks = data.get(key)
        if blocks is not None and not isinstance(blocks, list):
            raise ValueError(_("%(key)s debe ser una lista de bloques TOML.") % {"key": key})
        for block in blocks or []:
            if not isinstance(block, dict):
                raise ValueError(_("%(key)s debe contener bloques TOML validos.") % {"key": key})
            try:
                level = int(block.get("level"))
            except (TypeError, ValueError):
                raise ValueError(_("Cada bloque debe tener un nivel numerico.")) from None
            if level < 0 or level > 9:
                raise ValueError(_("El nivel debe estar entre 0 y 9."))
            for list_key in ("names", "groups", "derived_subdivisions", "ids", "codes"):
                value = block.get(list_key)
                if value is not None and not isinstance(value, list):
                    raise ValueError(_("%(key)s.%(field)s debe ser una lista.") % {"key": key, "field": list_key})
    for key in ("level", "source_population_year", "population_year", "year"):
        if data.get(key) not in (None, ""):
            try:
                int(data.get(key))
            except (TypeError, ValueError):
                raise ValueError(_("%(key)s debe ser numerico.") % {"key": key}) from None
    capitals = data.get("capitals")
    if capitals is not None and not isinstance(capitals, list):
        raise ValueError(_("capitals debe ser una lista."))
    capital_groups = data.get("capital_groups")
    if capital_groups is not None and not isinstance(capital_groups, list):
        raise ValueError(_("capital_groups debe ser una lista de bloques TOML."))
    for block in capital_groups or []:
        if not isinstance(block, dict):
            raise ValueError(_("capital_groups debe contener bloques TOML validos."))
        if block.get("level") not in (None, ""):
            try:
                int(block.get("level"))
            except (TypeError, ValueError):
                raise ValueError(_("capital_groups.level debe ser numerico.")) from None
        for list_key in ("names",):
            value = block.get(list_key)
            if value is not None and not isinstance(value, list):
                raise ValueError(_("capital_groups.%(field)s debe ser una lista.") % {"field": list_key})
    return data


def _default_derived_subdivision_toml(
    *,
    country_code: str,
    internal_name: str,
    name: str = "",
    code: str = "",
    parent_code: str = "",
    entity_type: str = "Provincia",
    level: int = 1,
) -> str:
    internal_name = _queries_groups._group_internal_name(internal_name)
    country_code = query_parameters.normalize_country_key(country_code)
    parent_code = parent_code or _forms_subdivisions._derived_subdivision_root_code(country_code)
    name = str(name or _queries_groups._group_entry_display_name(internal_name)).strip()
    return "\n".join(
        [
            "schema_version = 1",
            'kind = "derived_subdivision"',
            f"internal_name = {_toml_string(internal_name)}",
            f"source_country_code = {_toml_string(country_code)}",
            f"name = {_toml_string(name)}",
            f"code = {_toml_string(code)}",
            f"parent_code = {_toml_string(parent_code)}",
            f"entity_type = {_toml_string(entity_type)}",
            f"level = {int(level or 1)}",
            'generic_name = ""',
            "capitals = []",
            "",
            "# Capital compuesta opcional: puede usar un grupo o nombres directos.",
            "# [[capital_groups]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 3",
            '# group = ""',
            "# names = []",
            '# capital_name = ""',
            "",
            "# Incluye subdivisiones completas o grupos reutilizables.",
            "# [[include]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 2",
            "# names = []",
            "# groups = []",
            "",
            "# Resta solo elementos de niveles inferiores o grupos de esos niveles.",
            "# [[subtract]]",
            f"# country_code = {_toml_string(country_code)}",
            "# level = 3",
            "# names = []",
            "# groups = []",
            "",
        ]
    )


def _legacy_python_source_from_content(content: str) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return ""
    legacy = data.get("legacy") if isinstance(data, dict) else None
    return str(legacy.get("python_source") or "") if isinstance(legacy, dict) else ""


def _top_level_group_names_from_data(data: dict, internal_name: str) -> list[str]:
    keys = [str(internal_name or "").strip()]
    keys.extend(
        key
        for key, value in data.items()
        if key not in _view_support.GROUP_TOML_METADATA_KEYS and isinstance(value, list) and key not in keys
    )
    for key in keys:
        value = data.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            continue
        names = []
        seen: set[str] = set()
        for item in value:
            name = str(item or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
    return []


def _top_level_group_assignment_names(data: dict) -> list[str]:
    if not isinstance(data, dict):
        return []
    return [
        str(key)
        for key, value in data.items()
        if key not in _view_support.GROUP_TOML_METADATA_KEYS and isinstance(value, list) and all(isinstance(item, str) for item in value)
    ]


def _legacy_group_assignments(content: str) -> list[dict]:
    """Return top-level Python list assignments; dict assignments are subdivisions."""
    source = _legacy_python_source_from_content(content)
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    entries = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        names = []
        valid_list = True
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.append(item.value)
            else:
                valid_list = False
                break
        if not valid_list:
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            internal_name = target.id
            try:
                slug = _queries_groups._group_entry_slug(internal_name)
            except ValueError:
                continue
            entries.append(
                {
                    "slug": slug,
                    "internal_name": internal_name,
                    "name": _queries_groups._group_entry_display_name(internal_name),
                    "blocks": [{"country_code": "", "names": names}],
                    "record": None,
                    "record_slug": "",
                    "is_legacy": True,
                }
            )
    return entries


def _stored_group_entry_from_record(group: SubdivisionGroup, country_code: str) -> dict | None:
    try:
        data = tomllib.loads(group.content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    assignment_names = _top_level_group_assignment_names(data)
    internal_name = str(data.get("internal_name") or (assignment_names[0] if assignment_names else "")).strip()
    if not internal_name:
        source_country = query_parameters.normalize_country_key(group.source_country_code or country_code)
        if source_country and group.slug.startswith(f"{source_country}_"):
            internal_name = group.slug[len(source_country) + 1 :]
        else:
            internal_name = group.slug
    try:
        internal_name = _queries_groups._group_internal_name(internal_name)
        entry_slug = _queries_groups._group_entry_slug(str(data.get("entry_slug") or internal_name))
    except ValueError:
        return None
    blocks = []
    country_groups = selection.get("country_groups") if isinstance(selection, dict) else None
    if isinstance(country_groups, list):
        for item in country_groups:
            if not isinstance(item, dict):
                continue
            block_country = query_parameters.normalize_country_key(item.get("country_code") or country_code)
            names = [str(name) for name in (item.get("include_names") or item.get("names") or [])]
            sections = _queries_groups._group_sections_from_payload(item.get("sections") or [])
            section_names = {
                selected["name"]
                for section in sections
                for selected in section.get("selected", [])
                if selected.get("name")
            }
            blocks.append(
                {
                    "country_code": block_country,
                    "names": [name for name in names if name not in section_names],
                    "sections": sections,
                }
            )
    root_country_groups = data.get("country_groups") if isinstance(data.get("country_groups"), list) else None
    if root_country_groups:
        for item in root_country_groups:
            if not isinstance(item, dict):
                continue
            block_country = query_parameters.normalize_country_key(item.get("country_code") or country_code)
            names = [str(name) for name in (item.get("include_names") or item.get("names") or [])]
            sections = _queries_groups._group_sections_from_payload(item.get("sections") or [])
            section_names = {
                selected["name"]
                for section in sections
                for selected in section.get("selected", [])
                if selected.get("name")
            }
            blocks.append(
                {
                    "country_code": block_country,
                    "names": [name for name in names if name not in section_names],
                    "sections": sections,
                }
            )
    if not blocks:
        names = [str(name) for name in (selection.get("include_names") or [])] if isinstance(selection, dict) else []
        if not names:
            names = _top_level_group_names_from_data(data, internal_name)
        blocks.append({"country_code": query_parameters.normalize_country_key(group.source_country_code or country_code), "names": names})
    return {
        "slug": entry_slug,
        "internal_name": internal_name,
        "name": str(group.name or data.get("name") or internal_name),
        "blocks": blocks,
        "record": group,
        "record_slug": group.slug,
        "is_legacy": False,
    }


def _render_group_entry_toml(
    *,
    record_slug: str,
    entry_slug: str,
    internal_name: str,
    name: str,
    source_country_code: str,
    blocks: list[dict],
) -> str:
    clean_blocks = _queries_groups._group_blocks_with_required_country(blocks, fallback_country_code=source_country_code)

    compatibility_names = _queries_groups._group_selected_names_from_blocks(clean_blocks)
    lines = [
        f"source_country_code = {_toml_string(source_country_code)}",
        "",
        f"{internal_name} = {_forms_scraping._toml_array(compatibility_names)}",
        "",
    ]
    for block in clean_blocks:
        block_names = _queries_groups._group_block_selected_names(block)
        lines.extend(
            [
                "[[country_groups]]",
                f"country_code = {_toml_string(block['country_code'])}",
                f"include_names = {_forms_scraping._toml_array(block_names)}",
                "",
            ]
        )
        for section in block.get("sections") or []:
            selected = section.get("selected") or []
            lines.extend(
                [
                    "[[country_groups.sections]]",
                    f"level = {int(section.get('level') or 0)}",
                    f"area_id = {_toml_string(section.get('area_id') or '')}",
                    f"selected_ids = {_forms_scraping._toml_array([item.get('id') or '' for item in selected])}",
                    f"selected_names = {_forms_scraping._toml_array([item.get('name') or '' for item in selected])}",
                    f"selected_operations = {_forms_scraping._toml_array([item.get('operation') or 'add' for item in selected])}",
                    "",
                ]
            )
    return "\n".join(lines)


def _stats_chart_payloads() -> dict[str, dict]:
    country_populations = [
        {"display_label": row["label"], "population": row["population"]}
        for row in _admin_root_population_rows()
    ]
    countries = list(
        area_selectors.visible_admin_areas().values("country_code")
        .annotate(total=Count("id"), population=Sum("pop_latest"))
        .order_by("-population")
    )
    _apply_country_labels(countries, _admin_country_label_map())
    admin_levels = list(
        area_selectors.visible_admin_areas().values("level").annotate(total=Count("id")).order_by("level")
    )
    nuevo_levels = list(
        NuevoAdminArea.objects.values("level").annotate(total=Count("id")).order_by("level")
    )
    merge_statuses = list(
        area_selectors.visible_admin_areas().values("city_merge_status")
        .annotate(total=Count("id"))
        .order_by("city_merge_status")
    )
    return {
        "country_population": {
            "type": "bar",
            "items": _bar_rows(country_populations, "display_label", "population", limit=15),
        },
        "country_area": {
            "type": "bar",
            "items": _bar_rows(countries, "display_label", "total", limit=15),
        },
        "admin_levels": {
            "type": "bar",
            "items": _bar_rows(admin_levels, "level", "total", limit=8, prefix="L"),
        },
        "nuevo_levels": {
            "type": "bar",
            "items": _bar_rows(nuevo_levels, "level", "total", limit=8, prefix="L"),
        },
        "merge_status": {
            "type": "bar",
            "items": _merge_status_rows(merge_statuses),
        },
    }


def _country_browser_page(rows, page: int | str | None, page_size: int, *, paginate: bool = True):
    if not paginate:
        rows = list(rows)
        return Paginator(rows, max(1, len(rows))).get_page(1)
    paginator = Paginator(rows, max(1, min(int(page_size or _view_support.COUNTRY_BROWSER_DEFAULT_PAGE_SIZE), _view_support.COUNTRY_BROWSER_MAX_PAGE_SIZE)))
    return paginator.get_page(page)


def _pagination_payload(page_obj) -> dict:
    return {
        "page": page_obj.number,
        "page_size": page_obj.paginator.per_page,
        "total": page_obj.paginator.count,
        "num_pages": page_obj.paginator.num_pages,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
    }


def _admin_sort_choices():
    return [
        ("country_level_name", _("Pais, nivel y nombre")),
        ("name", _("Nombre")),
        ("population_desc", _("Poblacion descendente")),
        ("population_asc", _("Poblacion ascendente")),
        ("area_desc", _("Area descendente")),
        ("area_asc", _("Area ascendente")),
        ("updated_desc", _("Actualizacion reciente")),
    ]


def _area_search_query(area) -> str:
    parts = [area.name]
    parent = getattr(area, "parent", None)
    if parent:
        parts.append(parent.name)
    country_code = getattr(area, "country_code", None)
    if country_code:
        parts.append(country_code)
    return ", ".join(str(part) for part in parts if part)


def _area_display_name(area) -> str:
    dynamic = dynamic_area_name(area, get_language())
    if dynamic:
        return dynamic
    if getattr(area, "level", None) == 0:
        country_dynamic = dynamic_country_name(getattr(area, "country_code", ""), get_language())
        if country_dynamic:
            return country_dynamic
    return _display_name(
        getattr(area, "name", ""),
        getattr(area, "name", ""),
        country_code=getattr(area, "country_code", ""),
    )


def _area_capital_display_names(area, language_code: str | None = None) -> list[str]:
    capitals = getattr(area, "capitals", None)
    if not capitals:
        return []

    overrides = getattr(area, "capital_names_by_language", None) or {}
    language = (language_code or "").split("-", 1)[0]
    language_overrides = overrides.get(language, {}) if isinstance(overrides, dict) else {}
    names = []
    seen = set()
    for capital in capitals.all():
        name = language_overrides.get(capital.id) or _area_display_name(capital)
        marker = str(name or "").casefold()
        if marker and marker not in seen:
            seen.add(marker)
            names.append(name)
    return names


def _area_related_places(area, language_code: str | None = None) -> list[dict[str, str]]:
    places: list[dict[str, str]] = []

    def add(kind: str, name: str | None, query: str | None = None) -> None:
        name = (name or "").strip()
        query = (query or name).strip()
        if not name or not query:
            return
        key = (kind, query.lower())
        if any((place["kind"], place["query"].lower()) == key for place in places):
            return
        places.append({"kind": kind, "name": name, "query": query})

    country_code = getattr(area, "country_code", "")
    country_root = _area_country_root(area)
    if country_root:
        add(_("Pais"), _area_display_name(country_root), country_root.name)
    elif country_code:
        add(_("Pais"), str(country_code), str(country_code))

    parent = getattr(area, "parent", None)
    if parent:
        add(_("Region padre"), _area_display_name(parent), _area_search_query(parent))

    for capital_name in _area_capital_display_names(area, language_code):
        add(_("Capital registrada"), capital_name, f"{capital_name}, {_area_display_name(area)}")

    most_populated = getattr(area, "most_populate_city", None)
    if most_populated:
        add(_("Ciudad mayor registrada"), _area_display_name(most_populated), f"{most_populated.name}, {area.name}")

    return places


def _area_country_root(area):
    if getattr(area, "level", None) == 0:
        return area
    model = area.__class__
    country_code = getattr(area, "country_code", None)
    if not country_code:
        return None
    if not hasattr(model, "objects"):
        return None
    if model is NuevoAdminArea:
        return (
            NuevoAdminArea.objects.filter(country_code=country_code, parent__isnull=True)
            .order_by("name")
            .first()
        )
    if model is AdminArea:
        return _canonical_country_area(country_code)
    return model.objects.filter(country_code=country_code, level=0).order_by("name").first()


def _canonical_country_area(country_code: str) -> AdminArea | None:
    roots = area_selectors.visible_admin_areas().filter(country_code=country_code, level=0, parent__isnull=True)

    # Prefer the explicit country row over other level-0 rows.  Some countries
    # (France is the common case) scrape regions at level 0 from another page.
    # If we only accepted a single root, the browser treated the actual country
    # as another child row and rendered "France > France".
    preferred = roots.filter(code=country_code).order_by("name").first()
    if preferred:
        return preferred

    infosection = roots.filter(annotations__icontains="CityPopulation section: infosection").order_by("name").first()
    if infosection:
        return infosection

    wikidata_id = _wikidata_country_id(country_code)
    if wikidata_id:
        by_wikidata = roots.filter(data_wd=wikidata_id).order_by("name").first()
        if by_wikidata:
            return by_wikidata

    if roots.count() == 1:
        return roots.first()
    return None


def _country_top_level_areas(country_code: str, root: AdminArea | None = None):
    if root:
        children = area_selectors.visible_admin_areas().filter(parent=root)
        first_child_level = children.values_list("level", flat=True).order_by("level").first()
        if first_child_level is not None:
            return children.filter(level=first_child_level).order_by("name")
        next_level = (
            area_selectors.visible_admin_areas().filter(country_code=country_code)
            .exclude(id=root.id)
            .values_list("level", flat=True)
            .order_by("level")
            .first()
        )
        if next_level is not None:
            return area_selectors.visible_admin_areas().filter(country_code=country_code, level=next_level).exclude(id=root.id).order_by("name")
        return AdminArea.objects.none()
    return area_selectors.visible_admin_areas().filter(country_code=country_code, level=0, parent__isnull=True).order_by("name")


def _country_total(country_code: str, field: str, root: AdminArea | None):
    if root:
        return getattr(root, field)
    return _country_top_level_areas(country_code, root).aggregate(total=Sum(field))["total"]


def _admin_country_summary_records(country_code: str | None = None) -> list[dict]:
    records = []
    areas = area_selectors.visible_admin_areas()
    if country_code is not None:
        areas = areas.filter(country_code=country_code)
    roots_by_country: dict[str, list[AdminArea]] = {}
    for root in areas.filter(level=0, parent__isnull=True).only(
        "id", "country_code", "code", "name", "annotations", "data_wd", "pop_latest", "area_km2",
    ).order_by("country_code", "name"):
        roots_by_country.setdefault(root.country_code, []).append(root)
    # Rootless countries have no population in this summary. Reading their
    # subdivisions just to enumerate country codes adds an unbounded table scan.
    codes = list(roots_by_country)
    country_names = dynamic_country_names(codes, normalize_language_code(get_language()))
    for country_code in codes:
        candidates = roots_by_country.get(country_code, [])
        root = _canonical_country_from_candidates(country_code, candidates)
        population = root.pop_latest if root else sum(area.pop_latest or 0 for area in candidates)
        if not population:
            continue
        values = [area.area_km2 for area in candidates if area.area_km2 is not None]
        area = root.area_km2 if root else (sum(values) if values else None)
        source_name = root.name if root else ""
        records.append(
            {
                "code": country_code,
                "label": _display_name(
                    source_name, country_code, country_code=country_code,
                    dynamic_name=country_names.get(country_code.lower(), ""),
                ),
                "population": int(population or 0),
                "area_km2": _application_browser_metrics._number_or_none(area),
                "root_id": root.id if root else "",
                "synthetic": root is None,
            }
        )
    return sorted(records, key=lambda row: row["population"], reverse=True)


def _canonical_country_from_candidates(country_code: str, candidates: list[AdminArea]) -> AdminArea | None:
    preferred = next((area for area in candidates if area.code == country_code), None)
    if preferred:
        return preferred
    infosection = next(
        (area for area in candidates if "citypopulation section: infosection" in (area.annotations or "").lower()),
        None,
    )
    if infosection:
        return infosection
    wikidata_id = _wikidata_country_id(country_code)
    if wikidata_id:
        by_wikidata = next((area for area in candidates if area.data_wd == wikidata_id), None)
        if by_wikidata:
            return by_wikidata
    return candidates[0] if len(candidates) == 1 else None


def _admin_root_population_rows(
    *,
    detail_route: str = "ciudades_del_mundo:dashboard_country_detail",
    include_visual_assets: bool = False,
) -> list[dict]:
    rows = []
    records = _admin_country_summary_records()
    assets_by_country = (
        _queries_assets._stored_visual_assets_payloads("country", [row["code"] for row in records])
        if include_visual_assets else {}
    )
    for row in records:
        item = {
            "code": row["code"],
            "label": row["label"],
            "population": row["population"],
            "area_km2": row["area_km2"],
            "wikidata_id": _wikidata_country_id(row["code"]),
            "detail_url": reverse(
                detail_route,
                kwargs={"country_code": row["code"]},
            ),
        }
        if include_visual_assets:
            visual_assets = assets_by_country.get(row["code"], {})
            item["visual_assets"] = visual_assets
            item["flag_asset"] = visual_assets.get("flag", {})
            item["seal_asset"] = visual_assets.get("seal", {})
            item["coat_asset"] = visual_assets.get("coat", {}) or item["seal_asset"]
        rows.append(item)
    return rows


def _country_detail_payload(
    country_code: str,
    selected_level: int | str | None = None,
    *,
    page: int | str | None = None,
    page_size: int = _view_support.COUNTRY_BROWSER_DEFAULT_PAGE_SIZE,
    include_children: bool = True,
) -> dict:
    root = _canonical_country_area(country_code)
    country_record = next(
        iter(_admin_country_summary_records(country_code)),
        {
            "code": country_code,
            "label": _display_name("", country_code, country_code=country_code),
            "population": 0,
            "area_km2": None,
        },
    )
    available_levels = _country_available_levels(country_code, root)
    selected_level, selected_entity_types, selected_filter = _resolve_country_level_selection(
        selected_level,
        available_levels,
    )

    population_total = country_record["population"]
    area_total = country_record["area_km2"] or 0
    row_queryset = _country_level_rows_queryset(
        country_code,
        root,
        selected_level,
        selected_entity_types,
    )
    page_obj = _country_browser_page(
        row_queryset, page, page_size,
        paginate=bool(available_levels) and selected_level != available_levels[0]["value"],
    )
    first_order = list(page_obj.object_list)
    child_counts = {area.id: _admin_area_browser_child_count(area) for area in first_order}
    rows = _country_table_rows_from_records(first_order, country_code, population_total, area_total)
    subdivision_count = area_selectors.visible_admin_areas().filter(country_code=country_code).count() - (1 if root else 0)
    visual_assets = _queries_assets._visual_assets_payload("country", country_code, include_fallbacks=False)

    return {
        "country": {
            "id": root.id if root else "",
            "code": country_code,
            "name": country_record["label"],
            "official_name": country_record["label"],
            "entity_type": _entity_type_label(root.entity_type, country_code=country_code) if root else "",
            "level": root.level if root else None,
            "parent": "",
            "population": population_total,
            "area_km2": area_total,
            "density": _application_browser_metrics._density(population_total, area_total),
            "capital": _capital_names_for_area(root),
            "subdivision_count": max(0, subdivision_count),
            "type_summary": _country_type_summary(country_code, root),
            "wikidata_query": root.name if root else country_record["label"],
            "wikidata_id": _wikidata_country_id(country_code),
            "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "admin", "area_id": root.id}) if root else "",
            "visual_assets": visual_assets,
            "flag_asset": visual_assets.get("flag", {}),
            "seal_asset": visual_assets.get("seal", {}),
            "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
        },
        "levels": available_levels,
        "selected_level": selected_level,
        "selected_filter": selected_filter,
        "table": {
            "rows": rows,
            "pagination": _pagination_payload(page_obj),
        },
        "first_order": {
            "population_chart": _donut_payload(first_order, "pop_latest", population_total),
            "area_chart": _donut_payload(first_order, "area_km2", area_total),
            "cards": _first_order_cards(
                country_code, root, first_order, population_total, area_total,
                include_children=include_children, child_counts=child_counts,
            ),
            "child_groups": _country_first_order_child_groups(
                country_code, root, first_order, population_total, area_total,
                child_counts=child_counts,
            ),
            "pagination": _pagination_payload(page_obj),
        },
    }


def _country_first_order_areas_for_selection(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
) -> list[AdminArea]:
    return list(_country_level_rows_queryset(country_code, root, selected_level, selected_entity_types))


def _country_level_rows_queryset(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    selected_entity_types: tuple[str, ...],
):
    if selected_level is None:
        return area_selectors.visible_admin_areas().none()
    rows = (
        area_selectors.visible_admin_areas()
        .filter(country_code=country_code, level=selected_level)
        .select_related("parent")
        .order_by("name", "code", "id")
    )
    if selected_entity_types:
        rows = rows.filter(entity_type__in=selected_entity_types)
    if root:
        rows = rows.exclude(id=root.id)
    return rows


def _country_first_order_child_groups(
    country_code: str,
    root: AdminArea | None,
    children: list[AdminArea],
    population_total,
    area_total,
    *,
    child_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Return country-level child panels split by real child level.

    The first country view uses a synthetic "first order" list that can mix
    levels when CityPopulation exposes branches unevenly.  Grouping here keeps
    Italy-like cases readable: one box per child level, while the country info
    panel spans the full height in the frontend.
    """
    if not children:
        return []
    grouped: dict[int, list[AdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)
    return [
        {
            "level": level,
            "label": _admin_area_child_group_label(country_code, level, children=rows),
            "children": _admin_area_child_rows(rows, population_total, area_total, child_counts=child_counts),
        }
        for level, rows in sorted(grouped.items())
    ]


def _admin_area_detail_payload(area: AdminArea) -> dict:
    children = _admin_area_browser_children(area)
    child_counts = {child.id: _admin_area_browser_child_count(child) for child in children}
    return {
        "area": _admin_area_identity_payload(area, children),
        "children": _admin_area_child_rows(children, area.pop_latest, area.area_km2, child_counts=child_counts),
        "child_groups": _admin_area_child_groups(area, children, child_counts=child_counts),
    }


def _admin_area_child_groups(
    area: AdminArea, children: list[AdminArea], *, child_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Return direct browser children split by level for the country detail UI.

    Some CityPopulation pages attach lower-level rows to a higher-level parent
    because the source table does not expose the skipped administrative level.
    In that case the detail drawer should show one panel per child level rather
    than mixing, for example, communes and urban places in the same table.
    """
    if not children:
        return []

    grouped: dict[int, list[AdminArea]] = {}
    for child in children:
        grouped.setdefault(child.level, []).append(child)

    return [
        {
            "level": level,
            "label": _admin_area_child_group_label(area.country_code, level, children=rows),
            "children": _admin_area_child_rows(rows, area.pop_latest, area.area_km2, child_counts=child_counts),
        }
        for level, rows in sorted(grouped.items())
    ]


def _admin_area_child_group_label(
    country_code: str, level: int, *, children: list[AdminArea] | None = None,
) -> str:
    if children is None:
        types = _level_entity_types(country_code, level)
    else:
        counts: dict[str, int] = {}
        for child in children:
            entity_type = child.entity_type or ""
            counts[entity_type] = counts.get(entity_type, 0) + 1
        types = [
            _entity_type_label(item, country_code=country_code) or _("Sin tipo")
            for item in sorted(counts, key=lambda item: (-counts[item], item))
        ]
    base = _("Nivel %(level)s") % {"level": level}
    if types:
        return f"{base} · {', '.join(types[:3])}"
    return base


def _admin_area_browser_children(area: AdminArea, *, metadata_only: bool = False) -> list[AdminArea]:
    children_query = (
        area_selectors.visible_admin_areas()
        .filter(parent=area)
        .order_by("name")
    )
    if metadata_only:
        children_query = children_query.only("id", "name", "level")
    else:
        children_query = children_query.select_related("parent")
    children = list(children_query)
    extras = _same_name_root_children_with_descendants(area, {child.id for child in children}, metadata_only=metadata_only)
    if not extras:
        return _collapse_same_name_flat_children(children)

    return _collapse_same_name_flat_children([*children, *extras])


def _collapse_same_name_flat_children(children: list[AdminArea]) -> list[AdminArea]:
    if not children:
        return children

    child_ids = [child.id for child in children]
    children_with_descendants = set(
        area_selectors.visible_admin_areas()
        .filter(parent_id__in=child_ids)
        .values_list("parent_id", flat=True)
        .distinct()
    )
    names_with_descendants = {
        _area_name_key(child.name)
        for child in children
        if child.id in children_with_descendants
    }
    collapsed = [
        child
        for child in children
        if _area_name_key(child.name) not in names_with_descendants or child.id in children_with_descendants
    ]
    return sorted(collapsed, key=lambda child: (_area_name_key(child.name), child.level, child.id))


def _same_name_root_children_with_descendants(
    area: AdminArea, existing_ids: set[str], *, metadata_only: bool = False,
) -> list[AdminArea]:
    root = _area_country_root(area)
    if not root or area.parent_id != root.id:
        return []

    candidates_query = (
        area_selectors.visible_admin_areas()
        .filter(country_code=area.country_code, parent=root, level__gt=area.level)
        .exclude(id__in=existing_ids)
        .order_by("name", "level")
    )
    if metadata_only:
        candidates_query = candidates_query.only("id", "name", "level")
    else:
        candidates_query = candidates_query.select_related("parent")
    candidates = list(candidates_query)
    if not candidates:
        return []

    candidate_ids = [candidate.id for candidate in candidates]
    candidates_with_descendants = set(
        area_selectors.visible_admin_areas()
        .filter(parent_id__in=candidate_ids)
        .values_list("parent_id", flat=True)
        .distinct()
    )
    area_name_key = _area_name_key(area.name)
    return [
        candidate
        for candidate in candidates
        if candidate.id in candidates_with_descendants and _area_name_key(candidate.name) == area_name_key
    ]


def _area_name_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _admin_area_browser_child_count(area: AdminArea) -> int:
    return len(_admin_area_browser_children(area, metadata_only=True))


def _admin_area_identity_payload(area: AdminArea, children: list[AdminArea] | None = None) -> dict:
    country_root = _area_country_root(area)
    visual_assets = _queries_assets._visual_assets_payload("admin_area", str(area.id))
    return {
        "id": area.id,
        "code": area.code,
        "country_code": area.country_code,
        "name": _area_display_name(area),
        "official_name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "parent": _area_display_name(area.parent) if area.parent else "",
        "population": int(area.pop_latest or 0) if area.pop_latest is not None else None,
        "area_km2": _application_browser_metrics._number_or_none(area.area_km2),
        "density": _application_browser_metrics._number_or_none(area.density) or _application_browser_metrics._density(area.pop_latest, area.area_km2),
        "capital": _capital_names_for_area(area),
        "subdivision_count": len(children) if children is not None else _admin_area_browser_child_count(area),
        "type_summary": [],
        "wikidata_query": _area_wikidata_query(area, country_root),
        "wikidata_id": "",
        "map_url": reverse("ciudades_del_mundo:area_map_detail", kwargs={"source": "admin", "area_id": area.id}),
        "detail_url": _admin_area_detail_url(area),
        "visual_assets": visual_assets,
        "flag_asset": visual_assets.get("flag", {}),
        "seal_asset": visual_assets.get("seal", {}),
        "coat_asset": visual_assets.get("coat", {}) or visual_assets.get("seal", {}),
    }


def _admin_area_child_rows(
    children: list[AdminArea], population_total, area_total, *, child_counts: dict[str, int] | None = None,
) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "level": child.level,
            "area_km2": _application_browser_metrics._number_or_none(child.area_km2),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "density": _application_browser_metrics._number_or_none(child.density) or _application_browser_metrics._density(child.pop_latest, child.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(child.pop_latest, population_total),
            "area_percent": _application_browser_metrics._ratio_percent(child.area_km2, area_total),
            "child_count": child_counts[child.id] if child_counts is not None else _admin_area_browser_child_count(child),
            "detail_url": _admin_area_detail_url(child),
        }
        for child in children
    ]


def _area_wikidata_query(area: AdminArea, country_root: AdminArea | None = None) -> str:
    parts = [area.name]
    parent = area.parent
    if parent:
        parts.append(parent.name)
    if country_root and country_root.id != area.id:
        parts.append(country_root.name)
    elif area.country_code:
        parts.append(area.country_code)
    return ", ".join(str(part) for part in parts if part)


def _admin_area_detail_url(area: AdminArea) -> str:
    return reverse("ciudades_del_mundo:api_admin_area_detail", kwargs={"area_id": area.id})


def _resolve_country_level_selection(
    selected_value: int | str | None,
    available_levels: list[dict],
) -> tuple[int | None, tuple[str, ...], str]:
    if not available_levels:
        return None, (), ""
    parsed_level, parsed_types = _parse_country_level_filter(selected_value)
    by_filter = {str(item.get("filter_value") or item["value"]): item for item in available_levels}
    by_level = {int(item["value"]): item for item in available_levels}
    selected_key = str(selected_value or "").strip()
    selected = by_filter.get(selected_key) if selected_key else None
    if selected is None and parsed_level is not None:
        selected = by_level.get(parsed_level)
    if selected is None:
        selected = available_levels[0]
    filter_value = str(selected.get("filter_value") or selected["value"])
    level, entity_types = _parse_country_level_filter(filter_value)
    return level, entity_types, filter_value


def _parse_country_level_filter(value: int | str | None) -> tuple[int | None, tuple[str, ...]]:
    text = str(value or "").strip()
    if not text:
        return None, ()
    level_text, *raw_types = text.split("|")
    try:
        level = int(level_text)
    except (TypeError, ValueError):
        return None, ()
    entity_types = tuple(item for item in raw_types if item)
    return level, entity_types


def _country_available_levels(country_code: str, root: AdminArea | None) -> list[dict]:
    levels = list(
        area_selectors.visible_admin_areas().filter(country_code=country_code)
        .values_list("level", flat=True)
        .distinct()
        .order_by("level")
    )
    rows = []
    for level in levels:
        if root and level == root.level:
            continue
        level_rows = area_selectors.visible_admin_areas().filter(country_code=country_code, level=level)
        if root:
            level_rows = level_rows.exclude(id=root.id)
        type_stats = _country_level_type_stats(level_rows)
        level_total = sum(count for count, _rows_with_children in type_stats.values())
        if level_total <= 0:
            continue
        entity_type_values = tuple(sorted(type_stats, key=lambda item: (-type_stats[item][0], item)))
        entity_types = [_entity_type_label(item, country_code=country_code) or _("Sin tipo") for item in entity_type_values]
        entity_type_label = " / ".join(entity_types[:3])
        rows.append(
            {
                "value": level,
                "filter_value": str(level),
                "label": entity_type_label or _("Nivel %(level)s") % {"level": _display_level(level, root)},
                "entity_type": entity_type_label,
                "count": level_total,
            }
        )
    return rows


def _country_level_type_stats(level_rows) -> dict[str, tuple[int, int]]:
    counts = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in level_rows.values("entity_type").annotate(count=Count("id")).order_by("entity_type")
    }
    rows_with_children = {
        str(row["entity_type"] or ""): int(row["count"])
        for row in (
            level_rows.filter(children__isnull=False)
            .exclude(children__city_merge_status=AdminArea.CityMergeStatus.SOURCE)
            .values("entity_type")
            .annotate(count=Count("id", distinct=True))
            .order_by("entity_type")
        )
    }
    return {entity_type: (count, rows_with_children.get(entity_type, 0)) for entity_type, count in counts.items()}


def _display_level(level: int, root: AdminArea | None) -> int:
    if root is None:
        return level + 1
    return level


def _level_entity_types(country_code: str, level: int) -> list[str]:
    return [
        _entity_type_label(item, country_code=country_code) or _("Sin tipo")
        for item in (
            area_selectors.visible_admin_areas().filter(country_code=country_code, level=level)
            .exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        )
    ]


def _country_table_rows(
    country_code: str,
    root: AdminArea | None,
    selected_level: int | None,
    population_total=0,
    area_total=0,
    selected_entity_types: tuple[str, ...] = (),
) -> list[dict]:
    rows = _country_level_rows_queryset(country_code, root, selected_level, selected_entity_types)
    return _country_table_rows_from_records(rows, country_code, population_total, area_total)


def _country_table_rows_from_records(
    rows,
    country_code: str,
    population_total=0,
    area_total=0,
) -> list[dict]:
    return [
        {
            "id": row.id,
            "level": row.level,
            "detail_url": _admin_area_detail_url(row),
            "parent_id": row.parent_id or "",
            "parent_level": row.parent.level if row.parent else None,
            "parent_detail_url": _admin_area_detail_url(row.parent) if row.parent else "",
            "name": _area_display_name(row),
            "area_km2": _application_browser_metrics._number_or_none(row.area_km2),
            "population": int(row.pop_latest or 0) if row.pop_latest is not None else None,
            "density": _application_browser_metrics._number_or_none(row.density) or _application_browser_metrics._density(row.pop_latest, row.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(row.pop_latest, population_total),
            "area_percent": _application_browser_metrics._ratio_percent(row.area_km2, area_total),
            "parent": _area_display_name(row.parent) if row.parent else "",
            "entity_type": _entity_type_label(row.entity_type, country_code=country_code),
            "annotations": row.annotations or "",
        }
        for row in rows
    ]


def _country_type_summary(country_code: str, root: AdminArea | None) -> list[dict]:
    rows = area_selectors.visible_admin_areas().filter(country_code=country_code)
    if root:
        rows = rows.exclude(id=root.id)
    grouped = rows.values("level", "entity_type").annotate(total=Count("id")).order_by("level", "entity_type")
    return [
        {
            "level": item["level"],
            "label": _("Nivel %(level)s") % {"level": _display_level(item["level"], root)},
            "entity_type": _entity_type_label(item["entity_type"], country_code=country_code) or _("Sin tipo"),
            "total": item["total"],
        }
        for item in grouped
    ]


def _capital_names_for_area(area: AdminArea | None) -> list[str]:
    if not area:
        return []
    return [
        _area_display_name(capital)
        for capital in area.capitals.all().order_by("name")
    ]


def _donut_payload(areas: list[AdminArea], value_field: str, total) -> dict:
    items = []
    for area in areas:
        value = getattr(area, value_field)
        if not value:
            continue
        items.append(
            {
                "key": area.id,
                "label": _area_display_name(area),
                "value": _application_browser_metrics._number_or_none(value),
            }
        )
    return {
        "type": "donut",
        "total": _application_browser_metrics._number_or_none(total),
        "items": items,
    }


def _first_order_cards(
    country_code: str, root: AdminArea | None, first_order: list[AdminArea], population_total, area_total,
    *, include_children: bool = True, child_counts: dict[str, int] | None = None,
) -> list[dict]:
    child_level = _second_order_level(first_order) if include_children else None
    child_count = (
        area_selectors.visible_admin_areas().filter(country_code=country_code, level=child_level).count()
        if child_level is not None
        else 0
    )
    include_children = include_children and child_level is not None and child_count < 150
    return [
        _first_order_card(
            area, population_total, area_total, include_children=include_children,
            child_count=child_counts[area.id] if child_counts is not None else None,
        )
        for area in first_order
    ]


def _second_order_level(first_order: list[AdminArea]) -> int | None:
    levels = sorted({area.level for area in first_order})
    if len(levels) != 1:
        return None
    return levels[0] + 1


def _first_order_card(
    area: AdminArea, population_total, area_total,
    *, include_children: bool = False, child_count: int | None = None,
) -> dict:
    population = int(area.pop_latest or 0) if area.pop_latest is not None else 0
    area_km2 = _application_browser_metrics._number_or_none(area.area_km2) or 0
    return {
        "id": area.id,
        "name": _area_display_name(area),
        "entity_type": _entity_type_label(area.entity_type, country_code=area.country_code),
        "level": area.level,
        "population": population,
        "area_km2": area_km2,
        "density": _application_browser_metrics._number_or_none(area.density) or _application_browser_metrics._density(area.pop_latest, area.area_km2),
        "population_percent": _application_browser_metrics._ratio_percent(population, population_total),
        "area_percent": _application_browser_metrics._ratio_percent(area_km2, area_total),
        "child_count": child_count if child_count is not None else _admin_area_browser_child_count(area),
        "detail_url": _admin_area_detail_url(area),
        "children": _second_order_share_rows(area) if include_children else [],
    }


def _second_order_share_rows(area: AdminArea) -> list[dict]:
    children = _admin_area_browser_children(area)
    return [
        {
            "name": _area_display_name(child),
            "entity_type": _entity_type_label(child.entity_type, country_code=child.country_code),
            "population": int(child.pop_latest or 0) if child.pop_latest is not None else None,
            "area_km2": _application_browser_metrics._number_or_none(child.area_km2),
            "population_percent": _application_browser_metrics._ratio_percent(child.pop_latest, area.pop_latest),
            "area_percent": _application_browser_metrics._ratio_percent(child.area_km2, area.area_km2),
        }
        for child in children
    ]


def _looks_like_municipality(area: AdminArea) -> bool:
    text = " ".join(str(value or "").lower() for value in (area.entity_type, area.name))
    municipality_markers = (
        "municip",
        "commune",
        "comuna",
        "gemeinde",
        "gmina",
        "locality",
        "city",
        "town",
        "village",
    )
    return any(marker in text for marker in municipality_markers)


def _admin_country_label_map() -> dict[str, str]:
    roots = area_selectors.visible_admin_areas().filter(level=0).values_list("country_code", "name")
    return {country_code: _display_name(name, country_code, country_code=country_code) for country_code, name in roots}


def _admin_country_options() -> list[dict[str, str]]:
    codes = area_selectors.visible_admin_areas().values_list("country_code", flat=True).distinct().order_by("country_code")
    return _country_options(codes, _admin_country_label_map())


def _country_options(codes, label_map: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "code": code,
            "label": label_map.get(code, _display_name("", code, country_code=code)),
        }
        for code in codes
    ]


def _apply_country_labels(rows: list[dict], label_map: dict[str, str]) -> None:
    for row in rows:
        country_code = row.get("country_code")
        row["display_label"] = label_map.get(country_code, _display_name("", country_code, country_code=country_code))


def _area_country_display_name(area) -> str:
    root = _area_country_root(area)
    if root:
        return _display_name(root.name, area.country_code, country_code=area.country_code)
    area_country_code = getattr(area, "country_code", "")
    return _display_name("", area_country_code, country_code=area_country_code)


def _display_name(name, fallback, *, country_code: str | None = None, dynamic_name: str | None = None) -> str:
    fallback_value = str(fallback or "").strip()
    language = normalize_language_code(get_language())
    country_key = str(country_code or "").strip().lower()
    if country_key and (not str(name or "").strip() or fallback_value.lower() == country_key):
        dynamic = dynamic_country_name(country_key, language) if dynamic_name is None else dynamic_name
        if dynamic:
            return dynamic
    if language == "es":
        mapped = _view_support.COUNTRY_NAME_ES_BY_CODE.get(fallback_value.lower())
        if mapped:
            return mapped
    value = str(name or "").strip()
    if not value:
        value = fallback_value
    if not value:
        return ""
    if "_" in value:
        value = value.replace("_", " ")
    if str(country_code or "").lower() == "spain":
        value = spain_name(value, language)
    translated = _(value)
    return translated


def _entity_type_label(entity_type: str | None, *, country_code: str | None = None) -> str:
    value = str(entity_type or "").strip()
    if not value:
        return ""
    language = normalize_language_code(get_language())
    dynamic = dynamic_entity_type_label(value, language, country_code=country_code)
    if dynamic:
        return dynamic
    if str(country_code or "").lower() == "spain":
        return spain_entity_type(value, language)
    return _(value)


def _bar_rows(
    rows: list[dict],
    label_key: str,
    value_key: str,
    *,
    limit: int,
    prefix: str = "",
) -> list[dict]:
    cleaned = []
    for row in rows:
        value = row.get(value_key) or 0
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        cleaned.append({"label": f"{prefix}{row.get(label_key)}", "value": value})
    cleaned = sorted(cleaned, key=lambda item: item["value"], reverse=True)[:limit]
    maximum = max((item["value"] for item in cleaned), default=0)
    for item in cleaned:
        item["width"] = 0 if maximum == 0 else max(2, round(item["value"] * 100 / maximum))
    return cleaned


def _merge_status_rows(rows: list[dict]) -> list[dict]:
    labels = dict(AdminArea.CityMergeStatus.choices)
    normalized = [
        {
            "label": labels.get(row["city_merge_status"], row["city_merge_status"]),
            "value": row["total"] or 0,
        }
        for row in rows
    ]
    maximum = max((row["value"] for row in normalized), default=0)
    for row in normalized:
        row["width"] = 0 if maximum == 0 else max(2, round(row["value"] * 100 / maximum))
    return normalized


def _comparison_row(area: NuevoAdminArea, root: NuevoAdminArea) -> dict:
    return {
        "area": area,
        "pop_country_pct": _application_browser_metrics._percent(area.pop_latest, root.pop_latest),
        "area_country_pct": _application_browser_metrics._percent(area.area_km2, root.area_km2),
        "pop_parent_pct": _application_browser_metrics._percent(area.pop_latest, area.parent.pop_latest if area.parent else None),
        "area_parent_pct": _application_browser_metrics._percent(area.area_km2, area.parent.area_km2 if area.parent else None),
        "capitals": ", ".join(capital.name for capital in area.capitals.all()),
    }


def _csv_text_values(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _add_optional_metric(total, value):
    if value is None:
        return total
    if total is None:
        return value
    return total + value


def _derived_source_country_options() -> list[dict[str, str]]:
    options = []
    seen = set()
    try:
        roots = (
            area_selectors.visible_admin_areas()
            .filter(level=0)
            .order_by("name", "country_code", "id")
            .only("id", "country_code", "name", "level")
        )
        for root in roots:
            code = str(root.country_code or "").strip().lower()
            if not code or code in seen:
                continue
            seen.add(code)
            options.append(
                {
                    "value": code,
                    "label": _display_name(root.name, code, country_code=code),
                    "root_id": str(root.id),
                }
            )
        if options:
            return options
        country_codes = (
            area_selectors.visible_admin_areas()
            .values_list("country_code", flat=True)
            .distinct()
            .order_by("country_code")
        )
        return [
            {"value": str(code), "label": _display_name("", code, country_code=str(code)), "root_id": ""}
            for code in country_codes
            if str(code or "").strip()
        ]
    except (OperationalError, ProgrammingError):
        return []


def _source_country_root_for_code(country_code: str) -> AdminArea | None:
    country_code = str(country_code or "").strip().lower()
    if not country_code:
        return None
    return (
        area_selectors.visible_admin_areas()
        .filter(country_code__iexact=country_code, level=0)
        .order_by("parent_id", "name", "id")
        .first()
    )


def _source_country_display_label(country_code: str) -> str:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return ""
    root = _source_country_root_for_code(country_code)
    if root:
        return _display_name(root.name, country_code, country_code=country_code)
    return _display_name("", country_code, country_code=country_code)


def _validate_new_country_config_parent(
    country: DerivedCountry,
    parent_config_slug: str,
    current_config: DerivedCountryConfig | None = None,
) -> str:
    parent_config_slug = query_parameters.normalize_country_key(parent_config_slug)
    if not parent_config_slug:
        return ""
    if current_config and parent_config_slug == current_config.slug:
        raise ValueError(_("Una division no puede ser padre de si misma."))
    try:
        parent = country.configs.get(slug=parent_config_slug)
    except DerivedCountryConfig.DoesNotExist as exc:
        raise ValueError(_("El padre seleccionado no existe.")) from exc
    if current_config and current_config.slug in _queries_new_countries._new_country_config_parent_chain(parent):
        raise ValueError(_("El padre seleccionado crearia una referencia circular."))
    return parent.slug


def _admin_area_child_counts(parent_ids: list[str], *, group_source_only: bool = False) -> dict[str, int]:
    parent_ids = [str(value) for value in parent_ids if str(value or "").strip()]
    if not parent_ids:
        return {}
    try:
        queryset = area_selectors.group_source_admin_areas() if group_source_only else area_selectors.visible_admin_areas()
        return {
            str(row["parent_id"]): int(row["total"] or 0)
            for row in queryset
            .filter(parent_id__in=parent_ids)
            .values("parent_id")
            .annotate(total=Count("id"))
        }
    except (OperationalError, ProgrammingError):
        return {}


def _validate_plain_toml(content: str, *, expected_kind: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("TOML invalido."))
    kind = str(data.get("kind") or "").strip()
    if kind and kind != expected_kind:
        raise ValueError(_("El TOML debe usar kind = '%(kind)s'.") % {"kind": expected_kind})
    return data


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _web_task_table_exists() -> bool:
    """Return whether the persisted task table is migrated and usable."""
    try:
        from ciudades_del_mundo.models import WebTask

        return WebTask._meta.db_table in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def _replace_derived_subdivision_group_key(data: dict, *, old_key: str, new_key: str) -> bool:
    return replace_group_references(data, old_key=old_key, new_key=new_key,
                                    normalize_key=_queries_groups._group_internal_name)
