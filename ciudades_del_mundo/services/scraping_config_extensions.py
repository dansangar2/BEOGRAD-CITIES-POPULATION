"""Runtime-only extensions for SQL/TOML scraping configs.

These fields are deliberately kept outside the core domain parser because they
are rare post-processing hints for countries whose CityPopulation pages split
one administrative reality across several page families.
"""

from __future__ import annotations

import tomllib
from typing import Iterable

from ciudades_del_mundo.models import ScrapingConfig

_RUNTIME_FIELDS = (
    "runtime_synthetic_entities",
    "runtime_parent_overrides",
    "runtime_root_metric_sources",
)


def attach_runtime_config_extensions(configs: Iterable[object]) -> None:
    """Attach parsed extension dictionaries to already loaded config objects.

    The normal SQL repository still owns the typed config parser.  This helper is
    used only by composition roots that already depend on Django and need access
    to raw SQL TOML content for country-specific post-processing.
    """
    config_list = list(configs)
    if not config_list:
        return

    rows = {
        row.slug: row.content
        for row in ScrapingConfig.objects.filter(slug__in=[config.slug for config in config_list]).only(
            "slug",
            "content",
        )
    }
    for config in config_list:
        extensions = parse_runtime_config_extensions(rows.get(config.slug, ""))
        for name, value in extensions.items():
            object.__setattr__(config, name, value)


def parse_runtime_config_extensions(content: str) -> dict[str, tuple[dict, ...]]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Extensiones TOML invalidas: {exc}") from exc

    return {
        "runtime_synthetic_entities": _normalize_synthetic_entities(data.get("synthetic_entities")),
        "runtime_parent_overrides": _normalize_parent_overrides(data.get("parent_overrides")),
        "runtime_root_metric_sources": _normalize_root_metric_sources(data.get("root_metric_sources")),
    }


def _normalize_synthetic_entities(raw) -> tuple[dict, ...]:
    items = _as_list(raw)
    normalized = []
    for item in items:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or code).strip()
        if not code or not name:
            raise ValueError("Cada [[synthetic_entities]] necesita code y name.")
        normalized.append(
            {
                "code": code,
                "name": name,
                "level": _int_value(item.get("level"), field="synthetic_entities.level"),
                "parent_code": _optional_string(item.get("parent_code")),
                "entity_type": _optional_string(item.get("entity_type")),
                "raw_entity_type": _optional_string(item.get("raw_entity_type")),
                "area_km2": item.get("area_km2"),
                "pop_latest": item.get("pop_latest"),
                "pop_latest_date": _optional_string(item.get("pop_latest_date")),
                "url": _optional_string(item.get("url")),
                "copy_metrics_from": _optional_string(item.get("copy_metrics_from")),
                "metric_source_codes": _string_list(item.get("metric_source_codes")),
            }
        )
    return tuple(normalized)


def _normalize_parent_overrides(raw) -> tuple[dict, ...]:
    normalized = []
    for item in _as_list(raw):
        parent_code = _optional_string(item.get("parent_code"))
        if "level" not in item and parent_code is None:
            raise ValueError("Cada [[parent_overrides]] necesita parent_code o level.")
        values = {
            "codes": _string_list(item.get("codes") or item.get("code")),
            "names": _string_list(item.get("names") or item.get("name")),
            "match_levels": _int_list(item.get("match_levels") or item.get("match_level")),
            "exclude_codes": _string_list(item.get("exclude_codes") or item.get("exclude_code")),
        }
        if parent_code is not None:
            values["parent_code"] = parent_code
        if "level" in item:
            values["level"] = _int_value(item.get("level"), field="parent_overrides.level")
        normalized.append(values)
    return tuple(normalized)


def _normalize_root_metric_sources(raw) -> tuple[dict, ...]:
    normalized = []
    for item in _as_list(raw):
        code = _optional_string(item.get("code"))
        name = _optional_string(item.get("name"))
        path = _optional_string(item.get("path") or item.get("url"))
        if not (code or name or path):
            raise ValueError("Cada [[root_metric_sources]] necesita code, name o path.")
        normalized.append({"code": code, "name": name, "path": path})
    return tuple(normalized)


def _as_list(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return value
    raise ValueError("Las extensiones de config deben declararse como arrays de tablas TOML.")


def _string_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _int_list(value) -> tuple[int, ...]:
    return tuple(_int_value(item, field="list") for item in _string_list(value))


def _int_value(value, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} debe ser entero.") from exc


def _optional_string(value) -> str | None:
    text = str(value or "").strip()
    return text or None
