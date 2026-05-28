"""Global source-population multipliers for derived hierarchy builds.

This module has no Django dependency on purpose. The builder can pass any
source area object with ``country_code``, ``id``, ``code`` and ``name``
attributes, which keeps parsing and lookup logic easy to unit test.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import tomllib
import unicodedata


# ---------------------------------------------------------------------------
# Configuration path
# ---------------------------------------------------------------------------

DEFAULT_SOURCE_POPULATION_INDICES_PATH = (
    Path(__file__).resolve().parents[1] / "source_population_indices.toml"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SourcePopulationIndexConfigError(ValueError):
    """Raised when the source population index config cannot be parsed."""


# ---------------------------------------------------------------------------
# Schedule objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourcePopulationIndexSchedule:
    """Sorted lower-bound schedule for one source area multiplier.

    Each entry starts at its configured year and stays active until the next
    configured year. Years before the first entry return ``None`` so callers
    can distinguish "no configured match" from multiplier ``1``.
    """

    entries: tuple[tuple[int, Decimal], ...]

    def multiplier_for_year(self, year: int) -> Decimal | None:
        """Return the multiplier active for ``year`` or ``None`` before it."""
        active: Decimal | None = None
        for start_year, multiplier in self.entries:
            if year < start_year:
                break
            active = multiplier
        return active


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourcePopulationIndexRegistry:
    """Country-scoped lookup table for source population multipliers.

    ``by_country`` keeps exact labels for deterministic matching by id, code or
    name. ``normalized_by_country`` is a fallback index for case/accent/spacing
    differences in recipe labels and scraped source data.
    """

    by_country: dict[str, dict[str, SourcePopulationIndexSchedule]]
    normalized_by_country: dict[str, dict[str, SourcePopulationIndexSchedule]]

    @classmethod
    def empty(cls) -> "SourcePopulationIndexRegistry":
        """Return an explicit no-op registry."""
        return cls({}, {})

    @classmethod
    def from_mapping(cls, raw: Mapping | None) -> "SourcePopulationIndexRegistry":
        """Parse the TOML mapping shape into validated lookup schedules."""
        if not raw:
            return cls.empty()
        if not isinstance(raw, Mapping):
            raise SourcePopulationIndexConfigError(
                "source_population_indices.toml debe contener un mapa por pais origen."
            )

        by_country: dict[str, dict[str, SourcePopulationIndexSchedule]] = {}
        normalized_by_country: dict[str, dict[str, SourcePopulationIndexSchedule]] = {}

        for country_code, entries in raw.items():
            if not isinstance(entries, Mapping):
                raise SourcePopulationIndexConfigError(
                    f"El pais origen '{country_code}' debe contener un mapa de areas."
                )

            country_key = str(country_code)
            country_entries: dict[str, SourcePopulationIndexSchedule] = {}
            normalized_entries: dict[str, SourcePopulationIndexSchedule] = {}
            for label, schedule_raw in entries.items():
                schedule = _parse_schedule(schedule_raw, label=f"{country_code}.{label}")
                label_key = str(label)
                country_entries[label_key] = schedule
                normalized_label = _norm(label_key)
                if normalized_label and normalized_label not in normalized_entries:
                    normalized_entries[normalized_label] = schedule

            by_country[country_key] = country_entries
            normalized_by_country[country_key] = normalized_entries

        return cls(by_country, normalized_by_country)

    @property
    def is_empty(self) -> bool:
        """Whether the registry has no country entries."""
        return not self.by_country

    def multiplier_for_area(self, area, year: int) -> Decimal | None:
        """Find the configured multiplier for a source area at ``year``.

        Matching is country-scoped and tries exact ``id``, ``code`` and
        ``name`` first, then the normalized fallback index.
        """
        entries = self.by_country.get(str(area.country_code))
        if not entries:
            return None

        for raw_value in (area.id, area.code, area.name):
            if raw_value in (None, ""):
                continue
            schedule = entries.get(str(raw_value))
            if schedule is not None:
                return schedule.multiplier_for_year(year)

        normalized_entries = self.normalized_by_country.get(str(area.country_code), {})
        for raw_value in (area.id, area.code, area.name):
            if raw_value in (None, ""):
                continue
            schedule = normalized_entries.get(_norm(str(raw_value)))
            if schedule is not None:
                return schedule.multiplier_for_year(year)

        return None


# ---------------------------------------------------------------------------
# Public loading API
# ---------------------------------------------------------------------------

def load_source_population_index_registry(
    path: str | Path | None = None,
) -> SourcePopulationIndexRegistry:
    """Load the global TOML file, returning an empty registry when absent."""
    config_path = Path(path) if path is not None else DEFAULT_SOURCE_POPULATION_INDICES_PATH
    if not config_path.exists():
        return SourcePopulationIndexRegistry.empty()

    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SourcePopulationIndexConfigError(
            f"No se pudo leer {config_path}: {exc}"
        ) from exc

    return SourcePopulationIndexRegistry.from_mapping(raw)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_schedule(raw, *, label: str) -> SourcePopulationIndexSchedule:
    """Validate one ``{year: multiplier}`` mapping."""
    if not isinstance(raw, Mapping):
        raise SourcePopulationIndexConfigError(
            f"El indice de '{label}' debe ser un mapa {{anio: multiplicador}}."
        )

    entries: list[tuple[int, Decimal]] = []
    for raw_year, raw_multiplier in raw.items():
        try:
            year = int(raw_year)
        except (TypeError, ValueError) as exc:
            raise SourcePopulationIndexConfigError(
                f"El anio '{raw_year}' de '{label}' no es valido."
            ) from exc

        try:
            multiplier = Decimal(str(raw_multiplier))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SourcePopulationIndexConfigError(
                f"El multiplicador de '{label}' para {year} debe ser numerico."
            ) from exc

        if multiplier < 0:
            raise SourcePopulationIndexConfigError(
                f"El multiplicador de '{label}' para {year} no puede ser negativo."
            )
        entries.append((year, multiplier))

    if not entries:
        raise SourcePopulationIndexConfigError(
            f"El indice de '{label}' debe contener al menos un anio."
        )

    return SourcePopulationIndexSchedule(tuple(sorted(entries)))


def _norm(value: str | None) -> str:
    """Normalize labels for forgiving lookup without changing exact keys."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    return re.sub(r"\s+", " ", value).strip()
