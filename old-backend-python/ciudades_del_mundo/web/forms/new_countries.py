"""Parse and update TOML selections and parent relationships."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters

import json
import re
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import DerivedCountry, DerivedCountryConfig
from ciudades_del_mundo.services.derived_config_seeds import render_derived_country_selection_toml
from ciudades_del_mundo.services.derived_codes import derived_code_piece
from ciudades_del_mundo.application import browser_metrics as _application_browser_metrics
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import new_countries as _queries_new_countries
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals


def _new_country_config_visual_form(country: DerivedCountry, config: DerivedCountryConfig) -> dict:
    data = _queries_new_countries._new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    source_country_code = query_parameters.normalize_country_key(
        data.get("source_country_code")
        or selection.get("source_country_code")
        or config.source_country_code
        or country.source_country_code
    )
    parent_config_slug = query_parameters.normalize_country_key(
        entity.get("parent_config_slug")
        or entity.get("parent")
        or data.get("parent_config_slug")
        or data.get("parent")
    )
    selected_derived_subdivisions = _new_country_config_selected_derived_subdivisions(
        entity,
        fallback_country_code=source_country_code,
    )
    selected_subdivision_keys = [
        _queries_new_countries._new_country_derived_subdivision_selection_value(item["country_code"], item["key"])
        for item in selected_derived_subdivisions
    ]
    use_subdivisions = bool(entity.get("use_selected_entities_as_children")) or bool(selected_derived_subdivisions)
    selected_ids = _new_country_config_selected_source_ids(data, entity)
    selected_child_config_slugs = _new_country_config_direct_child_slugs(country, config.slug)
    selection_json = json.dumps(
        {
            "source_country_code": source_country_code,
            "selected_ids": selected_ids,
            "use_subdivisions": use_subdivisions,
            "selected_subdivision_keys": selected_subdivision_keys,
            "selected_derived_subdivisions": selected_derived_subdivisions,
            "selected_child_config_slugs": selected_child_config_slugs,
        },
        ensure_ascii=False,
    )
    try:
        entity_level = int(entity.get("level") or _queries_new_countries._new_country_config_level(config))
    except (TypeError, ValueError):
        entity_level = _queries_new_countries._new_country_config_level(config)
    parent_level = _queries_new_countries._new_country_parent_level(country, parent_config_slug)
    return {
        "slug": _new_country_config_local_code(config, data=data),
        "name": str(entity.get("name") or data.get("name") or config.name or "").strip(),
        "source_country_code": source_country_code,
        "parent_config_slug": parent_config_slug,
        "parent_level": str(parent_level),
        "entity_level": str(entity_level or 1),
        "use_subdivisions": use_subdivisions,
        "entity_type": str(entity.get("entity_type") or "").strip() or "Subdivision",
        "capitals": _queries_subdivision_capitals._derived_subdivision_capital_values(source_country_code, entity),
        "content": config.content,
        "selection_json": selection_json,
    }


def _new_country_config_first_entity(data: dict) -> dict:
    entities = data.get("entities") if isinstance(data, dict) else None
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                return entity
    return {}


def _new_country_config_raw_entity_code(config: DerivedCountryConfig, data: dict | None = None) -> str:
    data = data if isinstance(data, dict) else _queries_new_countries._new_country_config_toml(config)
    entity = _new_country_config_first_entity(data)
    try:
        return _queries_new_countries._new_country_entity_code_from_form(str(entity.get("code") or config.slug))
    except ValueError:
        return derived_code_piece(config.slug)


def _new_country_config_local_code(config: DerivedCountryConfig, data: dict | None = None) -> str:
    return _new_country_config_strip_parent_code_prefix(config, _new_country_config_raw_entity_code(config, data=data))


def _new_country_config_strip_parent_code_prefix(config: DerivedCountryConfig, code: str) -> str:
    code = derived_code_piece(code)
    if not code:
        return ""
    parent_slug = _queries_new_countries._new_country_config_parent_slug(config)
    if not parent_slug:
        return code
    parent = config.country.configs.filter(slug=parent_slug).first()
    if parent is None:
        return code
    parent_code = _new_country_config_expected_full_code(parent, seen={config.slug})
    return _new_country_code_without_parent_prefix(code, parent_code)


def _new_country_code_without_parent_prefix(code: str, parent_code: str) -> str:
    code = derived_code_piece(code)
    parent_code = derived_code_piece(parent_code)
    prefix = f"{parent_code}-" if parent_code else ""
    if prefix and code.startswith(prefix):
        return code[len(prefix) :] or code
    return code


def _new_country_config_selected_source_ids(data: dict, entity: dict) -> list[str]:
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    result: list[str] = []
    seen: set[str] = set()

    def add_many(values) -> None:
        for value in _new_country_config_text_values(values):
            if value in seen:
                continue
            seen.add(value)
            result.append(value)

    selection_items = selection.get("items") if isinstance(selection, dict) else None
    if isinstance(selection_items, list):
        for item in selection_items:
            if isinstance(item, dict):
                add_many([item.get("id") or item.get("value")])
    if result:
        return result

    if isinstance(selection, dict):
        add_many(selection.get("include_ids"))
        add_many(selection.get("subtract_ids"))
    if result:
        return result

    for key in ("include", "subtract"):
        blocks = entity.get(key) if isinstance(entity.get(key), list) else []
        for block in blocks:
            if isinstance(block, dict):
                add_many(block.get("ids"))
    return result


def _new_country_config_selected_derived_subdivisions(
    entity: dict,
    *,
    fallback_country_code: str,
) -> list[dict[str, str | int]]:
    values: list[dict[str, str | int]] = []
    blocks = entity.get("include") if isinstance(entity.get("include"), list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        block_level = _new_country_config_include_block_level(block)
        for value in _new_country_config_text_values(block.get("derived_subdivisions")):
            entry: dict[str, str | int] = {"country_code": block_country, "key": value}
            if block_level is not None:
                entry["level"] = block_level
            values.append(entry)
    return _queries_new_countries._new_country_normalized_derived_subdivision_selections(
        values,
        fallback_country_code=fallback_country_code,
    )


def _new_country_config_include_block_level(block: dict) -> int | None:
    try:
        level = int(block.get("level") or 0)
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None
    return max(1, min(9, level))


def _new_country_config_selected_subdivision_keys(entity: dict) -> list[str]:
    return [
        item["key"]
        for item in _new_country_config_selected_derived_subdivisions(
            entity,
            fallback_country_code="",
        )
    ]


def _new_country_config_text_values(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [str(value).strip() for value in values if str(value or "").strip()]


def _rename_new_country_config_parent_references(
    country: DerivedCountry,
    *,
    old_slug: str,
    new_slug: str,
    exclude_config: DerivedCountryConfig | None = None,
) -> int:
    old_slug = query_parameters.normalize_country_key(old_slug)
    new_slug = query_parameters.normalize_country_key(new_slug)
    if not old_slug or not new_slug or old_slug == new_slug:
        return 0
    updated = 0
    queryset = country.configs.select_for_update().order_by("slug")
    if exclude_config is not None:
        queryset = queryset.exclude(pk=exclude_config.pk)
    for child in queryset:
        if _queries_new_countries._new_country_config_parent_slug(child) != old_slug:
            continue
        content = _replace_new_country_parent_slug_in_content(child.content, old_slug=old_slug, new_slug=new_slug)
        _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
        child.content = content
        child.save(update_fields=["content", "updated_at"])
        updated += 1
    return updated


def _replace_new_country_parent_slug_in_content(content: str, *, old_slug: str, new_slug: str) -> str:
    old_slug = query_parameters.normalize_country_key(old_slug)
    new_slug = query_parameters.normalize_country_key(new_slug)
    if not old_slug or not new_slug or old_slug == new_slug:
        return str(content or "")

    def replace_line(match: re.Match) -> str:
        value = str(match.group("value") or "")
        if query_parameters.normalize_country_key(value) != old_slug:
            return match.group(0)
        return f"{match.group('prefix')}{_queries_browser._toml_string(new_slug)}{match.group('suffix') or ''}"

    return re.sub(
        r'(?m)^(?P<prefix>\s*(?:parent_config_slug|parent)\s*=\s*)(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        replace_line,
        str(content or ""),
    )


def _set_new_country_config_slug_in_content(content: str, *, slug: str) -> str:
    slug = _queries_new_countries._normalize_new_country_code(slug)
    text = str(content or "")
    value = _queries_browser._toml_string(slug)
    replaced = re.sub(
        r'(?m)^(?P<prefix>\s*slug\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        lambda match: f"{match.group('prefix')}{value}{match.group('suffix') or ''}",
        text,
        count=1,
    )
    if replaced != text:
        return replaced
    lines = text.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("kind ="):
            insert_at = index + 1
            break
    lines.insert(insert_at, f"slug = {value}")
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _set_toml_string_field_in_content(
    content: str,
    *,
    field: str,
    value: str,
    table_marker: str = "",
) -> str:
    text = str(content or "")
    assignment = f"{field} = {_queries_browser._toml_string(value)}"
    pattern = re.compile(
        rf'(?m)^(?P<prefix>\s*{re.escape(field)}\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$'
    )

    if not table_marker:
        first_table = re.search(r"(?m)^\s*\[", text)
        search_text = text[: first_table.start()] if first_table else text
        match = pattern.search(search_text)
        if match:
            return text[: match.start()] + assignment + (match.group("suffix") or "") + text[match.end() :]
        insert_at = first_table.start() if first_table else len(text)
        prefix = text[:insert_at]
        suffix = text[insert_at:]
        separator = "" if not prefix or prefix.endswith("\n") else "\n"
        return prefix + separator + assignment + ("\n" if suffix else "") + suffix

    marker_match = re.search(rf"(?m)^\s*{re.escape(table_marker)}\s*$", text)
    if marker_match is None:
        separator = "\n" if text and not text.endswith("\n") else ""
        return f"{text}{separator}{table_marker}\n{assignment}\n"

    section_start = marker_match.end()
    next_table = re.search(r"(?m)^\s*\[", text[section_start:])
    section_end = section_start + next_table.start() if next_table else len(text)
    section = text[section_start:section_end]
    match = pattern.search(section)
    if match:
        new_section = section[: match.start()] + assignment + (match.group("suffix") or "") + section[match.end() :]
    else:
        new_section = "\n" + assignment + section
    return text[:section_start] + new_section + text[section_end:]


def _clone_display_name(name: str) -> str:
    text = str(name or "").strip()
    suffix = _("Copia")
    return f"{text} {suffix}".strip() if text else suffix


def _new_country_clone_local_code(config: DerivedCountryConfig, data: dict, *, suffix_index: int = 1) -> str:
    entity = _new_country_config_first_entity(data)
    base_code = derived_code_piece(entity.get("code") or config.slug)
    if not base_code:
        base_code = derived_code_piece(config.slug)
    suffix = "COPIA" if suffix_index <= 1 else f"COPIA-{suffix_index}"
    if base_code.endswith(f"-{suffix}"):
        return base_code
    return f"{base_code}-{suffix}"


def _new_country_clone_slug_and_code(config: DerivedCountryConfig, data: dict) -> tuple[str, str]:
    base_slug = _queries_new_countries._normalize_new_country_code(f"{config.slug}_copia")
    suffix_index = 1
    candidate = base_slug
    while config.country.configs.filter(slug=candidate).exists():
        suffix_index += 1
        candidate = _queries_new_countries._normalize_new_country_code(f"{base_slug}_{suffix_index}")
    return candidate, _new_country_clone_local_code(config, data, suffix_index=suffix_index)


def _clone_new_country_config(config: DerivedCountryConfig) -> DerivedCountryConfig:
    data = _queries_new_countries._new_country_config_toml(config)
    clone_slug, clone_code = _new_country_clone_slug_and_code(config, data)
    clone_name = _clone_display_name(config.name)
    content = _set_new_country_config_slug_in_content(config.content, slug=clone_slug)
    content = _set_toml_string_field_in_content(content, field="name", value=clone_name)
    content = _set_toml_string_field_in_content(content, field="code", value=clone_code, table_marker="[[entities]]")
    content = _set_toml_string_field_in_content(content, field="name", value=clone_name, table_marker="[[entities]]")
    parsed = _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
    return DerivedCountryConfig.objects.create(
        country=config.country,
        slug=clone_slug,
        name=clone_name,
        content=content.rstrip() + "\n",
        source_country_code=query_parameters.normalize_country_key(parsed.get("source_country_code") or config.source_country_code),
        derived_country_code=query_parameters.normalize_country_key(parsed.get("derived_country_code") or config.derived_country_code),
        is_active=config.is_active,
    )


def _new_country_collision_child_slug(
    country: DerivedCountry,
    *,
    parent_slug: str,
    child_config: DerivedCountryConfig,
) -> str:
    parent_slug = _queries_new_countries._normalize_new_country_code(parent_slug)
    child_code = _new_country_config_local_code(child_config)
    base = _queries_new_countries._normalize_new_country_code(f"{parent_slug}_{_queries_new_countries._new_country_slug_from_entity_code(child_code)}")
    reserved = set(country.configs.exclude(pk=child_config.pk).values_list("slug", flat=True))
    reserved.add(parent_slug)
    if base not in reserved:
        return base
    index = 2
    while True:
        candidate = _queries_new_countries._normalize_new_country_code(f"{base}_{index}")
        if candidate not in reserved:
            return candidate
        index += 1


def _rename_new_country_config_for_parent_code_collision(
    country: DerivedCountry,
    *,
    child_config: DerivedCountryConfig,
    parent_slug: str,
) -> str:
    old_slug = child_config.slug
    new_slug = _new_country_collision_child_slug(country, parent_slug=parent_slug, child_config=child_config)
    if old_slug == new_slug:
        return new_slug
    content = _set_new_country_config_slug_in_content(child_config.content, slug=new_slug)
    _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
    child_config.slug = new_slug
    child_config.content = content
    child_config.save(update_fields=["slug", "content", "updated_at"])
    _rename_new_country_config_parent_references(
        country,
        old_slug=old_slug,
        new_slug=new_slug,
        exclude_config=child_config,
    )
    return new_slug


def _rename_new_country_selected_child_slug_collision(
    country: DerivedCountry,
    *,
    slug: str,
    use_subdivisions: bool,
    selected_child_config_slugs: list[str],
    current_config: DerivedCountryConfig | None = None,
) -> list[str]:
    colliding_children = country.configs.select_for_update().filter(slug=slug)
    if current_config is not None:
        colliding_children = colliding_children.exclude(pk=current_config.pk)
    colliding_child = colliding_children.first()
    if colliding_child is None:
        return selected_child_config_slugs
    if use_subdivisions or slug not in selected_child_config_slugs:
        raise ValueError(
            _("Ya existe una configuracion '%(slug)s' para este pais.") % {"slug": slug}
        )
    renamed_child_slug = _rename_new_country_config_for_parent_code_collision(
        country,
        child_config=colliding_child,
        parent_slug=slug,
    )
    return [
        renamed_child_slug if child_slug == slug else child_slug
        for child_slug in selected_child_config_slugs
    ]


def _new_country_selection_uses_subdivisions(selection_json: str) -> bool:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(selection, dict):
        return False
    return selection.get("use_subdivisions") in (True, "1", 1, "true", "True", "on")


def _new_country_selected_child_config_slugs_from_selection_json(selection_json: str) -> list[str]:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(selection, dict):
        return []
    values = (
        selection.get("selected_child_config_slugs")
        or selection.get("selected_config_slugs")
        or selection.get("selected_created_entity_slugs")
        or []
    )
    return _new_country_normalized_config_slugs(values)


def _new_country_child_config_slugs_for_capital_payload(
    payload: dict,
    *,
    country: DerivedCountry,
    current_config: DerivedCountryConfig | None = None,
) -> list[str]:
    if not isinstance(payload, dict):
        payload = {}
    selection_touched = _forms_scraping._form_bool(payload.get("selection_touched"), default=False) or _forms_scraping._form_bool(
        payload.get("child_config_selection_touched"),
        default=False,
    )
    for key in ("selected_child_config_slugs", "selected_config_slugs", "selected_created_entity_slugs"):
        if key in payload:
            slugs = _new_country_normalized_config_slugs(payload.get(key))
            if slugs or selection_touched or current_config is None:
                return slugs
            break
    if current_config is None:
        return []
    return _new_country_config_direct_child_slugs(country, current_config.slug)


def _new_country_selection_has_child_config_slugs(selection_json: str) -> bool:
    try:
        selection = json.loads(str(selection_json or "").strip() or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(selection, dict):
        return False
    return any(
        key in selection
        for key in ("selected_child_config_slugs", "selected_config_slugs", "selected_created_entity_slugs")
    )


def _new_country_normalized_config_slugs(values) -> list[str]:
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            slug = _queries_new_countries._normalize_new_country_code(str(value or ""))
        except ValueError:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        result.append(slug)
    return result


def _new_country_config_direct_child_slugs(country: DerivedCountry, parent_slug: str) -> list[str]:
    parent_slug = query_parameters.normalize_country_key(parent_slug)
    if not parent_slug:
        return []
    slugs: list[str] = []
    for config in country.configs.order_by("name", "slug"):
        if _queries_new_countries._new_country_config_parent_slug(config) == parent_slug:
            slugs.append(config.slug)
    return slugs


def _new_country_metric_refresh_slugs_for_child_sync(
    country: DerivedCountry,
    *,
    parent_config: DerivedCountryConfig,
    selected_child_slugs: list[str],
) -> list[str]:
    selected = set(_new_country_normalized_config_slugs(selected_child_slugs))
    if not selected:
        return []
    slugs: list[str] = []
    seen: set[str] = set()
    parent_slug = query_parameters.normalize_country_key(parent_config.slug)
    for child in country.configs.filter(slug__in=selected).order_by("slug"):
        old_parent_slug = _queries_new_countries._new_country_config_parent_slug(child)
        if old_parent_slug and old_parent_slug != parent_slug and old_parent_slug not in seen:
            seen.add(old_parent_slug)
            slugs.append(old_parent_slug)
    return slugs


def _refresh_new_country_config_metrics_for_slugs(country: DerivedCountry, config_slugs: list[str]) -> int:
    changed = 0
    visited: set[tuple[str, str]] = set()
    for config_slug in config_slugs:
        normalized_slug = query_parameters.normalize_country_key(config_slug)
        if not normalized_slug:
            continue
        config = country.configs.filter(slug=normalized_slug).first()
        if config is None:
            continue
        changed += _refresh_new_country_config_metrics_with_ancestors(country, config, visited=visited)
    return changed


def _refresh_new_country_config_metrics_with_ancestors(
    country: DerivedCountry,
    config: DerivedCountryConfig,
    *,
    visited: set[tuple[str, str]] | None = None,
) -> int:
    changed = 0
    visited = visited if visited is not None else set()
    current = config
    while current is not None:
        cache_key = (str(country.pk or ""), current.slug)
        if cache_key in visited:
            break
        visited.add(cache_key)
        changed += _refresh_new_country_config_metrics(current)
        parent_slug = _queries_new_countries._new_country_config_parent_slug(current)
        current = country.configs.filter(slug=parent_slug).first() if parent_slug else None
    return changed


def _refresh_new_country_config_metrics(config: DerivedCountryConfig) -> int:
    data = _queries_new_countries._new_country_config_toml(config)
    area_value, population_value = _queries_new_countries._new_country_config_preview_metric_values(config, data=data)
    content = _set_new_country_entity_metrics_in_content(
        config.content,
        area_value=area_value,
        population_value=population_value,
    )
    if content == config.content:
        return 0
    _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
    config.content = content
    config.save(update_fields=["content", "updated_at"])
    return 1


def _new_country_config_expected_full_code(
    config: DerivedCountryConfig,
    *,
    data: dict | None = None,
    seen: set[str] | None = None,
) -> str:
    data = data if isinstance(data, dict) else _queries_new_countries._new_country_config_toml(config)
    code = _new_country_config_raw_entity_code(config, data=data)
    if not code:
        return ""
    parent_slug = _queries_new_countries._new_country_config_parent_slug(config)
    if parent_slug:
        seen = set(seen or set())
        if config.slug in seen:
            return code
        seen.add(config.slug)
        parent = config.country.configs.filter(slug=parent_slug).first()
        if parent is not None:
            parent_code = _new_country_config_expected_full_code(parent, seen=seen)
            code = _new_country_code_without_parent_prefix(code, parent_code)
            if parent_code and code:
                return f"{parent_code}-{code}"
            return code or parent_code
    return code


def _sync_new_country_config_children(
    country: DerivedCountry,
    *,
    parent_config: DerivedCountryConfig,
    selected_child_slugs: list[str],
) -> int:
    parent_slug = query_parameters.normalize_country_key(parent_config.slug)
    selected = set(_new_country_normalized_config_slugs(selected_child_slugs))
    if parent_slug in selected:
        raise ValueError(_("Una division no puede ser hija de si misma."))
    parent_chain = _queries_new_countries._new_country_config_parent_chain(parent_config)
    invalid = selected.intersection(parent_chain)
    if invalid:
        raise ValueError(_("No se puede seleccionar una entidad padre como hija."))

    configs = list(country.configs.select_for_update().order_by("slug"))
    by_slug = {config.slug: config for config in configs}
    missing = sorted(slug for slug in selected if slug not in by_slug)
    if missing:
        raise ValueError(_("La seleccion contiene entidades creadas que no existen."))

    changed = 0
    direct_children = {config.slug for config in configs if _queries_new_countries._new_country_config_parent_slug(config) == parent_slug}
    target_slugs = selected.union(direct_children)
    for child_slug in sorted(target_slugs):
        child = by_slug.get(child_slug)
        if child is None or child.slug == parent_slug:
            continue
        new_parent_slug = parent_slug if child_slug in selected else ""
        changed += _set_new_country_config_parent_and_level(
            country,
            child,
            parent_config_slug=new_parent_slug,
        )
        changed += _cascade_new_country_config_levels(country, child.slug, by_slug=by_slug)
    return changed


def _reparent_new_country_config_children_before_delete(
    country: DerivedCountry,
    *,
    deleted_slug: str,
    new_parent_slug: str,
) -> list[str]:
    deleted_slug = query_parameters.normalize_country_key(deleted_slug)
    new_parent_slug = query_parameters.normalize_country_key(new_parent_slug)
    configs = list(country.configs.select_for_update().order_by("slug"))
    reparented_slugs: list[str] = []
    for child in configs:
        if child.slug == deleted_slug or _queries_new_countries._new_country_config_parent_slug(child) != deleted_slug:
            continue
        _set_new_country_config_parent_and_level(country, child, parent_config_slug=new_parent_slug)
        reparented_slugs.append(child.slug)

    if not reparented_slugs:
        return []

    by_slug = {config.slug: config for config in configs if config.slug != deleted_slug}
    for child_slug in reparented_slugs:
        if child_slug in by_slug:
            _cascade_new_country_config_levels(country, child_slug, by_slug=by_slug)
    return reparented_slugs


def _set_new_country_config_parent_and_level(
    country: DerivedCountry,
    config: DerivedCountryConfig,
    *,
    parent_config_slug: str,
) -> int:
    parent_config_slug = query_parameters.normalize_country_key(parent_config_slug)
    level = _queries_new_countries._new_country_parent_level(country, parent_config_slug) + 1
    local_code = _new_country_config_local_code(config)
    content = _set_toml_string_field_in_content(
        config.content,
        field="code",
        value=local_code,
        table_marker="[[entities]]",
    )
    content = _set_new_country_parent_slug_in_content(content, parent_config_slug=parent_config_slug)
    content = _set_new_country_entity_level_in_content(content, level=level)
    if content == config.content:
        return 0
    _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
    config.content = content
    config.save(update_fields=["content", "updated_at"])
    return 1


def _cascade_new_country_config_levels(
    country: DerivedCountry,
    parent_slug: str,
    *,
    by_slug: dict[str, DerivedCountryConfig] | None = None,
    seen: set[str] | None = None,
) -> int:
    parent_slug = query_parameters.normalize_country_key(parent_slug)
    if not parent_slug:
        return 0
    by_slug = by_slug or {config.slug: config for config in country.configs.order_by("slug")}
    seen = set(seen or set())
    if parent_slug in seen:
        raise ValueError(_("%(country)s tiene padres circulares entre sus divisiones.") % {"country": country.slug})
    seen.add(parent_slug)
    parent_level = _queries_new_countries._new_country_parent_level(country, parent_slug)
    changed = 0
    for child in list(by_slug.values()):
        if _queries_new_countries._new_country_config_parent_slug(child) != parent_slug:
            continue
        level = parent_level + 1
        content = _set_new_country_entity_level_in_content(child.content, level=level)
        if content != child.content:
            _queries_browser._validate_plain_toml(content, expected_kind="derived_country_config")
            child.content = content
            child.save(update_fields=["content", "updated_at"])
            changed += 1
        changed += _cascade_new_country_config_levels(country, child.slug, by_slug=by_slug, seen=seen)
    seen.remove(parent_slug)
    return changed


def _set_new_country_parent_slug_in_content(content: str, *, parent_config_slug: str) -> str:
    parent_config_slug = query_parameters.normalize_country_key(parent_config_slug)
    text = str(content or "")
    value = _queries_browser._toml_string(parent_config_slug)
    replaced = re.sub(
        r'(?m)^(?P<prefix>\s*parent_config_slug\s*=\s*)(?P<quote>["\'])(?P<old>[^"\']*)(?P=quote)(?P<suffix>\s*(?:#.*)?)$',
        lambda match: f"{match.group('prefix')}{value}{match.group('suffix') or ''}",
        text,
    )
    return replaced


def _set_new_country_entity_level_in_content(content: str, *, level: int) -> str:
    level = max(1, min(5, int(level or 1)))
    lines = str(content or "").splitlines()
    result: list[str] = []
    in_entity = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[[entities]]":
            in_entity = True
            replaced = False
            result.append(line)
            continue
        if in_entity and stripped.startswith("[") and stripped != "[[entities]]":
            if not replaced:
                result.append(f"level = {level}")
                replaced = True
            in_entity = False
        if in_entity and re.match(r"^\s*level\s*=", line):
            result.append(re.sub(r"^(\s*level\s*=\s*)\d+(\s*(?:#.*)?)$", rf"\g<1>{level}\2", line))
            replaced = True
            continue
        result.append(line)
    if in_entity and not replaced:
        result.append(f"level = {level}")
    suffix = "\n" if str(content or "").endswith("\n") else ""
    updated = "\n".join(result) + suffix
    return _set_new_country_derived_include_levels_in_content(updated, level=level + 1)


def _set_new_country_derived_include_levels_in_content(content: str, *, level: int) -> str:
    level = max(1, min(5, int(level or 1)))
    lines = str(content or "").splitlines()
    result: list[str] = []
    block: list[str] = []
    in_include = False

    def flush_block() -> None:
        nonlocal block, in_include
        if not in_include:
            result.extend(block)
            block = []
            return
        has_derived_subdivisions = any(re.match(r"^\s*derived_subdivisions\s*=", line) for line in block)
        if not has_derived_subdivisions:
            result.extend(block)
            block = []
            in_include = False
            return
        replaced = False
        output: list[str] = []
        insert_at = 1
        for index, line in enumerate(block):
            if re.match(r"^\s*country_code\s*=", line):
                insert_at = index + 1
            if re.match(r"^\s*level\s*=", line):
                output.append(re.sub(r"^(\s*level\s*=\s*)\d+(\s*(?:#.*)?)$", rf"\g<1>{level}\2", line))
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.insert(insert_at, f"level = {level}")
        result.extend(output)
        block = []
        in_include = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_block()
            if stripped == "[[entities.include]]":
                in_include = True
                block = [line]
            else:
                result.append(line)
            continue
        if in_include:
            block.append(line)
        else:
            result.append(line)
    flush_block()
    suffix = "\n" if str(content or "").endswith("\n") else ""
    return "\n".join(result) + suffix


def _set_new_country_entity_metrics_in_content(
    content: str,
    *,
    area_value,
    population_value,
) -> str:
    metric_lines = [
        line
        for line in (
            _new_country_metric_toml_line("area_km2", area_value),
            _new_country_metric_toml_line("pop_latest", population_value, integer=True),
            _new_country_metric_toml_line("density", _application_browser_metrics._density(population_value, area_value)),
        )
        if line
    ]
    metric_keys = {"area_km2", "area", "pop_latest", "population", "density"}
    lines = str(content or "").splitlines()
    result: list[str] = []
    in_entity = False
    entity_seen = False
    metrics_written = False

    def write_metrics() -> None:
        nonlocal metrics_written
        result.extend(metric_lines)
        metrics_written = True

    for line in lines:
        stripped = line.strip()
        if stripped == "[[entities]]":
            if in_entity and not metrics_written:
                write_metrics()
            in_entity = not entity_seen
            entity_seen = True
            result.append(line)
            continue
        if in_entity and stripped.startswith("["):
            if not metrics_written:
                write_metrics()
            in_entity = False
        if in_entity and re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line):
            key = line.split("=", 1)[0].strip()
            if key in metric_keys:
                continue
        result.append(line)
    if in_entity and not metrics_written:
        write_metrics()
    if not entity_seen:
        return str(content or "")
    suffix = "\n" if str(content or "").endswith("\n") else ""
    return "\n".join(result) + suffix


def _new_country_metric_toml_line(key: str, value, *, integer: bool = False) -> str:
    rendered = _new_country_toml_number(value, integer=integer)
    return f"{key} = {rendered}" if rendered is not None else ""


def _new_country_toml_number(value, *, integer: bool = False) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if integer:
        return str(int(round(number)))
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _derived_country_content_from_create_post(
    *,
    country: DerivedCountry,
    slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
    raw_content: str,
    selection_json: str,
    parent_config_slug: str = "",
    entity_level: int = 1,
    entity_code: str | None = None,
    entity_type: str = "",
    capitals=None,
) -> str:
    selection_json = str(selection_json or "").strip()
    if selection_json:
        try:
            selection = json.loads(selection_json)
        except json.JSONDecodeError as exc:
            raise ValueError(_("La seleccion visual no es JSON valido: %(error)s") % {"error": exc}) from exc
        selected_ids = selection.get("selected_ids") if isinstance(selection, dict) else None
        use_subdivisions = bool(selection.get("use_subdivisions")) if isinstance(selection, dict) else False
        selected_subdivision_keys = (
            selection.get("selected_subdivision_keys") if isinstance(selection, dict) else []
        ) or []
        selected_derived_subdivisions = (
            selection.get("selected_derived_subdivisions") if isinstance(selection, dict) else []
        ) or []
        selected_child_config_slugs = (
            selection.get("selected_child_config_slugs") if isinstance(selection, dict) else []
        ) or []
        if not isinstance(selected_ids, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones."))
        if not isinstance(selected_subdivision_keys, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones creadas."))
        if not isinstance(selected_derived_subdivisions, list):
            raise ValueError(_("La seleccion visual debe contener una lista de subdivisiones creadas."))
        if not isinstance(selected_child_config_slugs, list):
            raise ValueError(_("La seleccion visual debe contener una lista de entidades creadas."))
        selected_derived_subdivisions = _queries_new_countries._new_country_normalized_derived_subdivision_selections(
            selected_derived_subdivisions or selected_subdivision_keys,
            fallback_country_code=source_country_code,
        )
        selected_child_config_slugs = _new_country_normalized_config_slugs(selected_child_config_slugs)
        entity_mode = "intermediate" if not use_subdivisions and selected_child_config_slugs else "final"
        if not str(source_country_code or "").strip():
            raise ValueError(_("El pais fuente es obligatorio."))
        return render_derived_country_selection_toml(
            country_slug=country.slug,
            config_slug=slug,
            name=name,
            source_country_code=source_country_code,
            derived_country_code=derived_country_code,
            selected_ids=[str(value) for value in selected_ids],
            selected_derived_subdivisions=selected_derived_subdivisions,
            entity_name=name,
            entity_code=entity_code or slug,
            entity_type=entity_type,
            entity_mode=entity_mode,
            use_subdivisions=use_subdivisions,
            parent_config_slug=parent_config_slug,
            entity_level=entity_level,
            capitals=_queries_subdivision_capitals._normalized_derived_subdivision_capital_values(capitals),
        )

    content = str(raw_content or "").strip()
    if content:
        return content
    return _queries_new_countries._default_derived_country_toml(
        country=country,
        slug=slug,
        name=name,
        source_country_code=source_country_code,
        derived_country_code=derived_country_code,
    )
