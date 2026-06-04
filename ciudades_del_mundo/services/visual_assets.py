"""Persistent visual-identity asset helpers.

This module deliberately uses small raw-SQL helpers instead of importing new
Django model classes. That keeps the patch compatible with projects where the
existing ``models.py`` already contains several local models and avoids
rewriting that file just to add the visual identity tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
import json
import os
import re
import time
import tomllib

from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.utils import timezone

from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists

try:  # Imported lazily-safe for normal Django startup.
    from ciudades_del_mundo.models import ScrapingConfig
except Exception:  # pragma: no cover - migrations/import edge case.
    ScrapingConfig = None  # type: ignore[assignment]


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"
VISUAL_ASSET_TABLE = "ciudades_del_mundo_visual_asset"
VISUAL_TRANSLATION_TABLE = "ciudades_del_mundo_visual_asset_translation"
DEFAULT_LANGUAGES = ("es", "en", "fr", "de", "it", "ru", "sr", "sr_Latn", "ar")
VISUAL_ASSET_KINDS = ("flag", "coat", "seal")
WIKIDATA_IMAGE_PROPERTIES = {"flag": "P41", "coat": "P94", "seal": "P158", "locator": "P242"}
_ASSET_STARTUP_LOCK = False


@dataclass(frozen=True)
class AssetCandidate:
    kind: str
    remote_url: str = ""
    commons_filename: str = ""
    wikidata_id: str = ""
    source: str = ""
    source_url: str = ""
    title: str = ""
    description: str = ""
    blazon: str = ""
    license_name: str = ""
    author: str = ""
    attribution: str = ""


@dataclass(frozen=True)
class AssetSeedResult:
    scanned_pages: int = 0
    found: int = 0
    downloaded: int = 0
    missing: int = 0
    errors: int = 0

    def as_log_line(self, slug: str) -> str:
        return (
            f"[assets] {slug}: pages={self.scanned_pages}, found={self.found}, "
            f"downloaded={self.downloaded}, missing={self.missing}, errors={self.errors}"
        )


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs):  # noqa: D401 - HTMLParser signature.
        if tag.lower() != "img":
            return
        data = {str(key).lower(): str(value or "") for key, value in attrs}
        src = data.get("src") or data.get("data-src") or data.get("data-original") or ""
        if not src:
            return
        haystack = " ".join(data.get(key, "") for key in ("alt", "title", "aria-label"))
        self.images.append((src, haystack))


def visual_asset_tables_exist() -> bool:
    try:
        tables = set(connection.introspection.table_names())
        return VISUAL_ASSET_TABLE in tables and VISUAL_TRANSLATION_TABLE in tables
    except (OperationalError, ProgrammingError):
        return False


def ensure_visual_assets_for_config_slug(
    slug: str,
    *,
    download_missing: bool = True,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
) -> AssetSeedResult:
    """Discover and persist flag/coat metadata for one scraping configuration.

    Wikidata/Commons is authoritative for country flags/coats. CityPopulation
    pages are only a fallback for explicitly labelled images because their
    layout includes generic language flags that are not country assets.
    """
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    config = _load_config(slug)
    if not config:
        return AssetSeedResult(missing=2)

    country_code = str(config.get("country_code") or slug)
    entity_name = str(config.get("name") or country_code or slug)
    base_url = str(config.get("base_url") or CITYPOPULATION_BASE_URL)
    page_urls = _citypopulation_page_urls(config, base_url)

    candidates: dict[str, AssetCandidate] = {}
    wikidata_id = _find_wikidata_id(entity_name, country_code=country_code)
    wikidata_descriptions: dict[str, dict] = {}
    if wikidata_id:
        candidates, wikidata_descriptions = _wikidata_candidates(
            wikidata_id,
            kinds=list(VISUAL_ASSET_KINDS),
            languages=languages,
        )

    scanned_pages = 0
    if not all(kind in candidates for kind in VISUAL_ASSET_KINDS):
        for url in page_urls[:4]:
            html = _fetch_text(url, timeout=12)
            if not html:
                continue
            scanned_pages += 1
            for candidate in _citypopulation_candidates(html, url):
                candidates.setdefault(candidate.kind, candidate)
            if all(kind in candidates for kind in VISUAL_ASSET_KINDS):
                break

    result = _persist_visual_candidates(
        entity_type="country",
        entity_key=country_code,
        entity_name=entity_name,
        country_code=country_code,
        wikidata_id=wikidata_id,
        candidates=candidates,
        descriptions=wikidata_descriptions,
        download_missing=download_missing,
        languages=languages,
    )
    return AssetSeedResult(
        scanned_pages=scanned_pages,
        found=result.found,
        downloaded=result.downloaded,
        missing=result.missing,
        errors=result.errors,
    )


def ensure_visual_assets_for_all_configs(
    *,
    download_missing: bool = True,
    limit: int | None = None,
) -> AssetSeedResult:
    """Seed visual assets for persisted configs. Intended for explicit commands."""
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    slugs = _stored_config_slugs()
    if limit is not None:
        slugs = slugs[: max(0, int(limit))]
    total = AssetSeedResult()
    for slug in slugs:
        result = ensure_visual_assets_for_config_slug(slug, download_missing=download_missing)
        total = AssetSeedResult(
            scanned_pages=total.scanned_pages + result.scanned_pages,
            found=total.found + result.found,
            downloaded=total.downloaded + result.downloaded,
            missing=total.missing + result.missing,
            errors=total.errors + result.errors,
        )
    return total


def ensure_visual_assets_for_admin_area(
    area_id: str,
    *,
    download_missing: bool = True,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
) -> AssetSeedResult:
    """Discover and persist flag/coat metadata for one source AdminArea."""
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return AssetSeedResult(errors=1)

    area = AdminArea.objects.select_related("parent").filter(id=area_id).first()
    if not area:
        return AssetSeedResult(missing=2)
    country_root = (
        AdminArea.objects.filter(country_code=area.country_code, level=0, parent__isnull=True)
        .order_by("name")
        .first()
    )
    query = _wikidata_query_for_area(area.name, country_root.name if country_root else area.country_code)
    wikidata_id = _find_wikidata_id(query, country_code=area.country_code)
    descriptions: dict[str, dict] = {}
    candidates: dict[str, AssetCandidate] = {}
    if wikidata_id:
        candidates, descriptions = _wikidata_candidates(
            wikidata_id,
            kinds=list(VISUAL_ASSET_KINDS),
            languages=languages,
        )
    return _persist_visual_candidates(
        entity_type="admin_area",
        entity_key=str(area.id),
        entity_name=area.name,
        country_code=area.country_code,
        wikidata_id=wikidata_id,
        candidates=candidates,
        descriptions=descriptions,
        download_missing=download_missing,
        languages=languages,
    )


def ensure_visual_assets_for_country_admin_areas(
    country_code: str,
    *,
    download_missing: bool = True,
    levels: tuple[int, ...] | None = None,
    limit: int | None = None,
) -> AssetSeedResult:
    """Seed stored visual assets for visible AdminArea subdivisions in one country."""
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return AssetSeedResult(errors=1)

    areas = AdminArea.objects.filter(country_code=country_code).exclude(level=0).exclude(city_merge_status=3)
    if levels:
        areas = areas.filter(level__in=levels)
    areas = areas.order_by("level", "name", "id")
    if limit is not None:
        areas = areas[: max(0, int(limit))]

    total = AssetSeedResult()
    for area in areas:
        result = ensure_visual_assets_for_admin_area(str(area.id), download_missing=download_missing)
        total = _add_seed_results(total, result)
    return total


def _persist_visual_candidates(
    *,
    entity_type: str,
    entity_key: str,
    entity_name: str,
    country_code: str,
    wikidata_id: str,
    candidates: dict[str, AssetCandidate],
    descriptions: dict[str, dict],
    download_missing: bool,
    languages: tuple[str, ...],
) -> AssetSeedResult:
    found = downloaded = missing = errors = 0
    for kind in VISUAL_ASSET_KINDS:
        candidate = candidates.get(kind)
        if not candidate:
            _upsert_asset(
                entity_type=entity_type,
                entity_key=entity_key,
                entity_name=entity_name,
                country_code=country_code,
                kind=kind,
                status="missing",
                source="auto",
                wikidata_id=wikidata_id or "",
            )
            missing += 1
            continue
        asset_id = _upsert_asset(
            entity_type=entity_type,
            entity_key=entity_key,
            entity_name=entity_name,
            country_code=country_code,
            kind=kind,
            status="found",
            source=candidate.source,
            wikidata_id=candidate.wikidata_id or wikidata_id or "",
            commons_filename=candidate.commons_filename,
            remote_url=candidate.remote_url,
            source_url=candidate.source_url,
            license_name=candidate.license_name,
            author=candidate.author,
            attribution=candidate.attribution,
        )
        found += 1
        _store_candidate_translation(asset_id, candidate, languages=languages)
        _store_description_translations(
            asset_id,
            descriptions,
            source=f"wikidata:{wikidata_id}" if wikidata_id else "wikidata",
        )
        if download_missing and candidate.remote_url:
            try:
                folder_key = entity_key if entity_type != "country" else country_code
                if _download_asset(asset_id, kind, folder_key, candidate.remote_url, candidate.commons_filename):
                    downloaded += 1
            except Exception as exc:  # noqa: BLE001 - persisted as asset status.
                _mark_asset_error(asset_id, str(exc))
                errors += 1
    return AssetSeedResult(found=found, downloaded=downloaded, missing=missing, errors=errors)


def _add_seed_results(left: AssetSeedResult, right: AssetSeedResult) -> AssetSeedResult:
    return AssetSeedResult(
        scanned_pages=left.scanned_pages + right.scanned_pages,
        found=left.found + right.found,
        downloaded=left.downloaded + right.downloaded,
        missing=left.missing + right.missing,
        errors=left.errors + right.errors,
    )


def _wikidata_query_for_area(name: str, country_name: str) -> str:
    name = _clean_text(name)
    country_name = _clean_text(country_name)
    if name and country_name and country_name.casefold() not in name.casefold():
        return f"{name} {country_name}"
    return name or country_name


def ensure_missing_local_asset_files(*, limit: int | None = None) -> int:
    """Download local media files for assets that have a remote URL but no file."""
    if not visual_asset_tables_exist():
        return 0
    rows = _select_assets_missing_files(limit=limit)
    downloaded = 0
    for row in rows:
        asset_id = int(row["id"])
        local_path = str(row.get("local_path") or "")
        expected_path = _absolute_media_path(local_path) if local_path else None
        if expected_path and expected_path.is_file():
            _set_asset_local_exists(asset_id, True)
            continue
        try:
            if _download_asset(
                asset_id,
                str(row.get("kind") or "asset"),
                str(row.get("country_code") or row.get("entity_key") or "unknown"),
                str(row.get("remote_url") or ""),
                str(row.get("commons_filename") or ""),
            ):
                downloaded += 1
        except Exception as exc:  # noqa: BLE001
            _mark_asset_error(asset_id, str(exc))
    return downloaded


def ensure_visual_assets_after_startup_async() -> None:
    """Repair local files once after startup without touching DB in AppConfig.ready."""
    global _ASSET_STARTUP_LOCK
    if _ASSET_STARTUP_LOCK:
        return
    _ASSET_STARTUP_LOCK = True
    try:
        ensure_missing_local_asset_files(limit=None)
    finally:
        _ASSET_STARTUP_LOCK = False


def get_visual_assets_for_entity(entity_type: str, entity_key: str) -> dict[str, dict]:
    """Return stored visual assets for API/UI use."""
    if not visual_asset_tables_exist():
        return _local_folder_assets_for_entity(entity_type, entity_key)
    sql = f"""
        SELECT id, entity_type, entity_key, entity_name, country_code, kind,
               wikidata_id, commons_filename, remote_url, local_path,
               local_exists, source, status, error, license_name, author,
               attribution, source_url, updated_at
          FROM {VISUAL_ASSET_TABLE}
         WHERE entity_type = %s AND entity_key = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [entity_type, entity_key])
        rows = _dictfetchall(cursor)
    translations_by_asset = _translations_for_asset_rows(rows)
    assets: dict[str, dict] = {}
    for row in rows:
        kind = str(row["kind"])
        row_entity_type = str(row.get("entity_type") or entity_type)
        row_entity_key = str(row.get("entity_key") or entity_key)
        local_folder_key = (
            str(row.get("country_code") or row_entity_key)
            if row_entity_type == "country"
            else row_entity_key
        )
        bad_local_flag = _is_wrong_citypopulation_flag(
            kind,
            local_folder_key,
            local_path=str(row.get("local_path") or ""),
            remote_url=str(row.get("remote_url") or ""),
            commons_filename=str(row.get("commons_filename") or ""),
        )
        local_path = str(row.get("local_path") or "")
        local_url = "" if bad_local_flag else _existing_media_url(local_path)
        if not local_url and not bad_local_flag:
            folder_path = _first_local_asset_path(kind, local_folder_key)
            if folder_path:
                local_path = folder_path
                local_url = _media_url(folder_path)
        remote_url = "" if bad_local_flag else row.get("remote_url") or ""
        assets[kind] = {
            **row,
            "local_path": local_path,
            "local_exists": bool(local_url),
            "remote_url": remote_url,
            "local_url": local_url,
            "image_url": local_url or remote_url,
            "translations": translations_by_asset.get(int(row["id"]), {}),
        }
    for kind, asset in _local_folder_assets_for_entity(entity_type, entity_key).items():
        assets.setdefault(kind, asset)
    return assets


def _local_folder_assets_for_entity(entity_type: str, entity_key: str) -> dict[str, dict]:
    assets: dict[str, dict] = {}
    for kind in VISUAL_ASSET_KINDS:
        local_path = _first_local_asset_path(kind, entity_key)
        if not local_path:
            continue
        local_url = _media_url(local_path)
        assets[kind] = {
            "id": None,
            "entity_type": entity_type,
            "entity_key": entity_key,
            "entity_name": "",
            "country_code": entity_key if entity_type == "country" else "",
            "kind": kind,
            "wikidata_id": "",
            "commons_filename": "",
            "remote_url": "",
            "local_path": local_path,
            "local_exists": True,
            "source": "local",
            "status": "downloaded",
            "error": "",
            "license_name": "",
            "author": "",
            "attribution": "",
            "source_url": "",
            "updated_at": None,
            "local_url": local_url,
            "image_url": local_url,
            "translations": {},
        }
    return assets


def _translations_for_asset_rows(rows: list[dict]) -> dict[int, dict[str, dict]]:
    asset_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    if not asset_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(asset_ids))
    sql = f"""
        SELECT asset_id, language, title, description, blazon, source, needs_review
          FROM {VISUAL_TRANSLATION_TABLE}
         WHERE asset_id IN ({placeholders})
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, asset_ids)
        translations = _dictfetchall(cursor)
    grouped: dict[int, dict[str, dict]] = {}
    for row in translations:
        asset_id = int(row["asset_id"])
        language = str(row.get("language") or "")
        if not language:
            continue
        grouped.setdefault(asset_id, {})[language] = {
            "title": row.get("title") or "",
            "description": row.get("description") or "",
            "blazon": row.get("blazon") or "",
            "source": row.get("source") or "",
            "needs_review": bool(row.get("needs_review")),
        }
    return grouped


def _load_config(slug: str) -> dict | None:
    content = ""
    if ScrapingConfig is not None and scraping_config_table_exists():
        try:
            record = ScrapingConfig.objects.filter(slug=slug).first()
            if record:
                content = record.content
        except (OperationalError, ProgrammingError):
            content = ""
    if not content:
        return None
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None


def _stored_config_slugs() -> list[str]:
    if ScrapingConfig is not None and scraping_config_table_exists():
        try:
            return list(ScrapingConfig.objects.order_by("slug").values_list("slug", flat=True))
        except (OperationalError, ProgrammingError):
            pass
    return []


def _citypopulation_page_urls(config: dict, base_url: str) -> list[str]:
    urls: list[str] = []
    for page in config.get("pages") or []:
        if not isinstance(page, dict):
            continue
        source = str(page.get("source") or "")
        paths = page.get("path") or []
        if isinstance(paths, str):
            paths = [paths]
        for item in paths:
            path = str(item or "").strip()
            if not path:
                continue
            if path.startswith("http://") or path.startswith("https://"):
                urls.append(path)
                continue
            if source and not path.startswith(source):
                path = f"{source.rstrip('/')}/{path.lstrip('/')}"
            urls.append(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))
    # Some configs only define country code/name; probe the country landing page.
    if not urls:
        country_code = str(config.get("country_code") or "").strip("/")
        if country_code:
            urls.append(urljoin(base_url.rstrip("/") + "/", country_code + "/"))
    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _citypopulation_candidates(html: str, page_url: str) -> list[AssetCandidate]:
    parser = _ImageParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - partial HTML is enough.
        pass
    candidates: list[AssetCandidate] = []
    seen: set[tuple[str, str]] = set()
    for src, text in parser.images:
        url = urljoin(page_url, unescape(src))
        if not _looks_like_image_url(url):
            continue
        kind = _kind_from_text(text + " " + url)
        if not kind:
            continue
        key = (kind, url)
        if key in seen:
            continue
        seen.add(key)
        filename = _commons_filename_from_url(url)
        candidates.append(
            AssetCandidate(
                kind=kind,
                remote_url=url,
                commons_filename=filename,
                source="citypopulation",
                source_url=page_url,
                title=_clean_text(text)[:300],
            )
        )
    return candidates


def _kind_from_text(value: str) -> str:
    text = _normalize(value)
    if any(token in text for token in ("flag", "bandera", "drapeau", "fahne", "bandiera", "zastava", "flaga")):
        return "flag"
    if any(token in text for token in ("coat", "arms", "escudo", "blason", "armoir", "wappen", "grb", "shield", "stemma")):
        return "coat"
    if any(token in text for token in ("seal", "sello", "sigil", "sigilo", "sceau", "siegel", "pechat")):
        return "seal"
    return ""


def _find_wikidata_id(name: str, *, country_code: str = "") -> str:
    query = name or country_code
    if not query:
        return ""
    languages = ["en", "es", "fr"]
    for language in languages:
        params = {
            "action": "wbsearchentities",
            "format": "json",
            "limit": "1",
            "language": language,
            "search": query,
        }
        url = "https://www.wikidata.org/w/api.php?" + "&".join(
            f"{key}={quote(str(value))}" for key, value in params.items()
        )
        data = _fetch_json(url, timeout=10)
        results = data.get("search") if isinstance(data, dict) else None
        if results:
            entity_id = str(results[0].get("id") or "")
            if entity_id.startswith("Q"):
                return entity_id
    return ""


def _wikidata_candidates(
    wikidata_id: str,
    *,
    kinds: list[str],
    languages: tuple[str, ...],
) -> tuple[dict[str, AssetCandidate], dict[str, dict]]:
    if not wikidata_id:
        return {}, {}
    props = "claims|descriptions|labels"
    url = (
        "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props="
        + quote(props)
        + "&ids="
        + quote(wikidata_id)
        + "&languages="
        + quote("|".join(languages))
    )
    data = _fetch_json(url, timeout=12)
    entity = ((data.get("entities") or {}).get(wikidata_id) or {}) if isinstance(data, dict) else {}
    claims = entity.get("claims") or {}
    candidates: dict[str, AssetCandidate] = {}
    for kind in kinds:
        prop = WIKIDATA_IMAGE_PROPERTIES.get(kind)
        claim = prop and (claims.get(prop) or [None])[0]
        filename = _claim_filename(claim)
        if not filename:
            continue
        remote_url = commons_file_url(filename, width=1600)
        metadata = _commons_file_metadata(filename)
        candidates[kind] = AssetCandidate(
            kind=kind,
            remote_url=remote_url,
            commons_filename=filename,
            wikidata_id=wikidata_id,
            source="wikidata",
            source_url=f"https://www.wikidata.org/wiki/{wikidata_id}",
            title=metadata.get("title") or filename,
            description=metadata.get("description") or "",
            blazon=metadata.get("blazon") or "",
            license_name=metadata.get("license") or "",
            author=metadata.get("author") or "",
            attribution=metadata.get("attribution") or "",
        )
    descriptions: dict[str, dict] = {}
    labels = entity.get("labels") or {}
    descs = entity.get("descriptions") or {}
    for language in languages:
        descriptions[language] = {
            "title": ((labels.get(language) or {}).get("value") or ""),
            "description": ((descs.get(language) or {}).get("value") or ""),
        }
    return candidates, descriptions


def _claim_filename(claim) -> str:
    try:
        value = claim["mainsnak"]["datavalue"]["value"]
    except Exception:  # noqa: BLE001
        return ""
    return str(value or "")


def commons_file_url(filename: str, *, width: int = 1400) -> str:
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(filename.replace(" ", "_"), safe="/_()-.,'") + f"?width={int(width)}"


def _commons_file_metadata(filename: str) -> dict[str, str]:
    if not filename:
        return {}
    title = "File:" + filename
    url = (
        "https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo&iiprop=extmetadata|url&titles="
        + quote(title)
    )
    data = _fetch_json(url, timeout=10)
    try:
        pages = (data.get("query") or {}).get("pages") or {}
        page = next(iter(pages.values()))
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
    except Exception:  # noqa: BLE001
        return {}

    def meta_value(key: str) -> str:
        value = (metadata.get(key) or {}).get("value") or ""
        return _strip_html(str(value))

    return {
        "title": _strip_html(str(page.get("title") or filename)).replace("File:", "", 1),
        "description": meta_value("ImageDescription"),
        "blazon": (
            meta_value("Blazon")
            or meta_value("Inscription")
            or meta_value("ObjectName")
            or meta_value("ImageDescription")
        ),
        "license": meta_value("LicenseShortName") or meta_value("UsageTerms"),
        "author": meta_value("Artist"),
        "attribution": meta_value("Attribution") or meta_value("Credit"),
    }


def _upsert_asset(
    *,
    entity_type: str,
    entity_key: str,
    entity_name: str = "",
    country_code: str = "",
    kind: str,
    status: str,
    source: str = "",
    wikidata_id: str = "",
    commons_filename: str = "",
    remote_url: str = "",
    source_url: str = "",
    license_name: str = "",
    author: str = "",
    attribution: str = "",
) -> int:
    now = timezone.now()
    existing = _select_one(
        f"SELECT id, local_path FROM {VISUAL_ASSET_TABLE} WHERE entity_type=%s AND entity_key=%s AND kind=%s",
        [entity_type, entity_key, kind],
    )
    if existing:
        asset_id = int(existing["id"])
        sql = f"""
            UPDATE {VISUAL_ASSET_TABLE}
               SET entity_name=%s, country_code=%s, wikidata_id=%s,
                   commons_filename=%s, remote_url=%s, source=%s, status=%s,
                   error=%s, license_name=%s, author=%s, attribution=%s,
                   source_url=%s, updated_at=%s
             WHERE id=%s
        """
        _execute(sql, [
            entity_name, country_code, wikidata_id, commons_filename, remote_url,
            source, status, "", license_name, author, attribution, source_url, now, asset_id,
        ])
        return asset_id
    sql = f"""
        INSERT INTO {VISUAL_ASSET_TABLE}
            (entity_type, entity_key, entity_name, country_code, kind, wikidata_id,
             commons_filename, remote_url, local_path, local_exists, source, status,
             error, license_name, author, attribution, source_url, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', 0, %s, %s, '', %s, %s, %s, %s, %s, %s)
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [
            entity_type, entity_key, entity_name, country_code, kind, wikidata_id,
            commons_filename, remote_url, source, status, license_name, author,
            attribution, source_url, now, now,
        ])
        return int(cursor.lastrowid)


def _store_candidate_translation(asset_id: int, candidate: AssetCandidate, *, languages: tuple[str, ...]) -> None:
    title = candidate.title or candidate.commons_filename or candidate.kind
    description = candidate.description or ""
    blazon = candidate.blazon or ""
    for language in languages:
        _upsert_translation(
            asset_id,
            language,
            title=title,
            description=description,
            blazon=blazon,
            source=candidate.source or "auto",
            needs_review=not bool(description),
        )


def _store_description_translations(asset_id: int, descriptions: dict[str, dict], *, source: str) -> None:
    if not descriptions:
        return
    for language, values in descriptions.items():
        title = values.get("title") or ""
        description = values.get("description") or ""
        if not title and not description:
            continue
        _upsert_translation(
            asset_id,
            language,
            title=title,
            description=description,
            blazon=values.get("blazon") or "",
            source=source,
            needs_review=True,
        )


def _store_entity_descriptions(
    *,
    entity_type: str,
    entity_key: str,
    kinds: tuple[str, ...],
    descriptions: dict[str, dict],
    wikidata_id: str,
) -> None:
    if not descriptions:
        return
    for kind in kinds:
        row = _select_one(
            f"SELECT id FROM {VISUAL_ASSET_TABLE} WHERE entity_type=%s AND entity_key=%s AND kind=%s",
            [entity_type, entity_key, kind],
        )
        if not row:
            continue
        for language, values in descriptions.items():
            title = values.get("title") or ""
            description = values.get("description") or ""
            if not title and not description:
                continue
            _upsert_translation(
                int(row["id"]),
                language,
                title=title,
                description=description,
                blazon=values.get("blazon") or "",
                source=f"wikidata:{wikidata_id}",
                needs_review=True,
            )


def _upsert_translation(
    asset_id: int,
    language: str,
    *,
    title: str,
    description: str,
    source: str,
    blazon: str = "",
    needs_review: bool = True,
) -> None:
    existing = _select_one(
        f"SELECT id, title, description, blazon, source FROM {VISUAL_TRANSLATION_TABLE} WHERE asset_id=%s AND language=%s",
        [asset_id, language],
    )
    now = timezone.now()
    if existing:
        # Do not overwrite richer/manual descriptions with empty auto text.
        if not description and not blazon and (existing.get("description") or existing.get("blazon")):
            return
        title = title or existing.get("title") or ""
        description = description or existing.get("description") or ""
        blazon = blazon or existing.get("blazon") or ""
        sql = f"""
            UPDATE {VISUAL_TRANSLATION_TABLE}
               SET title=%s, description=%s, blazon=%s, source=%s, needs_review=%s, updated_at=%s
             WHERE id=%s
        """
        _execute(sql, [title, description, blazon, source, bool(needs_review), now, int(existing["id"])])
        return
    sql = f"""
        INSERT INTO {VISUAL_TRANSLATION_TABLE}
            (asset_id, language, title, description, blazon, source, needs_review, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    _execute(sql, [asset_id, language, title, description, blazon, source, bool(needs_review), now, now])


def _download_asset(asset_id: int, kind: str, country_code: str, remote_url: str, commons_filename: str = "") -> bool:
    if not remote_url:
        return False
    extension = _extension_from_url_or_filename(remote_url, commons_filename)
    filename_base = _safe_filename(commons_filename or Path(urlparse(remote_url).path).name or f"{kind}-{asset_id}")
    if not filename_base.lower().endswith(extension.lower()):
        filename_base += extension
    relative_path = str(Path("visual_assets") / kind / _safe_filename(country_code or "unknown") / filename_base)
    absolute_path = _absolute_media_path(relative_path)
    if absolute_path.is_file() and absolute_path.stat().st_size > 0:
        _set_asset_local_path(asset_id, relative_path, True)
        return False
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(remote_url, headers={"User-Agent": _user_agent()})
    with urlopen(request, timeout=25) as response:  # noqa: S310 - configured asset cache.
        content = response.read(12 * 1024 * 1024)
    if not content:
        raise ValueError("respuesta vacÃ­a al descargar la imagen")
    tmp_path = absolute_path.with_suffix(absolute_path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, absolute_path)
    _set_asset_local_path(asset_id, relative_path, True)
    return True


def _select_assets_missing_files(*, limit: int | None = None) -> list[dict]:
    sql = f"""
        SELECT id, entity_key, country_code, kind, commons_filename, remote_url, local_path
          FROM {VISUAL_ASSET_TABLE}
         WHERE remote_url <> ''
         ORDER BY updated_at DESC, id DESC
    """
    params: list = []
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = _dictfetchall(cursor)
    missing: list[dict] = []
    for row in rows:
        local_path = str(row.get("local_path") or "")
        if not local_path or not _absolute_media_path(local_path).is_file():
            missing.append(row)
    return missing


def _set_asset_local_path(asset_id: int, relative_path: str, exists: bool) -> None:
    _execute(
        f"UPDATE {VISUAL_ASSET_TABLE} SET local_path=%s, local_exists=%s, status=%s, error='', updated_at=%s WHERE id=%s",
        [relative_path, bool(exists), "downloaded" if exists else "found", timezone.now(), asset_id],
    )


def _set_asset_local_exists(asset_id: int, exists: bool) -> None:
    _execute(
        f"UPDATE {VISUAL_ASSET_TABLE} SET local_exists=%s, status=%s, updated_at=%s WHERE id=%s",
        [bool(exists), "downloaded" if exists else "found", timezone.now(), asset_id],
    )


def _mark_asset_error(asset_id: int, error: str) -> None:
    _execute(
        f"UPDATE {VISUAL_ASSET_TABLE} SET status='error', error=%s, updated_at=%s WHERE id=%s",
        [error[:2000], timezone.now(), asset_id],
    )


def _select_one(sql: str, params: list | tuple) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute(sql, list(params))
        rows = _dictfetchall(cursor)
    return rows[0] if rows else None


def _execute(sql: str, params: list | tuple) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql, list(params))


def _dictfetchall(cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _fetch_text(url: str, *, timeout: int = 10) -> str:
    try:
        request = Request(url, headers={"User-Agent": _user_agent()})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - trusted configured scrape source.
            content = response.read(4 * 1024 * 1024)
        return content.decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""


def _fetch_json(url: str, *, timeout: int = 10) -> dict:
    text = _fetch_text(url, timeout=timeout)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _commons_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = unescape(parsed.path)
    if "/wiki/Special:FilePath/" in path:
        return path.rsplit("/wiki/Special:FilePath/", 1)[-1].replace("_", " ")
    if "/wiki/File:" in path:
        return path.rsplit("/wiki/File:", 1)[-1].replace("_", " ")
    if "commons.wikimedia.org" in parsed.netloc and path:
        return Path(path).name.replace("_", " ")
    return ""


def _extension_from_url_or_filename(url: str, filename: str = "") -> str:
    candidate = (filename or Path(urlparse(url).path).name or "").lower()
    for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if candidate.endswith(ext):
            return ext
    return ".img"


def _absolute_media_path(relative_path: str) -> Path:
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or (Path(settings.BASE_DIR) / "media"))
    return media_root / relative_path


def _safe_media_relative_path(relative_path: str) -> str:
    relative_path = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in relative_path.split("/") if part not in ("", ".", "..")]
    return str(Path(*parts)) if parts else ""


def _existing_media_url(relative_path: str) -> str:
    safe_path = _safe_media_relative_path(relative_path)
    if not safe_path:
        return ""
    if not _absolute_media_path(safe_path).is_file():
        return ""
    return _media_url(safe_path)


def _is_wrong_citypopulation_flag(
    kind: str,
    entity_key: str,
    *,
    local_path: str = "",
    remote_url: str = "",
    commons_filename: str = "",
) -> bool:
    if str(kind or "").casefold() != "flag":
        return False
    filename = Path(local_path or urlparse(remote_url).path or commons_filename).name.casefold()
    match = re.fullmatch(r"([a-z0-9_-]+)_2_3\.svg", filename)
    if not match:
        return False
    prefix = _safe_filename(match.group(1)).casefold()
    expected = _safe_filename(entity_key).casefold()
    return bool(prefix and expected and prefix != expected)


def _first_local_asset_path(kind: str, entity_key: str) -> str:
    safe_kind = _safe_filename(kind or "asset")
    safe_key = _safe_filename(entity_key or "unknown")
    root = _absolute_media_path(str(Path("visual_assets") / safe_kind / safe_key))
    if not root.is_dir():
        return ""
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and _looks_like_image_url(path.name):
            if _is_wrong_citypopulation_flag(kind, entity_key, local_path=path.name):
                continue
            return _safe_media_relative_path(str(Path("visual_assets") / safe_kind / safe_key / path.name))
    return ""


def _media_url(relative_path: str) -> str:
    if not relative_path:
        return ""
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    return media_url.rstrip("/") + "/" + quote(relative_path.replace("\\", "/"), safe="/._-()")


def _safe_filename(value: str) -> str:
    value = unescape(str(value or "")).strip().replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9._()\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._")
    return value[:180] or "asset"


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return _clean_text(value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def _normalize(value: str) -> str:
    return _clean_text(value).lower()


def _user_agent() -> str:
    return "CiudadesDelMundo/1.0 (+local visual asset cache)"

