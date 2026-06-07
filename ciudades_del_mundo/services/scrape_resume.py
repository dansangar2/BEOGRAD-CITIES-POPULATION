"""Local page checkpoints for resumable CityPopulation scraping."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time
from typing import Any

from django.conf import settings
from django.utils import timezone

from ciudades_del_mundo.domain import ScrapedAdminArea


SCRAPE_RESUME_DIR_NAME = ".web_scrape_resume"
SCRAPE_RESUME_VERSION = 1


@dataclass(frozen=True)
class ResumePageSnapshot:
    """Cached payload for one completed configured scraping page."""

    found: int
    html: str
    entities: tuple[ScrapedAdminArea, ...]
    key: str = ""
    path: str = ""
    html_format: str = ""
    lowest_level: int = 0
    url: str = ""
    index: int = 0


class ScrapeResumeStore:
    """Persist page-level scraper checkpoints for one config/content version."""

    def __init__(
        self,
        *,
        slug: str,
        country_code: str,
        content_hash: str,
        root: Path | None = None,
    ) -> None:
        self.slug = str(slug or "").strip()
        self.country_code = str(country_code or "").strip()
        self.content_hash = str(content_hash or "").strip()
        self.root = root or (Path(settings.BASE_DIR) / SCRAPE_RESUME_DIR_NAME)

    @property
    def path(self) -> Path:
        return self.root / f"{_safe_name(self.slug)}.json"

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def load_page(self, page) -> ResumePageSnapshot | None:
        payload = self._read()
        pages = payload.get("pages")
        if not isinstance(pages, dict):
            return None
        item = pages.get(page_cache_key(page))
        if not isinstance(item, dict):
            return None
        entities = item.get("entities")
        if not isinstance(entities, list):
            return None
        return ResumePageSnapshot(
            found=int(item.get("found") or len(entities)),
            html=str(item.get("html") or ""),
            entities=tuple(_deserialize_entity(entity) for entity in entities if isinstance(entity, dict)),
            key=page_cache_key(page),
            path=str(item.get("path") or ""),
            html_format=str(item.get("html_format") or ""),
            lowest_level=int(item.get("lowest_level") or 0),
            url=str(item.get("url") or ""),
            index=int(item.get("index") or 0),
        )

    def iter_pages(self) -> tuple[ResumePageSnapshot, ...]:
        """Return all cached pages in configured order."""
        payload = self._read()
        pages = payload.get("pages")
        if not isinstance(pages, dict):
            return ()
        snapshots: list[ResumePageSnapshot] = []
        for key, item in pages.items():
            if not isinstance(item, dict):
                continue
            entities = item.get("entities")
            if not isinstance(entities, list):
                continue
            snapshots.append(
                ResumePageSnapshot(
                    found=int(item.get("found") or len(entities)),
                    html=str(item.get("html") or ""),
                    entities=tuple(_deserialize_entity(entity) for entity in entities if isinstance(entity, dict)),
                    key=str(key),
                    path=str(item.get("path") or ""),
                    html_format=str(item.get("html_format") or ""),
                    lowest_level=int(item.get("lowest_level") or 0),
                    url=str(item.get("url") or ""),
                    index=int(item.get("index") or 0),
                )
            )
        return tuple(sorted(snapshots, key=lambda page: (page.index, page.path, page.url)))

    def data_populated(self) -> bool:
        """Return whether scraping/import finished and only the asset phase remains."""
        return bool(self._read().get("data_populated"))

    def mark_data_populated(self) -> None:
        payload = self._read()
        payload["data_populated"] = True
        payload["data_populated_at"] = timezone.now().isoformat()
        payload["updated_at"] = timezone.now().isoformat()
        _write_json_atomic(self.path, payload)

    def asset_page_done(self, page_key: str) -> bool:
        asset_pages = self._read().get("asset_pages")
        return isinstance(asset_pages, dict) and str(asset_pages.get(str(page_key)) or "") == "completed"

    def mark_asset_page_done(self, page_key: str) -> None:
        payload = self._read()
        asset_pages = payload.get("asset_pages")
        if not isinstance(asset_pages, dict):
            asset_pages = {}
        asset_pages[str(page_key)] = "completed"
        payload["asset_pages"] = asset_pages
        payload["updated_at"] = timezone.now().isoformat()
        _write_json_atomic(self.path, payload)

    def save_page(self, page) -> None:
        payload = self._read()
        pages = payload.get("pages")
        if not isinstance(pages, dict):
            pages = {}
        entities = list(getattr(page, "entities", ()) or ())
        pages[page_cache_key(page)] = {
            "index": int(getattr(page, "index", 0) or 0),
            "path": str(getattr(page, "path", "") or ""),
            "html_format": str(getattr(page, "html_format", "") or ""),
            "lowest_level": int(getattr(page, "lowest_level", 0) or 0),
            "url": str(getattr(page, "url", "") or ""),
            "found": int(getattr(page, "found", None) or len(entities)),
            "html": str(getattr(page, "html", "") or ""),
            "entities": [_serialize_entity(entity) for entity in entities],
            "completed_at": timezone.now().isoformat(),
        }
        payload.update(
            {
                "version": SCRAPE_RESUME_VERSION,
                "slug": self.slug,
                "country_code": self.country_code,
                "content_hash": self.content_hash,
                "updated_at": timezone.now().isoformat(),
                "pages": pages,
            }
        )
        _write_json_atomic(self.path, payload)

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return self._empty_payload()
        if not self._matches_current_config(payload):
            return self._empty_payload()
        return payload

    def _empty_payload(self) -> dict[str, Any]:
        return {
            "version": SCRAPE_RESUME_VERSION,
            "slug": self.slug,
            "country_code": self.country_code,
            "content_hash": self.content_hash,
            "pages": {},
        }

    def _matches_current_config(self, payload: dict[str, Any]) -> bool:
        return (
            int(payload.get("version") or 0) == SCRAPE_RESUME_VERSION
            and str(payload.get("slug") or "") == self.slug
            and str(payload.get("country_code") or "") == self.country_code
            and str(payload.get("content_hash") or "") == self.content_hash
        )


def page_cache_key(page) -> str:
    """Return a stable key for one configured page occurrence."""

    payload = {
        "index": int(getattr(page, "index", 0) or 0),
        "path": str(getattr(page, "path", "") or ""),
        "html_format": str(getattr(page, "html_format", "") or ""),
        "lowest_level": int(getattr(page, "lowest_level", 0) or 0),
        "url": str(getattr(page, "url", "") or ""),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _safe_name(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})
    return safe or "config"


def _serialize_entity(entity: ScrapedAdminArea) -> dict[str, Any]:
    return {
        field.name: _serialize_value(getattr(entity, field.name))
        for field in fields(ScrapedAdminArea)
    }


def _serialize_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _deserialize_entity(data: dict[str, Any]) -> ScrapedAdminArea:
    allowed = {field.name for field in fields(ScrapedAdminArea)}
    values = {key: value for key, value in data.items() if key in allowed}
    return ScrapedAdminArea(**values)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    last_error: OSError | None = None
    for _ in range(4):
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                handle.write(serialized)
                handle.write("\n")
            os.replace(tmp_name, path)
            return
        except OSError as exc:
            last_error = exc
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            time.sleep(0.05)
    if last_error:
        raise last_error
