"""Helpers for deleting persisted visual assets for a country.

The clear/re-populate workflows remove AdminArea rows and must also remove the
flag/coat records generated for that same source country.  This module keeps the
asset deletion in one place so command-line clears, UI clears and re-populates
share the same behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.db import connection

from ciudades_del_mundo.models import AdminArea, ScrapingConfig


VISUAL_ASSET_TABLE = "ciudades_del_mundo_visual_asset"
VISUAL_TRANSLATION_TABLE = "ciudades_del_mundo_visual_asset_translation"


@dataclass(frozen=True)
class VisualAssetDeletionResult:
    assets: int = 0
    translations: int = 0
    local_files: int = 0


def delete_visual_assets_for_country(
    country_code: str,
    *,
    aliases: Iterable[str] | None = None,
    delete_local_files: bool = True,
) -> VisualAssetDeletionResult:
    """Delete flag/coat VisualAsset rows associated with one country.

    The function intentionally accepts aliases because the UI often works with a
    config slug (for example ``spain``) while the data operation is done over the
    effective ``AdminArea.country_code``.  Old records may also have been stored
    with the slug as ``entity_key`` or with no reliable ``country_code``.  To keep
    cleanup deterministic, deletion matches:

    * rows with a matching ``country_code``;
    * root country rows whose ``entity_key`` is one of the aliases;
    * admin-area rows whose ``entity_key`` still exists under that country;
    * legacy admin-area rows prefixed by the alias, such as ``spain_...``.
    """
    country_code = str(country_code or "").strip()
    identifiers = _country_identifiers(country_code, aliases=aliases)
    if not identifiers:
        return VisualAssetDeletionResult()

    existing_tables = set(connection.introspection.table_names())
    if VISUAL_ASSET_TABLE not in existing_tables:
        return VisualAssetDeletionResult()

    where_sql, params = _asset_delete_filter_sql(identifiers)
    if not where_sql:
        return VisualAssetDeletionResult()

    asset_ids: list[int] = []
    local_paths: list[str] = []
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT id, local_path
                  FROM {VISUAL_ASSET_TABLE}
                 WHERE {where_sql}
            """,
            params,
        )
        for asset_id, local_path in cursor.fetchall():
            asset_ids.append(int(asset_id))
            if local_path:
                local_paths.append(str(local_path))

    if not asset_ids:
        return VisualAssetDeletionResult()

    deleted_translations = 0
    deleted_assets = 0
    with connection.cursor() as cursor:
        for chunk in _chunks(asset_ids, 400):
            placeholders = _placeholders(chunk)
            if VISUAL_TRANSLATION_TABLE in existing_tables:
                cursor.execute(
                    f"DELETE FROM {VISUAL_TRANSLATION_TABLE} WHERE asset_id IN ({placeholders})",
                    chunk,
                )
                deleted_translations += cursor.rowcount if cursor.rowcount is not None else 0
            cursor.execute(
                f"DELETE FROM {VISUAL_ASSET_TABLE} WHERE id IN ({placeholders})",
                chunk,
            )
            deleted_assets += cursor.rowcount if cursor.rowcount is not None else 0

    deleted_files = _delete_local_media_files(local_paths) if delete_local_files else 0
    return VisualAssetDeletionResult(
        assets=deleted_assets,
        translations=deleted_translations,
        local_files=deleted_files,
    )


def _country_identifiers(country_code: str, *, aliases: Iterable[str] | None = None) -> list[str]:
    values: list[str] = []

    def add(value: str | None) -> None:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)

    add(country_code)
    for alias in aliases or []:
        add(str(alias))

    if country_code:
        for record in ScrapingConfig.objects.filter(country_code__iexact=country_code).only("slug", "country_code"):
            add(record.slug)
            add(record.country_code)
        slug_record = ScrapingConfig.objects.filter(slug=country_code).only("slug", "country_code").first()
        if slug_record:
            add(slug_record.slug)
            add(slug_record.country_code)
    return values


def _asset_delete_filter_sql(identifiers: list[str]) -> tuple[str, list[object]]:
    if not identifiers:
        return "", []

    admin_area_table = AdminArea._meta.db_table
    in_identifiers = _placeholders(identifiers)
    conditions: list[str] = []
    params: list[object] = []

    conditions.append(f"country_code IN ({in_identifiers})")
    params.extend(identifiers)

    conditions.append(f"(entity_type = %s AND entity_key IN ({in_identifiers}))")
    params.append("country")
    params.extend(identifiers)

    conditions.append(
        f"""
        (
            entity_type = %s
            AND entity_key IN (
                SELECT id
                  FROM {admin_area_table}
                 WHERE country_code IN ({in_identifiers})
            )
        )
        """
    )
    params.append("admin_area")
    params.extend(identifiers)

    for identifier in identifiers:
        conditions.append("(entity_type = %s AND entity_key LIKE %s ESCAPE '\\')")
        params.append("admin_area")
        params.append(f"{_escape_like(identifier)}\\_%")

    return " OR ".join(conditions), params


def _delete_local_media_files(relative_paths: Iterable[str]) -> int:
    deleted = 0
    media_root = _media_root()
    visual_assets_root = media_root / "visual_assets"
    for relative_path in dict.fromkeys(str(path or "") for path in relative_paths):
        safe_path = _safe_media_relative_path(relative_path)
        if not safe_path.parts or safe_path.parts[0] != "visual_assets":
            continue
        absolute_path = media_root / safe_path
        if not _is_inside(absolute_path, visual_assets_root):
            continue
        try:
            if absolute_path.is_file():
                absolute_path.unlink()
                deleted += 1
                _prune_empty_parents(absolute_path.parent, stop_at=visual_assets_root)
        except OSError:
            # File cleanup is best-effort; the DB records are the source of truth
            # for the UI and for later asset re-population.
            continue
    return deleted


def _media_root() -> Path:
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or (Path(settings.BASE_DIR) / "media"))
    return media_root.resolve()


def _safe_media_relative_path(relative_path: str) -> Path:
    relative_path = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    parts = [part for part in relative_path.split("/") if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _prune_empty_parents(path: Path, *, stop_at: Path) -> None:
    stop_at = stop_at.resolve()
    current = path.resolve()
    while current != stop_at and _is_inside(current, stop_at):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _escape_like(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _placeholders(values: Iterable[object]) -> str:
    return ", ".join(["%s"] * len(list(values)))


def _chunks(values: list[int], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]
