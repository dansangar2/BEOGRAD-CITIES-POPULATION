"""Typed configuration objects for scraping jobs and post-processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Iterable


class DivisionSourceType(StrEnum):
    """Supported scraper implementations for CityPopulation page layouts."""

    ADMIN = "admin"
    TABLE = "table"
    DOUBLE = "double"
    CITIES = "cities"
    INFOSECTION = "infosection"


class RepresentationSystem(StrEnum):
    """Seat allocation systems supported by the project."""

    DHONDT = "dhondt"


@dataclass(frozen=True)
class ScrapingPageConfig:
    """One logical scraping page after expanding grouped path arrays."""

    path: str
    html_format: str
    lowest_level: int = 1

    @classmethod
    def from_mapping(cls, data: dict, *, path: str) -> "ScrapingPageConfig":
        source = data.get("source", data.get("html_format"))
        if not source:
            raise ValueError("PAGE debe declarar 'source' o 'html_format'.")

        return cls(
            path=str(path).strip("/"),
            html_format=DivisionSourceType(str(source)).value,
            lowest_level=int(data.get("lowest_level", data.get("level", 1))),
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

    @classmethod
    def from_mapping(cls, data: dict) -> "CityConfig":
        raw_parent = data.get("from") or {}
        if not isinstance(raw_parent, dict):
            raise ValueError("CITIES['from'] debe ser un dict {level: [labels]}.")

        return cls(
            name=str(data["city"]),
            code=str(data["id"]),
            level=int(data["level"]),
            entity_type=str(data["type"]),
            district_types=_as_tuple(data.get("district_types") or ()),
            parent_from={int(level): _as_tuple(labels) for level, labels in raw_parent.items()},
            communes=_as_tuple(data.get("communes") or ()),
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


def parse_cities(items: Iterable[dict] | None) -> list[CityConfig]:
    return [CityConfig.from_mapping(item) for item in (items or [])]


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


def _normalize_page_path(slug: str, raw_path) -> str:
    value = str(raw_path or "").strip("/")
    if value.startswith(("http://", "https://")):
        return value
    if not value:
        return slug
    if value == slug or value.startswith(f"{slug}/"):
        return value
    return f"{slug}/{value}"
