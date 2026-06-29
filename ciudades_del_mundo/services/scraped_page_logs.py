"""Local diagnostics for completed CityPopulation page scrapes."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from django.conf import settings
from django.utils import timezone


SCRAPED_PAGE_LOG_DIR_NAME = ".web_scrape_pages"
SCRAPED_PAGE_LOG_VERSION = 1


class ScrapedPageLogWriter:
    """Write page HTML and parsed entities for one country/task run."""

    def __init__(
        self,
        *,
        slug: str,
        run_id: str,
        root: Path | None = None,
    ) -> None:
        self.slug = _safe_name(slug or "config")
        self.run_id = _safe_name(run_id or timezone.now().strftime("%Y%m%d_%H%M%S"))
        self.root = root or (Path(settings.BASE_DIR) / SCRAPED_PAGE_LOG_DIR_NAME)
        self._lock = threading.Lock()

    @property
    def run_path(self) -> Path:
        return self.root / self.slug / self.run_id

    @property
    def index_path(self) -> Path:
        return self.run_path / "index.jsonl"

    def write_page(self, page, *, cached: bool = False) -> Path:
        """Persist one completed page and append its metadata to the run index."""

        html_dir = self.run_path / "html"
        entity_dir = self.run_path / "entities"
        html_dir.mkdir(parents=True, exist_ok=True)
        entity_dir.mkdir(parents=True, exist_ok=True)

        stem = _page_stem(page)
        html_path = html_dir / f"{stem}.html"
        entities_path = entity_dir / f"{stem}.json"
        html_path.write_text(str(getattr(page, "html", "") or ""), encoding="utf-8")
        entities_path.write_text(
            json.dumps(
                {
                    "version": SCRAPED_PAGE_LOG_VERSION,
                    "entities": [_serialize_entity(entity) for entity in getattr(page, "entities", ()) or ()],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        item = {
            "version": SCRAPED_PAGE_LOG_VERSION,
            "completed_at": timezone.now().isoformat(),
            "cached": bool(cached),
            "index": int(getattr(page, "index", 0) or 0),
            "block_index": int(getattr(page, "block_index", 0) or 0),
            "path_index": int(getattr(page, "path_index", 0) or 0),
            "path": str(getattr(page, "path", "") or ""),
            "url": str(getattr(page, "url", "") or ""),
            "html_format": str(getattr(page, "html_format", "") or ""),
            "lowest_level": int(getattr(page, "lowest_level", 0) or 0),
            "found": int(getattr(page, "found", 0) or 0),
            "html_file": str(html_path.relative_to(self.run_path)).replace("\\", "/"),
            "entities_file": str(entities_path.relative_to(self.run_path)).replace("\\", "/"),
        }
        with self._lock:
            with self.index_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return html_path


def latest_page_run_dir(slug: str, *, base_dir: Path | None = None) -> Path | None:
    """Return the newest scraped-page run directory for a country."""

    root = Path(base_dir or settings.BASE_DIR) / SCRAPED_PAGE_LOG_DIR_NAME / _safe_name(slug)
    if not root.is_dir():
        return None
    runs = [path for path in root.iterdir() if path.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda path: path.stat().st_mtime)


def _page_stem(page) -> str:
    url = str(getattr(page, "url", "") or "")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return (
        f"{int(getattr(page, 'index', 0) or 0):04d}_"
        f"b{int(getattr(page, 'block_index', 0) or 0) + 1:03d}_"
        f"p{int(getattr(page, 'path_index', 0) or 0) + 1:03d}_"
        f"{digest}"
    )


def _safe_name(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_", "."})
    return safe.strip("._") or "item"


def _serialize_entity(entity) -> dict[str, Any]:
    if is_dataclass(entity):
        names = [field.name for field in fields(entity)]
    else:
        names = [
            "code",
            "name",
            "level",
            "country_code",
            "entity_type",
            "raw_entity_type",
            "parent_code",
            "pop_latest",
            "pop_latest_date",
            "url",
            "data_wd",
            "annotations",
        ]
    return {
        name: _serialize_value(getattr(entity, name))
        for name in names
        if hasattr(entity, name)
    }


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
