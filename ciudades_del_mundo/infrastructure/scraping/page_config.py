"""Helpers for applying per-page CityPopulation scraping hints.

The public TOML surface stays small, but a few CityPopulation layouts need the
same concepts across different scraper implementations: include/suppress a
page root, override the code assigned to that root, map logical tables to
explicit hierarchy levels, and decide whether a table is persisted or used only
as parent lookup context.  Keeping those rules here prevents country-specific
branches inside the concrete scrapers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ciudades_del_mundo.domain import ScrapedAdminArea


def include_root_for_page(page: Any) -> bool:
    return bool(getattr(page, "include_root", True))


def root_level_for_page(page: Any, default: int) -> int:
    configured = getattr(page, "root_level", None)
    return int(configured) if configured is not None else int(default)


def table_levels_for_page(page: Any) -> dict[str, int]:
    return {str(key).strip().lower(): int(value) for key, value in getattr(page, "table_levels", {}).items()}


def include_tables_for_page(page: Any) -> tuple[str, ...]:
    return tuple(str(value).strip().lower() for value in getattr(page, "include_tables", ()) if str(value).strip())


def should_include_table(include_tables: tuple[str, ...], table: str) -> bool:
    table = str(table).strip().lower()
    return not include_tables or table in include_tables


def configured_level(table_levels: dict[str, int], *keys: str, default: int) -> int:
    for key in keys:
        normalized = str(key).strip().lower()
        if normalized in table_levels:
            return table_levels[normalized]
    return int(default)


def apply_root_config(
    root: ScrapedAdminArea | None,
    *,
    page: Any,
    country_code: str,
    url: str,
    default_level: int,
) -> ScrapedAdminArea | None:
    """Return the page root after applying optional TOML hints.

    ``root_code`` is intentionally page-scoped.  It is useful for pages such as
    French overseas departments where CityPopulation exposes an infosection root
    but not the administrative code the rest of the project expects.
    """
    if not include_root_for_page(page):
        return None

    root_level = root_level_for_page(page, default_level)
    root_code = _optional_string(getattr(page, "root_code", None))
    root_name = _optional_string(getattr(page, "root_name", None))
    root_parent_code = _optional_string(getattr(page, "root_parent_code", None))
    root_entity_type = _optional_string(getattr(page, "root_entity_type", None))

    if root is None:
        if not (root_code or root_name):
            return None
        return ScrapedAdminArea(
            code=root_code or country_code,
            name=root_name or root_code or country_code,
            level=root_level,
            country_code=country_code,
            entity_type=root_entity_type,
            parent_code=root_parent_code,
            url=url,
        )

    return replace(
        root,
        code=root_code or root.code,
        name=root_name or root.name,
        level=root_level,
        entity_type=root_entity_type or root.entity_type,
        parent_code=root_parent_code if root_parent_code is not None else root.parent_code,
        url=root.url or url,
    )


def _optional_string(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def status_levels_for_page(page) -> dict[str, int]:
    if page is None:
        return {}
    raw = getattr(page, "status_levels", None) or {}
    return {str(key).strip().casefold(): int(value) for key, value in raw.items()}
