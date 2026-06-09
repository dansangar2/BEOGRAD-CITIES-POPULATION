"""Persistent manual visual-asset corrections for scraping configs."""

from __future__ import annotations

from django.db import OperationalError, ProgrammingError, connection
from django.utils import timezone


CONFIG_ASSET_OVERRIDE_TABLE = "ciudades_del_mundo_config_asset_override"


def config_asset_override_table_exists() -> bool:
    try:
        return CONFIG_ASSET_OVERRIDE_TABLE in connection.introspection.table_names()
    except (OperationalError, ProgrammingError):
        return False


def load_config_asset_overrides(slug: str) -> list[dict]:
    slug = str(slug or "").strip()
    if not slug or not config_asset_override_table_exists():
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                    SELECT country_code, level, entity_id, entity_code, entity_name, kind, wikidata_id
                    FROM {CONFIG_ASSET_OVERRIDE_TABLE}
                    WHERE config_slug = %s
                    ORDER BY id
                """,
                [slug],
            )
            rows = _dictfetchall(cursor)
    except (OperationalError, ProgrammingError):
        return []

    grouped: dict[str, dict] = {}
    for row in rows:
        entity_id = str(row.get("entity_id") or "").strip()
        if not entity_id:
            continue
        item = grouped.setdefault(
            entity_id,
            {
                "level": "" if row.get("level") is None else str(row.get("level")),
                "entity_id": entity_id,
                "flag_qid": "",
                "coat_qid": "",
            },
        )
        kind = str(row.get("kind") or "").strip().lower()
        qid = str(row.get("wikidata_id") or "").strip()
        if kind == "flag":
            item["flag_qid"] = qid
        elif kind == "coat":
            item["coat_qid"] = qid
    return list(grouped.values())


def save_config_asset_overrides(slug: str, country_code: str, rows: list[dict]) -> None:
    slug = str(slug or "").strip()
    if not slug or not config_asset_override_table_exists():
        return
    now = timezone.now()
    normalized = _normalized_override_rows(slug, country_code, rows)
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {CONFIG_ASSET_OVERRIDE_TABLE} WHERE config_slug = %s", [slug])
        for row in normalized:
            cursor.execute(
                f"""
                    INSERT INTO {CONFIG_ASSET_OVERRIDE_TABLE} (
                        config_slug, country_code, level, entity_id, entity_code, entity_name, kind,
                        wikidata_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    row["config_slug"],
                    row["country_code"],
                    row["level"],
                    row["entity_id"],
                    row["entity_code"],
                    row["entity_name"],
                    row["kind"],
                    row["wikidata_id"],
                    now,
                    now,
                ],
            )


def _normalized_override_rows(slug: str, country_code: str, rows: list[dict]) -> list[dict]:
    result = []
    seen: set[tuple[str, str]] = set()
    country_code = str(country_code or slug or "").strip()
    for row in rows or []:
        entity_id = str(row.get("entity_id") or "").strip()
        if not entity_id:
            continue
        level = row.get("level")
        try:
            level = int(level) if str(level or "").strip() != "" else None
        except (TypeError, ValueError):
            level = None
        for kind, qid_key in (("flag", "flag_qid"), ("coat", "coat_qid")):
            qid = str(row.get(qid_key) or "").strip().upper()
            if not qid:
                continue
            key = (entity_id, kind)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "config_slug": slug,
                    "country_code": country_code,
                    "level": level,
                    "entity_id": entity_id,
                    "entity_code": str(row.get("entity_code") or "").strip(),
                    "entity_name": str(row.get("entity_name") or "").strip(),
                    "kind": kind,
                    "wikidata_id": qid,
                }
            )
    return result


def _dictfetchall(cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
