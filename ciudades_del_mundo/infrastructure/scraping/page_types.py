"""Structural detection for CityPopulation page layouts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from bs4 import BeautifulSoup


class CityPopulationPageType(StrEnum):
    """Detected CityPopulation HTML structure, independent from SQL config."""

    ADMIN_HIERARCHY = "admin_hierarchy"
    STRUCTURED_TABLE = "structured_table"
    DOUBLE_TABLE = "double_table"
    SINGLE_TL_TABLE = "single_tl_table"
    SINGLE_TS_TABLE = "single_ts_table"
    INFOSECTION = "infosection"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CityPopulationPageProfile:
    """Reusable facts about one CityPopulation HTML page."""

    page_type: CityPopulationPageType
    has_tl: bool = False
    has_ts: bool = False
    has_admin_bodies: bool = False
    has_cpage_root: bool = False
    has_infosection_root: bool = False
    has_tfoot_root: bool = False
    ts_has_radm: bool = False
    admin_levels: tuple[int, ...] = ()

    @property
    def has_root(self) -> bool:
        return self.has_cpage_root or self.has_infosection_root or self.has_tfoot_root

    @property
    def ts_uses_first_child_level(self) -> bool:
        """`table#ts` is the first child table when no `table#tl` is present."""
        return self.has_ts and not self.has_tl

    @property
    def preferred_html_format(self) -> str:
        """Return the existing scraper source best suited for this structure."""
        if self.page_type == CityPopulationPageType.ADMIN_HIERARCHY:
            return "admin"
        if self.page_type == CityPopulationPageType.STRUCTURED_TABLE:
            return "table"
        if self.page_type in {
            CityPopulationPageType.DOUBLE_TABLE,
            CityPopulationPageType.SINGLE_TL_TABLE,
            CityPopulationPageType.SINGLE_TS_TABLE,
        }:
            return "double"
        if self.page_type == CityPopulationPageType.INFOSECTION:
            return "infosection"
        return "table"


def detect_citypopulation_page_profile(soup: BeautifulSoup) -> CityPopulationPageProfile:
    """Classify a CityPopulation page from its HTML landmarks."""
    tl = soup.find("table", id="tl")
    ts = soup.find("table", id="ts")
    admin_levels = _admin_levels(tl)
    has_tl = tl is not None
    has_ts = ts is not None
    has_cpage_root = _has_class(soup, "header", "cpage")
    has_infosection_root = _has_class(soup, None, "infosection")
    has_tfoot_root = bool(tl and tl.find("tfoot") and tl.find("tfoot").find("tr"))
    ts_has_radm = bool(ts and ts.find("th", class_=lambda value: value and "radm" in value.split()))

    if admin_levels:
        page_type = CityPopulationPageType.ADMIN_HIERARCHY
    elif (has_cpage_root or has_infosection_root or has_tfoot_root) and (has_tl or has_ts):
        page_type = CityPopulationPageType.STRUCTURED_TABLE
    elif has_tl and has_ts:
        page_type = CityPopulationPageType.DOUBLE_TABLE
    elif has_ts:
        page_type = CityPopulationPageType.SINGLE_TS_TABLE
    elif has_tl:
        page_type = CityPopulationPageType.SINGLE_TL_TABLE
    elif has_cpage_root or has_infosection_root:
        page_type = CityPopulationPageType.INFOSECTION
    else:
        page_type = CityPopulationPageType.UNKNOWN

    return CityPopulationPageProfile(
        page_type=page_type,
        has_tl=has_tl,
        has_ts=has_ts,
        has_admin_bodies=bool(admin_levels),
        has_cpage_root=has_cpage_root,
        has_infosection_root=has_infosection_root,
        has_tfoot_root=has_tfoot_root,
        ts_has_radm=ts_has_radm,
        admin_levels=admin_levels,
    )


def _admin_levels(table) -> tuple[int, ...]:
    if not table:
        return ()

    levels = []
    for tbody in table.find_all("tbody", recursive=False):
        for class_name in tbody.get("class") or []:
            match = re.fullmatch(r"admin(\d+)", class_name)
            if match:
                levels.append(int(match.group(1)))
                break
    return tuple(levels)


def _has_class(soup: BeautifulSoup, tag: str | None, class_name: str) -> bool:
    node = soup.find(tag, class_=lambda value: value and class_name in value.split())
    return node is not None
