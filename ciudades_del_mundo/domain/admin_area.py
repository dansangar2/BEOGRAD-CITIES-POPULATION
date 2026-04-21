from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ScrapedAdminArea:
    code: str
    name: str
    level: int
    country_code: str
    entity_type: str | None = None
    parent_code: str | None = None
    area_km2: Decimal | float | None = None
    density: Decimal | float | None = None
    pop_latest: int | None = None
    pop_latest_date: date | str | None = None
    last_census_year: int | None = None
    url: str | None = None

    @property
    def id(self) -> str:
        return f"{self.country_code}_{self.code}"
