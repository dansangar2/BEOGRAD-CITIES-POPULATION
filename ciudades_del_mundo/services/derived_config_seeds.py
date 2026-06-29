"""SQL import helpers for derived-country and subdivision-group TOML seeds."""

from __future__ import annotations

from pathlib import Path
import json
import re
import tomllib

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, SubdivisionGroup


PACKAGE_ROOT = Path(settings.BASE_DIR) / "ciudades_del_mundo"
NEW_COUNTRY_CONFIG_SEED_ROOT = PACKAGE_ROOT / "new_country_configs"
SUBDIVISION_GROUP_SEED_ROOT = PACKAGE_ROOT / "subdivision_groups"
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def bundled_new_country_config_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return TOML seed files for the `/new-countries/` SQL section."""
    return _seed_paths(NEW_COUNTRY_CONFIG_SEED_ROOT, slugs)


def bundled_subdivision_group_paths(slugs: list[str] | tuple[str, ...] | None = None) -> list[Path]:
    """Return TOML seed files for the `/groups/` SQL section."""
    return _seed_paths(SUBDIVISION_GROUP_SEED_ROOT, slugs)


def import_new_country_config_seed(slug: str, *, force: bool = True) -> DerivedCountryConfig:
    """Import one `new_country_configs/<slug>.toml` file into SQL."""
    slug = normalize_seed_slug(slug)
    path = _single_seed_path(NEW_COUNTRY_CONFIG_SEED_ROOT, slug)
    return import_new_country_config_path(path, force=force)


def import_subdivision_group_seed(slug: str, *, force: bool = True) -> SubdivisionGroup:
    """Import one `subdivision_groups/<slug>.toml` file into SQL."""
    slug = normalize_seed_slug(slug)
    path = _single_seed_path(SUBDIVISION_GROUP_SEED_ROOT, slug)
    return import_subdivision_group_path(path, force=force)


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
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    data = _parse_seed(content, expected_kind="subdivision_group", path=path)
    slug = normalize_seed_slug(str(data.get("slug") or path.stem))
    name = str(data.get("name") or _title_from_slug(slug)).strip()
    if not name:
        raise ValueError(_("%(path)s: el nombre es obligatorio.") % {"path": path})

    existing = SubdivisionGroup.objects.filter(slug=slug).first()
    if existing and not force:
        return existing
    group, _created = SubdivisionGroup.objects.update_or_create(
        slug=slug,
        defaults={
            "name": name,
            "description": str(data.get("description") or "").strip(),
            "content": content,
            "source_country_code": _clean_country_code(data.get("source_country_code") or ""),
        },
    )
    return group


def render_derived_country_selection_toml(
    *,
    country_slug: str,
    config_slug: str,
    name: str,
    source_country_code: str,
    derived_country_code: str,
    selected_ids: list[str],
) -> str:
    """Render a structured TOML config from the web tree selection."""
    country_slug = normalize_seed_slug(country_slug)
    config_slug = normalize_seed_slug(config_slug)
    source_country_code = _clean_country_code(source_country_code)
    derived_country_code = _clean_country_code(derived_country_code or config_slug) or config_slug
    selected_ids = [str(value) for value in selected_ids if str(value or "").strip()]
    if not selected_ids:
        raise ValueError(_("Selecciona al menos una subdivision fuente."))

    areas = list(
        AdminArea.objects.filter(id__in=selected_ids, country_code__iexact=source_country_code)
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
        "schema_version = 1",
        'kind = "derived_country_config"',
        f"country = {_toml_string(country_slug)}",
        f"slug = {_toml_string(config_slug)}",
        f"name = {_toml_string(name)}",
        f"source_country_code = {_toml_string(source_country_code)}",
        f"derived_country_code = {_toml_string(derived_country_code)}",
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
                f"code = {_toml_string(item['code'])}",
                f"name = {_toml_string(item['name'])}",
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


def _clean_country_code(value) -> str:
    return str(value or "").strip().lower()


def _title_from_slug(slug: str) -> str:
    return str(slug or "").replace("_", " ").title()


def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _has_selected_ancestor(area: AdminArea, selected_ids: set[str]) -> bool:
    current = area.parent
    while current is not None:
        if str(current.id) in selected_ids:
            return True
        current = current.parent
    return False
