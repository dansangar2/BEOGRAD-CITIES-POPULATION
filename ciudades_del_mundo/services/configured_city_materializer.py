"""Materialize SQL-configured city unifications into existing AdminArea rows."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from ciudades_del_mundo.application.configured_cities import apply_configured_cities
from ciudades_del_mundo.domain import CITY_MERGE_UNIFIED, CityConfig, ScrapedAdminArea
from ciudades_del_mundo.infrastructure.django.admin_area_repository import DjangoAdminAreaRepository
from ciudades_del_mundo.models import AdminArea, ScrapingConfig
from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config


@dataclass(frozen=True)
class ConfiguredCityMaterializationResult:
    """Summary for applying configured city rows without running a scrape."""

    country_code: str
    configs_total: int = 0
    configs_applied: int = 0
    rows_loaded: int = 0
    created: int = 0
    updated: int = 0
    skipped_reason: str = ""


def materialize_configured_cities_for_slug(slug: str) -> ConfiguredCityMaterializationResult:
    """Apply one ScrapingConfig's pending [[cities]] blocks to persisted AdminArea rows."""
    record = ScrapingConfig.objects.filter(slug=str(slug or "").strip()).first()
    if record is None:
        raise ValueError(_("No existe configuracion SQL para el slug '%(slug)s'.") % {"slug": slug})
    return materialize_configured_cities_for_record(record)


def materialize_configured_cities_for_record(record: ScrapingConfig) -> ConfiguredCityMaterializationResult:
    """Apply pending configured cities from a ScrapingConfig record.

    The regular scraper already applies these rules before persistence. This
    helper is for config-editor changes made after a country was populated, so
    the source selector can immediately see the unified city rows.
    """
    config = parse_scraping_job_config(record.slug, record.content)
    configs = list(config.cities or [])
    if not configs:
        return ConfiguredCityMaterializationResult(
            country_code=config.country_code,
            skipped_reason="no_configured_cities",
        )

    restored = _restore_shadowed_source_parents(config.country_code, configs)
    parents_refreshed = _refresh_configured_source_parent_metadata(config.country_code, configs)
    materialized_by_code = _materialized_city_rows(config.country_code, configs)
    refreshed = _refresh_materialized_city_metadata(materialized_by_code, configs)
    pending_configs = [
        city_config
        for city_config in configs
        if not _is_config_already_materialized(materialized_by_code.get(city_config.code), city_config)
    ]
    if not pending_configs:
        return ConfiguredCityMaterializationResult(
            country_code=config.country_code,
            configs_total=len(configs),
            rows_loaded=AdminArea.objects.filter(country_code=config.country_code).count(),
            updated=restored + parents_refreshed + refreshed,
            skipped_reason="already_materialized",
        )

    rows = list(
        AdminArea.objects.filter(country_code=config.country_code)
        .select_related("parent", "most_populate_city")
        .order_by("level", "id")
    )
    if not rows:
        return ConfiguredCityMaterializationResult(
            country_code=config.country_code,
            configs_total=len(configs),
            configs_applied=len(pending_configs),
            skipped_reason="no_admin_areas",
        )

    entities = [_admin_area_to_scraped(row) for row in rows]
    transformed = apply_configured_cities(config.country_code, entities, pending_configs)
    transformed = _deduplicate_entities_by_id(transformed)
    created, updated = DjangoAdminAreaRepository().save_many(config.country_code, transformed)
    return ConfiguredCityMaterializationResult(
        country_code=config.country_code,
        configs_total=len(configs),
        configs_applied=len(pending_configs),
        rows_loaded=len(rows),
        created=created,
        updated=updated + restored + parents_refreshed + refreshed,
    )


def _refresh_configured_source_parent_metadata(country_code: str, configs: list[CityConfig]) -> int:
    changed_by_id: dict[str, AdminArea] = {}
    now = timezone.now()
    for city_config in configs:
        parent_type = str(city_config.parent_entity_type or "").strip()
        if not parent_type:
            continue
        for parent_level, labels in city_config.parent_from.items():
            for label in labels:
                area = _source_parent_for_label(country_code, int(parent_level), label)
                if area is None:
                    continue
                if (area.entity_type or "") == parent_type and (area.raw_entity_type or "") == parent_type:
                    continue
                area.entity_type = parent_type
                area.raw_entity_type = parent_type
                area.updated_at = now
                changed_by_id[str(area.id)] = area
    if not changed_by_id:
        return 0
    with transaction.atomic():
        AdminArea.objects.bulk_update(
            list(changed_by_id.values()),
            ["entity_type", "raw_entity_type", "updated_at"],
        )
    return len(changed_by_id)


def _restore_shadowed_source_parents(country_code: str, configs: list[CityConfig]) -> int:
    changed_by_id: dict[str, AdminArea] = {}
    now = timezone.now()
    for city_config in configs:
        for parent_level, labels in city_config.parent_from.items():
            for label in labels:
                if _source_parent_exists(country_code, int(parent_level), label):
                    continue
                candidate = _shadowed_source_parent_candidate(country_code, label)
                if candidate is None:
                    continue
                candidate.level = int(parent_level)
                candidate.city_merge_status = AdminArea.CityMergeStatus.NONE
                inferred_type = _inferred_source_parent_entity_type(candidate, int(parent_level), city_config)
                if inferred_type:
                    candidate.entity_type = inferred_type
                    candidate.raw_entity_type = inferred_type
                candidate.updated_at = now
                changed_by_id[str(candidate.id)] = candidate
    if not changed_by_id:
        return 0
    with transaction.atomic():
        AdminArea.objects.bulk_update(
            list(changed_by_id.values()),
            ["level", "city_merge_status", "entity_type", "raw_entity_type", "updated_at"],
        )
    return len(changed_by_id)


def _source_parent_exists(country_code: str, parent_level: int, label: str) -> bool:
    return _source_parent_for_label(country_code, parent_level, label) is not None


def _source_parent_for_label(country_code: str, parent_level: int, label: str) -> AdminArea | None:
    target_key = _lookup_key(label)
    if not target_key:
        return None
    rows = AdminArea.objects.filter(country_code=country_code, level=parent_level).only(
        "id",
        "code",
        "name",
        "entity_type",
        "raw_entity_type",
    )
    matches = [row for row in rows if target_key in _admin_area_lookup_keys(row)]
    return matches[0] if len(matches) == 1 else None


def _shadowed_source_parent_candidate(country_code: str, label: str) -> AdminArea | None:
    target_key = _lookup_key(label)
    if not target_key:
        return None
    matches = [
        row
        for row in AdminArea.objects.filter(
            country_code=country_code,
            city_merge_status=CITY_MERGE_UNIFIED,
        )
        .select_related("parent")
        .only("id", "country_code", "code", "name", "level", "entity_type", "raw_entity_type", "parent_id")
        if target_key in _admin_area_lookup_keys(row)
    ]
    return matches[0] if len(matches) == 1 else None


def _inferred_source_parent_entity_type(area: AdminArea, parent_level: int, city_config: CityConfig) -> str:
    configured_type = str(city_config.parent_entity_type or "").strip()
    if configured_type:
        return configured_type
    sibling_types = [
        str(row.entity_type or row.raw_entity_type or "").strip()
        for row in AdminArea.objects.filter(
            country_code=area.country_code,
            parent_id=area.parent_id,
            level=parent_level,
        )
        .exclude(id=area.id)
        .only("entity_type", "raw_entity_type")
        if str(row.entity_type or row.raw_entity_type or "").strip()
    ]
    if not sibling_types:
        return ""
    return max(sorted(set(sibling_types)), key=sibling_types.count)


def _admin_area_lookup_keys(area: AdminArea) -> set[str]:
    return {_lookup_key(value) for value in (area.id, area.code, area.name) if str(value or "").strip()}


def _materialized_city_rows(country_code: str, configs: list[CityConfig]) -> dict[str, AdminArea]:
    codes = [city_config.code for city_config in configs if city_config.code]
    if not codes:
        return {}
    return {
        row.code: row
        for row in AdminArea.objects.filter(
            country_code=country_code,
            code__in=codes,
            city_merge_status=CITY_MERGE_UNIFIED,
        ).only("id", "country_code", "code", "name", "level", "entity_type", "raw_entity_type", "city_merge_status")
    }


def _refresh_materialized_city_metadata(materialized_by_code: dict[str, AdminArea], configs: list[CityConfig]) -> int:
    config_by_code = {city_config.code: city_config for city_config in configs}
    changed = []
    now = timezone.now()
    for code, area in materialized_by_code.items():
        city_config = config_by_code.get(code)
        if city_config is None:
            continue
        entity_type = city_config.entity_type or ""
        if (
            area.name == city_config.name
            and int(area.level) == int(city_config.level)
            and (area.entity_type or "") == entity_type
        ):
            continue
        area.name = city_config.name
        area.level = int(city_config.level)
        area.entity_type = entity_type
        area.raw_entity_type = entity_type
        area.updated_at = now
        changed.append(area)
    if not changed:
        return 0
    with transaction.atomic():
        AdminArea.objects.bulk_update(
            changed,
            ["name", "level", "entity_type", "raw_entity_type", "updated_at"],
        )
    return len(changed)


def _is_config_already_materialized(area: AdminArea | None, city_config: CityConfig) -> bool:
    if area is None:
        return False
    return int(area.city_merge_status) == CITY_MERGE_UNIFIED and int(area.level) == int(city_config.level)


def _admin_area_to_scraped(area: AdminArea) -> ScrapedAdminArea:
    parent = area.parent
    most_populated = area.most_populate_city
    return ScrapedAdminArea(
        code=str(area.code or ""),
        name=str(area.name or ""),
        level=int(area.level),
        country_code=str(area.country_code or ""),
        entity_type=area.entity_type,
        raw_entity_type=area.raw_entity_type or area.entity_type or "",
        parent_code=str(parent.code) if parent else None,
        area_km2=area.area_km2,
        density=area.density,
        pop_latest=area.pop_latest,
        pop_latest_date=area.pop_latest_date,
        last_census_year=area.last_census_year,
        url=area.url,
        data_wd=area.data_wd or "",
        city_merge_status=int(area.city_merge_status or 0),
        annotations=area.annotations or "",
        most_populated_city_code=str(most_populated.code) if most_populated else None,
    )


def _deduplicate_entities_by_id(entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    by_id: dict[str, ScrapedAdminArea] = {}
    for entity in entities:
        by_id[entity.id] = entity
    return list(by_id.values())


def _lookup_key(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()
