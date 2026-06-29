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
    CITIESADMIN = "citiesadmin"
    INFOSECTION = "infosection"


CONFIGURED_SOURCE_TYPES = {
    DivisionSourceType.CITIES.value,
    DivisionSourceType.ADMIN.value,
    DivisionSourceType.CITIESADMIN.value,
}


class RepresentationSystem(StrEnum):
    """Seat allocation systems supported by the project."""

    DHONDT = "dhondt"


@dataclass(frozen=True)
class ScrapingPageConfig:
    """One logical scraping page after expanding grouped path arrays.

    ``block_index`` identifies the original ``[[pages]]`` entry before a
    multi-path block was expanded.  It lets the application validate and fail a
    whole block before moving to the next configured block.

    ``table_levels`` and ``include_tables`` are optional hints for compound
    CityPopulation layouts.  They let a scraper use one table as parent lookup
    context while persisting another table at a different hierarchy level.
    """

    path: str
    html_format: str
    lowest_level: int = 1
    block_index: int = 0
    path_index: int = 0
    area_km2: Decimal | None = None
    area_overrides: dict[str, Decimal] = field(default_factory=dict)
    table_levels: dict[str, int] = field(default_factory=dict)
    status_levels: dict[str, int] = field(default_factory=dict)
    include_tables: tuple[str, ...] = ()
    include_sections: tuple[str, ...] = ()
    include_root: bool = True
    force_highest_level: int | None = None
    parent_level: int | None = None
    root_level: int | None = None
    root_code: str | None = None
    root_name: str | None = None
    root_parent_code: str | None = None
    root_entity_type: str | None = None
    repeat: dict[str, int] = field(default_factory=dict)
    sum_to_root: bool = False

    @property
    def include_infosection(self) -> bool:
        return self._section_enabled("infosection")

    @property
    def include_major_subdivision(self) -> bool:
        return self._section_enabled("major_subdivision")

    @property
    def include_minor_subdivision(self) -> bool:
        return self._section_enabled("minor_subdivision")

    @property
    def include_cities(self) -> bool:
        return self._section_enabled("cities")

    def _section_enabled(self, section: str) -> bool:
        if self.include_sections:
            return section in self.include_sections
        if section == "infosection":
            return self.include_root
        if not self.include_tables:
            return True
        if "__none__" in self.include_tables:
            return False
        aliases = {
            "major_subdivision": {"tl", "admin1", "1"},
            "minor_subdivision": {"tl_child", "admin2", "2"},
            "cities": {"ts"},
        }
        return bool(aliases.get(section, set(self.include_tables)) & set(self.include_tables))

    @classmethod
    def from_mapping(
        cls,
        data: dict,
        *,
        path: str,
        schema_version: int = 1,
        block_index: int = 0,
        path_index: int = 0,
    ) -> "ScrapingPageConfig":
        source = data.get("source", data.get("html_format"))
        if not source:
            raise ValueError("PAGE debe declarar 'source' o 'html_format'.")

        html_format = DivisionSourceType(str(source)).value
        if html_format not in CONFIGURED_SOURCE_TYPES:
            raise ValueError("source debe ser 'cities' o 'admin' o 'citiesadmin'.")
        include_spec = data.get("include")
        include_tables = _parse_include_tables(data.get("include_tables", data.get("tables")))
        include_sections = _parse_include_sections(data.get("include_sections"))
        if include_spec is not None:
            # ``include`` is authoritative in schema v2.  A page may request
            # only the infosection/root and explicitly disable every table,
            # e.g. ``include = { cities = false, major_subdivision = false,
            # infosection = true }``.  Do not treat that as the legacy empty
            # tuple meaning "include all tables".
            include_tables = _include_tables_from_include_spec(html_format, include_spec)
            include_sections = _include_sections_from_include_spec(html_format, include_spec)

        include_root = bool(data.get("include_root", True))
        if "include_root" not in data and isinstance(include_spec, dict) and "infosection" in include_spec:
            include_root = bool(include_spec.get("infosection"))
        if not include_root:
            include_sections = tuple(section for section in include_sections if section != "infosection")

        return cls(
            path=str(path).strip("/"),
            html_format=html_format,
            lowest_level=_page_lowest_level(data, path=path, source=html_format, schema_version=schema_version),
            block_index=int(block_index),
            path_index=int(path_index),
            area_km2=_decimal_or_none(data.get("area_km2", data.get("size", data.get("custom_size")))),
            area_overrides=_parse_area_overrides(
                data.get("area_overrides", data.get("size_overrides", data.get("custom_sizes")))
            ),
            table_levels=_parse_table_levels(data.get("table_levels", data.get("levels"))),
            status_levels=_parse_status_levels(data.get("status_levels", data.get("entity_type_levels"))),
            include_tables=include_tables,
            include_sections=include_sections,
            include_root=include_root,
            force_highest_level=_int_or_none(data.get("force_highest_level", data.get("forced_level"))),
            parent_level=_int_or_none(data.get("parent_level", data.get("force_parent_level"))),
            root_level=_int_or_none(data.get("root_level")),
            root_code=_optional_string(data.get("root_code")),
            root_name=_optional_string(data.get("root_name")),
            root_parent_code=_optional_string(data.get("root_parent_code")),
            root_entity_type=_optional_string(data.get("root_entity_type")),
            repeat=_parse_repeat(data.get("repeat")),
            sum_to_root=bool(data.get("sum_to_root", False)),
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


def parse_pages(
    items: Iterable[dict] | None,
    *,
    slug: str,
    schema_version: int = 1,
) -> list[ScrapingPageConfig]:
    pages = []
    for block_index, item in enumerate(items or []):
        if not _page_block_enabled(item):
            continue
        raw_paths = item.get("path")
        if raw_paths is None:
            raise ValueError("PAGE debe declarar 'path'.")

        paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        if not paths:
            raise ValueError("PAGE debe declarar al menos una ruta en 'path'.")

        for path_index, raw_path in enumerate(paths):
            normalized = _normalize_page_path(slug, raw_path)
            pages.append(
                ScrapingPageConfig.from_mapping(
                    item,
                    path=normalized,
                    schema_version=schema_version,
                    block_index=block_index,
                    path_index=path_index,
                )
            )
    return pages


def _page_block_enabled(item: dict) -> bool:
    if "enabled" in item:
        return bool(item.get("enabled"))
    if "active" in item:
        return bool(item.get("active"))
    if "disabled" in item:
        return not bool(item.get("disabled"))
    return True



def _page_lowest_level(data: dict, *, path: str, source: str, schema_version: int) -> int:
    if data.get("force_highest_level") is not None or data.get("forced_level") is not None:
        return int(data.get("force_highest_level", data.get("forced_level")))
    if data.get("lowest_level") is not None or data.get("level") is not None:
        return int(data.get("lowest_level", data.get("level")))

    if int(schema_version or 1) < 2:
        return 1

    normalized_path = str(path or "").strip("/").casefold()
    if _is_country_root_path(normalized_path):
        return 0
    if source == DivisionSourceType.ADMIN.value and _is_country_root_admin_path(normalized_path):
        return 0
    if (
        source in {DivisionSourceType.CITIES.value, DivisionSourceType.CITIESADMIN.value}
        and _is_country_root_admin_path(normalized_path)
        and _include_spec_allows_infosection(data.get("include"))
    ):
        return 0
    if source in {DivisionSourceType.CITIES.value, DivisionSourceType.CITIESADMIN.value} and _is_country_root_cities_path(normalized_path):
        # Country-level CityPopulation /cities/ pages usually expose the
        # country total in table#tl/tfoot instead of in a normal infosection.
        # Treat the page itself as level 0 so the cities scraper can parse and
        # persist that root before reading Regions/Provinces and Cities.
        return 0
    if "/localities/" in f"/{normalized_path}/":
        return 2
    return 1


def _is_country_root_path(path: str) -> bool:
    return len(_relative_path_segments(path)) == 1


def _is_country_root_admin_path(path: str) -> bool:
    segments = _relative_path_segments(path)
    return len(segments) == 2 and segments[-1] == "admin"


def _is_country_root_cities_path(path: str) -> bool:
    segments = _relative_path_segments(path)
    return len(segments) == 2 and segments[-1] == "cities"


def _relative_path_segments(path: str) -> tuple[str, ...]:
    normalized = str(path or "").strip("/").casefold()
    if not normalized or normalized.startswith(("http://", "https://")):
        return ()
    return tuple(segment for segment in normalized.split("/") if segment)


def _include_spec_allows_infosection(value) -> bool:
    if isinstance(value, dict) and "infosection" in value:
        return bool(value.get("infosection"))
    return True


def _include_tables_from_include_spec(source: str, value) -> tuple[str, ...]:
    """Return legacy table hints derived from the logical section flags.

    New scrapers use ``include_sections``.  ``include_tables`` is still filled
    for backwards compatibility with existing diagnostics and older adapters.
    """
    sections = _include_sections_from_include_spec(source, value)
    tables: list[str] = []
    if "major_subdivision" in sections:
        tables.append("admin1" if source == DivisionSourceType.ADMIN.value else "tl")
    if "minor_subdivision" in sections:
        tables.append("admin2" if source == DivisionSourceType.ADMIN.value else "tl_child")
    if "cities" in sections:
        tables.append("ts")
    return tuple(tables) if tables else ("__none__",)


def _include_sections_from_include_spec(source: str, value) -> tuple[str, ...]:
    if not isinstance(value, dict):
        raise ValueError("PAGE.include debe ser un dict de banderas booleanas.")

    normalized = {str(key).strip().casefold(): bool(enabled) for key, enabled in value.items()}
    if source == DivisionSourceType.ADMIN.value:
        order = ("infosection", "major_subdivision", "minor_subdivision")
        defaults = {
            "infosection": normalized.get("infosection", True),
            "major_subdivision": normalized.get("major_subdivision", normalized.get("admin1", True)),
            "minor_subdivision": normalized.get("minor_subdivision", normalized.get("admin2", True)),
        }
    elif source == DivisionSourceType.CITIESADMIN.value:
        order = ("infosection", "major_subdivision", "minor_subdivision", "cities")
        defaults = {section: normalized.get(section, True) for section in order}
    elif source == DivisionSourceType.CITIES.value:
        order = ("infosection", "major_subdivision", "cities")
        defaults = {section: normalized.get(section, True) for section in order}
    else:
        order = ("infosection", "major_subdivision", "minor_subdivision", "cities")
        defaults = {section: normalized.get(section, True) for section in order}
    return tuple(section for section in order if defaults.get(section, True))


def _parse_include_sections(value) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, Iterable):
        raw_values = list(value)
    else:
        raw_values = [value]
    sections: list[str] = []
    for raw in raw_values:
        section = str(raw).strip().casefold()
        if section and section not in sections:
            sections.append(section)
    return tuple(sections)


def _parse_repeat(value) -> dict[str, int]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("PAGE.repeat debe ser un dict {seccion: veces}.")
    repeats: dict[str, int] = {}
    for key, raw_count in value.items():
        section = str(key).strip().casefold()
        if not section:
            raise ValueError("PAGE.repeat no puede declarar una sección vacía.")
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"PAGE.repeat[{key!r}] debe ser entero.") from exc
        repeats[section] = count
    return repeats

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


def _parse_status_levels(value) -> dict[str, int]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError("status_levels debe ser un dict {status: nivel}.")
    levels = {}
    for key, raw_level in value.items():
        status = str(key).strip().casefold()
        if not status:
            raise ValueError("status_levels no puede declarar un status vacio.")
        try:
            levels[status] = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"status_levels[{key!r}] debe ser entero.") from exc
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
