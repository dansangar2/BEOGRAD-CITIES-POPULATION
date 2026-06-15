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
import re
from typing import Any
from urllib.parse import unquote, urlparse

from ciudades_del_mundo.domain import ScrapedAdminArea



PAGE_REPEAT_ROOT_ANNOTATION = "Raíz de página repetida"


def repeat_count_for_page(page: Any, section: str) -> int:
    repeat = getattr(page, "repeat", None) or {}
    try:
        return int(repeat.get(str(section).strip().casefold(), 1))
    except (TypeError, ValueError):
        return 1


def repeated_infosection_roots(
    root: ScrapedAdminArea | None,
    *,
    page: Any,
    country_code: str,
    url: str,
) -> list[ScrapedAdminArea]:
    if root is None:
        return []

    count = repeat_count_for_page(page, "infosection")
    if count <= 1:
        return [root]

    try:
        root_level = int(root.level or 0)
    except (TypeError, ValueError):
        root_level = 0

    base_code = root.code
    if root.code == country_code:
        base_code = _page_root_code(country_code=country_code, root=root, url=url)

    chain: list[ScrapedAdminArea] = []
    for index in range(count):
        previous = chain[-1] if chain else None
        code = base_code if index == 0 else f"{base_code}__repeat{index + 1}"
        annotations = _append_annotation(root.annotations, PAGE_REPEAT_ROOT_ANNOTATION)
        if index > 0:
            annotations = _append_annotation(annotations, "Duplicación explícita por página")
        chain.append(
            replace(
                root,
                code=code,
                level=root_level + index,
                parent_code=previous.code if previous else root.parent_code,
                annotations=annotations,
                url=root.url or url,
            )
        )
    return chain


def _append_annotation(value: str | None, annotation: str) -> str:
    current = str(value or "").strip()
    if not current:
        return annotation
    if annotation in current:
        return current
    return f"{current}; {annotation}"


def _page_root_code(*, country_code: str, root: ScrapedAdminArea, url: str) -> str:
    slug = _page_context_slug(url) or _slugify(root.name) or "root"
    return f"{country_code}_{slug}"


def _page_context_slug(url: str | None) -> str:
    segments = [segment for segment in urlparse(str(url or "")).path.split("/") if segment]
    generic = {"admin", "cities", "localities"}
    for segment in reversed(segments):
        slug = _slugify(segment)
        if slug and slug not in generic:
            return slug
    return ""


def _slugify(value: str | None) -> str:
    text = unquote(str(value or "")).strip().casefold()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")

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
    if "__none__" in include_tables:
        return False
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
