"""Domain DTOs used by exports of derived administrative areas."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


CellValue = str | int | float | Decimal | None


@dataclass(frozen=True)
class NuevoAdminCitySummary:
    id: str
    name: str
    pop_latest: int | None = None


@dataclass(frozen=True)
class NuevoAdminAreaSummary:
    id: str
    country_code: str
    code: str
    name: str
    level: int
    entity_type: str | None = None
    parent_id: str | None = None
    area_km2: Decimal | None = None
    pop_latest: int | None = None
    representatives: int | None = None
    capitals: tuple[NuevoAdminCitySummary, ...] = ()
    most_populated_city: NuevoAdminCitySummary | None = None
    source_units_count: int = 0


@dataclass(frozen=True)
class NuevoAdminExportData:
    root: NuevoAdminAreaSummary
    areas: tuple[NuevoAdminAreaSummary, ...]


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: tuple[tuple[CellValue, ...], ...]
    freeze_panes: str | None = None
    auto_filter: bool = True


@dataclass(frozen=True)
class Workbook:
    sheets: tuple[Sheet, ...]
    properties: dict[str, Any] | None = None
