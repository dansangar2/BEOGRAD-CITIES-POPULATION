"""Build ``NuevoAdminArea`` trees from SQL ``DerivedCountryConfig`` rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import tomllib

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext as _

from ciudades_del_mundo.models import AdminArea, DerivedCountryConfig, NuevoAdminArea
from ciudades_del_mundo.services.derived_codes import derived_country_root_code
from ciudades_del_mundo.services.derived_subdivision_builder import (
    _BuildContext,
    _build_node,
    _child_code_for_context,
    _clean_country_code,
    _deepest_source_level,
    _density,
    _refresh_container_totals,
)
from ciudades_del_mundo.services.nuevo_admin_builder import effective_source_municipal_level
from ciudades_del_mundo.services.source_population_indices import (
    SourcePopulationIndexConfigError,
    load_source_population_index_registry,
)


@dataclass(frozen=True)
class DerivedCountryConfigBuildResult:
    config: DerivedCountryConfig
    country_code: str
    root_id: str
    entities: int
    built: int
    most_populated_updated: int


def resolve_derived_country_config_for_country_id(country_id: str) -> DerivedCountryConfig | None:
    """Return the SQL new-country config addressed by a build/export country id."""
    country_id = _clean_country_code(country_id)
    if not country_id:
        return None

    queryset = DerivedCountryConfig.objects.select_related("country").order_by(
        "-is_active",
        "country__name",
        "name",
        "slug",
    )
    for filters in (
        {"derived_country_code__iexact": country_id},
        {"slug__iexact": country_id},
        {"country_id__iexact": country_id},
    ):
        config = queryset.filter(**filters).first()
        if config:
            return config
    return None


def build_derived_country_config_for_country_id(
    country_id: str,
    *,
    force: bool = True,
    source_population_year: int | None = None,
) -> DerivedCountryConfigBuildResult:
    """Resolve and materialize a SQL new-country config by logical country id."""
    config = resolve_derived_country_config_for_country_id(country_id)
    if config is None:
        raise ValueError(_("No hay configuracion SQL de pais nuevo para '%(country)s'.") % {"country": country_id})
    return build_derived_country_config(
        config,
        force=force,
        source_population_year=source_population_year,
    )


def build_derived_country_config(
    config: DerivedCountryConfig,
    *,
    force: bool = True,
    source_population_year: int | None = None,
) -> DerivedCountryConfigBuildResult:
    """Materialize one SQL ``DerivedCountryConfig`` as a ``NuevoAdminArea`` tree."""
    data = _parse_config_toml(config)
    source_country = _clean_country_code(
        data.get("source_country_code")
        or config.source_country_code
        or config.country.source_country_code
    )
    if not source_country:
        raise ValueError(_("La configuracion %(config)s no tiene pais fuente.") % {"config": config.full_slug})
    derived_country_code = _clean_country_code(
        data.get("derived_country_code") or config.derived_country_code or config.slug
    )
    if not derived_country_code:
        raise ValueError(_("La configuracion %(config)s no tiene codigo derivado.") % {"config": config.full_slug})

    try:
        population_index_registry = load_source_population_index_registry()
    except SourcePopulationIndexConfigError as exc:
        raise ValueError(str(exc)) from exc

    with transaction.atomic():
        build_items = _build_group_configs(config, data, derived_country_code)
        root = _ensure_root(config, data, source_country=source_country, derived_country_code=derived_country_code)
        existing = NuevoAdminArea.objects.filter(country_code=derived_country_code).exclude(id=root.id)
        if existing.exists():
            if not force:
                raise ValueError(
                    _("%(country)s ya tiene filas NuevoAdminArea. Usa force para reconstruirlo.")
                    % {"country": derived_country_code}
                )
            existing.delete()

        context = _BuildContext(
            country_code=source_country,
            source_population_year=source_population_year,
            source_population_index_registry=population_index_registry,
            group_cache={},
            derived_subdivision_cache={},
            derived_subdivision_stack=set(),
            root_child_uses_local_code=True,
        )
        entities_total = 0
        built = 0
        built_nodes: dict[str, NuevoAdminArea] = {}
        pending = list(build_items)
        while pending:
            progressed = False
            pending_slugs = {candidate.slug for candidate, _data in pending}
            for item_config, item_data in list(pending):
                parent_slug = _config_parent_slug(item_data)
                if parent_slug and parent_slug not in pending_slugs and parent_slug not in built_nodes:
                    raise ValueError(
                        _("%(config)s: el padre '%(parent)s' no existe en este pais nuevo.")
                        % {"config": item_config.full_slug, "parent": parent_slug}
                    )
                if parent_slug and parent_slug not in built_nodes:
                    continue
                parent = built_nodes.get(parent_slug) or root
                item_source_country = _clean_country_code(
                    item_data.get("source_country_code")
                    or item_config.source_country_code
                    or item_config.country.source_country_code
                    or source_country
                )
                entities = _country_entities_from_config(item_config, item_data, source_country=item_source_country)
                first_node = None
                for index, entity in enumerate(entities, start=1):
                    entity.setdefault("source_country_code", item_source_country)
                    entity["level"] = int(parent.level or 0) + 1
                    expected_code = _child_code_for_context(
                        parent,
                        str(entity.get("code") or entity.get("internal_name") or entity.get("name") or item_config.slug),
                        context,
                    )
                    built += _build_node(
                        entity,
                        parent_id=parent.id,
                        context=context,
                        label=f"{item_config.full_slug}.entities[{index}]",
                    )
                    if first_node is None:
                        first_node = (
                            NuevoAdminArea.objects.filter(
                                country_code=derived_country_code,
                                parent_id=parent.id,
                                code=expected_code,
                            )
                            .order_by("id")
                            .first()
                        )
                entities_total += len(entities)
                if first_node is not None:
                    built_nodes[item_config.slug] = first_node
                pending.remove((item_config, item_data))
                progressed = True
            if not progressed:
                raise ValueError(_("%(country)s tiene padres circulares entre sus divisiones.") % {"country": config.country_id})
        _refresh_container_totals(root)

    from ciudades_del_mundo.services.nuevo_admin_builder import refresh_nuevo_admin_most_populated

    updated = refresh_nuevo_admin_most_populated(derived_country_code)
    return DerivedCountryConfigBuildResult(
        config=config,
        country_code=derived_country_code,
        root_id=root.id,
        entities=entities_total,
        built=built,
        most_populated_updated=updated,
    )


def _build_group_configs(
    config: DerivedCountryConfig,
    data: dict[str, Any],
    derived_country_code: str,
) -> list[tuple[DerivedCountryConfig, dict[str, Any]]]:
    rows: list[tuple[DerivedCountryConfig, dict[str, Any]]] = []
    for candidate in config.country.configs.order_by("name", "slug"):
        candidate_data = data if candidate.pk == config.pk else _parse_config_toml(candidate)
        candidate_code = _clean_country_code(
            candidate_data.get("derived_country_code")
            or candidate.derived_country_code
            or candidate.slug
        )
        if candidate_code == derived_country_code:
            rows.append((candidate, candidate_data))
    if not any(candidate.pk == config.pk for candidate, _data in rows):
        rows.append((config, data))
    return rows


def _config_parent_slug(data: dict[str, Any]) -> str:
    for entity in data.get("entities") or []:
        if isinstance(entity, dict):
            parent = _clean_country_code(entity.get("parent_config_slug") or entity.get("parent"))
            if parent:
                return parent
    return _clean_country_code(data.get("parent_config_slug") or data.get("parent"))


def _parse_config_toml(config: DerivedCountryConfig) -> dict[str, Any]:
    try:
        data = tomllib.loads(config.content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_("%(config)s: TOML invalido: %(error)s") % {"config": config.full_slug, "error": exc}) from exc
    return data if isinstance(data, dict) else {}


def _ensure_root(
    config: DerivedCountryConfig,
    data: dict[str, Any],
    *,
    source_country: str,
    derived_country_code: str,
) -> NuevoAdminArea:
    source_root = AdminArea.objects.filter(country_code__iexact=source_country, level=0).order_by("id").first()
    root_name = str(
        data.get("country_name")
        or data.get("root_name")
        or config.country.name
        or data.get("name")
        or config.name
    ).strip()
    root_code = derived_country_root_code(
        derived_country_code,
        data.get("root_code") or data.get("code") or derived_country_code,
    )
    municipal_level = _config_municipal_level(data, source_country)
    root, _created = NuevoAdminArea.objects.update_or_create(
        id=derived_country_code,
        defaults={
            "country_code": derived_country_code,
            "code": root_code,
            "name": root_name or derived_country_code.replace("_", " ").title(),
            "level": NuevoAdminArea.Level.COUNTRY,
            "entity_type": "Country",
            "parent": None,
            "area_km2": source_root.area_km2 if source_root and not _has_configured_entities(data) else None,
            "density": (
                _density(source_root.pop_latest, source_root.area_km2)
                if source_root and not _has_configured_entities(data)
                else None
            ),
            "pop_latest": source_root.pop_latest if source_root and not _has_configured_entities(data) else None,
            "municipal_level": municipal_level,
        },
    )
    return root


def _config_municipal_level(data: dict[str, Any], source_country: str) -> int | None:
    raw = data.get("municipal_level")
    if raw not in (None, ""):
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(_("municipal_level debe ser numerico.")) from exc
    configured_level = effective_source_municipal_level(source_country)
    if configured_level is not None:
        return configured_level
    return _deepest_source_level(source_country)


def _has_configured_entities(data: dict[str, Any]) -> bool:
    return bool(data.get("entities") or data.get("children") or data.get("selection"))


def _country_entities_from_config(
    config: DerivedCountryConfig,
    data: dict[str, Any],
    *,
    source_country: str,
) -> list[dict[str, Any]]:
    entities = data.get("entities")
    if entities is None:
        entities = data.get("children")
    if isinstance(entities, list) and all(isinstance(entity, dict) for entity in entities):
        return [dict(entity) for entity in entities]
    if entities not in (None, ""):
        raise ValueError(_("%(config)s: entities debe ser una lista TOML.") % {"config": config.full_slug})

    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    if selection:
        return [_entity_from_selection(config, data, selection, source_country=source_country)]
    return []


def _entity_from_selection(
    config: DerivedCountryConfig,
    data: dict[str, Any],
    selection: dict[str, Any],
    *,
    source_country: str,
) -> dict[str, Any]:
    entity = {
        "mode": "final",
        "name": str(data.get("entity_name") or config.name or data.get("name") or config.slug).strip(),
        "code": str(data.get("entity_code") or config.slug).strip(),
        "entity_type": str(data.get("entity_type") or _("Subdivision")).strip(),
        "level": 1,
        "capitals": _string_list(data.get("capitals") or data.get("capital_names")),
        "use_selected_entities_as_children": False,
    }
    include_blocks = _selection_blocks(selection, "add", source_country=source_country)
    subtract_blocks = _selection_blocks(selection, "subtract", source_country=source_country)
    if include_blocks:
        entity["include"] = include_blocks
    if subtract_blocks:
        entity["subtract"] = subtract_blocks
    return entity


def _selection_blocks(selection: dict[str, Any], operation: str, *, source_country: str) -> list[dict[str, Any]]:
    items = [
        item
        for item in selection.get("items") or []
        if isinstance(item, dict) and str(item.get("operation") or "add").strip().lower() == operation
    ]
    blocks: dict[tuple[str, int], dict[str, Any]] = {}
    for item in items:
        country = _clean_country_code(item.get("country_code") or source_country)
        try:
            level = int(item.get("level"))
        except (TypeError, ValueError):
            continue
        block = blocks.setdefault((country, level), {"country_code": country, "level": level, "ids": []})
        item_id = str(item.get("id") or "").strip()
        if item_id:
            block["ids"].append(item_id)
    if blocks:
        return list(blocks.values())

    ids_key = "subtract_ids" if operation == "subtract" else "include_ids"
    return _area_id_blocks(_string_list(selection.get(ids_key)), source_country=source_country)


def _area_id_blocks(area_ids: list[str], *, source_country: str) -> list[dict[str, Any]]:
    if not area_ids:
        return []
    try:
        areas = list(AdminArea.objects.filter(id__in=area_ids).only("id", "country_code", "level"))
    except (OperationalError, ProgrammingError):
        areas = []
    found = {str(area.id): area for area in areas}
    blocks: dict[tuple[str, int], dict[str, Any]] = {}
    for area_id in area_ids:
        area = found.get(str(area_id))
        if area is None:
            continue
        country = _clean_country_code(area.country_code or source_country)
        level = int(area.level or 0)
        block = blocks.setdefault((country, level), {"country_code": country, "level": level, "ids": []})
        block["ids"].append(str(area.id))
    return list(blocks.values())


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []
