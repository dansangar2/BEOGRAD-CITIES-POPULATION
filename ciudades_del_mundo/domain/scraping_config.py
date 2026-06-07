"""Typed configuration objects for scraping jobs and post-processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import ceil
from typing import Iterable


class DivisionSourceType(StrEnum):
    """Supported scraper implementations for CityPopulation page layouts."""

    ADMIN = "admin"
    AUTO = "auto"
    TABLE = "table"
    DOUBLE = "double"
    CITIES = "cities"
    INFOSECTION = "infosection"


class RepresentationSystem(StrEnum):
    """Seat allocation systems supported by the project."""

    DHONDT = "dhondt"


@dataclass(frozen=True)
class ScrapingPageConfig:
    """One logical scraping page after expanding grouped path arrays.

    ``table_levels`` and ``include_tables`` are optional hints for compound
    CityPopulation layouts.  They let a scraper use one table as parent lookup
    context while persisting another table at a different hierarchy level.
    """

    path: str
    html_format: str
    lowest_level: int = 1
    area_km2: Decimal | None = None
    area_overrides: dict[str, Decimal] = field(default_factory=dict)
    table_levels: dict[str, int] = field(default_factory=dict)
    include_tables: tuple[str, ...] = ()
    include_root: bool = True
    root_level: int | None = None
    root_code: str | None = None
    root_name: str | None = None
    root_parent_code: str | None = None
    root_entity_type: str | None = None

    @classmethod
    def from_mapping(cls, data: dict, *, path: str) -> "ScrapingPageConfig":
        source = data.get("source", data.get("html_format"))
        if not source:
            raise ValueError("PAGE debe declarar 'source' o 'html_format'.")

        return cls(
            path=str(path).strip("/"),
            html_format=DivisionSourceType(str(source)).value,
            lowest_level=int(data.get("lowest_level", data.get("level", 1))),
            area_km2=_decimal_or_none(data.get("area_km2", data.get("size", data.get("custom_size")))),
            area_overrides=_parse_area_overrides(
                data.get("area_overrides", data.get("size_overrides", data.get("custom_sizes")))
            ),
            table_levels=_parse_table_levels(data.get("table_levels", data.get("levels"))),
            include_tables=_parse_include_tables(data.get("include_tables", data.get("tables"))),
            include_root=bool(data.get("include_root", True)),
            root_level=_int_or_none(data.get("root_level")),
            root_code=_optional_string(data.get("root_code")),
            root_name=_optional_string(data.get("root_name")),
            root_parent_code=_optional_string(data.get("root_parent_code")),
            root_entity_type=_optional_string(data.get("root_entity_type")),
        )


@dataclass(frozen=True)
class CityConfig:
    """Rule to collapse multiple scraped rows into a single city entity."""

    name: str
    code: str
    level: int
    entity_type: str
    district_types: tuple[str, ...]
    parent_from: dict[int, tuple[str, ...]]
    communes: tuple[str, ...]
    keep_communes: bool = True
    child_code: str | None = None
    child_level: int | None = None
    child_entity_type: str | None = None

    @classmethod
    def from_mapping(cls, data: dict) -> "CityConfig":
        raw_parent = data.get("from") or {}
        if not isinstance(raw_parent, dict):
            raise ValueError("CITIES['from'] debe ser un dict {level: [labels]}.")
        level = int(data["level"])
        child_level = int(data["child_level"]) if data.get("child_level") is not None else None
        if child_level is not None and child_level <= level:
            raise ValueError("CITIES['child_level'] debe ser mayor que 'level'.")

        return cls(
            name=str(data["city"]),
            code=str(data["id"]),
            level=level,
            entity_type=str(data["type"]),
            district_types=_as_tuple(data.get("district_types") or ()),
            parent_from={int(level): _as_tuple(labels) for level, labels in raw_parent.items()},
            communes=_as_tuple(data.get("communes") or ()),
            keep_communes=bool(data.get("keep_communes", True)),
            child_code=str(data["child_id"]) if data.get("child_id") is not None else None,
            child_level=child_level,
            child_entity_type=str(data["child_type"]) if data.get("child_type") is not None else None,
        )


@dataclass(frozen=True)
class EntityMergeConfig:
    """Rule to merge same-level scraped entities by parent and normalized base name."""

    entity_types: tuple[str, ...]
    entity_type: str
    strip_numeric_suffix: bool = True
    keep_sources: bool = False

    @classmethod
    def from_mapping(cls, data: dict) -> "EntityMergeConfig":
        return cls(
            entity_types=_as_tuple(data.get("entity_types") or data.get("district_types") or ()),
            entity_type=str(data.get("type", data.get("entity_type", "City"))),
            strip_numeric_suffix=bool(data.get("strip_numeric_suffix", True)),
            keep_sources=bool(data.get("keep_sources", False)),
        )


@dataclass(frozen=True)
class RepresentationConfig:
    """Seat allocation rules applied after scraping/import."""

    level: int
    system: RepresentationSystem
    minimum: int = 0
    min_exceptions: dict[str, int] = field(default_factory=dict)
    maximum: int | None = None
    max_exceptions: dict[str, int] = field(default_factory=dict)
    total: int | None = None
    habitant: int | None = None

    @classmethod
    def from_mapping(cls, data: dict | None) -> "RepresentationConfig | None":
        if not data:
            return None

        total = data.get("total")
        habitant = data.get("habitant")
        if total is None and habitant is None:
            raise ValueError("REPRESENTATION debe declarar 'total' o 'habitant'.")
        if total is not None and habitant is not None:
            raise ValueError("REPRESENTATION no puede declarar 'total' y 'habitant' a la vez.")

        return cls(
            level=int(data["level"]),
            system=RepresentationSystem(data["system"]),
            minimum=int(data.get("min", 0)),
            min_exceptions={str(key): int(value) for key, value in (data.get("min_exceptions") or {}).items()},
            maximum=int(data["max"]) if data.get("max") is not None else None,
            max_exceptions={str(key): int(value) for key, value in (data.get("max_exceptions") or {}).items()},
            total=int(total) if total is not None else None,
            habitant=int(habitant) if habitant is not None else None,
        )

    def total_for_populations(self, populations: Iterable[int | None]) -> int:
        if self.total is not None:
            return self.total
        if not self.habitant or self.habitant <= 0:
            raise ValueError("REPRESENTATION['habitant'] debe ser mayor que cero.")
        return sum(ceil(max(pop or 0, 0) / self.habitant) for pop in populations)


@dataclass(frozen=True)
class ScrapingPlanPage:
    """Lightweight DTO used when planning or displaying a scrape."""

    url: str
    source_type: str
    lowest_level: int


@dataclass(frozen=True)
class ScrapingJobConfig:
    """Full configuration for scraping one country or territory."""

    slug: str
    country_code: str
    base_url: str
    legal_subdivision_level: int | None = None
    name: str | None = None
    reset_before_import: bool = False
    representation: RepresentationConfig | None = None
    pages: list[ScrapingPageConfig] = field(default_factory=list)
    cities: list[CityConfig] = field(default_factory=list)
    entity_merges: list[EntityMergeConfig] = field(default_factory=list)


def parse_cities(items: Iterable[dict] | None) -> list[CityConfig]:
    return [CityConfig.from_mapping(item) for item in (items or [])]


def parse_entity_merges(items: Iterable[dict] | None) -> list[EntityMergeConfig]:
    return [EntityMergeConfig.from_mapping(item) for item in (items or [])]


def parse_pages(items: Iterable[dict] | None, *, slug: str) -> list[ScrapingPageConfig]:
    pages = []
    for item in items or []:
        raw_paths = item.get("path")
        if raw_paths is None:
            raise ValueError("PAGE debe declarar 'path'.")

        paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        if not paths:
            raise ValueError("PAGE debe declarar al menos una ruta en 'path'.")

        for raw_path in paths:
            normalized = _normalize_page_path(slug, raw_path)
            pages.append(ScrapingPageConfig.from_mapping(item, path=normalized))
    return pages


def _as_tuple(value) -> tuple[str, ...]:
    if isinstance(value, (str, int)):
        return (str(value),)
    return tuple(str(item) for item in value)


def _optional_string(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"El tamano personalizado debe ser numerico: {value!r}.") from exc
    if decimal < 0:
        raise ValueError("El tamano personalizado no puede ser negativo.")
    return decimal


def _parse_area_overrides(value) -> dict[str, Decimal]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError("area_overrides debe ser un dict {codigo: area_km2}.")
    overrides = {}
    for key, raw_value in value.items():
        area_km2 = _decimal_or_none(raw_value)
        if area_km2 is None:
            raise ValueError(f"area_overrides[{key!r}] debe declarar un area numerica.")
        overrides[str(key)] = area_km2
    return overrides


def _parse_table_levels(value) -> dict[str, int]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError("table_levels debe ser un dict {tabla: nivel}.")
    levels = {}
    for key, raw_level in value.items():
        table = str(key).strip().lower()
        if not table:
            raise ValueError("table_levels no puede declarar una tabla vacia.")
        try:
            levels[table] = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"table_levels[{key!r}] debe ser entero.") from exc
    return levels


def _parse_include_tables(value) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, Iterable):
        raw_values = list(value)
    else:
        raw_values = [value]
    tables = []
    for raw_table in raw_values:
        table = str(raw_table).strip().lower()
        if table and table not in tables:
            tables.append(table)
    return tuple(tables)


def _normalize_page_path(slug: str, raw_path) -> str:
    value = str(raw_path or "").strip("/")
    if value.startswith(("http://", "https://")):
        return value
    if not value:
        return slug
    if value == slug or value.startswith(f"{slug}/"):
        return value
    return f"{slug}/{value}"
