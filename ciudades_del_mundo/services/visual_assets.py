"""Persistent visual-identity asset helpers.

This module deliberately uses small raw-SQL helpers instead of importing new
Django model classes. That keeps the patch compatible with projects where the
existing ``models.py`` already contains several local models and avoids
rewriting that file just to add the visual identity tables.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
import json
import os
import re
import time
import tomllib
import unicodedata

from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.utils import timezone

from ciudades_del_mundo.services.scraping_configs import scraping_config_table_exists

try:  # Imported lazily-safe for normal Django startup.
    from ciudades_del_mundo.models import ScrapingConfig
except Exception:  # pragma: no cover - migrations/import edge case.
    ScrapingConfig = None  # type: ignore[assignment]


CITYPOPULATION_BASE_URL = "https://www.citypopulation.de/en/"
WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
VISUAL_ASSET_TABLE = "ciudades_del_mundo_visual_asset"
VISUAL_TRANSLATION_TABLE = "ciudades_del_mundo_visual_asset_translation"
DEFAULT_LANGUAGES = ("es", "en", "fr", "de", "it", "ru", "sr", "sr_Latn", "ar")
VISUAL_ASSET_KINDS = ("flag", "coat", "seal")
WIKIDATA_IMAGE_PROPERTIES = {"flag": "P41", "coat": "P94", "seal": "P158", "locator": "P242"}
VISUAL_ASSET_KIND_FILENAME_PARTS = {"flag": "flag", "coat": "coat", "seal": "seal"}
WIKIMEDIA_HOSTS = ("wikidata.org", "wikimedia.org")
WIKIMEDIA_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
WIKIMEDIA_REQUEST_DELAY_SECONDS = 1.1
WIKIMEDIA_COMMONS_PREVIEW_WIDTH = 500
WIKIMEDIA_COMMONS_THUMBNAIL_FALLBACK_WIDTH = 500
WIKIDATA_BULK_ENTITY_LIMIT = 50
WIKIDATA_DEFAULT_INDIVIDUAL_LOOKUP_LIMIT = 0
_ASSET_STARTUP_LOCK = False
_LAST_WIKIMEDIA_REQUEST_AT = 0.0
_WIKIDATA_COUNTRY_ASSET_CACHE: dict[tuple[str, tuple[str, ...]], list["WikidataVisualAssetRecord"]] = {}
_WIKIDATA_ENTITY_ASSET_CACHE: dict[tuple[str, tuple[str, ...], tuple[str, ...], bool], tuple[dict[str, "AssetCandidate"], dict[str, dict]]] = {}
AssetLogger = Callable[[str], None]


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


@dataclass
class WikidataVisualAssetRecord:
    wikidata_id: str
    label: str = ""
    description: str = ""
    type_id: str = ""
    type_label: str = ""
    parent_id: str = ""
    parent_label: str = ""
    candidates: dict[str, AssetCandidate] = field(default_factory=dict)


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
        haystack = " ".join(data.get(key, "") for key in ("alt", "title", "aria-label", "class"))
        self.images.append((src, haystack))


class _InfoSectionImageParser(HTMLParser):
    """Collect images from CityPopulation infosections keyed by source row id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[str, list[tuple[str, str]]] = {}
        self.wikidata_ids: dict[str, str] = {}
        self._current_id = ""
        self._div_depth = 0

    def handle_starttag(self, tag: str, attrs):  # noqa: D401 - HTMLParser signature.
        data = {str(key).lower(): str(value or "") for key, value in attrs}
        tag_name = tag.lower()
        raw_id = data.get("id", "")
        wikidata_id = data.get("data-wd", "")
        if raw_id.startswith("i") and re.fullmatch(r"Q\d+", wikidata_id):
            self.wikidata_ids[raw_id[1:]] = wikidata_id

        if tag_name == "div":
            if self._current_id:
                self._div_depth += 1
            classes = data.get("class", "")
            if raw_id.startswith("ir") and "infosection" in classes:
                self._current_id = raw_id[2:]
                self._div_depth = 1
                self.sections.setdefault(self._current_id, [])
            return
        if tag_name != "img" or not self._current_id:
            return
        src = data.get("src") or data.get("data-src") or data.get("data-original") or ""
        if not src:
            return
        haystack = " ".join(data.get(key, "") for key in ("alt", "title", "aria-label", "class"))
        self.sections.setdefault(self._current_id, []).append((src, haystack))

    def handle_endtag(self, tag: str):  # noqa: D401 - HTMLParser signature.
        if tag.lower() != "div" or not self._current_id:
            return
        self._div_depth -= 1
        if self._div_depth <= 0:
            self._current_id = ""
            self._div_depth = 0


def visual_asset_tables_exist() -> bool:
    try:
        tables = set(connection.introspection.table_names())
        return VISUAL_ASSET_TABLE in tables and VISUAL_TRANSLATION_TABLE in tables
    except (OperationalError, ProgrammingError):
        return False


def ensure_visual_assets_for_config_slug(
    slug: str,
    *,
    download_missing: bool = False,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    fetch_citypopulation: bool = True,
    logger: AssetLogger | None = None,
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
    _asset_log(logger, f"[assets] country:{country_code} busca Wikidata/Commons: {entity_name}")
    wikidata_id = _wikidata_qid(config.get("wikidata_id")) or _configured_country_wikidata_id(country_code)
    if not wikidata_id:
        wikidata_id = _find_wikidata_id(entity_name, country_code=country_code)
    wikidata_descriptions: dict[str, dict] = {}
    if wikidata_id:
        _asset_log(logger, f"[assets] country:{country_code} Wikidata: {wikidata_id}")
        candidates, wikidata_descriptions = _wikidata_candidates(
            wikidata_id,
            kinds=list(VISUAL_ASSET_KINDS),
            languages=languages,
        )
    else:
        _asset_log(logger, f"[assets] country:{country_code} Wikidata: sin resultado")

    scanned_pages = 0
    if fetch_citypopulation and not all(kind in candidates for kind in VISUAL_ASSET_KINDS):
        for url in page_urls[:4]:
            _asset_log(logger, f"[assets] country:{country_code} busca CityPopulation: {url}")
            html = _fetch_text(url, timeout=12)
            if not html:
                _asset_log(logger, f"[assets] country:{country_code} CityPopulation sin HTML: {url}")
                continue
            scanned_pages += 1
            for candidate in _citypopulation_candidates(html, url):
                candidates.setdefault(candidate.kind, candidate)
            if all(kind in candidates for kind in VISUAL_ASSET_KINDS):
                break
    elif not fetch_citypopulation:
        _asset_log(logger, f"[assets] country:{country_code} no relee paginas CityPopulation")

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
        logger=logger,
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
    download_missing: bool = False,
    limit: int | None = None,
    logger: AssetLogger | None = None,
) -> AssetSeedResult:
    """Seed visual assets for persisted configs. Intended for explicit commands."""
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    slugs = _stored_config_slugs()
    if limit is not None:
        slugs = slugs[: max(0, int(limit))]
    total = AssetSeedResult()
    for slug in slugs:
        result = ensure_visual_assets_for_config_slug(
            slug,
            download_missing=download_missing,
            logger=logger,
        )
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
    download_missing: bool = False,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    logger: AssetLogger | None = None,
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
    _asset_log(logger, f"[assets] admin_area:{area.id} busca Wikidata/Commons: {query}")
    wikidata_id = _find_wikidata_id(query, country_code=area.country_code)
    descriptions: dict[str, dict] = {}
    candidates: dict[str, AssetCandidate] = {}
    if wikidata_id:
        _asset_log(logger, f"[assets] admin_area:{area.id} Wikidata: {wikidata_id}")
        candidates, descriptions = _wikidata_candidates(
            wikidata_id,
            kinds=list(VISUAL_ASSET_KINDS),
            languages=languages,
        )
    else:
        _asset_log(logger, f"[assets] admin_area:{area.id} Wikidata: sin resultado")
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
        logger=logger,
    )


def ensure_visual_assets_for_country_admin_areas(
    country_code: str,
    *,
    download_missing: bool = False,
    levels: tuple[int, ...] | None = None,
    limit: int | None = None,
    logger: AssetLogger | None = None,
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
        result = ensure_visual_assets_for_admin_area(
            str(area.id),
            download_missing=download_missing,
            logger=logger,
        )
        total = _add_seed_results(total, result)
    return total


def seed_visual_assets_from_scraped_page(
    *,
    country_code: str,
    page_url: str,
    html: str,
    entities: list,
    download_missing: bool = False,
    subdivision_levels: tuple[int, ...] | None = None,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    fill_missing_with_wikidata: bool = False,
    max_individual_wikidata_lookups: int = WIKIDATA_DEFAULT_INDIVIDUAL_LOOKUP_LIMIT,
    logger: AssetLogger | None = None,
) -> AssetSeedResult:
    """Persist visual assets found in the already-downloaded CityPopulation page."""
    if not visual_asset_tables_exist():
        return AssetSeedResult(errors=1)
    if not html or not entities:
        return AssetSeedResult(missing=1)

    _asset_log(logger, f"[assets] busca CityPopulation en pagina ya scrapeada: {page_url}")
    grouped = _citypopulation_entity_candidates(html, page_url, entities)
    if not grouped:
        _asset_log(logger, f"[assets] CityPopulation sin candidatos asociados: {page_url}")

    total = AssetSeedResult(scanned_pages=1)
    grouped_by_id = {str(getattr(entity, "id", "")): candidates for entity, candidates in grouped}
    seedable_entities = _seedable_page_entities(country_code, entities, subdivision_levels)
    page_wikidata_ids = _citypopulation_wikidata_ids(html) if fill_missing_with_wikidata else {}
    country_wikidata_id = ""
    wikidata_records_by_qid: dict[str, WikidataVisualAssetRecord] = {}
    wikidata_records_by_label: dict[str, list[WikidataVisualAssetRecord]] = {}
    page_wikidata_candidate_cache: dict[str, tuple[dict[str, AssetCandidate], dict[str, dict]]] = {}
    explicit_lookup_budget = max(0, int(max_individual_wikidata_lookups or 0))
    if fill_missing_with_wikidata:
        root = _root_page_entity(country_code, entities)
        country_query = str(getattr(root, "name", "") or country_code)
        # Prefer configured QIDs. They avoid one search request per page and
        # prevent false matches for translated country names such as "España".
        country_wikidata_id = _configured_country_wikidata_id(country_code)
        if not country_wikidata_id:
            country_wikidata_id = _find_wikidata_id(
                country_query,
                country_code=country_code,
            )
        country_wikidata_id = country_wikidata_id or (_page_wikidata_id_for_entity(root, page_wikidata_ids) if root else "")
        page_qids = [
            _page_wikidata_id_for_entity(entity, page_wikidata_ids)
            for entity in seedable_entities
            if getattr(entity, "level", None) != 0
        ]
        page_wikidata_candidate_cache = _wikidata_candidates_for_ids(
            page_qids,
            kinds=list(VISUAL_ASSET_KINDS),
            languages=languages,
            fetch_metadata=False,
        )
        if country_wikidata_id:
            records = _wikidata_country_visual_asset_records(country_wikidata_id, languages=languages)
            wikidata_records_by_qid = {record.wikidata_id: record for record in records}
            wikidata_records_by_label = _wikidata_records_by_label(records)

    for entity in seedable_entities:
        if getattr(entity, "level", None) == 0:
            entity_type = "country"
            entity_key = country_code
        else:
            entity_type = "admin_area"
            entity_key = str(entity.id)

        by_kind: dict[str, AssetCandidate] = {}
        for candidate in grouped_by_id.get(str(getattr(entity, "id", "")), []):
            by_kind.setdefault(candidate.kind, candidate)
        descriptions: dict[str, dict] = {}
        wikidata_id = _page_wikidata_id_for_entity(entity, page_wikidata_ids) if fill_missing_with_wikidata else ""
        missing_kinds = [kind for kind in VISUAL_ASSET_KINDS if kind not in by_kind]
        if fill_missing_with_wikidata and missing_kinds:
            if getattr(entity, "level", None) == 0 and country_wikidata_id:
                wikidata_id = country_wikidata_id
                wikidata_candidates, descriptions = _wikidata_candidates(
                    wikidata_id,
                    kinds=missing_kinds,
                    languages=languages,
                )
                for kind, candidate in wikidata_candidates.items():
                    by_kind.setdefault(kind, candidate)
            else:
                cached = page_wikidata_candidate_cache.get(wikidata_id) if wikidata_id else None
                if cached:
                    wikidata_candidates, descriptions = cached
                    for kind in missing_kinds:
                        candidate = wikidata_candidates.get(kind)
                        if candidate:
                            by_kind.setdefault(kind, candidate)

                missing_kinds = [kind for kind in VISUAL_ASSET_KINDS if kind not in by_kind]
                record = wikidata_records_by_qid.get(wikidata_id) if wikidata_id and missing_kinds else None
                if not record and missing_kinds:
                    record = _match_wikidata_visual_asset_record(
                        entity,
                        entities,
                        wikidata_records_by_label,
                    )
                    wikidata_id = record.wikidata_id if record else wikidata_id
                if record:
                    descriptions = descriptions or _record_description_translations(record, languages=languages)
                    for kind in missing_kinds:
                        candidate = record.candidates.get(kind)
                        if candidate:
                            by_kind.setdefault(kind, candidate)

                missing_kinds = [kind for kind in VISUAL_ASSET_KINDS if kind not in by_kind]
                if missing_kinds:
                    can_lookup_entity = explicit_lookup_budget > 0
                    if can_lookup_entity:
                        query = _wikidata_query_for_page_entity(entity, country_code, entities)
                        _asset_log(logger, f"[assets] {entity_type}:{entity_key} busca Wikidata/Commons: {query}")
                        wikidata_id = wikidata_id or _find_wikidata_id(query, country_code=country_code)
                        if explicit_lookup_budget is not None:
                            explicit_lookup_budget -= 1
                        if wikidata_id:
                            _asset_log(logger, f"[assets] {entity_type}:{entity_key} Wikidata: {wikidata_id}")
                            wikidata_candidates, descriptions = _wikidata_candidates(
                                wikidata_id,
                                kinds=missing_kinds,
                                languages=languages,
                                fetch_metadata=False,
                            )
                            for kind, candidate in wikidata_candidates.items():
                                by_kind.setdefault(kind, candidate)
                        else:
                            _asset_log(logger, f"[assets] {entity_type}:{entity_key} Wikidata: sin resultado")
                    else:
                        _asset_log(
                            logger,
                            f"[assets] {entity_type}:{entity_key} Wikidata: sin candidato en lotes; "
                            "búsqueda individual omitida para evitar 429",
                        )
        result = _persist_visual_candidates(
            entity_type=entity_type,
            entity_key=entity_key,
            entity_name=str(getattr(entity, "name", "") or entity_key),
            country_code=country_code,
            wikidata_id=wikidata_id,
            candidates=by_kind,
            descriptions=descriptions,
            download_missing=download_missing,
            languages=languages,
            mark_missing=fill_missing_with_wikidata,
            logger=logger,
        )
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
    mark_missing: bool = True,
    logger: AssetLogger | None = None,
) -> AssetSeedResult:
    found = downloaded = missing = errors = 0
    for kind in VISUAL_ASSET_KINDS:
        candidate = candidates.get(kind)
        if not candidate:
            if not mark_missing:
                continue
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
            _asset_log(logger, f"[assets] {entity_type}:{entity_key} {kind}: sin candidato")
            continue
        _asset_log(
            logger,
            f"[assets] {entity_type}:{entity_key} {kind}: {candidate.source or 'auto'} -> "
            f"{candidate.source_url or candidate.remote_url}",
        )
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
                folder_parts = _asset_folder_parts(entity_type, entity_key, country_code)
                if _download_asset(asset_id, kind, folder_parts, candidate.remote_url, candidate.commons_filename):
                    downloaded += 1
                    _asset_log(logger, f"[assets] {entity_type}:{entity_key} {kind}: fichero local descargado")
            except Exception as exc:  # noqa: BLE001 - persisted as asset status.
                _mark_asset_download_error(asset_id, exc)
                _asset_log(logger, f"[assets] {entity_type}:{entity_key} {kind}: error {exc}")
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
    if name and country_name and _normalize(country_name) not in _normalize(name):
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
        if expected_path and _local_asset_file_is_usable(local_path):
            _set_asset_local_exists(asset_id, True)
            continue
        try:
            if _download_asset(
                asset_id,
                str(row.get("kind") or "asset"),
                _asset_folder_parts(
                    str(row.get("entity_type") or ""),
                    str(row.get("entity_key") or ""),
                    str(row.get("country_code") or ""),
                ),
                str(row.get("remote_url") or ""),
                str(row.get("commons_filename") or ""),
            ):
                downloaded += 1
        except Exception as exc:  # noqa: BLE001
            _mark_asset_download_error(asset_id, exc)
    return downloaded


def ensure_visual_assets_after_startup_async() -> None:
    """Legacy no-op: normal visual assets are rendered from stored remote URLs."""
    return


def get_visual_assets_for_entity(
    entity_type: str,
    entity_key: str,
    *,
    include_fallbacks: bool = True,
) -> dict[str, dict]:
    """Return stored visual assets for API/UI use."""
    if not visual_asset_tables_exist():
        if not include_fallbacks:
            return {}
        assets = _local_folder_assets_for_entity(entity_type, entity_key)
        if entity_type == "country":
            assets = {**_configured_country_visual_assets(entity_key), **assets}
        return assets
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
        row_country_code = str(row.get("country_code") or "")
        bad_local_flag = _is_wrong_citypopulation_flag(
            kind,
            row_country_code if row_entity_type == "country" else row_entity_key,
            local_path=str(row.get("local_path") or ""),
            remote_url=str(row.get("remote_url") or ""),
            commons_filename=str(row.get("commons_filename") or ""),
        )
        local_path = str(row.get("local_path") or "")
        local_url = "" if bad_local_flag else _existing_media_url(local_path)
        if not local_url and not bad_local_flag and include_fallbacks:
            folder_path = _first_local_asset_path(
                kind,
                row_entity_type,
                row_entity_key,
                country_code=row_country_code,
            )
            if folder_path:
                local_path = folder_path
                local_url = _media_url(folder_path)
        remote_url = "" if bad_local_flag else row.get("remote_url") or ""
        if not remote_url and not bad_local_flag and row.get("commons_filename"):
            remote_url = commons_file_url(str(row.get("commons_filename") or ""), width=WIKIMEDIA_COMMONS_PREVIEW_WIDTH)
        source = str(row.get("source") or "")
        status = str(row.get("status") or "")
        if local_url:
            status = "downloaded"
            if source == "auto":
                source = "local"
        assets[kind] = {
            **row,
            "source": source,
            "status": status,
            "local_path": local_path,
            "local_exists": bool(local_url),
            "remote_url": remote_url,
            "local_url": local_url,
            # UI should use Commons/Wikimedia directly when metadata exists.
            # Local files are kept only as a legacy fallback and are not preferred.
            "image_url": remote_url or local_url,
            "translations": translations_by_asset.get(int(row["id"]), {}),
        }
    if include_fallbacks and entity_type == "country":
        for kind, asset in _configured_country_visual_assets(entity_key).items():
            assets.setdefault(kind, asset)
    if include_fallbacks:
        for kind, asset in _local_folder_assets_for_entity(entity_type, entity_key).items():
            assets.setdefault(kind, asset)
    return assets


def get_visual_asset_for_entity_kind(entity_type: str, entity_key: str, kind: str) -> dict | None:
    """Return one stored visual asset, including translations, for the Ficha page."""
    if not visual_asset_tables_exist():
        return None
    row = _select_one(
        f"""
        SELECT id, entity_type, entity_key, entity_name, country_code, kind,
               wikidata_id, commons_filename, remote_url, local_path,
               local_exists, source, status, error, license_name, author,
               attribution, source_url, updated_at
          FROM {VISUAL_ASSET_TABLE}
         WHERE entity_type=%s AND entity_key=%s AND kind=%s
         LIMIT 1
        """,
        [entity_type, entity_key, kind],
    )
    if not row:
        return None
    return _asset_row_payload(row)


def get_visual_asset_by_local_or_commons(value: str, kind: str = "") -> dict | None:
    """Resolve legacy identity URLs that still pass a filename or local media path."""
    if not visual_asset_tables_exist():
        return None
    raw = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return None
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/").strip("/") + "/"
    if raw.startswith(media_url):
        raw = raw[len(media_url):]
    filename = Path(raw).name
    where = ["REPLACE(local_path, '\\', '/') = %s", "commons_filename = %s"]
    params: list[str] = [raw, filename]
    if filename != raw:
        where.append("commons_filename = %s")
        params.append(raw)
    if kind:
        kind_clause = " AND kind=%s"
        params.append(kind)
    else:
        kind_clause = ""
    row = _select_one(
        f"""
        SELECT id, entity_type, entity_key, entity_name, country_code, kind,
               wikidata_id, commons_filename, remote_url, local_path,
               local_exists, source, status, error, license_name, author,
               attribution, source_url, updated_at
          FROM {VISUAL_ASSET_TABLE}
         WHERE ({' OR '.join(where)}){kind_clause}
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
        """,
        params,
    )
    if not row:
        return None
    return _asset_row_payload(row)


def _asset_row_payload(row: dict) -> dict:
    row_id = int(row["id"])
    translations_by_asset = _translations_for_asset_rows([row])
    local_path = str(row.get("local_path") or "")
    local_url = _existing_media_url(local_path)
    remote_url = str(row.get("remote_url") or "")
    commons_filename = str(row.get("commons_filename") or "")
    if not remote_url and commons_filename:
        remote_url = commons_file_url(commons_filename, width=WIKIMEDIA_COMMONS_PREVIEW_WIDTH)
    status = str(row.get("status") or "")
    if local_url and not remote_url:
        status = "downloaded"
    return {
        **row,
        "id": row_id,
        "status": status,
        "local_path": local_path,
        "local_exists": bool(local_url),
        "remote_url": remote_url,
        "local_url": local_url,
        "image_url": remote_url or local_url,
        "translations": translations_by_asset.get(row_id, {}),
    }


def _local_folder_assets_for_entity(entity_type: str, entity_key: str, country_code: str = "") -> dict[str, dict]:
    if not country_code and entity_type == "admin_area":
        country_code = _admin_area_country_code(entity_key)
    assets: dict[str, dict] = {}
    for kind in VISUAL_ASSET_KINDS:
        local_path = _first_local_asset_path(kind, entity_type, entity_key, country_code=country_code)
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


def _configured_country_wikidata_id(country_code: str) -> str:
    """Return a configured country QID without issuing a Wikidata search."""
    country_code = str(country_code or "").strip()
    if not country_code:
        return ""

    configs: list[dict] = []
    config = _load_config(country_code)
    if config:
        configs.append(config)

    if ScrapingConfig is not None and scraping_config_table_exists():
        try:
            for content in ScrapingConfig.objects.filter(country_code=country_code).values_list("content", flat=True)[:3]:
                try:
                    configs.append(tomllib.loads(str(content or "")))
                except tomllib.TOMLDecodeError:
                    continue
        except (OperationalError, ProgrammingError):
            pass

    seed_path = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions" / f"{country_code}.toml"
    if seed_path.exists():
        try:
            configs.append(tomllib.loads(seed_path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError):
            pass

    for config in configs:
        qid = _wikidata_qid(config.get("wikidata_id"))
        if qid:
            return qid
    return ""


def _configured_country_visual_assets(country_code: str) -> dict[str, dict]:
    """Return explicit visual asset fallbacks declared in the country TOML."""
    config = _load_config(country_code)
    if not config:
        seed_path = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions" / f"{country_code}.toml"
        if seed_path.exists():
            try:
                config = tomllib.loads(seed_path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                config = None
    if not config:
        return {}

    visual_assets = config.get("visual_assets") or {}
    if not isinstance(visual_assets, dict):
        return {}

    country_qid = _wikidata_qid(config.get("wikidata_id"))
    assets: dict[str, dict] = {}
    for kind in VISUAL_ASSET_KINDS:
        raw_asset = visual_assets.get(kind) or {}
        if not isinstance(raw_asset, dict):
            continue
        commons_filename = str(raw_asset.get("commons_filename") or "").strip()
        remote_url = str(raw_asset.get("remote_url") or "").strip()
        if not remote_url and commons_filename:
            remote_url = commons_file_url(commons_filename, width=WIKIMEDIA_COMMONS_PREVIEW_WIDTH)
        if not remote_url:
            continue
        assets[kind] = {
            "id": "",
            "entity_type": "country",
            "entity_key": country_code,
            "entity_name": str(config.get("name") or country_code),
            "country_code": country_code,
            "kind": kind,
            "wikidata_id": _wikidata_qid(raw_asset.get("wikidata_id")) or country_qid,
            "commons_filename": commons_filename,
            "remote_url": remote_url,
            "local_path": "",
            "local_exists": False,
            "local_url": "",
            "image_url": remote_url,
            "source": str(raw_asset.get("source") or "config"),
            "status": "remote",
            "error": "",
            "license_name": "",
            "author": "",
            "attribution": "",
            "source_url": f"https://www.wikidata.org/wiki/{country_qid}" if country_qid else "",
            "translations": {},
        }
    return assets


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
        kind = _citypopulation_kind_from_image(text, url)
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


def _citypopulation_entity_candidates(html: str, page_url: str, entities: list) -> list[tuple[object, list[AssetCandidate]]]:
    parser = _InfoSectionImageParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - partial HTML is enough.
        pass
    if not parser.sections:
        return []

    entities_by_code = {str(getattr(entity, "code", "")): entity for entity in entities}
    root_entities = [entity for entity in entities if getattr(entity, "level", None) == 0]
    grouped: list[tuple[object, list[AssetCandidate]]] = []
    for source_code, images in parser.sections.items():
        entity = entities_by_code.get(str(source_code))
        if not entity and len(parser.sections) == 1 and len(root_entities) == 1:
            entity = root_entities[0]
        if not entity:
            continue
        candidates = _citypopulation_candidates_from_images(images, page_url)
        if candidates:
            grouped.append((entity, candidates))
    return grouped


def _citypopulation_wikidata_ids(html: str) -> dict[str, str]:
    parser = _InfoSectionImageParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - partial HTML is enough.
        pass
    return dict(parser.wikidata_ids)


def _page_wikidata_id_for_entity(entity, page_wikidata_ids: dict[str, str]) -> str:
    if not entity or not page_wikidata_ids:
        return ""
    entity_id = str(getattr(entity, "id", "") or "")
    keys = [
        str(getattr(entity, "code", "") or ""),
        entity_id,
        entity_id.split("_", 1)[-1] if "_" in entity_id else "",
    ]
    for key in keys:
        wikidata_id = page_wikidata_ids.get(key)
        if wikidata_id:
            return wikidata_id
    return ""


def _seedable_page_entities(country_code: str, entities: list, subdivision_levels: tuple[int, ...] | None) -> list:
    seen: set[str] = set()
    selected = []
    for entity in entities:
        entity_id = str(getattr(entity, "id", "") or "")
        level = getattr(entity, "level", None)
        if not entity_id or entity_id in seen:
            continue
        if level == 0:
            pass
        elif subdivision_levels is not None and int(level) not in subdivision_levels:
            continue
        elif getattr(entity, "country_code", "") != country_code:
            continue
        seen.add(entity_id)
        selected.append(entity)
    return selected


def _root_page_entity(country_code: str, entities: list):
    for entity in entities:
        if getattr(entity, "level", None) == 0:
            return entity
    return next((entity for entity in entities if getattr(entity, "code", None) == country_code), None)


def _wikidata_query_for_page_entity(entity, country_code: str, entities: list) -> str:
    name = str(getattr(entity, "name", "") or "")
    if getattr(entity, "level", None) == 0:
        return name or country_code
    root = next((item for item in entities if getattr(item, "level", None) == 0), None)
    country_name = str(getattr(root, "name", "") or country_code)
    return _wikidata_query_for_area(name, country_name)


def _citypopulation_candidates_from_images(images: list[tuple[str, str]], page_url: str) -> list[AssetCandidate]:
    candidates: list[AssetCandidate] = []
    seen: set[tuple[str, str]] = set()
    for src, text in images:
        url = urljoin(page_url, unescape(src))
        if not _looks_like_image_url(url):
            continue
        kind = _citypopulation_kind_from_image(text, url)
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


def _citypopulation_kind_from_image(text: str, url: str) -> str:
    """Classify by explicit metadata or filename, not by generic URL folders."""
    kind = _kind_from_text(text)
    if kind:
        return kind
    filename = Path(unquote(unescape(urlparse(url).path))).name
    return _kind_from_text(filename)


def _wikidata_country_visual_asset_records(
    country_wikidata_id: str,
    *,
    languages: tuple[str, ...],
) -> list[WikidataVisualAssetRecord]:
    country_wikidata_id = _wikidata_qid(country_wikidata_id)
    if not country_wikidata_id:
        return []
    language_key = tuple(_wikidata_label_languages(languages))
    cache_key = (country_wikidata_id, language_key)
    if cache_key in _WIKIDATA_COUNTRY_ASSET_CACHE:
        return _WIKIDATA_COUNTRY_ASSET_CACHE[cache_key]

    query = _wikidata_country_assets_sparql(country_wikidata_id, languages=language_key)
    url = f"{WIKIDATA_SPARQL_ENDPOINT}?query={quote(query)}&format=json"
    data = _fetch_json(url, timeout=25)
    records = _parse_wikidata_sparql_asset_records(data)
    _WIKIDATA_COUNTRY_ASSET_CACHE[cache_key] = records
    return records


def _wikidata_country_assets_sparql(country_wikidata_id: str, *, languages: tuple[str, ...]) -> str:
    language_text = ",".join(languages or ("es", "en"))
    return f"""
SELECT ?item ?itemLabel ?itemDescription ?type ?typeLabel ?parent ?parentLabel ?flag ?coat ?seal WHERE {{
  ?item wdt:P17 wd:{country_wikidata_id}.

  OPTIONAL {{ ?item wdt:P31 ?type. }}
  OPTIONAL {{ ?item wdt:P131 ?parent. }}

  OPTIONAL {{ ?item wdt:P41 ?flag. }}
  OPTIONAL {{ ?item wdt:P94 ?coat. }}
  OPTIONAL {{ ?item wdt:P158 ?seal. }}

  FILTER(BOUND(?flag) || BOUND(?coat) || BOUND(?seal))

  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "{language_text}".
  }}
}}
LIMIT 10000
""".strip()


def _parse_wikidata_sparql_asset_records(data: dict) -> list[WikidataVisualAssetRecord]:
    bindings = (((data or {}).get("results") or {}).get("bindings") or []) if isinstance(data, dict) else []
    records_by_qid: dict[str, WikidataVisualAssetRecord] = {}
    for row in bindings:
        if not isinstance(row, dict):
            continue
        wikidata_id = _wikidata_qid(_binding_value(row, "item"))
        if not wikidata_id:
            continue
        record = records_by_qid.get(wikidata_id)
        if not record:
            record = WikidataVisualAssetRecord(
                wikidata_id=wikidata_id,
                label=_binding_value(row, "itemLabel"),
                description=_binding_value(row, "itemDescription"),
                type_id=_wikidata_qid(_binding_value(row, "type")),
                type_label=_binding_value(row, "typeLabel"),
                parent_id=_wikidata_qid(_binding_value(row, "parent")),
                parent_label=_binding_value(row, "parentLabel"),
            )
            records_by_qid[wikidata_id] = record
        else:
            record.label = record.label or _binding_value(row, "itemLabel")
            record.description = record.description or _binding_value(row, "itemDescription")
            record.type_id = record.type_id or _wikidata_qid(_binding_value(row, "type"))
            record.type_label = record.type_label or _binding_value(row, "typeLabel")
            record.parent_id = record.parent_id or _wikidata_qid(_binding_value(row, "parent"))
            record.parent_label = record.parent_label or _binding_value(row, "parentLabel")

        for kind in VISUAL_ASSET_KINDS:
            if kind in record.candidates:
                continue
            candidate = _wikidata_sparql_asset_candidate(
                kind=kind,
                image_url=_binding_value(row, kind),
                wikidata_id=wikidata_id,
            )
            if candidate:
                record.candidates[kind] = candidate
    return list(records_by_qid.values())


def _wikidata_sparql_asset_candidate(
    *,
    kind: str,
    image_url: str,
    wikidata_id: str,
) -> AssetCandidate | None:
    if not image_url:
        return None
    filename = _commons_filename_from_url(image_url)
    remote_url = commons_file_url(filename, width=WIKIMEDIA_COMMONS_PREVIEW_WIDTH) if filename else image_url
    return AssetCandidate(
        kind=kind,
        remote_url=remote_url,
        commons_filename=filename,
        wikidata_id=wikidata_id,
        source="wikidata",
        source_url=f"https://www.wikidata.org/wiki/{wikidata_id}",
        title=filename or kind,
    )



def _record_description_translations(
    record: WikidataVisualAssetRecord,
    *,
    languages: tuple[str, ...],
) -> dict[str, dict]:
    """Return the SPARQL label-service text without extra per-entity requests.

    The label service returns a best-language label/description rather than all
    requested translations. Store it under the first preferred language so the
    UI has useful text while avoiding another wbgetentities call per row.
    """
    title = _clean_text(record.label)
    description = _clean_text(record.description)
    if not title and not description:
        return {}
    language = str((languages or DEFAULT_LANGUAGES)[0] or "es").replace("_", "-")
    return {language: {"title": title, "description": description}}

def _wikidata_records_by_label(
    records: list[WikidataVisualAssetRecord],
) -> dict[str, list[WikidataVisualAssetRecord]]:
    grouped: dict[str, list[WikidataVisualAssetRecord]] = {}
    for record in records:
        label = _normalize(record.label)
        if label:
            grouped.setdefault(label, []).append(record)
    return grouped


def _match_wikidata_visual_asset_record(
    entity,
    entities: list,
    records_by_label: dict[str, list[WikidataVisualAssetRecord]],
) -> WikidataVisualAssetRecord | None:
    label = _normalize(str(getattr(entity, "name", "") or ""))
    if not label:
        return None
    candidates = records_by_label.get(label) or []
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    parent_label = _normalize(_page_entity_parent_name(entity, entities))
    if parent_label:
        parent_matches = [record for record in candidates if _normalize(record.parent_label) == parent_label]
        if len(parent_matches) == 1:
            return parent_matches[0]
    return None


def _page_entity_parent_name(entity, entities: list) -> str:
    parent_code = str(getattr(entity, "parent_code", "") or "")
    if not parent_code:
        return ""
    for candidate in entities:
        if str(getattr(candidate, "code", "") or "") == parent_code:
            return str(getattr(candidate, "name", "") or "")
    return ""


def _wikidata_label_languages(languages: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    for language in ("es", "en", *languages):
        language = str(language or "").replace("_", "-").strip()
        if language and language not in selected:
            selected.append(language)
    return tuple(selected)


def _binding_value(row: dict, key: str) -> str:
    return str(((row.get(key) or {}).get("value") or "")).strip()


def _wikidata_qid(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if re.fullmatch(r"Q\d+", value):
        return value
    match = re.search(r"/entity/(Q\d+)$", value)
    return match.group(1) if match else ""


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
    for search_query in _wikidata_search_queries(query):
        for language in languages:
            params = {
                "action": "wbsearchentities",
                "format": "json",
                "limit": "1",
                "language": language,
                "search": search_query,
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


def _wikidata_search_queries(value: str) -> list[str]:
    clean = _clean_text(value)
    if not clean:
        return []
    queries = [clean]
    without_brackets = _clean_text(re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", clean))
    if without_brackets:
        queries.append(without_brackets)
    for alias in re.findall(r"\[([^\]]+)\]|\(([^)]+)\)", clean):
        candidate = _clean_text(next((part for part in alias if part), ""))
        if candidate:
            queries.append(candidate)
    folded = _strip_diacritics(clean)
    if folded != clean:
        queries.append(folded)
    deduped: list[str] = []
    for query in queries:
        if query and query not in deduped:
            deduped.append(query)
    return deduped


def _wikidata_candidates(
    wikidata_id: str,
    *,
    kinds: list[str],
    languages: tuple[str, ...],
    fetch_metadata: bool = False,
) -> tuple[dict[str, AssetCandidate], dict[str, dict]]:
    wikidata_id = _wikidata_qid(wikidata_id)
    if not wikidata_id:
        return {}, {}
    kind_key = tuple(sorted(str(kind) for kind in kinds))
    language_key = tuple(_wikidata_label_languages(languages))
    cache_key = (wikidata_id, kind_key, language_key, bool(fetch_metadata))
    if cache_key in _WIKIDATA_ENTITY_ASSET_CACHE:
        return _WIKIDATA_ENTITY_ASSET_CACHE[cache_key]

    data = _fetch_wikidata_entities([wikidata_id], languages=language_key)
    entity = ((data.get("entities") or {}).get(wikidata_id) or {}) if isinstance(data, dict) else {}
    result = _wikidata_candidates_from_entity(
        wikidata_id,
        entity,
        kinds=kinds,
        languages=language_key,
        fetch_metadata=fetch_metadata,
    )
    _WIKIDATA_ENTITY_ASSET_CACHE[cache_key] = result
    return result


def _wikidata_candidates_for_ids(
    wikidata_ids: list[str],
    *,
    kinds: list[str],
    languages: tuple[str, ...],
    fetch_metadata: bool = False,
) -> dict[str, tuple[dict[str, AssetCandidate], dict[str, dict]]]:
    qids = []
    for wikidata_id in wikidata_ids:
        qid = _wikidata_qid(wikidata_id)
        if qid and qid not in qids:
            qids.append(qid)
    if not qids:
        return {}

    language_key = tuple(_wikidata_label_languages(languages))
    results: dict[str, tuple[dict[str, AssetCandidate], dict[str, dict]]] = {}
    for start in range(0, len(qids), WIKIDATA_BULK_ENTITY_LIMIT):
        chunk = qids[start:start + WIKIDATA_BULK_ENTITY_LIMIT]
        data = _fetch_wikidata_entities(chunk, languages=language_key)
        entities = (data.get("entities") or {}) if isinstance(data, dict) else {}
        for qid in chunk:
            entity = entities.get(qid) or {}
            result = _wikidata_candidates_from_entity(
                qid,
                entity,
                kinds=kinds,
                languages=language_key,
                fetch_metadata=fetch_metadata,
            )
            results[qid] = result
            kind_key = tuple(sorted(str(kind) for kind in kinds))
            cache_key = (qid, kind_key, language_key, bool(fetch_metadata))
            _WIKIDATA_ENTITY_ASSET_CACHE[cache_key] = result
    return results


def _fetch_wikidata_entities(wikidata_ids: list[str], *, languages: tuple[str, ...]) -> dict:
    qids = [_wikidata_qid(wikidata_id) for wikidata_id in wikidata_ids]
    qids = [qid for index, qid in enumerate(qids) if qid and qid not in qids[:index]]
    if not qids:
        return {}
    props = "claims|descriptions|labels"
    url = (
        "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props="
        + quote(props)
        + "&ids="
        + quote("|".join(qids))
        + "&languages="
        + quote("|".join(languages))
    )
    return _fetch_json(url, timeout=16)


def _wikidata_candidates_from_entity(
    wikidata_id: str,
    entity: dict,
    *,
    kinds: list[str],
    languages: tuple[str, ...],
    fetch_metadata: bool,
) -> tuple[dict[str, AssetCandidate], dict[str, dict]]:
    claims = (entity.get("claims") or {}) if isinstance(entity, dict) else {}
    candidates: dict[str, AssetCandidate] = {}
    for kind in kinds:
        prop = WIKIDATA_IMAGE_PROPERTIES.get(kind)
        filename = _first_claim_filename(claims.get(prop) if prop else [])
        if not filename:
            continue
        remote_url = commons_file_url(filename, width=WIKIMEDIA_COMMONS_PREVIEW_WIDTH)
        metadata = _commons_file_metadata(filename) if fetch_metadata else {}
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
    labels = (entity.get("labels") or {}) if isinstance(entity, dict) else {}
    descs = (entity.get("descriptions") or {}) if isinstance(entity, dict) else {}
    for language in languages:
        descriptions[language] = {
            "title": ((labels.get(language) or {}).get("value") or ""),
            "description": ((descs.get(language) or {}).get("value") or ""),
        }
    return candidates, descriptions


def _first_claim_filename(claims) -> str:
    preferred: list = []
    normal: list = []
    deprecated: list = []
    for claim in claims or []:
        rank = str((claim or {}).get("rank") or "normal")
        if rank == "preferred":
            preferred.append(claim)
        elif rank == "deprecated":
            deprecated.append(claim)
        else:
            normal.append(claim)
    for claim in [*preferred, *normal, *deprecated]:
        filename = _claim_filename(claim)
        if filename:
            return filename
    return ""


def _claim_filename(claim) -> str:
    try:
        snak = claim["mainsnak"]
        if snak.get("snaktype") and snak.get("snaktype") != "value":
            return ""
        datavalue = snak["datavalue"]
        if datavalue.get("type") and datavalue.get("type") != "string":
            return ""
        value = datavalue["value"]
    except Exception:  # noqa: BLE001
        return ""
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return _commons_filename_from_url(value)
    return value


def commons_file_url(filename: str, *, width: int | None = 1400) -> str:
    url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(filename.replace(" ", "_"), safe="/_()-.,'")
    if width:
        url += f"?width={int(width)}"
    return url


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


def upsert_visual_asset_translation(
    asset_id: int,
    language: str,
    *,
    title: str = "",
    description: str = "",
    blazon: str = "",
    source: str = "",
    needs_review: bool = True,
) -> None:
    """Persist one visual identity translation/description row."""

    _upsert_translation(
        asset_id,
        language,
        title=title,
        description=description,
        blazon=blazon,
        source=source,
        needs_review=needs_review,
    )


def _download_asset(
    asset_id: int,
    kind: str,
    folder_parts: tuple[str, ...],
    remote_url: str,
    commons_filename: str = "",
) -> bool:
    if not remote_url:
        return False
    extension = _extension_from_url_or_filename(remote_url, commons_filename)
    folder = _safe_folder_parts(folder_parts)

    for reusable_extension in _asset_download_extensions(extension, commons_filename):
        reusable_path = _asset_relative_download_path(kind, folder, reusable_extension)
        if _local_asset_file_is_usable(reusable_path):
            _set_asset_local_path(asset_id, reusable_path, True)
            return False

    primary_url = _asset_download_url(remote_url, commons_filename, extension)
    attempts = [(extension, primary_url)]
    fallback_url = _asset_thumbnail_fallback_url(remote_url, commons_filename, extension)
    if fallback_url:
        attempts.append((".png", fallback_url))

    last_error: Exception | None = None
    for index, (target_extension, download_url) in enumerate(attempts):
        relative_path = _asset_relative_download_path(kind, folder, target_extension)
        absolute_path = _absolute_media_path(relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = _read_url_bytes(download_url, timeout=25, max_bytes=12 * 1024 * 1024)
            if not content:
                raise ValueError("respuesta vacía al descargar la imagen")
            tmp_path = absolute_path.with_suffix(absolute_path.suffix + ".tmp")
            tmp_path.write_bytes(content)
            os.replace(tmp_path, absolute_path)
            if not _local_asset_file_is_usable(relative_path):
                raise ValueError("la imagen descargada no coincide con la extensión del fichero")
            _set_asset_local_path(asset_id, relative_path, True)
            return True
        except HTTPError as exc:
            last_error = exc
            if not _should_try_thumbnail_fallback(exc, index, attempts):
                raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            raise

    if last_error:
        raise last_error
    return False


def _asset_download_filename(
    kind: str,
    folder_parts: tuple[str, ...],
    extension: str,
    *,
    acquired_date=None,
) -> str:
    del acquired_date  # Kept as a backward-compatible keyword; filenames are deterministic.
    country = str((folder_parts or ("asset",))[0] or "asset")
    entity = str((folder_parts[1] if len(folder_parts) > 1 else country) if folder_parts else country)
    label = VISUAL_ASSET_KIND_FILENAME_PARTS.get(str(kind or "").casefold(), _safe_filename(kind or "asset"))
    extension = extension if str(extension or "").startswith(".") else f".{extension or 'img'}"
    return _safe_filename(f"{country}_{entity}_{label}") + extension


def _asset_relative_download_path(kind: str, folder: tuple[str, ...], extension: str) -> str:
    filename_base = _asset_download_filename(kind, folder, extension)
    return str(Path("visual_assets") / _safe_filename(kind or "asset") / Path(*folder) / filename_base)


def _asset_download_extensions(extension: str, commons_filename: str = "") -> tuple[str, ...]:
    extension = extension if str(extension or "").startswith(".") else f".{extension or 'img'}"
    extensions = [extension]
    if extension.casefold() == ".svg" and str(commons_filename or "").casefold().endswith(".svg"):
        extensions.append(".png")
    return tuple(dict.fromkeys(extensions))


def _asset_thumbnail_fallback_url(remote_url: str, commons_filename: str = "", extension: str = "") -> str:
    if str(extension or "").casefold() != ".svg":
        return ""
    if not str(commons_filename or "").casefold().endswith(".svg"):
        return ""
    return commons_file_url(commons_filename, width=WIKIMEDIA_COMMONS_THUMBNAIL_FALLBACK_WIDTH)


def _should_try_thumbnail_fallback(exc: HTTPError, attempt_index: int, attempts: list[tuple[str, str]]) -> bool:
    return exc.code == 429 and attempt_index == 0 and len(attempts) > 1


def _asset_download_url(remote_url: str, commons_filename: str = "", extension: str = "") -> str:
    """Use original Commons SVG bytes when the local filename has .svg extension."""
    if str(extension or "").casefold() == ".svg" and str(commons_filename or "").casefold().endswith(".svg"):
        return commons_file_url(commons_filename, width=None)
    return remote_url


def _select_assets_missing_files(*, limit: int | None = None) -> list[dict]:
    sql = f"""
        SELECT id, entity_type, entity_key, country_code, kind, commons_filename, remote_url, local_path
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
        if not local_path or not _local_asset_file_is_usable(local_path):
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


def _mark_asset_download_error(asset_id: int, error: Exception) -> None:
    if _is_retryable_asset_download_error(error):
        _execute(
            f"UPDATE {VISUAL_ASSET_TABLE} SET status='found', local_exists=0, error=%s, updated_at=%s WHERE id=%s",
            [("reintentable: " + str(error))[:2000], timezone.now(), asset_id],
        )
        return
    _mark_asset_error(asset_id, str(error))


def _is_retryable_asset_download_error(error: Exception) -> bool:
    return isinstance(error, HTTPError) and error.code in WIKIMEDIA_RETRY_STATUS_CODES


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
        content = _read_url_bytes(url, timeout=timeout, max_bytes=4 * 1024 * 1024)
        return content.decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""


def _read_url_bytes(url: str, *, timeout: int = 10, max_bytes: int = 4 * 1024 * 1024, attempts: int = 4) -> bytes:
    """Read a URL with light Wikimedia throttling and retryable 429/5xx handling."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            _throttle_wikimedia_url(url)
            request = Request(url, headers={"User-Agent": _user_agent()})
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - trusted configured scrape source.
                return response.read(max_bytes)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in WIKIMEDIA_RETRY_STATUS_CODES or attempt >= attempts - 1:
                raise
            time.sleep(_retry_delay_seconds(exc, attempt))
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= attempts - 1:
                raise
            time.sleep(_retry_delay_seconds(exc, attempt))
    if last_error:
        raise last_error
    return b""


def _retry_delay_seconds(error: Exception, attempt: int) -> float:
    retry_after = ""
    if isinstance(error, HTTPError):
        retry_after = str(error.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return min(float(retry_after), 20.0)
    return min(1.0 * (2 ** max(0, attempt)), 8.0)


def _throttle_wikimedia_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    if not any(marker in host for marker in WIKIMEDIA_HOSTS):
        return
    global _LAST_WIKIMEDIA_REQUEST_AT
    now = time.monotonic()
    elapsed = now - _LAST_WIKIMEDIA_REQUEST_AT
    if elapsed < WIKIMEDIA_REQUEST_DELAY_SECONDS:
        time.sleep(WIKIMEDIA_REQUEST_DELAY_SECONDS - elapsed)
    _LAST_WIKIMEDIA_REQUEST_AT = time.monotonic()

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
    path = unquote(unescape(parsed.path))
    if "/wiki/Special:FilePath/" in path:
        return path.rsplit("/wiki/Special:FilePath/", 1)[-1].replace("_", " ")
    if "/wiki/File:" in path:
        return path.rsplit("/wiki/File:", 1)[-1].replace("_", " ")
    if "commons.wikimedia.org" in parsed.netloc and path:
        return Path(path).name.replace("_", " ")
    return ""


def _extension_from_url_or_filename(url: str, filename: str = "") -> str:
    candidate = (filename or unquote(unescape(Path(urlparse(url).path).name)) or "").lower()
    for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if candidate.endswith(ext):
            return ext
    return ".img"


def _asset_folder_parts(entity_type: str, entity_key: str, country_code: str = "") -> tuple[str, ...]:
    entity_type = str(entity_type or "")
    entity_key = str(entity_key or "")
    country_code = str(country_code or "")
    if entity_type == "admin_area":
        country = country_code or _country_code_from_admin_area_id(entity_key)
        return (country or "unknown", entity_key or "unknown")
    if entity_type == "country":
        return (country_code or entity_key or "unknown",)
    return (country_code or entity_key or "unknown",)


def _safe_folder_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    safe = tuple(_safe_filename(part) for part in parts if str(part or "").strip())
    return safe or ("unknown",)


def _country_code_from_admin_area_id(entity_key: str) -> str:
    if "_" in entity_key:
        return entity_key.split("_", 1)[0]
    return ""


def _admin_area_country_code(entity_key: str) -> str:
    try:
        from ciudades_del_mundo.models import AdminArea
    except Exception:  # pragma: no cover - startup/import edge case.
        return _country_code_from_admin_area_id(entity_key)
    try:
        area = AdminArea.objects.filter(id=entity_key).only("country_code").first()
    except (OperationalError, ProgrammingError):
        return _country_code_from_admin_area_id(entity_key)
    return str(area.country_code) if area else _country_code_from_admin_area_id(entity_key)


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
    if not _local_asset_file_is_usable(safe_path):
        return ""
    return _media_url(safe_path)


def _local_asset_file_is_usable(relative_path: str) -> bool:
    path = _absolute_media_path(_safe_media_relative_path(relative_path))
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    suffix = path.suffix.casefold()
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    stripped = head.lstrip(b"\xef\xbb\xbf\r\n\t ")
    lowered = stripped.lower()
    if suffix == ".svg":
        # Commons SVGs may begin with XML declarations, comments or doctypes.
        return lowered.startswith((b"<svg", b"<?xml")) or b"<svg" in lowered[:2048]
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8")
    if suffix == ".gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    return True


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


def _first_local_asset_path(kind: str, entity_type: str, entity_key: str, *, country_code: str = "") -> str:
    safe_kind = _safe_filename(kind or "asset")
    folder_parts = _asset_folder_parts(entity_type, entity_key, country_code)
    roots = [
        Path("visual_assets") / safe_kind / Path(*_safe_folder_parts(folder_parts)),
    ]
    legacy_key = _safe_filename(entity_key or country_code or "unknown")
    legacy_root = Path("visual_assets") / safe_kind / legacy_key
    if legacy_root not in roots:
        roots.append(legacy_root)
    expected_key = country_code if entity_type == "country" else entity_key
    for relative_root in roots:
        root = _absolute_media_path(str(relative_root))
        if not root.is_dir():
            continue
        image_files = [path for path in root.iterdir() if path.is_file() and _looks_like_image_url(path.name)]
        for path in sorted(image_files, key=lambda item: _local_asset_sort_key(item, kind, folder_parts)):
            if _is_wrong_citypopulation_flag(kind, expected_key, local_path=path.name):
                continue
            if _local_asset_file_is_usable(str(relative_root / path.name)):
                return _safe_media_relative_path(str(relative_root / path.name))
    return ""


def _local_asset_sort_key(path: Path, kind: str, folder_parts: tuple[str, ...]) -> tuple[int, str]:
    extension = path.suffix or ".svg"
    expected_name = _asset_download_filename(kind, folder_parts, extension).casefold()
    expected_stem = Path(expected_name).stem.casefold()
    name = path.name.casefold()
    stem = path.stem.casefold()
    if name == expected_name:
        return (0, name)
    if stem == expected_stem:
        return (1, name)
    return (2, name)


def _media_url(relative_path: str) -> str:
    if not relative_path:
        return ""
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    return media_url.rstrip("/") + "/" + quote(relative_path.replace("\\", "/"), safe="/._-()")


def _safe_filename(value: str) -> str:
    value = unicodedata.normalize("NFC", unquote(unescape(str(value or "")))).strip().replace(" ", "_")
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    value = re.sub(r"[^\w._()\-]+", "_", value, flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("._")
    return value[:180] or "asset"


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return _clean_text(value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unquote(unescape(str(value or "")))).strip()


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", _clean_text(value)).casefold()
    value = _strip_diacritics(value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _asset_log(logger: AssetLogger | None, message: str) -> None:
    if logger:
        logger(message)


def _user_agent() -> str:
    return "CiudadesDelMundo/1.0 (+local visual asset cache)"
