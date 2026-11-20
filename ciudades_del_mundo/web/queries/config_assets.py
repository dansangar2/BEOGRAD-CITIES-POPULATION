"""Select SQL visual resources for the configuration editor."""

from __future__ import annotations

from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from django.db import OperationalError, ProgrammingError, connection
from django.db.models import Count
from django.utils.translation import gettext as _
from ciudades_del_mundo.services.visual_assets import visual_asset_tables_exist
from ciudades_del_mundo.web.queries import browser as _queries_browser


def _raw_asset_overrides(data: dict) -> list[dict]:
    raw = (
        data.get("wikidata_asset_overrides")
        or data.get("visual_asset_overrides")
        or data.get("asset_overrides")
        or []
    )
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)]


def _asset_assignment_rows_for_form(data: dict, *, country_code: str) -> list[dict]:
    grouped: dict[str, dict[str, str]] = {}
    for override in _raw_asset_overrides(data):
        entity_id = _asset_assignment_entity_id_from_override(override, country_code=country_code)
        if not entity_id:
            continue
        level = "" if override.get("level") is None else str(override.get("level"))
        key = entity_id
        row = grouped.setdefault(
            key,
            {
                "level": level,
                "entity_id": entity_id,
                "flag_qid": "",
                "coat_qid": "",
            },
        )
        if level and not row.get("level"):
            row["level"] = level
        qid = str(override.get("wikidata_id") or override.get("qid") or override.get("wikidata") or "")
        kinds = _list_from_value(override.get("kinds") or override.get("kind") or [])
        if not kinds:
            kinds = ["flag", "coat"]
        for kind in kinds:
            kind = str(kind or "").strip().lower()
            if kind == "flag":
                row["flag_qid"] = qid
            elif kind == "coat":
                row["coat_qid"] = qid
    return list(grouped.values())


def _asset_assignment_entity_id_from_override(override: dict, *, country_code: str) -> str:
    ids = _list_from_value(
        override.get("ids")
        or override.get("id")
        or override.get("entity_keys")
        or override.get("entity_key")
    )
    if ids:
        return str(ids[0])
    try:
        queryset = area_selectors.visible_admin_areas().filter(country_code=country_code)
        level = override.get("level")
        if level is not None and str(level).strip() != "":
            queryset = queryset.filter(level=int(level))
        codes = {value.casefold() for value in _list_from_value(override.get("codes") or override.get("code"))}
        names = {_normalized_text(value) for value in _list_from_value(override.get("names") or override.get("name"))}
        contains = [_normalized_text(value) for value in _list_from_value(override.get("name_contains") or override.get("names_contains"))]
        for area in queryset.order_by("level", "name", "id"):
            if codes and str(area.code or "").casefold() in codes:
                return str(area.id)
            area_names = {_normalized_text(area.name), _normalized_text(getattr(area, "official_name", "") or "")}
            if names and area_names & names:
                return str(area.id)
            haystack = " ".join(name for name in area_names if name)
            if contains and any(needle in haystack for needle in contains):
                return str(area.id)
    except Exception:
        return ""
    return ""


def _asset_assignment_options_for_form(
    country_code: str,
    *,
    enabled: bool,
    selected_entity_ids: list[str] | None = None,
    selected_resource_qids: list[str] | None = None,
) -> dict:
    """Return only the data needed to render the first asset editor paint.

    Large countries can have tens of thousands of AdminArea rows. The edit page
    must not load every entity and every resource just because the user enters
    /configs/{pais}/. Full lists are now requested on demand through
    config_source_entities?asset_options=1, filtered by level and selected
    entity.
    """
    base = {"enabled": bool(enabled), "levels": [], "entities": [], "flags": [], "coats": []}
    if not enabled or not country_code:
        return base
    base["levels"] = _asset_assignment_level_options(country_code)
    entity_ids = [str(value) for value in (selected_entity_ids or []) if str(value or "").strip()]
    qids = [str(value) for value in (selected_resource_qids or []) if str(value or "").strip()]
    if entity_ids:
        base["entities"] = _asset_assignment_entity_options(country_code, entity_ids=entity_ids)
    if qids:
        resources = _country_asset_resource_options(country_code, selected_qids=qids)
        base["flags"] = resources.get("flag", [])
        base["coats"] = resources.get("coat", [])
    return base


def _asset_assignment_options_payload(country_code: str, *, level: str | None = None, entity_id: str | None = None) -> dict:
    """Payload used by the correction editor to load options lazily."""
    enabled = bool(country_code and area_selectors.visible_admin_areas().filter(country_code__iexact=country_code).exists())
    payload = {"enabled": enabled, "levels": [], "entities": [], "flags": [], "coats": []}
    if not enabled:
        return payload
    payload["levels"] = _asset_assignment_level_options(country_code)
    if level is not None and str(level).strip() != "":
        payload["entities"] = _asset_assignment_entity_options(country_code, level=level)
    selected_entity_id = str(entity_id or "").strip()
    if selected_entity_id:
        resources = _country_asset_resource_options(country_code, entity_id=selected_entity_id)
        payload["flags"] = resources.get("flag", [])
        payload["coats"] = resources.get("coat", [])
    return payload


def _asset_assignment_level_options(country_code: str) -> list[dict[str, str]]:
    if not country_code:
        return []
    try:
        rows = (
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .values("level")
            .annotate(total=Count("id"))
            .order_by("level")
        )
        return [
            {"value": str(row["level"]), "label": _("Nivel %(level)s (%(count)s)") % {"level": row["level"], "count": row["total"]}}
            for row in rows
        ]
    except (OperationalError, ProgrammingError, ValueError):
        return []


def _asset_assignment_entity_options(
    country_code: str,
    *,
    level: str | int | None = None,
    entity_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    if not country_code:
        return []
    try:
        queryset = (
            area_selectors.visible_admin_areas()
            .filter(country_code__iexact=country_code)
            .order_by("level", "name", "id")
            .only("id", "name", "level", "code")
        )
        ids = [int(value) for value in (entity_ids or []) if str(value or "").isdigit()]
        if ids:
            queryset = queryset.filter(id__in=ids)
        elif level is not None and str(level).strip() != "":
            queryset = queryset.filter(level=int(level))
        else:
            return []
        areas = list(queryset)
    except (OperationalError, ProgrammingError, TypeError, ValueError):
        return []
    if not areas:
        return []

    assets_by_entity = _admin_area_asset_kind_map([str(area.id) for area in areas])
    entities = []
    for area in areas:
        kinds = assets_by_entity.get(str(area.id), set())
        has_flag = "flag" in kinds
        has_coat = "coat" in kinds
        status = "complete"
        if not has_flag and not has_coat:
            status = "missing_both"
        elif not has_flag:
            status = "missing_flag"
        elif not has_coat:
            status = "missing_coat"
        entities.append(
            {
                "id": str(area.id),
                "level": str(int(area.level or 0)),
                "label": _queries_browser._area_display_name(area),
                "status": status,
                "status_label": _asset_entity_status_label(status),
            }
        )
    return entities


def _asset_entity_status_label(status: str) -> str:
    if status == "missing_both":
        return _("Sin bandera ni escudo")
    if status == "missing_flag":
        return _("Sin bandera")
    if status == "missing_coat":
        return _("Sin escudo")
    return _("Completo")


def _admin_area_asset_kind_map(area_ids: list[str]) -> dict[str, set[str]]:
    if not area_ids or not visual_asset_tables_exist():
        return {}
    area_ids = [str(area_id) for area_id in area_ids if str(area_id or "").strip()]
    result: dict[str, set[str]] = {}
    # SQLite has a hard limit on the number of SQL variables per statement.
    # Large countries such as Spain can have tens of thousands of AdminArea rows,
    # so the asset lookup must be split into small IN() batches. Keep the batch
    # intentionally low because some local SQLite builds are compiled with a
    # lower SQLITE_MAX_VARIABLE_NUMBER than the common 999/32766 defaults.
    batch_size = 100
    with connection.cursor() as cursor:
        for start in range(0, len(area_ids), batch_size):
            batch = area_ids[start : start + batch_size]
            if not batch:
                continue
            placeholders = ", ".join(["%s"] * len(batch))
            cursor.execute(
                f"""
                    SELECT entity_key, kind, commons_filename, remote_url, local_path, local_exists
                    FROM ciudades_del_mundo_visual_asset
                    WHERE entity_type = %s
                      AND entity_key IN ({placeholders})
                      AND kind IN (%s, %s)
                """,
                ["admin_area", *batch, "flag", "coat"],
            )
            for row in _dictfetchall(cursor):
                if _visual_asset_row_has_image(row):
                    result.setdefault(str(row.get("entity_key")), set()).add(str(row.get("kind")))
    return result


def _country_asset_resource_options(
    country_code: str,
    *,
    entity_id: str | None = None,
    selected_qids: list[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    result = {"flag": [], "coat": []}
    if not country_code or not visual_asset_tables_exist():
        return result

    selected_qids_set = {str(value) for value in (selected_qids or []) if str(value or "").strip()}
    selected_area = None
    current_entity_qids: set[str] = set()
    entity_id = str(entity_id or "").strip()
    if entity_id:
        try:
            selected_area = area_selectors.visible_admin_areas().filter(country_code__iexact=country_code, id=int(entity_id)).only(
                "id", "name", "code", "level"
            ).first()
        except (OperationalError, ProgrammingError, TypeError, ValueError):
            selected_area = None

    resources: dict[tuple[str, str], dict[str, object]] = {}
    assigned: set[tuple[str, str]] = set()
    rows: list[dict] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT entity_type, entity_key, entity_name, kind, wikidata_id, commons_filename, remote_url, local_path, local_exists
                FROM ciudades_del_mundo_visual_asset
                WHERE country_code = %s
                  AND kind IN (%s, %s)
                  AND wikidata_id <> ''
                  AND (
                    entity_type = %s
                    OR entity_type = %s
                    OR (entity_type = %s AND entity_key = %s)
                  )
                ORDER BY kind, entity_name, commons_filename
            """,
            [country_code, "flag", "coat", "wikidata_country_resource", "country", "admin_area", entity_id],
        )
        rows = _dictfetchall(cursor)

    for row in rows:
        if not _visual_asset_row_has_image(row):
            continue
        kind = str(row.get("kind") or "")
        qid = str(row.get("wikidata_id") or "")
        if kind not in result or not qid:
            continue
        key = (kind, qid)
        if str(row.get("entity_type") or "") in {"admin_area", "country"}:
            assigned.add(key)
            if not entity_id or str(row.get("entity_key") or "") == entity_id:
                current_entity_qids.add(qid)
        if entity_id and not _asset_resource_matches_selected_entity(row, selected_area, selected_qids_set | current_entity_qids):
            continue
        label = str(row.get("entity_name") or "").strip() or qid
        filename = str(row.get("commons_filename") or "").strip()
        if filename and filename not in label:
            label = f"{label} — {filename}"
        resources.setdefault(key, {"value": qid, "label": label, "assigned": False})

    for key, option in resources.items():
        option["assigned"] = key in assigned
        result[key[0]].append(option)
    for kind in result:
        result[kind].sort(key=lambda item: (bool(item.get("assigned")), str(item.get("label") or "").casefold()))
    return result


def _asset_resource_matches_selected_entity(row: dict, area, forced_qids: set[str]) -> bool:
    qid = str(row.get("wikidata_id") or "")
    if qid and qid in forced_qids:
        return True
    if str(row.get("entity_type") or "") == "admin_area":
        return True
    if area is None:
        return False
    needles = set()
    for value in (getattr(area, "name", ""), getattr(area, "official_name", ""), getattr(area, "code", "")):
        needles.update(_name_match_variants(value))
    haystack_values = [
        row.get("entity_name"),
        row.get("commons_filename"),
        row.get("entity_key"),
    ]
    haystack = " ".join(_normalized_text(value) for value in haystack_values if str(value or "").strip())
    if not haystack:
        return False
    return any(needle and (needle in haystack or haystack in needle) for needle in needles)


def _list_from_value(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("\n", ",").split(",")
    return [str(item or "").strip() for item in raw_values if str(item or "").strip()]


def _visual_asset_row_has_image(row: dict) -> bool:
    return bool(
        row.get("commons_filename")
        or row.get("remote_url")
        or row.get("local_path")
        or row.get("local_exists")
    )


def _dictfetchall(cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
