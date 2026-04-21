from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ScrapingPageConfig:
    path: str
    html_format: str
    target_level: int | None = None
    prefer_table: str = "auto"
    lowest_level: int = 1


@dataclass(frozen=True)
class DivisionConfig:
    source_type: str
    urls: tuple[str, ...]
    lowest_level: int

    @classmethod
    def from_mapping(cls, data: dict) -> "DivisionConfig":
        raw_urls = data["urls"]
        urls = (raw_urls,) if isinstance(raw_urls, str) else tuple(raw_urls)
        return cls(
            source_type=data["type"],
            urls=urls,
            lowest_level=int(data["level"]),
        )


@dataclass(frozen=True)
class ScrapingPlanPage:
    url: str
    source_type: str
    lowest_level: int


@dataclass(frozen=True)
class ScrapingJobConfig:
    slug: str
    country_code: str
    base_url: str
    name: str | None = None
    reset_before_import: bool = False
    pages: list[ScrapingPageConfig] = field(default_factory=list)


def parse_divisions(items: Iterable[dict]) -> list[DivisionConfig]:
    return [DivisionConfig.from_mapping(item) for item in items]
