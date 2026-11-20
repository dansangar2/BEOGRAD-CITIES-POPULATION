"""Parse manual/raw scraping forms and render SQL TOML content."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from itertools import zip_longest
import json
import re
import tomllib
from django.db import OperationalError, ProgrammingError
from django.db.models import Count
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea
from ciudades_del_mundo.services.config_asset_overrides import load_config_asset_overrides, save_config_asset_overrides
from ciudades_del_mundo.services.nuevo_admin_builder import effective_source_municipal_level
from ciudades_del_mundo.web.forms import city_merges as _forms_city_merges
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import config_assets as _queries_config_assets
from ciudades_del_mundo.web.queries import configs as _queries_configs
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web import view_support as _view_support


def _config_form_context(mode: str, slug: str, content: str, active_task=None) -> dict:
    workflow = {
        "status_filter": "pending",
        "can_validate": False,
        "can_scrape": False,
        "can_clear": False,
        "can_stop": False,
        "rows": 0,
        "country_code": slug,
    }
    if mode == "edit" and slug:
        try:
            workflow = _queries_configs._decorate_config_workflow_flags(_queries_configs._config_summary_for_slug(slug))
        except (OperationalError, ProgrammingError):
            pass
    asset_editor_enabled = bool(mode == "edit" and int(workflow.get("rows") or 0) > 0)
    if mode == "edit":
        # Render a light shell first; the full TOML/manual data is hydrated via
        # config_editor_data so large configs do not block the initial page.
        parsed = _parse_config_editor_data(slug, "", asset_editor_enabled=False, include_config_cities=False)
        rendered_content = ""
    else:
        parsed = _parse_config_editor_data(
            slug,
            content,
            asset_editor_enabled=asset_editor_enabled,
            include_config_cities=False,
        )
        rendered_content = content
    return {
        "mode": mode,
        "slug": slug,
        "content": rendered_content,
        "active_task": active_task,
        "config_workflow": workflow,
        "manual": parsed,
        "editor_data_url": reverse("ciudades_del_mundo:config_editor_data", kwargs={"slug": slug}) if mode == "edit" and slug else "",
        "scrape_types": _scrape_type_choices(),
        "ai_enabled": _view_support.AI_CONFIG_ENABLED,
        "ai_personal_login_enabled": _view_support.AI_PERSONAL_LOGIN_ENABLED,
        "ai_provider_choices": _ai_provider_choices(),
    }


def _scrape_type_choices() -> list[tuple[str, str]]:
    return [
        ("cities", _("Cities: infosection + subdivisión superior + ciudades")),
        ("admin", _("Admin: infosection + subdivisión superior + subdivisión inferior")),
        ("citiesadmin", _("CitiesAdmin: infosection + subdivisión superior + subdivisión inferior + ciudades")),
    ]


def _normalize_page_source_for_form(value) -> str:
    source = str(value or "cities").strip().lower()
    return source if source in {"cities", "admin", "citiesadmin"} else "cities"


def _persist_manual_asset_overrides(slug: str, content: str) -> None:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return
    country_code = str(data.get("country_code") or slug or "").strip()
    rows = _queries_config_assets._asset_assignment_rows_for_form(data, country_code=country_code)
    save_config_asset_overrides(slug, country_code, rows)


def _ai_provider_choices() -> list[tuple[str, str]]:
    labels = {
        "chatgpt": "ChatGPT",
        "openai": "OpenAI",
        "claude": "Claude",
        "gemini": "Gemini",
    }
    return [(provider, labels.get(provider, provider.title())) for provider in _view_support.AI_PROVIDER_LOGIN_URLS.keys()]


def _config_city_rows_for_country(country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    municipal_level = effective_source_municipal_level(country_code)
    try:
        areas = list(
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .only("id", "country_code", "code", "name", "level", "entity_type", "raw_entity_type", "pop_latest")
            .order_by("level", "name", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []

    rows = []
    for area in areas:
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            level = 0
        raw_type = str(area.entity_type or area.raw_entity_type or "").strip()
        type_key = raw_type.casefold()
        is_config_city = any(
            token in type_key
            for token in (
                "city",
                "town",
                "village",
                "municip",
                "commune",
                "comuna",
            )
        )
        if not is_config_city and municipal_level is not None:
            is_config_city = level == int(municipal_level)
        if not is_config_city:
            continue
        name = _queries_browser._area_display_name(area)
        population_text = _queries_groups._group_metric_text(area.pop_latest)
        rows.append(
            {
                "id": str(area.id),
                "code": str(area.code or ""),
                "name": name,
                "level": level,
                "type_text": raw_type or "",
                "population": area.pop_latest if area.pop_latest is not None else "",
                "population_text": population_text,
                "href": reverse(
                    "ciudades_del_mundo:area_map_detail",
                    kwargs={"source": "admin", "area_id": area.id},
                ),
                "search_text": " ".join(
                    str(part)
                    for part in (name, area.code, raw_type, level, population_text)
                    if part is not None and str(part).strip()
                ),
            }
        )
    return rows


def _parse_config_editor_data(
    slug: str,
    content: str,
    *,
    asset_editor_enabled: bool = False,
    include_config_cities: bool = True,
) -> dict:
    data = {}
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    pages = []
    for page in data.get("pages") or []:
        raw_paths = page.get("path")
        include = page.get("include") if isinstance(page.get("include"), dict) else {}
        include_infosection = include.get("infosection", True)
        include_major = include.get("major_subdivision", include.get("admin1", True))
        include_minor = include.get("minor_subdivision", include.get("admin2", True))
        include_cities = include.get("cities", True)
        force_level = page.get("force_highest_level")
        parent_level = page.get("parent_level", page.get("force_parent_level"))
        repeat = page.get("repeat") if isinstance(page.get("repeat"), dict) else {}
        pages.append(
            {
                "paths": _page_paths_list_for_form(raw_paths),
                "paths_text": _page_paths_text_for_form(raw_paths),
                "source": _normalize_page_source_for_form(page.get("source", "admin")),
                "force_highest_level": "" if force_level is None else str(force_level),
                "parent_level": "" if parent_level is None else str(parent_level),
                "include_infosection": _bool_text(include_infosection),
                "include_major_subdivision": _bool_text(include_major),
                "include_minor_subdivision": _bool_text(include_minor),
                "include_cities": _bool_text(include_cities),
                "repeat_infosection": "" if repeat.get("infosection") is None else str(repeat.get("infosection")),
                "repeat_major_subdivision": "" if repeat.get("major_subdivision", repeat.get("admin1")) is None else str(repeat.get("major_subdivision", repeat.get("admin1"))),
                "repeat_minor_subdivision": "" if repeat.get("minor_subdivision", repeat.get("admin2")) is None else str(repeat.get("minor_subdivision", repeat.get("admin2"))),
                "repeat_cities": "" if repeat.get("cities") is None else str(repeat.get("cities")),
                "sum_to_root": _bool_text(page.get("sum_to_root", False)),
                "enabled": _bool_text(page.get("enabled", not page.get("disabled", False))),
            }
        )
    country_code = str(data.get("country_code") or slug or "")
    visual_assets = data.get("visual_assets") if isinstance(data.get("visual_assets"), dict) else {}
    asset_overrides = _queries_config_assets._asset_assignment_rows_for_form(data, country_code=country_code)
    persisted_asset_overrides = load_config_asset_overrides(slug) if slug else []
    if persisted_asset_overrides:
        asset_overrides = persisted_asset_overrides
    selected_entity_ids = [row.get("entity_id") for row in asset_overrides if row.get("entity_id")]
    selected_resource_qids = [
        row.get(key)
        for row in asset_overrides
        for key in ("flag_qid", "coat_qid")
        if row.get(key)
    ]
    return {
        "country_code": country_code,
        "wikidata_id": str(data.get("wikidata_id") or ""),
        "legal_subdivision": "" if data.get("LEGAL_SUBDIVISION") is None else str(data.get("LEGAL_SUBDIVISION")),
        "pages": pages,
        "visual_assets": {
            "bulk_country_wikidata": _bool_text(visual_assets.get("bulk_country_wikidata", True)),
            "strict_required": _bool_text(visual_assets.get("strict_required", True)),
            "required_kinds": _list_text_for_form(visual_assets.get("required_kinds") or ["flag", "coat"]),
            "required_levels": _list_text_for_form(visual_assets.get("required_levels") or []),
        },
        "asset_options": _queries_config_assets._asset_assignment_options_for_form(
            country_code,
            enabled=asset_editor_enabled,
            selected_entity_ids=selected_entity_ids,
            selected_resource_qids=selected_resource_qids,
        ),
        "asset_overrides": asset_overrides,
        "config_cities": [],
        "city_builder": _forms_city_merges._config_city_builder_payload(country_code, data, enabled=include_config_cities),
    }


def _selected_ids_from_city_config(country_code: str, city_config: dict) -> list[str]:
    labels = [str(value) for value in city_config.get("communes") or [] if str(value).strip()]
    areas = area_selectors.visible_admin_areas().filter(country_code=country_code).only("id", "name", "code", "level", "entity_type", "parent")
    by_key = {}
    for area in areas:
        for key in (area.id, area.code, area.name):
            by_key[str(key).casefold()] = area.id
    selected: list[str] = []
    for label in labels:
        match = by_key.get(label.casefold())
        if match and match not in selected:
            selected.append(match)

    if selected:
        return selected

    # Algunas configuraciones históricas sólo definen la unificación mediante
    # `from` + `district_types`, sin listar `communes`. En ese caso reconstruimos
    # la selección para que la tabla derecha no aparezca vacía al editar.
    raw_from = city_config.get("from") or {}
    if not isinstance(raw_from, dict):
        return selected
    district_types = {str(value) for value in city_config.get("district_types") or [] if str(value).strip()}
    for raw_level, raw_parent_labels in raw_from.items():
        try:
            parent_level = int(raw_level)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_parent_labels, (str, int)):
            parent_labels = [str(raw_parent_labels)]
        else:
            parent_labels = [str(value) for value in raw_parent_labels or [] if str(value).strip()]
        if not parent_labels:
            continue
        parent_keys = {label.casefold() for label in parent_labels}
        parents = [
            area.id
            for area in area_selectors.visible_admin_areas().filter(country_code=country_code, level=parent_level).only("id", "name", "code")
            if str(area.name).casefold() in parent_keys or str(area.code).casefold() in parent_keys or str(area.id).casefold() in parent_keys
        ]
        if not parents:
            continue
        children = area_selectors.visible_admin_areas().filter(country_code=country_code, parent_id__in=parents).only("id", "entity_type")
        if district_types:
            children = children.filter(entity_type__in=district_types)
        for area in children:
            if area.id not in selected:
                selected.append(area.id)
    return selected


def _source_entities_for_config(slug: str, *, level: str | int | None = None) -> list[dict]:
    if not slug:
        return []
    areas = (
        area_selectors.visible_admin_areas()
        .filter(country_code__iexact=slug)
        .exclude(level=0)
        .select_related("parent")
        .order_by("level", "parent__name", "name")
    )
    if level is not None and str(level).strip():
        areas = areas.filter(level=int(level))
    rows = []
    for area in areas:
        parent = area.parent
        entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
        entity_type_filter = _source_entity_type_filter_label(area.entity_type, entity_type, area.country_code)
        rows.append(
            {
                "id": area.id,
                "name": _queries_browser._area_display_name(area),
                "raw_name": area.name,
                "level": area.level,
                "entity_type": entity_type,
                "raw_entity_type": area.entity_type,
                "entity_type_filter": entity_type_filter,
                "parent": _queries_browser._area_display_name(parent) if parent else "",
                "parent_key": parent.id if parent else "",
                "parent_level": parent.level if parent else None,
                "raw_parent": parent.name if parent else "",
            }
        )
    return rows


def _source_level_filter_options_for_country(country_code: str) -> list[dict]:
    if not country_code:
        return []
    levels: dict[str, dict[str, object]] = {}
    rows = (
        area_selectors.group_source_admin_areas()
        .filter(country_code__iexact=country_code)
        .exclude(level=0)
        .values("level", "entity_type")
        .annotate(total=Count("id"))
        .order_by("level", "entity_type")
    )
    for row in rows:
        level_key = str(row["level"])
        level_data = levels.setdefault(level_key, {"count": 0, "types": {}})
        total = int(row["total"] or 0)
        level_data["count"] = int(level_data["count"]) + total
        raw_type = str(row["entity_type"] or "")
        display_type = _queries_browser._entity_type_label(raw_type, country_code=country_code)
        type_label = _source_entity_type_filter_label(raw_type, display_type, country_code)
        if type_label:
            types = level_data["types"]
            assert isinstance(types, dict)
            types[type_label] = int(types.get(type_label, 0)) + total
    return _source_level_rows_from_counts(levels)


def _source_level_filter_options(entities: list[dict]) -> list[dict]:
    levels: dict[str, dict[str, object]] = {}
    for entity in entities:
        level_key = str(entity.get("level") or "").strip()
        if not level_key:
            continue
        level_data = levels.setdefault(level_key, {"count": 0, "types": {}})
        level_data["count"] = int(level_data["count"]) + 1
        type_label = str(entity.get("entity_type_filter") or entity.get("entity_type") or "").strip()
        if type_label:
            types = level_data["types"]
            assert isinstance(types, dict)
            types[type_label] = int(types.get(type_label, 0)) + 1

    return _source_level_rows_from_counts(levels)


def _source_level_rows_from_counts(levels: dict[str, dict[str, object]]) -> list[dict]:
    rows = []
    for level in sorted(levels, key=_source_level_sort_key):
        level_data = levels[level]
        count = int(level_data["count"])
        types = level_data["types"]
        assert isinstance(types, dict)
        type_labels = sorted(types, key=lambda value: (-int(types[value]), value.casefold()))
        label = "/".join(type_labels) if type_labels else _("Nivel %(level)s") % {"level": level}
        rows.append({"value": level, "label": f"{label} ({count})"})
    return rows


def _source_level_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def _source_entity_type_filter_label(raw_type: str | None, display_type: str, country_code: str) -> str:
    if str(country_code or "").lower() == "spain" and str(raw_type or "") in {"Municipality seat", "Locality"}:
        return _("Cabecera de Municipio/Localidad")
    return display_type


def _source_parent_filter_options(entities: list[dict], *, level: str | None = None) -> list[dict]:
    parents: dict[str, dict[str, str]] = {}
    level_value = str(level or "").strip()
    if level_value == "1":
        return []
    for entity in entities:
        if level_value and str(entity.get("level") or "").strip() != level_value:
            continue
        parent_level = entity.get("parent_level")
        if parent_level is not None and int(parent_level) == 0:
            continue
        parent_key = str(entity.get("parent_key") or "").strip()
        if parent_key and parent_key not in parents:
            parents[parent_key] = {
                "value": parent_key,
                "label": str(entity.get("parent") or entity.get("raw_parent") or parent_key),
            }
    return sorted(parents.values(), key=lambda row: row["label"].casefold())


def _source_entity_type_filter_options(entities: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for entity in entities:
        value = str(entity.get("entity_type_filter") or entity.get("entity_type") or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [
        {"value": value, "label": f"{value} ({counts[value]})"}
        for value in sorted(counts, key=str.casefold)
    ]


def _config_content_from_request(request, slug: str, current_content: str, *, existing: bool) -> str:
    editor_mode = str(request.POST.get("editor_mode") or "file").strip().lower()
    if editor_mode == "manual":
        return _render_config_from_manual_post(slug, request.POST, current_content=current_content)
    if editor_mode == "file":
        return request.POST.get("content", current_content)
    raise ValueError(_("La sección Scrapping solo genera una previsualización. Para guardar, abre Manual o Archivo."))


def _render_config_from_manual_post(slug: str, post, *, current_content: str = "") -> str:
    country_code = (post.get("manual_country_code") or slug).strip() or slug
    legal_subdivision = (post.get("manual_legal_subdivision") or "").strip()
    pages = _manual_pages_from_post(post)
    cities_loaded = str(post.get("config_cities_loaded") or "").strip() == "1"
    configured_cities = _manual_cities_from_post(post, legal_subdivision=legal_subdivision) if cities_loaded else None
    visual_assets = _manual_visual_assets_from_post(post)
    asset_overrides = _manual_asset_overrides_from_post(post)
    if not pages:
        raise ValueError(_("Debes indicar al menos una ruta de scrapeo."))

    preserved = _preserved_manual_config_fragments(current_content, preserve_cities=not cities_loaded)
    lines = []
    if country_code and country_code != slug:
        lines.append(f"country_code = {_queries_browser._toml_string(country_code)}")
    if legal_subdivision:
        try:
            lines.append(f"LEGAL_SUBDIVISION = {int(legal_subdivision)}")
        except (TypeError, ValueError) as exc:
            raise ValueError(_("La subdivisión legal debe ser numérica.")) from exc
    lines.append("scrape_schema_version = 2")
    if preserved["top_level"]:
        lines.extend(preserved["top_level"])
    lines.append("")

    page_settings = _preserved_manual_page_settings(current_content, pages)
    for page, settings in zip(pages, page_settings):
        lines.extend(
            [
                "[[pages]]",
                f"source = {_queries_browser._toml_string(page['source'])}",
                f"path = {_toml_array(page['path'])}",
            ]
        )
        if page.get("enabled") is False:
            lines.append("enabled = false")
        if page.get("force_highest_level") is not None:
            lines.append(f"force_highest_level = {page['force_highest_level']}")
        if page.get("parent_level") is not None:
            lines.append(f"parent_level = {page['parent_level']}")
        if page.get("sum_to_root"):
            lines.append("sum_to_root = true")
        lines.append(f"include = {_toml_scalar_inline_table(page['include'])}")
        if page.get("repeat"):
            lines.append(f"repeat = {_toml_scalar_inline_table(page['repeat'])}")
        for key, value in settings.items():
            lines.append(f"{_toml_key(key)} = {_toml_preserved_page_value(value)}")
        lines.append("")

    if configured_cities is not None:
        for city in configured_cities:
            lines.extend(
                [
                    "[[cities]]",
                    f"city = {_queries_browser._toml_string(city['city'])}",
                    f"id = {_queries_browser._toml_string(city['id'])}",
                    f"level = {int(city['level'])}",
                    f"type = {_queries_browser._toml_string(city['type'])}",
                    f"parent_type = {_queries_browser._toml_string(city['parent_type'])}",
                    f"from = {_toml_inline_table({int(city['parent_level']): [city['parent_name']]})}",
                ]
            )
            if city.get("communes"):
                lines.append(f"communes = {_toml_array(city['communes'])}")
            lines.append(f"keep_communes = {_toml_bool(city.get('keep_communes', False))}")
            lines.append("")

    if preserved["blocks"]:
        lines.extend(preserved["blocks"])
        if lines and lines[-1] != "":
            lines.append("")

    if visual_assets:
        lines.append("[visual_assets]")
        lines.append(f"bulk_country_wikidata = {_toml_bool(visual_assets.get('bulk_country_wikidata', True))}")
        lines.append(f"strict_required = {_toml_bool(visual_assets.get('strict_required', True))}")
        if visual_assets.get("required_kinds"):
            lines.append(f"required_kinds = {_toml_array(visual_assets['required_kinds'])}")
        if visual_assets.get("required_levels"):
            lines.append(f"required_levels = [{', '.join(str(int(level)) for level in visual_assets['required_levels'])}]")
        lines.append("")

    for override in asset_overrides:
        lines.append("[[wikidata_asset_overrides]]")
        for key in ("ids", "names", "name_contains", "codes", "kinds"):
            if override.get(key):
                lines.append(f"{key} = {_toml_array(override[key])}")
        if override.get("level") is not None:
            lines.append(f"level = {int(override['level'])}")
        lines.append(f"wikidata_id = {_queries_browser._toml_string(override['wikidata_id'])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _preserved_manual_page_settings(content: str, pages: list[dict]) -> list[dict]:
    """Keep SQL page settings that have no control in the manual editor.

    Match by source/paths and then forced level so reordered pages and repeated
    URLs at different territorial levels retain their own root identities.
    """
    try:
        original_pages = tomllib.loads(content or "").get("pages", [])
    except tomllib.TOMLDecodeError:
        original_pages = []
    managed_keys = {
        "source", "path", "enabled", "disabled", "force_highest_level", "forced_level",
        "parent_level", "force_parent_level", "sum_to_root", "include", "include_root", "repeat",
    }
    result = []
    for index, page in enumerate(pages):
        candidates = [
            original for original in original_pages
            if isinstance(original, dict)
            and _normalize_page_source_for_form(original.get("source", "admin")) == page["source"]
            and _page_paths_list_for_form(original.get("path")) == page["path"]
        ]
        exact = [
            original for original in candidates
            if original.get("force_highest_level", original.get("forced_level")) == page.get("force_highest_level")
        ]
        if len(exact) == 1:
            original = exact[0]
        elif len(candidates) == 1:
            original = candidates[0]
        elif index < len(original_pages) and original_pages[index] in candidates:
            original = original_pages[index]
        else:
            original = {}
        result.append({key: value for key, value in original.items() if key not in managed_keys})
    return result


def _toml_preserved_page_value(value) -> str:
    """Serialize already parsed TOML settings, including nested inline maps."""
    if isinstance(value, bool):
        return _toml_bool(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_toml_key(str(key))} = {_toml_preserved_page_value(item)}" for key, item in value.items()
        ) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_preserved_page_value(item) for item in value) + "]"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _queries_browser._toml_string(value)


def _preserved_manual_config_fragments(content: str, *, preserve_cities: bool = False) -> dict[str, list[str]]:
    """Keep TOML sections that the visual manual editor does not own.

    The manual editor edits the country identity fields, [[pages]], [[cities]],
    [visual_assets] and explicit wikidata asset overrides.  Everything else
    (representation, entity merges, runtime synthetic rows, etc.) must survive a
    manual save/populate cycle.
    """
    managed_top_keys = {
        "country_code",
        "LEGAL_SUBDIVISION",
        "scrape_schema_version",
        "base_url",
    }
    managed_tables = {
        "pages",
        "visual_assets",
        "wikidata_asset_overrides",
        "visual_asset_overrides",
        "asset_overrides",
    }
    if not preserve_cities:
        managed_tables.add("cities")
    top_level: list[str] = []
    blocks: list[str] = []
    current_block: list[str] = []
    current_header = ""

    def flush_block() -> None:
        nonlocal current_block, current_header
        if not current_block:
            return
        header_name = _toml_header_name(current_header)
        if header_name not in managed_tables:
            while blocks and blocks[-1] == "" and current_block and current_block[0] == "":
                current_block = current_block[1:]
            blocks.extend(current_block)
            if blocks and blocks[-1] != "":
                blocks.append("")
        current_block = []
        current_header = ""

    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_block()
            current_header = stripped
            current_block = [raw_line]
            continue
        if current_block:
            current_block.append(raw_line)
            continue
        if not stripped or stripped.startswith("#"):
            if top_level and top_level[-1] != "":
                top_level.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key and key not in managed_top_keys:
            top_level.append(raw_line)
    flush_block()

    while top_level and top_level[-1] == "":
        top_level.pop()
    while blocks and blocks[-1] == "":
        blocks.pop()
    return {"top_level": top_level, "blocks": blocks}


def _toml_header_name(header: str) -> str:
    text = str(header or "").strip()
    while text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.split(".", 1)[0].strip()


def _manual_cities_from_post(post, *, legal_subdivision: str) -> list[dict]:
    raw = str(post.get("config_cities_json") or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(_("La configuración de ciudades no es JSON válido.")) from exc
    if not isinstance(payload, list):
        raise ValueError(_("La configuración de ciudades debe ser una lista."))

    fallback_level = None
    if str(legal_subdivision or "").strip():
        try:
            fallback_level = int(legal_subdivision)
        except (TypeError, ValueError):
            fallback_level = None

    cities = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        parent_name = str(item.get("parent_name") or "").strip()
        parent_level = _manual_city_int(item.get("parent_level"))
        city_name = str(item.get("city") or "").strip()
        if not city_name:
            raise ValueError(_("El nombre de la ciudad unificada no puede estar vacío."))
        city_code = str(item.get("code") or item.get("id") or item.get("parent_code") or "").strip()
        city_level = _manual_city_int(item.get("level"))
        if city_level is None:
            city_level = fallback_level if fallback_level is not None else (parent_level + 1 if parent_level is not None else None)
        communes = []
        for value in item.get("communes") or item.get("selected_codes") or []:
            text = str(value or "").strip()
            if text and text not in communes:
                communes.append(text)
        if not parent_name or parent_level is None or not city_name or not city_code or city_level is None or not communes:
            continue
        cities.append(
            {
                "city": city_name,
                "id": city_code,
                "level": city_level,
                "type": str(item.get("type") or "City").strip() or "City",
                "parent_level": parent_level,
                "parent_name": parent_name,
                "parent_type": str(item.get("parent_type") or "").strip(),
                "communes": communes,
                "keep_communes": _form_bool(item.get("keep_communes"), default=False),
            }
        )
    return cities


def _manual_city_int(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manual_visual_assets_from_post(post) -> dict:
    required_kinds = _list_from_text(post.get("visual_asset_required_kinds")) or ["flag", "coat"]
    required_levels = _int_list_from_text(post.get("visual_asset_required_levels"), label="required_levels")
    return {
        "bulk_country_wikidata": _form_bool(post.get("visual_asset_bulk_country_wikidata"), default=True),
        "strict_required": _form_bool(post.get("visual_asset_strict_required"), default=True),
        "required_kinds": required_kinds,
        "required_levels": required_levels,
    }


def _manual_asset_overrides_from_post(post) -> list[dict]:
    levels = post.getlist("asset_assignment_level")
    entity_ids = post.getlist("asset_assignment_entity_id")
    flag_qids = post.getlist("asset_assignment_flag_qid")
    coat_qids = post.getlist("asset_assignment_coat_qid")

    rows = []
    for index, values in enumerate(
        zip_longest(levels, entity_ids, flag_qids, coat_qids, fillvalue=""),
        start=1,
    ):
        raw_level, raw_entity_id, raw_flag_qid, raw_coat_qid = values
        entity_id = str(raw_entity_id or "").strip()
        flag_qid = str(raw_flag_qid or "").strip()
        coat_qid = str(raw_coat_qid or "").strip()
        if not entity_id and not flag_qid and not coat_qid and not str(raw_level or "").strip():
            continue
        if not entity_id:
            raise ValueError(_("La corrección de assets %(index)s debe seleccionar una entidad.") % {"index": index})
        level = str(raw_level or "").strip()
        level_value = None
        if level:
            try:
                level_value = int(level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El nivel de la corrección de assets %(index)s debe ser numérico.") % {"index": index}) from exc
        for kind, qid in (("flag", flag_qid), ("coat", coat_qid)):
            if not qid:
                continue
            row = {
                "ids": [entity_id],
                "wikidata_id": qid,
                "kinds": [kind],
            }
            if level_value is not None:
                row["level"] = level_value
            rows.append(row)
    return rows


def _form_bool(value, *, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "si", "sí", "on"}


def _int_list_from_text(value: str | None, *, label: str) -> list[int]:
    result = []
    for item in _list_from_text(value):
        try:
            result.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(_("%(label)s debe contener solo números separados por comas.") % {"label": label}) from exc
    return result


def _manual_page_from_payload(raw_page: dict, *, index: int) -> dict:
    if not isinstance(raw_page, dict):
        raise ValueError(_("La página %(index)s no tiene un formato válido.") % {"index": index})
    source = _normalize_page_source_for_form(raw_page.get("source"))
    raw_paths = raw_page.get("path", raw_page.get("paths", []))
    if isinstance(raw_paths, str):
        paths = _page_paths_from_text(raw_paths)
    elif isinstance(raw_paths, (list, tuple)):
        paths = [str(item).strip() for item in raw_paths if str(item or "").strip()]
    else:
        paths = []
    if not paths:
        return {}
    raw_level = str(raw_page.get("force_highest_level", "") or "").strip()
    level_int = None
    if raw_level:
        try:
            level_int = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("El nivel forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
    raw_parent_level = str(raw_page.get("parent_level", raw_page.get("force_parent_level", "")) or "").strip()
    parent_level_int = None
    if raw_parent_level:
        try:
            parent_level_int = int(raw_parent_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("El padre forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
    include = raw_page.get("include") if isinstance(raw_page.get("include"), dict) else {}
    page: dict[str, object] = {
        "enabled": _form_bool(raw_page.get("enabled"), default=True),
        "source": source,
        "path": paths,
        "sum_to_root": _form_bool(raw_page.get("sum_to_root"), default=False),
    }
    if level_int is not None:
        page["force_highest_level"] = level_int
    if parent_level_int is not None:
        page["parent_level"] = parent_level_int
    repeat = raw_page.get("repeat") if isinstance(raw_page.get("repeat"), dict) else {}
    page["include"] = {
        "infosection": _form_bool(include.get("infosection"), default=True),
        "major_subdivision": _form_bool(include.get("major_subdivision", include.get("admin1")), default=True),
    }
    if source in {"admin", "citiesadmin"}:
        page["include"]["minor_subdivision"] = _form_bool(
            include.get("minor_subdivision", include.get("admin2")),
            default=True,
        )
    if source in {"cities", "citiesadmin"}:
        page["include"]["cities"] = _form_bool(include.get("cities"), default=True)
    repeat_rows = _manual_repeat_dict(
        infosection=repeat.get("infosection", raw_page.get("repeat_infosection")),
        major_subdivision=repeat.get("major_subdivision", repeat.get("admin1", raw_page.get("repeat_major_subdivision"))),
        minor_subdivision=repeat.get("minor_subdivision", repeat.get("admin2", raw_page.get("repeat_minor_subdivision"))),
        cities=repeat.get("cities", raw_page.get("repeat_cities")),
    )
    if repeat_rows:
        page["repeat"] = repeat_rows
    return page


def _manual_repeat_dict(**values) -> dict[str, int]:
    repeats: dict[str, int] = {}
    for key, value in values.items():
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 1:
            repeats[key] = parsed
    return repeats


def _manual_pages_from_json(post) -> list[dict] | None:
    raw = str(post.get("pages_json") or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(_("No se pudo leer la configuración de páginas enviada por el formulario.")) from exc
    if not isinstance(payload, list):
        raise ValueError(_("La configuración de páginas enviada por el formulario no es una lista válida."))

    # La UI envía `pages_json` para conservar rutas múltiples y orden de filas.
    # Si el navegador conserva una versión antigua del JS, ese JSON puede traer
    # `source = cities` aunque el `<select name="page_source">` visible tenga
    # `citiesadmin`. Por seguridad, el backend toma el procedimiento directamente
    # del campo POST real, que es la fuente más fiable para este valor puntual.
    source_overrides = [
        _normalize_page_source_for_form(value)
        for value in post.getlist("page_source")
    ]
    enabled_overrides = [
        _form_bool(value, default=True)
        for value in post.getlist("page_enabled")
    ]
    force_level_overrides = [str(value or "").strip() for value in post.getlist("page_force_highest_level")]
    parent_level_overrides = [str(value or "").strip() for value in post.getlist("page_parent_level")]
    sum_to_root_overrides = [
        _form_bool(value, default=False)
        for value in post.getlist("page_sum_to_root")
    ]
    include_overrides = {
        "infosection": [_form_bool(value, default=True) for value in post.getlist("page_include_infosection")],
        "major_subdivision": [
            _form_bool(value, default=True)
            for value in post.getlist("page_include_major_subdivision")
        ],
        "minor_subdivision": [
            _form_bool(value, default=True)
            for value in (post.getlist("page_include_minor_subdivision") or post.getlist("page_include_admin2"))
        ],
        "cities": [_form_bool(value, default=True) for value in post.getlist("page_include_cities")],
    }
    repeat_overrides = {
        "infosection": [str(value or "").strip() for value in post.getlist("page_repeat_infosection")],
        "major_subdivision": [str(value or "").strip() for value in post.getlist("page_repeat_major_subdivision")],
        "minor_subdivision": [
            str(value or "").strip()
            for value in (post.getlist("page_repeat_minor_subdivision") or post.getlist("page_repeat_admin2"))
        ],
        "cities": [str(value or "").strip() for value in post.getlist("page_repeat_cities")],
    }

    pages: list[dict] = []
    for index, raw_page in enumerate(payload, start=1):
        if isinstance(raw_page, dict) and index <= len(source_overrides):
            raw_page = {**raw_page, "source": source_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(enabled_overrides):
            raw_page = {**raw_page, "enabled": enabled_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(force_level_overrides):
            raw_page = {**raw_page, "force_highest_level": force_level_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(parent_level_overrides):
            raw_page = {**raw_page, "parent_level": parent_level_overrides[index - 1]}
        if isinstance(raw_page, dict) and index <= len(sum_to_root_overrides):
            raw_page = {**raw_page, "sum_to_root": sum_to_root_overrides[index - 1]}
        if isinstance(raw_page, dict):
            include = raw_page.get("include") if isinstance(raw_page.get("include"), dict) else {}
            include = dict(include)
            for key, values in include_overrides.items():
                if index <= len(values):
                    include[key] = values[index - 1]
            raw_page = {**raw_page, "include": include}
            repeat = raw_page.get("repeat") if isinstance(raw_page.get("repeat"), dict) else {}
            repeat = dict(repeat)
            for key, values in repeat_overrides.items():
                if index <= len(values):
                    repeat[key] = values[index - 1]
            raw_page = {**raw_page, "repeat": repeat}
        page = _manual_page_from_payload(raw_page, index=index)
        if page:
            pages.append(page)
    return pages


def _manual_pages_from_post(post) -> list[dict]:
    pages_from_json = _manual_pages_from_json(post)
    if pages_from_json is not None:
        return pages_from_json
    page_paths = post.getlist("page_path") or post.getlist("page_url")
    page_sources = post.getlist("page_source")
    page_levels = post.getlist("page_force_highest_level")
    page_parent_levels = post.getlist("page_parent_level")
    page_enabled_values = post.getlist("page_enabled")
    include_infosections = post.getlist("page_include_infosection")
    include_major_subdivisions = post.getlist("page_include_major_subdivision")
    include_minor_subdivisions = post.getlist("page_include_minor_subdivision") or post.getlist("page_include_admin2")
    include_cities = post.getlist("page_include_cities")
    page_sum_to_roots = post.getlist("page_sum_to_root")
    repeat_infosections = post.getlist("page_repeat_infosection")
    repeat_major_subdivisions = post.getlist("page_repeat_major_subdivision")
    repeat_minor_subdivisions = post.getlist("page_repeat_minor_subdivision") or post.getlist("page_repeat_admin2")
    repeat_cities_values = post.getlist("page_repeat_cities")

    pages = []
    for index, values in enumerate(
        zip_longest(
            page_sources,
            page_paths,
            page_levels,
            page_parent_levels,
            page_enabled_values,
            include_infosections,
            include_major_subdivisions,
            include_minor_subdivisions,
            include_cities,
            page_sum_to_roots,
            repeat_infosections,
            repeat_major_subdivisions,
            repeat_minor_subdivisions,
            repeat_cities_values,
            fillvalue="",
        ),
        start=1,
    ):
        (
            source,
            raw_paths,
            raw_level,
            raw_parent_level,
            raw_enabled,
            raw_include_infosection,
            raw_include_major,
            raw_include_minor,
            raw_include_cities,
            raw_sum_to_root,
            raw_repeat_infosection,
            raw_repeat_major,
            raw_repeat_minor,
            raw_repeat_cities,
        ) = values
        paths = _page_paths_from_text(raw_paths)
        if not paths:
            continue
        source = _normalize_page_source_for_form(source)
        raw_level = str(raw_level or "").strip()
        level_int = None
        if raw_level:
            try:
                level_int = int(raw_level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El nivel forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
        raw_parent_level = str(raw_parent_level or "").strip()
        parent_level_int = None
        if raw_parent_level:
            try:
                parent_level_int = int(raw_parent_level)
            except (TypeError, ValueError) as exc:
                raise ValueError(_("El padre forzado de la página %(index)s debe ser numérico.") % {"index": index}) from exc
        page: dict[str, object] = {"source": source, "path": paths, "enabled": _form_bool(raw_enabled, default=True)}
        if level_int is not None:
            page["force_highest_level"] = level_int
        if parent_level_int is not None:
            page["parent_level"] = parent_level_int
        page["sum_to_root"] = _form_bool(raw_sum_to_root, default=False)
        page["include"] = {
            "infosection": _form_bool(raw_include_infosection, default=True),
            "major_subdivision": _form_bool(raw_include_major, default=True),
        }
        if source in {"admin", "citiesadmin"}:
            page["include"]["minor_subdivision"] = _form_bool(raw_include_minor, default=True)
        if source in {"cities", "citiesadmin"}:
            page["include"]["cities"] = _form_bool(raw_include_cities, default=True)
        repeat_rows = _manual_repeat_dict(
            infosection=raw_repeat_infosection,
            major_subdivision=raw_repeat_major,
            minor_subdivision=raw_repeat_minor,
            cities=raw_repeat_cities,
        )
        if repeat_rows:
            page["repeat"] = repeat_rows
        pages.append(page)
    return pages


def _page_paths_from_text(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = tomllib.loads(f"value = {text}\n").get("value")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(_("path debe ser una lista TOML válida o una ruta por línea.")) from exc
        values = parsed if isinstance(parsed, list) else [parsed]
    else:
        parts: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "," in stripped:
                parts.extend(part.strip() for part in stripped.split(","))
            else:
                parts.append(stripped)
        values = parts
    paths = []
    for value in values:
        path = str(value or "").strip()
        if path in {"/", ".", "<root>", "<raiz>", "<raíz>"}:
            path = ""
        path = path.strip("/")
        if path not in paths:
            paths.append(path)
    return paths


def _page_paths_text_for_form(value) -> str:
    return "\n".join(_page_paths_list_for_form(value))


def _page_paths_list_for_form(value) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    paths: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            text = "/"
        if text not in paths:
            paths.append(text)
    return paths


def _list_from_text(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = tomllib.loads(f"value = {text}\n").get("value")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(_("La lista debe ser TOML válida o valores separados por comas.")) from exc
        raw_values = parsed if isinstance(parsed, list) else [parsed]
    else:
        raw_values = []
        for line in text.splitlines():
            raw_values.extend(line.split(","))
    values = []
    for raw in raw_values:
        item = str(raw or "").strip()
        if item and item not in values:
            values.append(item)
    return values


def _int_mapping_from_text(value: str | None, *, label: str) -> dict[str, int]:
    text = str(value or "").strip()
    if not text:
        return {}
    if not text.startswith("{"):
        text = "{ " + text + " }"
    try:
        parsed = tomllib.loads(f"value = {text}\n").get("value")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(label)s debe tener formato TOML, por ejemplo: ts = 3, tl = 2") % {"label": label}) from exc
    if not isinstance(parsed, dict):
        raise ValueError(_("%(label)s debe ser un mapa TOML.") % {"label": label})
    result = {}
    for key, raw_value in parsed.items():
        try:
            result[str(key)] = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("%(label)s[%(key)s] debe ser numérico.") % {"label": label, "key": key}) from exc
    return result


def _optional_bool(value: str | None) -> bool | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "si", "sí"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(_("El valor booleano debe ser true o false."))


def _bool_text(value) -> str:
    return "true" if bool(value) else "false"


def _list_text_for_form(value) -> str:
    if not value:
        return ""
    values = value if isinstance(value, list) else [value]
    return ", ".join(str(item) for item in values)


def _mapping_text_for_form(value) -> str:
    if not value or not isinstance(value, dict):
        return ""
    return ", ".join(f"{key} = {value[key]}" for key in sorted(value))


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _parent_from_for_selected(selected: list[AdminArea]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for area in selected:
        parent = area.parent
        if not parent:
            continue
        labels = grouped.setdefault(parent.level, [])
        if parent.name not in labels:
            labels.append(parent.name)
    return grouped


def _toml_array(values) -> str:
    return "[" + ", ".join(_queries_browser._toml_string(value) for value in values) + "]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_key(value: str) -> str:
    text = str(value)
    if re.match(r"^[A-Za-z0-9_-]+$", text):
        return text
    return _queries_browser._toml_string(text)


def _toml_scalar_inline_table(mapping: dict[str, object]) -> str:
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        value = mapping[key]
        if isinstance(value, bool):
            rendered = _toml_bool(value)
        elif isinstance(value, int):
            rendered = str(value)
        else:
            rendered = _queries_browser._toml_string(str(value))
        parts.append(f"{_toml_key(str(key))} = {rendered}")
    return "{ " + ", ".join(parts) + " }"


def _toml_inline_table(mapping: dict[int, list[str]]) -> str:
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        parts.append(f"{int(key)} = {_toml_array(mapping[key])}")
    return "{ " + ", ".join(parts) + " }"


def _slugify_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower())
    return normalized.strip("-") or "city"
