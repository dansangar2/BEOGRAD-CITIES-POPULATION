"""Parse and render derived-subdivision forms."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters

import tomllib
from django.db import OperationalError, ProgrammingError
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedSubdivision, NuevoAdminArea
from ciudades_del_mundo.services.derived_codes import derived_code_piece, derived_country_root_code
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions


def _derived_subdivision_form_payload(*, country_code: str, record: DerivedSubdivision | None, mode: str) -> dict:
    country_code = query_parameters.normalize_country_key(country_code)
    if record:
        form = _derived_subdivision_form_initial(country_code=country_code, record=record)
    else:
        root_code = _derived_subdivision_root_code(country_code)
        form = {
            "internal_name": "",
            "name": "",
            "source_country_code": country_code,
            "code": "",
            "code_suffix": "",
            "parent_code": root_code,
            "entity_type": "Provincia",
            "level": "1",
            "capital": "",
            "capitals": [],
            "flag_url": "",
            "coat_url": "",
            "use_selected_entities_as_children": False,
            "description": "",
            "content": _queries_browser._default_derived_subdivision_toml(
                country_code=country_code,
                internal_name="NUEVA_SUBDIVISION",
                parent_code=root_code,
            ),
        }
    parent_options = _derived_subdivision_root_parent_options(country_code)
    group_entries_cache: dict[str, list[dict]] = {}
    group_entry_cache: dict[tuple[str, str], dict | None] = {}
    source_ids_cache: dict[tuple[str, str], set[str]] = {}
    return {
        "ok": True,
        "mode": mode,
        "title": str(_("NUEVA SUBDIVISION")) if mode == "new" else form.get("internal_name", ""),
        "form": form,
        "parent_options": parent_options,
        "capital_options": _queries_subdivision_capitals._derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        ),
        "include_items": _queries_subdivision_sources._derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "include",
            fallback_country_code=country_code,
            group_entries_cache=group_entries_cache,
            group_entry_cache=group_entry_cache,
            source_ids_cache=source_ids_cache,
        ),
        "subtract_items": _queries_subdivision_sources._derived_subdivision_visual_source_items_from_content(
            form.get("content", ""),
            "subtract",
            fallback_country_code=country_code,
            group_entries_cache=group_entries_cache,
            group_entry_cache=group_entry_cache,
            source_ids_cache=source_ids_cache,
        ),
    }


def _derived_subdivision_form_save_payload(*, country_code: str, record: DerivedSubdivision) -> dict:
    country_code = query_parameters.normalize_country_key(country_code)
    form = _derived_subdivision_form_initial(country_code=country_code, record=record)
    return {
        "ok": True,
        "mode": "edit",
        "title": form.get("internal_name", ""),
        "form": form,
        "parent_options": _derived_subdivision_root_parent_options(country_code),
        "capital_options": _queries_subdivision_capitals._derived_subdivision_capital_options(
            country_code,
            selected_ids=form.get("capitals", []),
        ),
    }


def _derived_subdivision_form_initial(*, country_code: str, record: DerivedSubdivision) -> dict:
    data = _derived_subdivision_toml_data(record.content)
    internal_name = str(data.get("internal_name") or record.internal_name or _queries_subdivisions._derived_subdivision_entry_slug(record)).strip()
    parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    level = _derived_subdivision_level(data.get("level") or 1)
    code = _derived_subdivision_compose_code(
        country_code=country_code,
        parent_code=parent_code,
        code_value=data.get("code") or record.code or internal_name,
    )
    capitals = _queries_subdivision_capitals._derived_subdivision_capital_values(country_code, data)
    return {
        "internal_name": internal_name,
        "name": str(data.get("name") or record.name or _queries_groups._group_entry_display_name(internal_name)).strip(),
        "source_country_code": country_code,
        "code": code,
        "code_suffix": _derived_subdivision_code_suffix(code, parent_code),
        "parent_code": parent_code,
        "entity_type": str(data.get("entity_type") or record.entity_type or "").strip(),
        "level": str(level),
        "capital": capitals[0] if capitals else "",
        "capitals": capitals,
        "flag_url": str(data.get("flag_url") or "").strip(),
        "coat_url": str(data.get("coat_url") or "").strip(),
        "use_selected_entities_as_children": _forms_scraping._form_bool(
            data.get("use_selected_entities_as_children"),
            default=False,
        ),
        "description": record.description,
        "content": record.content,
    }


def _derived_subdivision_toml_data(content: str) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derived_subdivision_level(value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise ValueError(_("El nivel debe ser numerico.")) from None
    if level < 1 or level > 5:
        raise ValueError(_("El nivel debe estar entre 1 y 5."))
    return level


def _derived_subdivision_root_code(country_code: str) -> str:
    country_code = query_parameters.normalize_country_key(country_code)
    try:
        root = AdminArea.objects.filter(country_code__iexact=country_code, level=0).order_by("id").only("code").first()
    except (OperationalError, ProgrammingError):
        root = None
    code = str(getattr(root, "code", "") or "").strip()
    return derived_country_root_code(country_code, code)


def _derived_subdivision_code_piece(value: str) -> str:
    return derived_code_piece(value)


def _derived_subdivision_clean_parent_code(value, *, country_code: str) -> str:
    text = _derived_subdivision_code_piece(str(value or ""))
    return text or _derived_subdivision_root_code(country_code)


def _derived_subdivision_compose_code(*, country_code: str, parent_code: str, code_value: str) -> str:
    parent_code = _derived_subdivision_clean_parent_code(parent_code, country_code=country_code)
    code = _derived_subdivision_code_piece(code_value)
    if not code:
        raise ValueError(_("El codigo es obligatorio."))
    if parent_code and code.startswith(f"{parent_code}-"):
        return code
    return f"{parent_code}-{code}" if parent_code else code


def _derived_subdivision_code_suffix(code: str, parent_code: str) -> str:
    code = _derived_subdivision_code_piece(code)
    parent_code = _derived_subdivision_code_piece(parent_code)
    prefix = f"{parent_code}-" if parent_code else ""
    return code[len(prefix) :] if prefix and code.startswith(prefix) else code


def _derived_subdivision_root_parent_options(country_code: str) -> list[dict]:
    root_code = _derived_subdivision_root_code(country_code)
    return [
        {
            "value": root_code,
            "code": root_code,
            "level": 0,
            "label": _queries_browser._display_name("", country_code, country_code=country_code),
        }
    ]


def _derived_subdivision_parent_options(country_code: str, *, exclude_record: DerivedSubdivision | None = None) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    root_code = _derived_subdivision_root_code(country_code)
    excluded_codes = set()
    if exclude_record:
        excluded_data = _derived_subdivision_toml_data(exclude_record.content)
        excluded_parent_code = _derived_subdivision_clean_parent_code(
            excluded_data.get("parent_code"),
            country_code=country_code,
        )
        try:
            excluded_codes.add(
                _derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=excluded_parent_code,
                    code_value=excluded_data.get("code") or exclude_record.code or exclude_record.internal_name,
                )
            )
        except ValueError:
            pass
    options = [
        {
            "value": root_code,
            "code": root_code,
            "level": 0,
            "label": _queries_browser._display_name("", country_code, country_code=country_code),
        }
    ]
    seen_codes = {root_code}
    exclude_slug = exclude_record.slug if exclude_record else ""
    records = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("name", "slug")
    for candidate in records:
        if exclude_slug and candidate.slug == exclude_slug:
            continue
        data = _derived_subdivision_toml_data(candidate.content)
        try:
            level = _derived_subdivision_level(data.get("level") or 1)
        except ValueError:
            level = 1
        parent_code = _derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
        raw_code = data.get("code") or candidate.code or candidate.internal_name or candidate.slug
        try:
            code = _derived_subdivision_compose_code(
                country_code=country_code,
                parent_code=parent_code,
                code_value=raw_code,
            )
        except ValueError:
            continue
        if code in seen_codes or code in excluded_codes:
            continue
        seen_codes.add(code)
        label = str(data.get("name") or candidate.name or candidate.internal_name or candidate.slug).strip()
        entity_type = str(data.get("entity_type") or candidate.entity_type or "").strip()
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append({"value": code, "code": code, "level": level, "label": f"{label} - {code}"})
    try:
        areas = (
            NuevoAdminArea.objects.filter(country_code__iexact=country_code, level__gt=0, level__lt=5)
            .only("code", "name", "level", "entity_type")
            .order_by("level", "name", "code")
        )
    except (OperationalError, ProgrammingError):
        areas = []
    for area in areas:
        code = _derived_subdivision_code_piece(area.code)
        if not code or code in seen_codes or code in excluded_codes:
            continue
        try:
            level = int(area.level or 0)
        except (TypeError, ValueError):
            continue
        label = str(area.name or code).strip()
        entity_type = str(area.entity_type or "").strip()
        if entity_type:
            label = f"{label} ({entity_type})"
        options.append({"value": code, "code": code, "level": level, "label": f"{label} - {code}"})
        seen_codes.add(code)
    return options


def _derived_subdivision_group_country_sections_payload(sections: list[dict]) -> list[dict]:
    payload = []
    for country in sections:
        payload.append(
            {
                "country_code": str(country.get("country_code") or ""),
                "country_label": str(country.get("country_label") or ""),
                "default_group_level": int(country.get("default_group_level") or 0),
                "groups": [
                    {
                        "internal_name": str(group.get("internal_name") or ""),
                        "municipality_count_text": str(group.get("municipality_count_text") or ""),
                    }
                    for group in country.get("groups") or []
                    if str(group.get("internal_name") or "").strip()
                ],
            }
        )
    return payload


def _render_derived_subdivision_form_toml(
    content: str,
    *,
    country_code: str,
    internal_name: str,
    name: str,
    code: str,
    parent_code: str,
    entity_type: str,
    level: int,
    capital: str = "",
    capitals: list[str] | tuple[str, ...] | None = None,
    flag_url: str = "",
    coat_url: str = "",
    use_selected_entities_as_children: bool = False,
) -> str:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("TOML invalido: %(error)s") % {"error": exc}) from exc
    if not isinstance(data, dict):
        data = {}
    data.update(
        {
            "schema_version": int(data.get("schema_version") or 1),
            "kind": "derived_subdivision",
            "internal_name": internal_name,
            "source_country_code": country_code,
            "name": name,
            "code": code,
            "parent_code": parent_code,
            "entity_type": str(entity_type or "").strip(),
            "level": level,
            "flag_url": str(flag_url or "").strip(),
            "coat_url": str(coat_url or "").strip(),
            "use_selected_entities_as_children": bool(use_selected_entities_as_children),
        }
    )
    if capitals is None:
        capitals = [capital] if str(capital or "").strip() else []
    data["capitals"] = _queries_subdivision_capitals._normalized_derived_subdivision_capital_values(capitals)
    return _render_derived_subdivision_toml_data(data)


def _render_derived_subdivision_toml_data(data: dict) -> str:
    lines = [
        f"schema_version = {int(data.get('schema_version') or 1)}",
        'kind = "derived_subdivision"',
        f"internal_name = {_queries_browser._toml_string(data.get('internal_name') or '')}",
        f"source_country_code = {_queries_browser._toml_string(data.get('source_country_code') or '')}",
        f"name = {_queries_browser._toml_string(data.get('name') or '')}",
        f"code = {_queries_browser._toml_string(data.get('code') or '')}",
        f"parent_code = {_queries_browser._toml_string(data.get('parent_code') or '')}",
        f"entity_type = {_queries_browser._toml_string(data.get('entity_type') or '')}",
        f"level = {int(data.get('level') or 1)}",
        f"use_selected_entities_as_children = {_forms_scraping._toml_bool(_forms_scraping._form_bool(data.get('use_selected_entities_as_children'), default=False))}",
    ]
    if data.get("generic_name") not in (None, ""):
        lines.append(f"generic_name = {_queries_browser._toml_string(data.get('generic_name') or '')}")
    if data.get("capitals") is not None:
        lines.append(f"capitals = {_forms_scraping._toml_array([str(item) for item in data.get('capitals') or []])}")
    if data.get("flag_url"):
        lines.append(f"flag_url = {_queries_browser._toml_string(data.get('flag_url') or '')}")
    if data.get("coat_url"):
        lines.append(f"coat_url = {_queries_browser._toml_string(data.get('coat_url') or '')}")
    lines.append("")
    _append_derived_capital_group_toml(lines, data.get("capital_groups") if isinstance(data.get("capital_groups"), list) else [])
    _append_derived_block_toml(lines, "include", data.get("include") if isinstance(data.get("include"), list) else [])
    _append_derived_block_toml(lines, "subtract", data.get("subtract") if isinstance(data.get("subtract"), list) else [])
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if not isinstance(child, dict):
            continue
        lines.extend(
            [
                "[[children]]",
                f"name = {_queries_browser._toml_string(child.get('name') or '')}",
                f"code = {_queries_browser._toml_string(child.get('code') or '')}",
                f"entity_type = {_queries_browser._toml_string(child.get('entity_type') or '')}",
                f"level = {int(child.get('level') or 1)}",
                "",
            ]
        )
        _append_derived_capital_group_toml(lines, child.get("capital_groups") if isinstance(child.get("capital_groups"), list) else [], table_name="children.capital_groups")
        _append_derived_block_toml(lines, "children.include", child.get("include") if isinstance(child.get("include"), list) else [])
        _append_derived_block_toml(lines, "children.subtract", child.get("subtract") if isinstance(child.get("subtract"), list) else [])
    return "\n".join(lines).rstrip() + "\n"


def _append_derived_capital_group_toml(lines: list[str], blocks: list[dict], *, table_name: str = "capital_groups") -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        level = int(block.get("level") or 0)
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_queries_browser._toml_string(block.get('country_code') or '')}",
                f"level = {level}",
                f"group = {_queries_browser._toml_string(block.get('group') or block.get('group_key') or '')}",
                f"names = {_forms_scraping._toml_array([str(item) for item in block.get('names') or []])}",
                f"capital_name = {_queries_browser._toml_string(block.get('capital_name') or block.get('display_name') or '')}",
                "",
            ]
        )


def _append_derived_block_toml(lines: list[str], table_name: str, blocks: list[dict]) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        level = int(block.get("level") or 0)
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_queries_browser._toml_string(block.get('country_code') or '')}",
                f"level = {level}",
                f"names = {_forms_scraping._toml_array([str(item) for item in block.get('names') or []])}",
                f"ids = {_forms_scraping._toml_array([str(item) for item in block.get('ids') or []])}",
                f"codes = {_forms_scraping._toml_array([str(item) for item in block.get('codes') or []])}",
                f"groups = {_forms_scraping._toml_array([_queries_groups._group_internal_name(str(item)) for item in block.get('groups') or []])}",
                f"derived_subdivisions = {_forms_scraping._toml_array([_queries_groups._group_internal_name(str(item)) for item in block.get('derived_subdivisions') or []])}",
                f"expressions = {_forms_scraping._toml_array([str(item) for item in block.get('expressions') or []])}",
                "",
            ]
        )
