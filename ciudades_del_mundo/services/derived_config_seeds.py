"""SQL import helpers for derived-country and subdivision-group TOML seeds."""

from __future__ import annotations

import ast
from pathlib import Path
import json
import re
import tomllib

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import (
    AdminArea,
    DerivedCountry,
    DerivedCountryConfig,
    DerivedSubdivision,
    SubdivisionGroup,
)
from ciudades_del_mundo.services.derived_codes import derived_code_piece


PACKAGE_ROOT = Path(settings.BASE_DIR) / "ciudades_del_mundo"
NEW_COUNTRY_CONFIG_SEED_ROOT = PACKAGE_ROOT / "new_country_configs"
SUBDIVISION_GROUP_SEED_ROOT = PACKAGE_ROOT / "subdivision_groups"
SUBDIVISION_GROUP_GROUP_DIRNAME = "groups"
SUBDIVISION_GROUP_SUBDIVISION_DIRNAME = "subdivisions"
DERIVED_SUBDIVISION_SEED_ROOT = SUBDIVISION_GROUP_SEED_ROOT / SUBDIVISION_GROUP_SUBDIVISION_DIRNAME
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
GROUP_SEED_METADATA_KEYS = {
    "schema_version",
    "kind",
    "slug",
    "entry_slug",
    "internal_name",
    "name",
    "description",
    "source_country_code",
    "source_bundle",
    "source_python",
    "country_groups",
    "selection",
    "legacy",
    "groups",
    "is_active",
}


def bundled_new_country_config_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return TOML seed files for the `/new-countries/` SQL section."""
    return _seed_paths(NEW_COUNTRY_CONFIG_SEED_ROOT, slugs)


def bundled_subdivision_group_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return TOML seed files for the `/groups/` SQL section."""
    return _group_seed_paths(slugs)


def bundled_derived_subdivision_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return TOML seed files for the `/subdivisions/` SQL section."""
    return _derived_subdivision_seed_paths(slugs)


def import_new_country_config_seed(slug: str, *, force: bool = True) -> DerivedCountryConfig:
    """Import one `new_country_configs/<slug>.toml` file into SQL."""
    slug = normalize_seed_slug(slug)
    path = _single_seed_path(NEW_COUNTRY_CONFIG_SEED_ROOT, slug)
    return import_new_country_config_path(path, force=force)


def import_subdivision_group_seed(slug: str, *, force: bool = True) -> SubdivisionGroup:
    """Import one `subdivision_groups/groups/<country>.toml` file into SQL."""
    slug = normalize_seed_slug(slug)
    path = _single_group_seed_path(slug)
    groups = import_subdivision_group_path_records(path, force=force)
    if not groups:
        raise ValueError(_("No se importo ningun grupo desde %(path)s.") % {"path": path})
    return groups[0]


def import_derived_subdivision_seed(slug: str, *, force: bool = True) -> DerivedSubdivision:
    """Import one derived-subdivision TOML bundle into SQL."""
    paths = bundled_derived_subdivision_paths([slug])
    if not paths:
        raise ValueError(_("No existe semilla TOML de subdivision para '%(slug)s'.") % {"slug": slug})
    records = import_derived_subdivision_path_records(paths[0], force=force)
    if not records:
        raise ValueError(_("No se importo ninguna subdivision desde %(path)s.") % {"path": paths[0]})
    return records[0]


def import_new_country_config_path(path: Path, *, force: bool = True) -> DerivedCountryConfig:
    """Create or refresh a `DerivedCountryConfig` from one TOML seed."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    data = _parse_seed(content, expected_kind="derived_country_config", path=path)
    slug = normalize_seed_slug(str(data.get("slug") or path.stem))
    name = str(data.get("name") or _title_from_slug(slug)).strip()
    if not name:
        raise ValueError(_("%(path)s: el nombre es obligatorio.") % {"path": path})

    country_slug = normalize_seed_slug(str(data.get("country") or data.get("country_slug") or slug))
    source_country_code = _clean_country_code(data.get("source_country_code") or "")
    derived_country_code = _clean_country_code(data.get("derived_country_code") or slug) or slug
    country_name = str(data.get("country_name") or "").strip() or (
        name if country_slug == slug else _title_from_slug(country_slug)
    )

    with transaction.atomic():
        country, _created = DerivedCountry.objects.update_or_create(
            slug=country_slug,
            defaults={
                "name": country_name,
                "description": str(data.get("description") or "").strip(),
                "source_country_code": source_country_code,
            },
        )
        existing = country.configs.filter(slug=slug).first()
        if existing and not force:
            return existing
        config, _created = DerivedCountryConfig.objects.update_or_create(
            country=country,
            slug=slug,
            defaults={
                "name": name,
                "content": content,
                "source_country_code": source_country_code,
                "derived_country_code": derived_country_code,
                "is_active": bool(data.get("is_active", True)),
            },
        )
    return config


def import_subdivision_group_path(path: Path, *, force: bool = True) -> SubdivisionGroup:
    """Create or refresh a `SubdivisionGroup` from one TOML seed."""
    groups = import_subdivision_group_path_records(path, force=force)
    if not groups:
        raise ValueError(_("No se importo ningun grupo desde %(path)s.") % {"path": path})
    return groups[0]


def import_subdivision_group_path_records(path: Path, *, force: bool = True) -> list[SubdivisionGroup]:
    """Create or refresh all `SubdivisionGroup` records from one country TOML seed."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    records = _parse_subdivision_group_seed_records(content, path=path)
    groups: list[SubdivisionGroup] = []
    with transaction.atomic():
        for data in records:
            internal_name = _safe_group_toml_key(data.get("internal_name") or _single_group_assignment_name(data))
            entry_slug = normalize_seed_slug(str(data.get("entry_slug") or internal_name.lower()))
            source_country_code = _clean_country_code(data.get("source_country_code") or "")
            slug = normalize_seed_slug(str(data.get("slug") or _country_group_record_slug(source_country_code, entry_slug)))
            name = str(data.get("name") or internal_name).strip()
            if not name:
                raise ValueError(_("%(path)s: el nombre es obligatorio.") % {"path": path})

            duplicate = _subdivision_group_duplicate_for_key(
                source_country_code=source_country_code,
                entry_slug=entry_slug,
                exclude_slug=slug,
            )
            if duplicate:
                raise ValueError(
                    _("%(path)s: el grupo '%(name)s' ya existe para '%(country)s'.")
                    % {"path": path, "name": internal_name, "country": source_country_code or "-"}
                )

            existing = SubdivisionGroup.objects.filter(slug=slug).first()
            if existing and not force:
                groups.append(existing)
                continue
            group, _created = SubdivisionGroup.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": str(data.get("description") or "").strip(),
                    "content": str(data.get("_content") or content),
                    "source_country_code": source_country_code,
                },
            )
            groups.append(group)
    return groups


def import_derived_subdivision_path_records(path: Path, *, force: bool = True) -> list[DerivedSubdivision]:
    """Create or refresh `DerivedSubdivision` records from one TOML seed."""
    path = Path(path)
    records = _parse_derived_subdivision_seed_records(path)
    default_source_country_code = _derived_subdivision_import_default_country_code(path)
    subdivisions: list[DerivedSubdivision] = []
    with transaction.atomic():
        for data in records:
            internal_name = _safe_group_toml_key(data.get("internal_name") or "")
            if not internal_name:
                continue
            source_country_code = _clean_country_code(data.get("source_country_code") or "")
            entry_slug = normalize_seed_slug(internal_name.lower())
            slug = normalize_seed_slug(str(data.get("slug") or _derived_subdivision_record_slug(source_country_code, entry_slug)))
            existing = DerivedSubdivision.objects.filter(slug=slug).first()
            if existing is None and force:
                default_slug = _derived_subdivision_record_slug(default_source_country_code, entry_slug)
                if default_slug != slug:
                    DerivedSubdivision.objects.filter(slug=default_slug, internal_name__iexact=internal_name).delete()
            if existing and not force:
                subdivisions.append(existing)
                continue
            subdivision, _created = DerivedSubdivision.objects.update_or_create(
                slug=slug,
                defaults={
                    "internal_name": internal_name,
                    "name": str(data.get("name") or internal_name).strip(),
                    "source_country_code": source_country_code,
                    "entity_type": str(data.get("entity_type") or "").strip(),
                    "code": str(data.get("code") or "").strip(),
                    "description": str(data.get("description") or "").strip(),
                    "content": str(data.get("_content") or "").strip(),
                },
            )
            subdivisions.append(subdivision)
    return subdivisions


def _derived_subdivision_import_default_country_code(path: Path) -> str:
    country_code = _clean_country_code(_derived_subdivision_seed_country_from_path(path))
    if country_code:
        return country_code
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    if isinstance(data, dict):
        return _clean_country_code(data.get("source_country_code") or "")
    return ""


def export_subdivision_groups_to_toml(
    *,
    force: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
) -> int:
    """Export SQL subdivision groups back to one TOML seed file per source country."""
    root = Path(output_dir) if output_dir is not None else _subdivision_group_groups_root()
    queryset = SubdivisionGroup.objects.order_by("source_country_code", "slug")
    if slugs:
        clean_slugs = [normalize_seed_slug(slug) for slug in slugs]
        selected_groups = list(queryset.filter(slug__in=clean_slugs))
        selected_countries = {_subdivision_group_country_dir_name(group) for group in selected_groups}
        groups = [
            group
            for group in SubdivisionGroup.objects.order_by("source_country_code", "slug")
            if _subdivision_group_country_dir_name(group) in selected_countries
        ]
    else:
        groups = list(queryset)

    groups_by_country: dict[str, list[SubdivisionGroup]] = {}
    for group in groups:
        groups_by_country.setdefault(_subdivision_group_country_dir_name(group), []).append(group)
    exported = 0
    for country_code, country_groups in sorted(groups_by_country.items()):
        path = root / f"{country_code}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _normalized_group_bundle_seed_content(country_code, country_groups).rstrip() + "\n"
        if not force and path.is_file():
            try:
                if path.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
        path.write_text(content, encoding="utf-8")
        exported += 1
    return exported


def export_derived_subdivisions_to_toml(
    *,
    force: bool = False,
    slugs: list[str] | tuple[str, ...] | None = None,
    country_codes: list[str] | tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
) -> int:
    """Export SQL derived subdivisions back to one TOML seed file per source country."""
    root = Path(output_dir) if output_dir is not None else DERIVED_SUBDIVISION_SEED_ROOT
    queryset = DerivedSubdivision.objects.order_by("source_country_code", "slug")
    selected_countries = {_safe_seed_dir_name(code) for code in (country_codes or []) if str(code or "").strip()}
    if slugs:
        clean_slugs = [normalize_seed_slug(slug) for slug in slugs]
        selected_records = list(queryset.filter(slug__in=clean_slugs))
        selected_countries.update(_derived_subdivision_country_dir_name(record) for record in selected_records)
    if selected_countries:
        records = [
            record
            for record in queryset
            if _derived_subdivision_country_dir_name(record) in selected_countries
        ]
    else:
        records = list(queryset)

    records_by_country: dict[str, list[DerivedSubdivision]] = {}
    for record in records:
        records_by_country.setdefault(_derived_subdivision_country_dir_name(record), []).append(record)
    exported = 0
    for country_code, country_records in sorted(records_by_country.items()):
        path = root / f"{country_code}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _normalized_derived_subdivision_bundle_seed_content(country_code, country_records).rstrip() + "\n"
        if not force and path.is_file():
            try:
                if path.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
        path.write_text(content, encoding="utf-8")
        exported += 1
    return exported


def render_derived_country_selection_toml(
    *,
    country_slug: str,
    config_slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
    selected_ids: list[str],
    selected_derived_subdivisions: list | tuple | None = None,
    entity_name: str | None = None,
    entity_code: str | None = None,
    entity_type: str | None = None,
    entity_mode: str = "final",
    use_subdivisions: bool = False,
    parent_config_slug: str = "",
    entity_level: int = 1,
    capitals: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Render a structured TOML config from the web tree selection."""
    country_slug = normalize_seed_slug(country_slug)
    config_slug = normalize_seed_slug(config_slug)
    source_country_code = _clean_country_code(source_country_code)
    derived_country_code = _clean_country_code(derived_country_code or config_slug) or config_slug
    selected_ids = [str(value) for value in selected_ids if str(value or "").strip()]
    selected_derived_subdivision_groups = _derived_country_selected_subdivisions_by_country(
        selected_derived_subdivisions or [],
        fallback_country_code=source_country_code,
    )
    use_subdivisions = bool(use_subdivisions)
    parent_config_slug = normalize_seed_slug(parent_config_slug) if str(parent_config_slug or "").strip() else ""
    try:
        entity_level = int(entity_level or 1)
    except (TypeError, ValueError):
        entity_level = 1
    entity_level = max(1, min(5, entity_level))
    assigned_subdivision_level = max(1, min(5, entity_level + 1))
    entity_mode = str(entity_mode or "final").strip().lower()
    if entity_mode not in {"final", "intermediate"}:
        entity_mode = "final"
    if use_subdivisions:
        entity_mode = "intermediate"
    if use_subdivisions and not selected_derived_subdivision_groups and not selected_ids:
        raise ValueError(_("Selecciona al menos una subdivision creada."))
    if entity_mode == "final" and not selected_ids:
        raise ValueError(_("Selecciona al menos una subdivision fuente."))

    areas = list(
        AdminArea.objects.filter(
            id__in=selected_ids,
            city_merge_status__in=[
                int(AdminArea.CityMergeStatus.NONE),
                int(AdminArea.CityMergeStatus.UNIFIED),
            ],
        )
        .select_related("parent")
        .order_by("level", "name", "id")
    )
    found_ids = {str(area.id) for area in areas}
    missing = [area_id for area_id in selected_ids if area_id not in found_ids]
    if missing:
        raise ValueError(_("La seleccion contiene subdivisiones que no pertenecen al pais fuente."))

    selected_set = set(found_ids)
    items = []
    for area in areas:
        operation = "subtract" if _has_selected_ancestor(area, selected_set) else "add"
        items.append(
            {
                "id": str(area.id),
                "country_code": str(area.country_code or source_country_code),
                "code": str(area.code or ""),
                "name": str(area.name or ""),
                "level": int(area.level or 0),
                "operation": operation,
                "parent_id": str(area.parent_id or ""),
            }
        )

    include_items = [item for item in items if item["operation"] == "add"]
    subtract_items = [item for item in items if item["operation"] == "subtract"]
    entity_label = str(entity_name or name or config_slug).strip()
    entity_code_value = derived_code_piece(entity_code or config_slug)
    entity_type_value = str(entity_type or ("Contenedor" if entity_mode == "intermediate" else "Subdivision")).strip()
    capital_values = [str(value).strip() for value in (capitals or []) if str(value or "").strip()]
    lines = [
        "schema_version = 1",
        'kind = "derived_country_config"',
        f"country = {_toml_string(country_slug)}",
        f"slug = {_toml_string(config_slug)}",
        f"name = {_toml_string(name)}",
            f"source_country_code = {_toml_string(source_country_code)}",
            f"derived_country_code = {_toml_string(derived_country_code)}",
            f"parent_config_slug = {_toml_string(parent_config_slug)}",
            "groups = []",
            "",
        "[selection]",
        f"source_country_code = {_toml_string(source_country_code)}",
        f"include_ids = {_toml_array([item['id'] for item in include_items])}",
        f"subtract_ids = {_toml_array([item['id'] for item in subtract_items])}",
        f"include_codes = {_toml_array([item['code'] for item in include_items if item['code']])}",
        f"subtract_codes = {_toml_array([item['code'] for item in subtract_items if item['code']])}",
        "group_slugs = []",
        "",
    ]
    for item in items:
        lines.extend(
            [
                "[[selection.items]]",
                f"id = {_toml_string(item['id'])}",
                f"country_code = {_toml_string(item['country_code'])}",
                f"code = {_toml_string(item['code'])}",
                f"name = {_toml_string(item['name'])}",
                f"level = {item['level']}",
                f"operation = {_toml_string(item['operation'])}",
                f"parent_id = {_toml_string(item['parent_id'])}",
                "",
            ]
        )
    lines.extend(
        [
            "[[entities]]",
            f"mode = {_toml_string(entity_mode)}",
            f"name = {_toml_string(entity_label)}",
            f"code = {_toml_string(entity_code_value)}",
            f"entity_type = {_toml_string(entity_type_value)}",
            f"level = {entity_level}",
            f"parent_config_slug = {_toml_string(parent_config_slug)}",
            f"capitals = {_toml_array(capital_values)}",
        ]
    )
    if use_subdivisions:
        lines.append("use_selected_entities_as_children = true")
        grouped_area_items: dict[tuple[str, int], list[str]] = {}
        for item in items:
            grouped_area_items.setdefault((item["country_code"], int(item["level"])), []).append(item["id"])
        for (item_country_code, item_level), ids in sorted(grouped_area_items.items()):
            lines.extend(
                [
                    "",
                    "[[entities.include]]",
                    f"country_code = {_toml_string(item_country_code)}",
                    f"level = {item_level}",
                    f"ids = {_toml_array(ids)}",
                ]
            )
        for group_country_code, group_subdivision_keys in selected_derived_subdivision_groups.items():
            lines.extend(
                [
                    "",
                    "[[entities.include]]",
                    f"country_code = {_toml_string(group_country_code)}",
                    f"level = {assigned_subdivision_level}",
                    f"derived_subdivisions = {_toml_array(group_subdivision_keys)}",
                ]
            )
    elif entity_mode == "final":
        lines.append("use_selected_entities_as_children = false")
        for operation, selected in (("include", include_items), ("subtract", subtract_items)):
            grouped: dict[tuple[str, int], list[str]] = {}
            for item in selected:
                grouped.setdefault((item["country_code"], int(item["level"])), []).append(item["id"])
            for (item_country_code, level), ids in sorted(grouped.items()):
                lines.extend(
                    [
                        "",
                        f"[[entities.{operation}]]",
                        f"country_code = {_toml_string(item_country_code)}",
                        f"level = {level}",
                        f"ids = {_toml_array(ids)}",
                    ]
                )
    lines.append("")
    return "\n".join(lines)


def _derived_country_selected_subdivisions_by_country(
    values,
    *,
    fallback_country_code: str,
) -> dict[str, list[str]]:
    fallback_country_code = _clean_country_code(fallback_country_code)
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    if values in (None, ""):
        return grouped
    if not isinstance(values, (list, tuple)):
        values = [values]
    for value in values:
        country_code = ""
        subdivision_key = ""
        if isinstance(value, dict):
            encoded_country, encoded_key = _split_derived_subdivision_reference(
                value.get("id") or value.get("value") or ""
            )
            country_code = _clean_country_code(
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
        else:
            encoded_country, encoded_key = _split_derived_subdivision_reference(value)
            country_code = _clean_country_code(encoded_country or fallback_country_code)
            subdivision_key = encoded_key or str(value or "").strip()
        subdivision_key = _safe_group_toml_key(subdivision_key) if str(subdivision_key or "").strip() else ""
        marker = (country_code, subdivision_key.casefold())
        if not country_code or not subdivision_key or marker in seen:
            continue
        seen.add(marker)
        grouped.setdefault(country_code, []).append(subdivision_key)
    return grouped


def _split_derived_subdivision_reference(value) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text.startswith("derived-subdivision::"):
        return "", text
    parts = text.split("::", 2)
    if len(parts) < 3:
        return "", ""
    return _clean_country_code(parts[1]), parts[2]


def render_subdivision_group_selection_toml(
    *,
    slug: str,
    name: str,
    source_country_code: str,
    selected_ids: list[str],
    include_levels: list[int] | tuple[int, ...] | None = None,
) -> str:
    """Render a structured subdivision-group TOML from a SQL AdminArea selection."""
    slug = normalize_seed_slug(slug)
    source_country_code = _clean_country_code(source_country_code)
    selected_ids = [str(value) for value in selected_ids if str(value or "").strip()]
    include_levels = _clean_levels(include_levels or [])
    if (selected_ids or include_levels) and not source_country_code:
        raise ValueError(_("El pais fuente es obligatorio."))
    internal_name = _safe_group_toml_key(slug)

    areas = []
    if selected_ids:
        areas = list(
            AdminArea.objects.filter(id__in=selected_ids, country_code__iexact=source_country_code)
            .select_related("parent")
            .order_by("level", "name", "id")
        )
        found_ids = {str(area.id) for area in areas}
        missing = [area_id for area_id in selected_ids if area_id not in found_ids]
        if missing:
            raise ValueError(_("La seleccion contiene subdivisiones que no pertenecen al pais fuente."))

    selected_set = {str(area.id) for area in areas}
    items = []
    for area in areas:
        operation = "subtract" if _has_selected_ancestor(area, selected_set) else "add"
        items.append(
            {
                "id": str(area.id),
                "country_code": _clean_country_code(area.country_code or source_country_code),
                "code": str(area.code or ""),
                "name": str(area.name or ""),
                "level": int(area.level or 0),
                "operation": operation,
                "parent_id": str(area.parent_id or ""),
            }
        )

    include_items = [item for item in items if item["operation"] == "add"]
    subtract_items = [item for item in items if item["operation"] == "subtract"]
    lines = [
        f"source_country_code = {_toml_string(source_country_code)}",
        "",
        f"{internal_name} = {_toml_array([item['name'] for item in include_items])}",
        "",
        "[selection]",
        f"source_country_code = {_toml_string(source_country_code)}",
        f"include_ids = {_toml_array([item['id'] for item in include_items])}",
        f"subtract_ids = {_toml_array([item['id'] for item in subtract_items])}",
        f"include_codes = {_toml_array([item['code'] for item in include_items if item['code']])}",
        f"subtract_codes = {_toml_array([item['code'] for item in subtract_items if item['code']])}",
        f"include_levels = {_toml_int_array(include_levels)}",
        "include_names = []",
        "",
    ]
    for level in include_levels:
        lines.extend(
            [
                "[[selection.levels]]",
                f"country_code = {_toml_string(source_country_code)}",
                f"level = {level}",
                'operation = "add"',
                "",
            ]
        )
    for item in items:
        lines.extend(
            [
                "[[selection.items]]",
                f"id = {_toml_string(item['id'])}",
                f"code = {_toml_string(item['code'])}",
                f"level = {item['level']}",
                f"operation = {_toml_string(item['operation'])}",
                f"parent_id = {_toml_string(item['parent_id'])}",
                "",
            ]
        )
    return "\n".join(lines)


def normalize_seed_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(_("El slug debe ser un identificador en minusculas."))
    return slug


def _seed_paths(root: Path, slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    if not root.is_dir():
        return []
    allowed = {normalize_seed_slug(slug) for slug in (slugs or [])}
    return sorted(
        path
        for path in root.glob("*.toml")
        if not path.name.startswith("_") and (not allowed or path.stem in allowed)
    )


def _single_seed_path(root: Path, slug: str) -> Path:
    path = root / f"{slug}.toml"
    if not path.is_file():
        raise ValueError(_("No existe el TOML semilla: %(path)s.") % {"path": path})
    return path


def _recursive_seed_paths(root: Path, slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    if not root.is_dir():
        return []
    allowed = {normalize_seed_slug(slug) for slug in (slugs or [])}
    return sorted(
        (
            path
            for path in root.rglob("*.toml")
            if path.is_file()
            and not path.name.startswith("_")
            and not any(part.startswith("_") for part in path.relative_to(root).parts[:-1])
            and (not allowed or path.stem in allowed)
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _subdivision_group_groups_root() -> Path:
    return SUBDIVISION_GROUP_SEED_ROOT / SUBDIVISION_GROUP_GROUP_DIRNAME


def _subdivision_group_subdivisions_root() -> Path:
    return SUBDIVISION_GROUP_SEED_ROOT / SUBDIVISION_GROUP_SUBDIVISION_DIRNAME


def _group_seed_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    root = _subdivision_group_groups_root()
    if not root.is_dir():
        return []
    if not slugs:
        bundle_paths = _seed_paths(root)
        bundled_countries = {path.stem for path in bundle_paths}
        legacy_paths = [
            path
            for path in _recursive_seed_paths(root)
            if path.parent != root and path.parent.name not in bundled_countries
        ]
        return sorted(
            bundle_paths + legacy_paths,
            key=lambda path: path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix(),
        )

    paths: list[Path] = []
    for raw_slug in slugs:
        paths.extend(_group_seed_paths_for_token(root, raw_slug))
    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    return sorted(
        unique_paths,
        key=lambda path: path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix(),
    )


def _derived_subdivision_seed_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    root = DERIVED_SUBDIVISION_SEED_ROOT
    if not root.is_dir():
        return []
    if not slugs:
        bundle_paths = _seed_paths(root)
        bundled_countries = {path.stem for path in bundle_paths}
        legacy_paths = [
            path
            for path in _recursive_seed_paths(root)
            if path.parent != root and path.parent.name not in bundled_countries
        ]
        return sorted(
            bundle_paths + legacy_paths,
            key=lambda path: path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix(),
        )
    paths: list[Path] = []
    for raw_slug in slugs:
        paths.extend(_derived_subdivision_seed_paths_for_token(root, raw_slug))
    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    return sorted(unique_paths, key=lambda path: path.relative_to(root).as_posix())


def _derived_subdivision_seed_paths_for_token(root: Path, raw_slug) -> list[Path]:
    token = str(raw_slug or "").strip().replace("\\", "/").strip("/")
    if not token:
        return []
    if "/" in token:
        parts = [part for part in token.split("/") if part]
        if len(parts) != 2:
            raise ValueError(_("Slug TOML de subdivision invalido: %(slug)s.") % {"slug": raw_slug})
        country = _safe_seed_dir_name(parts[0])
        entry_slug = normalize_seed_slug(parts[1].removesuffix(".toml"))
        bundle = root / f"{country}.toml"
        if bundle.is_file():
            return [bundle]
        legacy = root / country / f"{entry_slug}.toml"
        return [legacy] if legacy.is_file() else []
    slug = normalize_seed_slug(token.removesuffix(".toml"))
    direct = root / f"{slug}.toml"
    if direct.is_file():
        return [direct]
    country_dir = root / slug
    if country_dir.is_dir():
        return _recursive_seed_paths(country_dir)
    legacy_matches = [
        path
        for path in _recursive_seed_paths(root, [slug])
        if path.parent != root
    ]
    if legacy_matches:
        return legacy_matches
    bundle_matches = [
        path
        for path in _seed_paths(root)
        if _country_bundle_contains_derived_subdivision(path, slug)
    ]
    return bundle_matches


def _single_group_seed_path(slug: str) -> Path:
    paths = _group_seed_paths([slug])
    if len(paths) == 1:
        return paths[0]
    if len(paths) > 1:
        raise ValueError(_("Hay mas de un TOML semilla para el slug: %(slug)s.") % {"slug": slug})
    path = _subdivision_group_groups_root() / f"{slug}.toml"
    legacy_path = SUBDIVISION_GROUP_SEED_ROOT / f"{slug}.toml"
    if legacy_path.is_file():
        return legacy_path
    raise ValueError(_("No existe el TOML semilla: %(path)s.") % {"path": path})


def _group_seed_paths_for_token(root: Path, raw_slug) -> list[Path]:
    token = str(raw_slug or "").strip().replace("\\", "/").strip("/")
    if not token:
        return []
    if "/" in token:
        parts = [part for part in token.split("/") if part]
        if len(parts) != 2:
            raise ValueError(_("Slug TOML de grupo invalido: %(slug)s.") % {"slug": raw_slug})
        country = _safe_seed_dir_name(parts[0])
        entry_slug = normalize_seed_slug(parts[1].removesuffix(".toml"))
        bundle = root / f"{country}.toml"
        if bundle.is_file() and _country_bundle_contains_group(bundle, entry_slug):
            return [bundle]
        legacy = root / country / f"{entry_slug}.toml"
        return [legacy] if legacy.is_file() else []

    slug = normalize_seed_slug(token.removesuffix(".toml"))
    bundle = root / f"{slug}.toml"
    if bundle.is_file():
        return [bundle]
    legacy_country_dir = root / slug
    if legacy_country_dir.is_dir():
        return _recursive_seed_paths(legacy_country_dir)
    legacy_matches = [
        path
        for path in _recursive_seed_paths(root, [slug])
        if path.parent != root
    ]
    if legacy_matches:
        return legacy_matches
    bundle_matches = [
        path
        for path in _seed_paths(root)
        if _country_bundle_contains_group(path, slug)
    ]
    if bundle_matches:
        return bundle_matches
    legacy_path = SUBDIVISION_GROUP_SEED_ROOT / f"{slug}.toml"
    return [legacy_path] if legacy_path.is_file() else []


def _country_bundle_contains_group(path: Path, entry_slug: str) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    normalized_entry = normalize_seed_slug(entry_slug)
    for internal_name, _names in _top_level_group_assignments(data):
        if normalize_seed_slug(_safe_group_toml_key(internal_name).lower()) == normalized_entry:
            return True
    return False


def _country_bundle_contains_derived_subdivision(path: Path, entry_slug: str) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    normalized_entry = normalize_seed_slug(entry_slug)
    entries = data.get("subdivisions")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            names = [
                entry.get("entry_slug"),
                entry.get("internal_name"),
                entry.get("slug"),
            ]
            content = str(entry.get("content") or "")
            if content:
                try:
                    parsed = tomllib.loads(content)
                except tomllib.TOMLDecodeError:
                    parsed = {}
                if isinstance(parsed, dict):
                    names.extend([parsed.get("entry_slug"), parsed.get("internal_name"), parsed.get("slug")])
            for name in names:
                if not str(name or "").strip():
                    continue
                if normalize_seed_slug(_safe_group_toml_key(name).lower()) == normalized_entry:
                    return True
                if normalize_seed_slug(str(name)) == normalized_entry:
                    return True
        return False
    internal_name = str(data.get("internal_name") or "").strip()
    if internal_name and normalize_seed_slug(_safe_group_toml_key(internal_name).lower()) == normalized_entry:
        return True
    return False


def _parse_seed(content: str, *, expected_kind: str, path: Path) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(path)s: TOML invalido: %(error)s") % {"path": path, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(path)s: TOML invalido.") % {"path": path})
    kind = str(data.get("kind") or "").strip()
    if kind != expected_kind:
        raise ValueError(_("%(path)s: kind debe ser '%(kind)s'.") % {"path": path, "kind": expected_kind})
    return data


def _parse_subdivision_group_seed_records(content: str, *, path: Path) -> list[dict]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(path)s: TOML invalido: %(error)s") % {"path": path, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(path)s: TOML invalido.") % {"path": path})

    default_country_code = _group_seed_country_from_path(path)
    if default_country_code and not _clean_country_code(data.get("source_country_code") or ""):
        data["source_country_code"] = default_country_code

    kind = str(data.get("kind") or "").strip()
    if kind:
        if kind != "subdivision_group":
            raise ValueError(_("%(path)s: kind debe ser '%(kind)s'.") % {"path": path, "kind": "subdivision_group"})
        assignments = _top_level_group_assignments(data)
        if assignments and not data.get("internal_name"):
            data["internal_name"] = _safe_group_toml_key(assignments[0][0])
        file_slug = normalize_seed_slug(path.stem)
        internal_name = _safe_group_toml_key(data.get("internal_name") or _single_group_assignment_name(data) or file_slug)
        entry_slug = normalize_seed_slug(str(data.get("entry_slug") or internal_name.lower()))
        source_country_code = _clean_country_code(data.get("source_country_code") or default_country_code)
        data["slug"] = normalize_seed_slug(
            str(data.get("slug") or _country_group_record_slug(source_country_code, entry_slug))
        )
        data["entry_slug"] = entry_slug
        data["internal_name"] = internal_name
        data["name"] = str(data.get("name") or internal_name).strip()
        data["source_country_code"] = source_country_code
        data["_content"] = content
        return [data]

    assignments = _top_level_group_assignments(data)
    if not assignments:
        raise ValueError(_("%(path)s: los TOML de grupos deben definir al menos una asignacion NOMBRE = [municipios].") % {"path": path})

    source_country_code = _clean_country_code(data.get("source_country_code") or default_country_code)
    records = []
    seen_slugs: set[str] = set()
    for internal_name, names in assignments:
        safe_internal_name = _safe_group_toml_key(internal_name)
        entry_slug = normalize_seed_slug(str(data.get("entry_slug") or safe_internal_name.lower()))
        if entry_slug in seen_slugs:
            raise ValueError(
                _("%(path)s: el grupo '%(name)s' esta repetido en el TOML.")
                % {"path": path, "name": safe_internal_name}
            )
        seen_slugs.add(entry_slug)
        records.append(
            {
                "schema_version": 1,
                "slug": _country_group_record_slug(source_country_code, entry_slug),
                "entry_slug": entry_slug,
                "internal_name": safe_internal_name,
                "name": str(data.get("name") or safe_internal_name).strip(),
                "description": str(data.get("description") or "").strip(),
                "source_country_code": source_country_code,
                "selection": {
                    "source_country_code": source_country_code,
                    "include_names": names,
                },
                "_content": _single_group_seed_content(
                    source_country_code=source_country_code,
                    internal_name=safe_internal_name,
                    names=names,
                ),
            }
        )
    return records


def _parse_derived_subdivision_seed_records(path: Path) -> list[dict]:
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(_("%(path)s: no se pudo leer el TOML: %(error)s") % {"path": path, "error": exc}) from exc
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(path)s: TOML invalido: %(error)s") % {"path": path, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(path)s: TOML invalido.") % {"path": path})

    source_country_code = _clean_country_code(
        data.get("source_country_code") or _derived_subdivision_seed_country_from_path(path)
    )
    entries = data.get("subdivisions")
    if isinstance(entries, list):
        return _parse_derived_subdivision_bundle_records(
            data,
            path=path,
            default_country_code=source_country_code,
        )

    kind = str(data.get("kind") or "").strip()
    if kind == "derived_subdivision":
        return [
            _derived_subdivision_record_from_toml_data(
                data,
                content,
                path=path,
                default_country_code=source_country_code,
            )
        ]

    legacy = data.get("legacy") if isinstance(data.get("legacy"), dict) else {}
    source = str(legacy.get("python_source") or "").strip()
    if kind and not source:
        return []
    if not source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(_("%(path)s: Python legacy invalido: %(error)s") % {"path": path, "error": exc}) from exc

    records = []
    seen: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue
        internal_name = statement.targets[0].id
        if not isinstance(statement.value, ast.Dict) or not _legacy_dict_is_subdivision(statement.value):
            continue
        safe_internal_name = _safe_group_toml_key(internal_name)
        if safe_internal_name in seen:
            continue
        seen.add(safe_internal_name)
        record = _legacy_subdivision_record(
            internal_name=safe_internal_name,
            value=statement.value,
            source_country_code=source_country_code,
            path=path,
        )
        records.append(record)
    return records


def _parse_derived_subdivision_bundle_records(
    data: dict,
    *,
    path: Path,
    default_country_code: str,
) -> list[dict]:
    entries = data.get("subdivisions")
    if not isinstance(entries, list):
        return []
    records = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(
                _("%(path)s: la entrada %(index)s de subdivisions debe ser una tabla TOML.")
                % {"path": path, "index": index}
            )
        entry_country_code = _clean_country_code(entry.get("source_country_code") or default_country_code)
        entry_content = str(entry.get("content") or "").strip()
        if entry_content:
            record = _derived_subdivision_record_from_content(
                entry_content,
                path=path,
                default_country_code=entry_country_code,
                metadata=entry,
            )
        else:
            record = _derived_subdivision_record_from_toml_data(
                entry,
                "",
                path=path,
                default_country_code=entry_country_code,
            )
        entry_slug = normalize_seed_slug(record["internal_name"].lower())
        key = (_clean_country_code(record.get("source_country_code") or ""), entry_slug)
        if key in seen:
            raise ValueError(
                _("%(path)s: la subdivision '%(name)s' esta repetida en el TOML.")
                % {"path": path, "name": record["internal_name"]}
            )
        seen.add(key)
        records.append(record)
    return records


def _derived_subdivision_record_from_content(
    content: str,
    *,
    path: Path,
    default_country_code: str,
    metadata: dict | None = None,
) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(path)s: TOML de subdivision invalido: %(error)s") % {"path": path, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(path)s: TOML de subdivision invalido.") % {"path": path})
    kind = str(data.get("kind") or "").strip()
    if kind and kind != "derived_subdivision":
        raise ValueError(
            _("%(path)s: kind de subdivision debe ser '%(kind)s'.")
            % {"path": path, "kind": "derived_subdivision"}
        )
    return _derived_subdivision_record_from_toml_data(
        data,
        content,
        path=path,
        default_country_code=default_country_code,
        metadata=metadata,
    )


def _derived_subdivision_record_from_toml_data(
    data: dict,
    content: str,
    *,
    path: Path,
    default_country_code: str,
    metadata: dict | None = None,
) -> dict:
    metadata = metadata or {}
    source_country_code = _clean_country_code(
        metadata.get("source_country_code")
        or data.get("source_country_code")
        or default_country_code
    )
    internal_name = _safe_group_toml_key(metadata.get("internal_name") or data.get("internal_name") or path.stem)
    entry_slug = normalize_seed_slug(str(metadata.get("entry_slug") or data.get("entry_slug") or internal_name.lower()))
    record_content = str(content or "").strip()
    payload = {
        "name": metadata.get("name") or data.get("name") or internal_name,
        "code": metadata.get("code") or data.get("code") or "",
        "entity_type": metadata.get("entity_type") or data.get("entity_type") or "",
        "level": data.get("level") or metadata.get("level") or 1,
        "generic_name": data.get("generic_name") or metadata.get("generic_name") or "",
        "capitals": data.get("capitals") if isinstance(data.get("capitals"), list) else [],
        "flag_url": data.get("flag_url") or metadata.get("flag_url") or "",
        "coat_url": data.get("coat_url") or metadata.get("coat_url") or "",
        "capital_groups": data.get("capital_groups") if isinstance(data.get("capital_groups"), list) else [],
        "include": data.get("include") if isinstance(data.get("include"), list) else [],
        "subtract": data.get("subtract") if isinstance(data.get("subtract"), list) else [],
        "children": data.get("children") if isinstance(data.get("children"), list) else [],
    }
    if not record_content:
        record_content = _render_derived_subdivision_toml(
            internal_name=internal_name,
            source_country_code=source_country_code,
            payload=payload,
        ).strip()
    slug = normalize_seed_slug(
        str(metadata.get("slug") or data.get("slug") or _derived_subdivision_record_slug(source_country_code, entry_slug))
    )
    return {
        "internal_name": internal_name,
        "slug": slug,
        "name": str(payload["name"] or internal_name).strip(),
        "source_country_code": source_country_code,
        "entity_type": str(payload["entity_type"] or "").strip(),
        "code": str(payload["code"] or "").strip(),
        "description": str(metadata.get("description") or data.get("description") or "").strip(),
        "_content": record_content.rstrip() + "\n",
    }


def _legacy_dict_is_subdivision(value: ast.Dict) -> bool:
    keys = {_legacy_key_name(key) for key in value.keys}
    return bool(keys & {"name", "code", "entity_type", "capitals", "spec", "childs", "children"})


def _legacy_subdivision_record(
    *,
    internal_name: str,
    value: ast.Dict,
    source_country_code: str,
    path: Path,
) -> dict:
    source_country_code = _clean_country_code(source_country_code)
    payload = _legacy_subdivision_payload(value, source_country_code=source_country_code)
    inferred_source_country_code = _legacy_subdivision_primary_source_country(
        payload,
        default_country_code=source_country_code,
    )
    if inferred_source_country_code != source_country_code:
        source_country_code = inferred_source_country_code
        payload = _legacy_subdivision_payload(value, source_country_code=source_country_code)
    name = str(payload.get("name") or internal_name).strip()
    entry_slug = normalize_seed_slug(internal_name.lower())
    record = {
        "internal_name": internal_name,
        "slug": _derived_subdivision_record_slug(source_country_code, entry_slug),
        "name": name,
        "source_country_code": source_country_code,
        "entity_type": str(payload.get("entity_type") or "").strip(),
        "code": str(payload.get("code") or "").strip(),
        "description": _("Importada desde %(path)s.") % {"path": path},
    }
    record["_content"] = _render_derived_subdivision_toml(
        internal_name=internal_name,
        source_country_code=source_country_code,
        payload=payload,
    )
    return record


def _legacy_subdivision_primary_source_country(payload: dict, *, default_country_code: str) -> str:
    countries = {
        _clean_country_code(block.get("country_code"))
        for block in payload.get("include") or []
        if isinstance(block, dict) and _clean_country_code(block.get("country_code"))
    }
    if len(countries) == 1:
        return next(iter(countries))
    return _clean_country_code(default_country_code)


def _legacy_subdivision_payload(value: ast.Dict, *, source_country_code: str) -> dict:
    spec = _legacy_dict_get(value, "spec")
    children_node = _legacy_dict_get(value, "childs") or _legacy_dict_get(value, "children")
    children = []
    if isinstance(children_node, ast.List):
        for child in children_node.elts:
            if isinstance(child, ast.Dict):
                children.append(_legacy_subdivision_payload(child, source_country_code=source_country_code))
    blocks = _legacy_spec_blocks(spec, default_country_code=source_country_code) if isinstance(spec, ast.Dict) else []
    return {
        "name": _legacy_string(value, "name"),
        "code": _legacy_string(value, "code"),
        "entity_type": _legacy_string(value, "entity_type"),
        "level": 1,
        "generic_name": _legacy_string(value, "generic_name"),
        "capitals": _legacy_string_list(_legacy_dict_get(value, "capitals")),
        "flag_url": _legacy_string(value, "flag_url"),
        "coat_url": _legacy_string(value, "coat_url"),
        "capital_groups": [],
        "include": [block for block in blocks if block["operation"] == "include"],
        "subtract": [block for block in blocks if block["operation"] == "subtract"],
        "children": children,
    }


def _legacy_spec_blocks(spec: ast.Dict, *, default_country_code: str) -> list[dict]:
    blocks: list[dict] = []
    for key, value in zip(spec.keys, spec.values):
        key_name = _legacy_key_name(key)
        if key_name in {"restar", "subtract"} and isinstance(value, ast.Dict):
            for subtract_level, country_map in zip(value.keys, value.values):
                level = _legacy_int_key(subtract_level)
                if level is not None:
                    blocks.extend(
                        _legacy_country_map_blocks(
                            country_map,
                            operation="subtract",
                            level=level,
                            default_country_code=default_country_code,
                        )
                    )
            continue
        level = _legacy_int_key(key)
        if level is None:
            continue
        blocks.extend(
            _legacy_country_map_blocks(
                value,
                operation="include",
                level=level,
                default_country_code=default_country_code,
            )
        )
    return blocks


def _legacy_country_map_blocks(
    value: ast.AST,
    *,
    operation: str,
    level: int,
    default_country_code: str,
) -> list[dict]:
    if not isinstance(value, ast.Dict):
        refs = _legacy_expr_refs(value)
        return [
            {
                "operation": operation,
                "country_code": _clean_country_code(default_country_code),
                "level": level,
                **refs,
            }
        ]
    blocks = []
    for country_key, expression in zip(value.keys, value.values):
        refs = _legacy_expr_refs(expression)
        country_code = _clean_country_code(_legacy_key_name(country_key) or default_country_code)
        blocks.append({"operation": operation, "country_code": country_code, "level": level, **refs})
    return blocks


def _legacy_expr_refs(value: ast.AST) -> dict[str, list[str]]:
    names: list[str] = []
    groups: list[str] = []
    expressions: list[str] = []

    def add_name(text: str) -> None:
        text = str(text or "").strip()
        if text and text not in names:
            names.append(text)

    def add_group(text: str) -> None:
        text = _safe_group_toml_key(text)
        if text and text not in groups:
            groups.append(text)

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                add_name(node.value)
            return
        if isinstance(node, ast.Name):
            add_group(node.id)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                visit(item)
            return
        try:
            expression = ast.unparse(node)
        except Exception:  # noqa: BLE001 - best-effort preservation of legacy expressions.
            expression = ""
        if expression and expression not in expressions:
            expressions.append(expression)

    visit(value)
    return {"names": names, "groups": groups, "expressions": expressions}


def _legacy_dict_get(value: ast.Dict, key_name: str) -> ast.AST | None:
    for key, item in zip(value.keys, value.values):
        if _legacy_key_name(key) == key_name:
            return item
    return None


def _legacy_key_name(value: ast.AST | None) -> str:
    if isinstance(value, ast.Constant):
        return str(value.value)
    return ""


def _legacy_int_key(value: ast.AST | None) -> int | None:
    if isinstance(value, ast.Constant):
        try:
            return int(value.value)
        except (TypeError, ValueError):
            return None
    return None


def _legacy_string(value: ast.Dict, key_name: str) -> str:
    node = _legacy_dict_get(value, key_name)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return str(node.value).strip()
    return ""


def _legacy_string_list(value: ast.AST | None) -> list[str]:
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return []
    result = []
    for item in value.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            text = str(item.value).strip()
            if text:
                result.append(text)
    return result


def _render_derived_subdivision_toml(*, internal_name: str, source_country_code: str, payload: dict) -> str:
    lines = [
        "schema_version = 1",
        'kind = "derived_subdivision"',
        f"internal_name = {_toml_string(internal_name)}",
        f"source_country_code = {_toml_string(_clean_country_code(source_country_code))}",
        f"name = {_toml_string(payload.get('name') or internal_name)}",
        f"code = {_toml_string(payload.get('code') or '')}",
        f"entity_type = {_toml_string(payload.get('entity_type') or '')}",
        f"level = {int(payload.get('level') or 1)}",
    ]
    if "use_selected_entities_as_children" in payload:
        lines.append(f"use_selected_entities_as_children = {_toml_bool(_form_bool(payload.get('use_selected_entities_as_children')))}")
    lines.extend(
        [
            f"generic_name = {_toml_string(payload.get('generic_name') or '')}",
            f"capitals = {_toml_array([str(item) for item in payload.get('capitals') or []])}",
        ]
    )
    if payload.get("flag_url"):
        lines.append(f"flag_url = {_toml_string(payload.get('flag_url') or '')}")
    if payload.get("coat_url"):
        lines.append(f"coat_url = {_toml_string(payload.get('coat_url') or '')}")
    lines.append("")
    _append_derived_subdivision_capital_groups(lines, payload.get("capital_groups") or [])
    _append_derived_subdivision_blocks(lines, "include", payload.get("include") or [])
    _append_derived_subdivision_blocks(lines, "subtract", payload.get("subtract") or [])
    for child in payload.get("children") or []:
        lines.extend(
            [
                "[[children]]",
                f"name = {_toml_string(child.get('name') or '')}",
                f"code = {_toml_string(child.get('code') or '')}",
                f"entity_type = {_toml_string(child.get('entity_type') or '')}",
                f"level = {int(child.get('level') or 1)}",
            ]
        )
        if "use_selected_entities_as_children" in child:
            lines.append(f"use_selected_entities_as_children = {_toml_bool(_form_bool(child.get('use_selected_entities_as_children')))}")
        lines.extend(
            [
                f"generic_name = {_toml_string(child.get('generic_name') or '')}",
                f"capitals = {_toml_array([str(item) for item in child.get('capitals') or []])}",
                "",
            ]
        )
        _append_derived_subdivision_capital_groups(lines, child.get("capital_groups") or [], table_name="children.capital_groups")
        _append_derived_subdivision_blocks(lines, "children.include", child.get("include") or [])
        _append_derived_subdivision_blocks(lines, "children.subtract", child.get("subtract") or [])
    return "\n".join(lines).rstrip() + "\n"


def _append_derived_subdivision_capital_groups(
    lines: list[str],
    blocks: list[dict],
    *,
    table_name: str = "capital_groups",
) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_toml_string(block.get('country_code') or '')}",
                f"level = {int(block.get('level') or 0)}",
                f"group = {_toml_string(block.get('group') or block.get('group_key') or '')}",
                f"names = {_toml_array([str(item) for item in block.get('names') or []])}",
                f"capital_name = {_toml_string(block.get('capital_name') or block.get('display_name') or '')}",
                "",
            ]
        )


def _append_derived_subdivision_blocks(lines: list[str], table_name: str, blocks: list[dict]) -> None:
    for block in blocks:
        lines.extend(
            [
                f"[[{table_name}]]",
                f"country_code = {_toml_string(block.get('country_code') or '')}",
                f"level = {int(block.get('level') or 0)}",
                f"names = {_toml_array([str(item) for item in block.get('names') or []])}",
                f"ids = {_toml_array([str(item) for item in block.get('ids') or []])}",
                f"codes = {_toml_array([str(item) for item in block.get('codes') or []])}",
                f"groups = {_toml_array([_safe_group_toml_key(item) for item in block.get('groups') or []])}",
                f"derived_subdivisions = {_toml_array([_safe_group_toml_key(item) for item in block.get('derived_subdivisions') or []])}",
                f"expressions = {_toml_array([str(item) for item in block.get('expressions') or []])}",
                "",
            ]
        )


def _parse_subdivision_group_seed(content: str, *, path: Path) -> dict:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(path)s: TOML invalido: %(error)s") % {"path": path, "error": exc}) from exc
    if not isinstance(data, dict):
        raise ValueError(_("%(path)s: TOML invalido.") % {"path": path})

    default_country_code = _group_seed_country_from_path(path)
    if default_country_code and not _clean_country_code(data.get("source_country_code") or ""):
        data["source_country_code"] = default_country_code

    kind = str(data.get("kind") or "").strip()
    if kind and kind != "subdivision_group":
        raise ValueError(_("%(path)s: kind debe ser '%(kind)s'.") % {"path": path, "kind": "subdivision_group"})

    if kind:
        assignments = _top_level_group_assignments(data)
        if assignments and not data.get("internal_name"):
            data["internal_name"] = _safe_group_toml_key(assignments[0][0])
        return data

    assignments = _top_level_group_assignments(data)
    if len(assignments) != 1:
        raise ValueError(
            _(
                "%(path)s: los TOML de grupos deben definir kind = 'subdivision_group' "
                "o una unica asignacion NOMBRE = [municipios]."
            )
            % {"path": path}
    )
    internal_name, names = assignments[0]
    source_country_code = _clean_country_code(data.get("source_country_code") or default_country_code)
    entry_slug = normalize_seed_slug(str(data.get("entry_slug") or _safe_group_toml_key(internal_name).lower()))
    return {
        "schema_version": 1,
        "slug": _country_group_record_slug(source_country_code, entry_slug),
        "entry_slug": entry_slug,
        "internal_name": _safe_group_toml_key(internal_name),
        "name": str(data.get("name") or _safe_group_toml_key(internal_name)).strip(),
        "description": str(data.get("description") or "").strip(),
        "source_country_code": source_country_code,
        "selection": {
            "source_country_code": source_country_code,
            "include_names": names,
        },
    }


def _normalized_group_seed_content(group: SubdivisionGroup) -> str:
    content = str(group.content or "").strip()
    if not content:
        return content
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return content
    if not isinstance(data, dict):
        return content

    internal_name = _safe_group_toml_key(data.get("internal_name") or _single_group_assignment_name(data) or _subdivision_group_file_stem(group))

    names = _group_selected_names_from_seed_data(data, internal_name=internal_name)
    source_country_code = _clean_country_code(group.source_country_code or data.get("source_country_code") or "")
    lines = [f"source_country_code = {_toml_string(source_country_code)}", "", f"{internal_name} = {_toml_array(names)}"]
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    country_groups = selection.get("country_groups") if isinstance(selection, dict) else None
    if isinstance(country_groups, list):
        lines.append("")
        for block in country_groups:
            if not isinstance(block, dict):
                continue
            block_country = _clean_country_code(block.get("country_code") or source_country_code)
            lines.extend(
                [
                    "[[country_groups]]",
                    f"country_code = {_toml_string(block_country)}",
                ]
            )
            for section in block.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                selected_names = section.get("selected_names") or []
                selected_ids = section.get("selected_ids") or []
                if not selected_names and isinstance(section.get("selected"), list):
                    selected_names = [
                        str(item.get("name") or "")
                        for item in section["selected"]
                        if isinstance(item, dict) and str(item.get("name") or "").strip()
                    ]
                    selected_ids = [
                        str(item.get("id") or "")
                        for item in section["selected"]
                        if isinstance(item, dict) and str(item.get("name") or "").strip()
                    ]
                lines.extend(
                    [
                        "",
                        "[[country_groups.sections]]",
                        f"area_id = {_toml_string(section.get('area_id') or '')}",
                        f"selected_ids = {_toml_array([str(item or '') for item in selected_ids])}",
                        f"selected_names = {_toml_array([str(item or '') for item in selected_names])}",
                    ]
                )
            lines.append("")
    return "\n".join(lines).rstrip()


def _normalized_group_bundle_seed_content(country_code: str, groups: list[SubdivisionGroup]) -> str:
    country_code = _safe_seed_dir_name(country_code)
    payloads: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for group in groups:
        content = str(group.content or "").strip()
        data = {}
        if content:
            try:
                parsed = tomllib.loads(content)
            except tomllib.TOMLDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                data = parsed
        internal_name = _safe_group_toml_key(
            data.get("internal_name") or _single_group_assignment_name(data) or _subdivision_group_file_stem(group)
        )
        entry_slug = normalize_seed_slug(internal_name.lower())
        if entry_slug in seen:
            raise ValueError(
                _("El grupo '%(name)s' esta repetido para el pais '%(country)s'.")
                % {"name": internal_name, "country": country_code}
            )
        seen.add(entry_slug)
        payloads.append((internal_name, _group_selected_names_from_seed_data(data, internal_name=internal_name)))

    lines = [f"source_country_code = {_toml_string(country_code)}", ""]
    for index, (internal_name, names) in enumerate(sorted(payloads, key=lambda item: item[0])):
        lines.append(f"{internal_name} = {_toml_multiline_array(names)}")
        if index < len(payloads) - 1:
            lines.append("")
    return "\n".join(lines).rstrip()


def _normalized_derived_subdivision_bundle_seed_content(
    country_code: str,
    records: list[DerivedSubdivision],
) -> str:
    country_code = _safe_seed_dir_name(country_code)
    payloads: list[tuple[str, DerivedSubdivision, str]] = []
    seen: set[str] = set()
    for record in records:
        internal_name = _safe_group_toml_key(record.internal_name or _derived_subdivision_file_stem(record))
        entry_slug = normalize_seed_slug(internal_name.lower())
        if entry_slug in seen:
            raise ValueError(
                _("La subdivision '%(name)s' esta repetida para el pais '%(country)s'.")
                % {"name": internal_name, "country": country_code}
            )
        seen.add(entry_slug)
        content = str(record.content or "").strip()
        if not content:
            content = _render_derived_subdivision_toml(
                internal_name=internal_name,
                source_country_code=record.source_country_code or country_code,
                payload={
                    "name": record.name or internal_name,
                    "code": record.code or "",
                    "entity_type": record.entity_type or "",
                    "capitals": [],
                    "include": [],
                    "subtract": [],
                    "children": [],
                },
            ).strip()
        payloads.append((internal_name, record, content))

    lines = [
        "schema_version = 1",
        'kind = "derived_subdivision_bundle"',
        f"source_country_code = {_toml_string(country_code)}",
        "",
    ]
    for index, (internal_name, record, content) in enumerate(sorted(payloads, key=lambda item: item[0])):
        lines.extend(
            [
                "[[subdivisions]]",
                f"internal_name = {_toml_string(internal_name)}",
                f"name = {_toml_string(record.name or internal_name)}",
                f"code = {_toml_string(record.code or '')}",
                f"entity_type = {_toml_string(record.entity_type or '')}",
            ]
        )
        if record.description:
            lines.append(f"description = {_toml_string(record.description)}")
        lines.append(f"content = {_toml_string(content.rstrip() + chr(10))}")
        if index < len(payloads) - 1:
            lines.append("")
    return "\n".join(lines).rstrip()


def _single_group_seed_content(*, source_country_code: str, internal_name: str, names: list[str]) -> str:
    return "\n".join(
        [
            f"source_country_code = {_toml_string(_clean_country_code(source_country_code))}",
            "",
            f"{_safe_group_toml_key(internal_name)} = {_toml_array(names)}",
            "",
        ]
    )


def _top_level_group_assignments(data: dict) -> list[tuple[str, list[str]]]:
    assignments: list[tuple[str, list[str]]] = []
    for key, value in data.items():
        if key in GROUP_SEED_METADATA_KEYS:
            continue
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            assignments.append((str(key), [str(item) for item in value]))
    return assignments


def _single_group_assignment_name(data: dict) -> str:
    assignments = _top_level_group_assignments(data)
    return assignments[0][0] if len(assignments) == 1 else ""


def _group_selected_names_from_seed_data(data: dict, *, internal_name: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add_many(values) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)

    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    add_many(selection.get("include_names") if isinstance(selection, dict) else [])
    country_groups_sources = []
    country_groups = selection.get("country_groups") if isinstance(selection, dict) else None
    if isinstance(country_groups, list):
        country_groups_sources.append(country_groups)
    root_country_groups = data.get("country_groups") if isinstance(data.get("country_groups"), list) else None
    if isinstance(root_country_groups, list):
        country_groups_sources.append(root_country_groups)
    for country_groups in country_groups_sources:
        for block in country_groups:
            if not isinstance(block, dict):
                continue
            add_many(block.get("include_names") or block.get("names") or [])
            sections = block.get("sections") or []
            if not isinstance(sections, list):
                continue
            for section in sections:
                if isinstance(section, dict):
                    add_many(section.get("selected_names") or [])

    if not names:
        assignment = data.get(internal_name)
        if isinstance(assignment, list):
            add_many(assignment)
    if not names:
        for _key, values in _top_level_group_assignments(data):
            add_many(values)
            break
    return names


def _subdivision_group_country_dir_name(group: SubdivisionGroup) -> str:
    content = str(group.content or "")
    data = {}
    if content:
        try:
            parsed = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    country_code = _clean_country_code(group.source_country_code or data.get("source_country_code") or "")
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    if not country_code and isinstance(selection, dict):
        country_code = _clean_country_code(selection.get("source_country_code") or "")
    return _safe_seed_dir_name(country_code or "unknown")


def _subdivision_group_file_stem(group: SubdivisionGroup) -> str:
    content = str(group.content or "")
    data = {}
    if content:
        try:
            parsed = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    internal_name = _safe_group_toml_key(data.get("internal_name") or _single_group_assignment_name(data) or group.slug)
    return normalize_seed_slug(internal_name.lower())


def _derived_subdivision_country_dir_name(record: DerivedSubdivision) -> str:
    content = str(record.content or "")
    data = {}
    if content:
        try:
            parsed = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    country_code = _clean_country_code(record.source_country_code or data.get("source_country_code") or "")
    return _safe_seed_dir_name(country_code or "unknown")


def _derived_subdivision_file_stem(record: DerivedSubdivision) -> str:
    content = str(record.content or "")
    data = {}
    if content:
        try:
            parsed = tomllib.loads(content)
        except tomllib.TOMLDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed
    internal_name = str(data.get("internal_name") or record.internal_name or "").strip()
    if internal_name:
        return normalize_seed_slug(_safe_group_toml_key(internal_name).lower())
    country_code = _safe_seed_dir_name(record.source_country_code or "")
    slug = normalize_seed_slug(record.slug or "")
    if country_code and slug.startswith(f"{country_code}_"):
        return slug[len(country_code) + 1 :]
    return slug or "subdivision"


def _country_group_record_slug(source_country_code: str, entry_slug: str) -> str:
    source_country_code = _safe_seed_dir_name(source_country_code)
    entry_slug = normalize_seed_slug(entry_slug)
    return normalize_seed_slug(f"{source_country_code}_{entry_slug}") if source_country_code else entry_slug


def _derived_subdivision_record_slug(source_country_code: str, entry_slug: str) -> str:
    source_country_code = _safe_seed_dir_name(source_country_code)
    entry_slug = normalize_seed_slug(entry_slug)
    return normalize_seed_slug(f"{source_country_code}_{entry_slug}") if source_country_code else entry_slug


def _subdivision_group_duplicate_for_key(
    *,
    source_country_code: str,
    entry_slug: str,
    exclude_slug: str = "",
) -> SubdivisionGroup | None:
    source_country_code = _clean_country_code(source_country_code)
    entry_slug = normalize_seed_slug(entry_slug)
    expected_slug = _country_group_record_slug(source_country_code, entry_slug)
    exclude_slug = str(exclude_slug or "").strip()
    candidates = SubdivisionGroup.objects.filter(source_country_code__iexact=source_country_code)
    if exclude_slug:
        candidates = candidates.exclude(slug=exclude_slug)
    for group in candidates:
        if group.slug == expected_slug:
            return group
        try:
            data = tomllib.loads(group.content or "")
        except tomllib.TOMLDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        names = {
            normalize_seed_slug(_safe_group_toml_key(name).lower())
            for name, _values in _top_level_group_assignments(data)
        }
        internal_name = str(data.get("internal_name") or "").strip()
        if internal_name:
            names.add(normalize_seed_slug(_safe_group_toml_key(internal_name).lower()))
        entry = str(data.get("entry_slug") or "").strip()
        if entry:
            names.add(normalize_seed_slug(entry))
        if entry_slug in names:
            return group
    return None


def _safe_seed_dir_name(value) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return name or "unknown"


def _group_seed_country_from_path(path: Path) -> str:
    path = Path(path)
    if path.parent.name == SUBDIVISION_GROUP_GROUP_DIRNAME:
        return _safe_seed_dir_name(path.stem)
    if path.parent.parent.name == SUBDIVISION_GROUP_GROUP_DIRNAME:
        return _safe_seed_dir_name(path.parent.name)
    return ""


def _derived_subdivision_seed_country_from_path(path: Path) -> str:
    path = Path(path)
    if path.parent.name == SUBDIVISION_GROUP_SUBDIVISION_DIRNAME:
        return _safe_seed_dir_name(path.stem)
    try:
        if path.parent.parent.name == SUBDIVISION_GROUP_SUBDIVISION_DIRNAME:
            return _safe_seed_dir_name(path.parent.name)
    except IndexError:
        return ""
    return ""


def _insert_top_level_group_assignment(content: str, assignment: str) -> str:
    lines = content.splitlines()
    insert_at = len(lines)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            insert_at = index
            break
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    inserted = ["", assignment] if insert_at > 0 else [assignment]
    lines[insert_at:insert_at] = inserted
    return "\n".join(lines)


def _safe_group_toml_key(value) -> str:
    key = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
    if not key:
        key = "GROUP"
    if not re.match(r"^[A-Za-z]", key):
        key = f"GROUP_{key}"
    return key.upper()


def _clean_country_code(value) -> str:
    return str(value or "").strip().lower()


def _title_from_slug(slug: str) -> str:
    return str(slug or "").replace("_", " ").title()


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _form_bool(value) -> bool:
    text = str(value if value is not None else "").strip().lower()
    return text in {"1", "true", "yes", "si", "sí", "on"}


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _toml_multiline_array(values: list[str]) -> str:
    if not values:
        return "[]"
    lines = ["["]
    lines.extend(f"    {_toml_string(value)}," for value in values)
    lines.append("]")
    return "\n".join(lines)


def _toml_int_array(values: list[int]) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def _clean_levels(values) -> list[int]:
    levels: list[int] = []
    for value in values:
        try:
            level = int(value)
        except (TypeError, ValueError):
            raise ValueError(_("El nivel debe ser numerico.")) from None
        if level < 0 or level > 9:
            raise ValueError(_("El nivel debe estar entre 0 y 9."))
        if level not in levels:
            levels.append(level)
    return levels


def _has_selected_ancestor(area: AdminArea, selected_ids: set[str]) -> bool:
    current = area.parent
    while current is not None:
        if str(current.id) in selected_ids:
            return True
        current = current.parent
    return False
