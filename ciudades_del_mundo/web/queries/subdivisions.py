"""Select derived subdivision definitions and materialized metrics."""

from __future__ import annotations

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

from collections import Counter
from pathlib import Path
import tomllib
from django.db import OperationalError, ProgrammingError
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import DerivedSubdivision, NuevoAdminArea
from ciudades_del_mundo.services.derived_codes import derived_code_piece
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.forms import subdivisions as _forms_subdivisions
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import groups as _queries_groups
from ciudades_del_mundo.web.queries import subdivision_capitals as _queries_subdivision_capitals
from ciudades_del_mundo.web.queries import subdivision_sources as _queries_subdivision_sources


def _derived_subdivision_country_cards(
    records: list[DerivedSubdivision],
    *,
    seed_paths: list[Path],
) -> list[dict]:
    records_by_country: dict[str, list[DerivedSubdivision]] = {}
    fallback_records: list[DerivedSubdivision] = []
    for record in records:
        country_code = query_parameters.normalize_country_key(record.source_country_code)
        if not country_code:
            fallback_records.append(record)
            continue
        records_by_country.setdefault(country_code, []).append(record)

    seed_counts_by_country: dict[str, int] = {}
    fallback_seed_count = 0
    for path in seed_paths:
        country_code = _derived_subdivision_seed_country_from_path(path)
        if country_code:
            seed_counts_by_country[country_code] = seed_counts_by_country.get(country_code, 0) + 1
        else:
            fallback_seed_count += 1

    country_rows = _queries_browser._admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes: set[str] = set()
    cards = []
    for country in country_rows:
        code = query_parameters.normalize_country_key(country.get("code"))
        if not code or code not in records_by_country:
            continue
        visible_country_codes.add(code)
        country_records = records_by_country.pop(code, [])
        group_rows = _queries_groups._subdivision_group_entries_for_country(_queries_groups._subdivision_groups_for_country(code), code)
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population_text": _queries_groups._group_metric_text(country.get("population")),
                "area_text": _queries_groups._group_metric_text(country.get("area_km2")),
                "flag_url": _queries_groups._stored_visual_asset_image_url(country.get("flag_asset")),
                "detail_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_country_detail_data",
                    kwargs={"country_code": code},
                ),
                "subdivisions": [],
                "subdivision_count": len(country_records),
                "groups": [],
                "group_count": len(group_rows),
                "seed_count": seed_counts_by_country.pop(code, 0),
                "export_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_export_toml",
                    kwargs={"slug": code},
                ),
                "build_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_build",
                    kwargs={"country_code": code},
                ),
                "new_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_new",
                    kwargs={"country_code": code},
                ),
            }
        )

    unmatched_records = list(fallback_records)
    for country_code in sorted(records_by_country):
        if country_code not in visible_country_codes:
            unmatched_records.extend(records_by_country[country_code])
    unmatched_seed_count = fallback_seed_count
    if unmatched_records:
        cards.append(
            {
                "key": "__other__",
                "code": "",
                "label": _("Otros paises"),
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "detail_url": "",
                "subdivisions": [],
                "subdivision_count": len(unmatched_records),
                "groups": [],
                "group_count": 0,
                "seed_count": unmatched_seed_count,
                "export_url": "",
                "build_url": "",
                "new_url": reverse("ciudades_del_mundo:derived_subdivision_list"),
            }
        )
    return cards


def _derived_subdivision_rows_for_country(records: list[DerivedSubdivision], country_code: str) -> list[dict]:
    rows = []
    data_by_slug = {record.slug: _derived_subdivision_record_toml(record) for record in records}
    code_counts = Counter(
        _derived_subdivision_requested_code_count_key(record, data_by_slug.get(record.slug) or {})
        for record in records
    )
    materialized_rows = _derived_subdivision_materialized_rows_for_records(
        records,
        data_by_slug=data_by_slug,
        code_counts=code_counts,
    )
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        area_value, population_value = _derived_subdivision_metric_values(
            record,
            data,
            code_counts=code_counts,
            materialized_rows=materialized_rows,
        )
        area_text = _queries_groups._group_metric_text(area_value)
        population_text = _queries_groups._group_metric_text(population_value)
        summary = _derived_subdivision_content_summary(record.content)
        entry_slug = _derived_subdivision_entry_slug(record)
        href = (
            reverse(
                "ciudades_del_mundo:derived_subdivision_edit",
                kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
            )
            if country_code
            else "#"
        )
        rows.append(
            {
                "record": record,
                "internal_name": record.internal_name or entry_slug.upper(),
                "display_code": str(record.code or _queries_subdivision_sources._derived_subdivision_source_key(record, data) or entry_slug.upper()),
                "entry_slug": entry_slug,
                "href": href,
                "delete_url": (
                    reverse(
                        "ciudades_del_mundo:derived_subdivision_delete",
                        kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
                    )
                    if country_code
                    else ""
                ),
                "clone_url": (
                    reverse(
                        "ciudades_del_mundo:derived_subdivision_clone",
                        kwargs={"country_code": country_code, "subdivision_slug": entry_slug},
                    )
                    if country_code
                    else ""
                ),
                "include_count": summary["include_count"],
                "subtract_count": summary["subtract_count"],
                "child_count": summary["child_count"],
                "group_count": summary["group_count"],
                "area_text": area_text,
                "population_text": population_text,
                "search_text": " ".join(
                    [
                        record.slug,
                        record.internal_name,
                        record.name,
                        record.entity_type,
                        record.code,
                        str(summary["include_count"]),
                        str(summary["subtract_count"]),
                        area_text,
                        population_text,
                    ]
                ),
            }
        )
    return sorted(rows, key=lambda row: (str(row["internal_name"]), str(row["record"].name)))


def _derived_subdivision_country_detail_payload(country_code: str) -> dict:
    country_code = query_parameters.normalize_country_key(country_code)
    try:
        records = list(
            DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by(
                "source_country_code",
                "name",
                "slug",
            )
        )
    except (OperationalError, ProgrammingError):
        records = []
    subdivision_rows = _derived_subdivision_rows_for_country(records, country_code)
    group_entries = _queries_groups._subdivision_group_entries_for_country(_queries_groups._subdivision_groups_for_country(country_code), country_code)
    return {
        "ok": True,
        "country_code": country_code,
        "subdivisions": _derived_subdivision_list_rows_payload(subdivision_rows),
        "groups": _queries_groups._group_entries_payload(group_entries),
    }


def _derived_subdivision_list_rows_payload(rows: list[dict]) -> list[dict]:
    payload = []
    for row in rows:
        record = row.get("record")
        payload.append(
            {
                "display_code": str(row.get("display_code") or ""),
                "name": str(getattr(record, "name", "") or ""),
                "entity_type": str(getattr(record, "entity_type", "") or "-"),
                "area_text": str(row.get("area_text") or "-"),
                "population_text": str(row.get("population_text") or "-"),
                "include_count": int(row.get("include_count") or 0),
                "subtract_count": int(row.get("subtract_count") or 0),
                "group_count": int(row.get("group_count") or 0),
                "href": str(row.get("href") or ""),
                "clone_url": str(row.get("clone_url") or ""),
                "delete_url": str(row.get("delete_url") or ""),
                "search_text": str(row.get("search_text") or ""),
            }
        )
    return payload


def _derived_subdivision_materialized_rows_for_records(
    records: list[DerivedSubdivision],
    *,
    data_by_slug: dict[str, dict],
    code_counts: Counter,
) -> dict[tuple[str, str], NuevoAdminArea]:
    codes_by_country: dict[str, set[str]] = {}
    for record in records:
        data = data_by_slug.get(record.slug) or {}
        source_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
        code = _derived_subdivision_materialized_code(record, data, code_counts)
        if source_code and code:
            codes_by_country.setdefault(source_code, set()).add(code)

    materialized: dict[tuple[str, str], NuevoAdminArea] = {}
    for source_code, codes in codes_by_country.items():
        try:
            rows = (
                NuevoAdminArea.objects
                .filter(country_code__iexact=source_code, code__in=codes)
                .only("country_code", "code", "area_km2", "pop_latest", "updated_at")
            )
        except (OperationalError, ProgrammingError, ValueError):
            continue
        for row in rows:
            materialized[(source_code, str(row.code or ""))] = row
    return materialized


def _derived_subdivision_metric_values(
    record: DerivedSubdivision,
    data: dict,
    *,
    code_counts: Counter,
    materialized_rows: dict[tuple[str, str], NuevoAdminArea],
) -> tuple[object, object]:
    source_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    code = _derived_subdivision_materialized_code(record, data, code_counts)
    area_value = None
    population_value = None
    materialized = materialized_rows.get((source_code, code))
    if materialized is not None:
        area_value = materialized.area_km2
        population_value = materialized.pop_latest
    if materialized is not None and not _derived_subdivision_materialized_row_is_stale(record, materialized):
        if area_value is not None and population_value is not None:
            return area_value, population_value
    preview_area, preview_population = _derived_subdivision_preview_metric_values(source_code, record.content)
    if materialized is not None and _derived_subdivision_materialized_row_is_stale(record, materialized):
        return (
            preview_area if preview_area is not None else area_value,
            preview_population if preview_population is not None else population_value,
        )
    if area_value is not None and population_value is not None:
        return area_value, population_value
    return (
        area_value if area_value is not None else preview_area,
        population_value if population_value is not None else preview_population,
    )


def _derived_subdivision_materialized_row_is_stale(record: DerivedSubdivision, materialized: NuevoAdminArea) -> bool:
    record_updated = getattr(record, "updated_at", None)
    materialized_updated = getattr(materialized, "updated_at", None)
    return bool(record_updated and materialized_updated and record_updated > materialized_updated)


def _derived_subdivision_preview_metric_values(country_code: str, content: str) -> tuple[object, object]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return None, None
    try:
        source_ids = _queries_subdivision_capitals._derived_subdivision_capital_source_ids(country_code, content)
    except (OperationalError, ProgrammingError, ValueError):
        return None, None
    if not source_ids:
        return None, None
    try:
        aggregate = area_selectors.group_source_admin_areas().filter(id__in=source_ids).aggregate(
            area=Sum("area_km2"),
            population=Sum("pop_latest"),
        )
    except (OperationalError, ProgrammingError, ValueError):
        return None, None
    return aggregate.get("area"), aggregate.get("population")


def _derived_subdivision_content_summary(content: str) -> dict[str, int]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    def list_count(key: str) -> int:
        value = data.get(key)
        return len(value) if isinstance(value, list) else 0

    group_names: set[str] = set()
    for key in ("include", "subtract"):
        blocks = data.get(key) if isinstance(data.get(key), list) else []
        for block in blocks:
            if isinstance(block, dict):
                for group in block.get("groups") or []:
                    if str(group or "").strip():
                        group_names.add(str(group).strip())
    for block in (data.get("capital_groups") if isinstance(data.get("capital_groups"), list) else []):
        if isinstance(block, dict) and str(block.get("group") or "").strip():
            group_names.add(str(block.get("group")).strip())
    children = data.get("children") if isinstance(data.get("children"), list) else []
    for child in children:
        if not isinstance(child, dict):
            continue
        for key in ("include", "subtract"):
            blocks = child.get(key) if isinstance(child.get(key), list) else []
            for block in blocks:
                if isinstance(block, dict):
                    for group in block.get("groups") or []:
                        if str(group or "").strip():
                            group_names.add(str(group).strip())
        for block in (child.get("capital_groups") if isinstance(child.get("capital_groups"), list) else []):
            if isinstance(block, dict) and str(block.get("group") or "").strip():
                group_names.add(str(block.get("group")).strip())
    return {
        "include_count": list_count("include"),
        "subtract_count": list_count("subtract"),
        "child_count": len(children),
        "group_count": len(group_names),
    }


def _derived_subdivision_record_toml(record: DerivedSubdivision) -> dict:
    try:
        data = tomllib.loads(record.content or "")
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derived_subdivision_requested_code(record: DerivedSubdivision, data: dict | None = None) -> str:
    data = data if data is not None else _derived_subdivision_record_toml(record)
    return str(data.get("code") or record.code or "").strip()


def _derived_subdivision_requested_code_count_key(
    record: DerivedSubdivision,
    data: dict | None = None,
) -> tuple[str, str]:
    data = data if data is not None else _derived_subdivision_record_toml(record)
    country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    return country_code, _derived_subdivision_requested_code(record, data)


def _derived_subdivision_materialized_code(
    record: DerivedSubdivision,
    data: dict,
    code_counts: Counter,
) -> str:
    requested_code = _derived_subdivision_requested_code(record, data)
    country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    count_key = (country_code, requested_code)
    requested_code_count = code_counts[count_key] if count_key in code_counts else code_counts[requested_code]
    if requested_code and requested_code_count == 1:
        code_value = requested_code
    else:
        code_value = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    parent_code = _forms_subdivisions._derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    try:
        return _forms_subdivisions._derived_subdivision_compose_code(
            country_code=country_code,
            parent_code=parent_code,
            code_value=code_value,
        )
    except ValueError:
        return ""


def _derived_subdivision_materialized_code_candidates(record: DerivedSubdivision) -> tuple[str, set[str]]:
    data = _derived_subdivision_record_toml(record)
    country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    if not country_code:
        return "", set()
    country_records = list(DerivedSubdivision.objects.filter(source_country_code__iexact=country_code))
    data_by_slug = {item.slug: _derived_subdivision_record_toml(item) for item in country_records}
    code_counts = Counter(
        _derived_subdivision_requested_code(item, data_by_slug.get(item.slug) or {})
        for item in country_records
    )
    parent_code = _forms_subdivisions._derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=country_code)
    candidates = set()
    materialized_code = _derived_subdivision_materialized_code(record, data, code_counts)
    if materialized_code:
        candidates.add(materialized_code)
    requested_code = _derived_subdivision_requested_code(record, data)
    if requested_code and code_counts[requested_code] == 1:
        try:
            candidates.add(
                _forms_subdivisions._derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=parent_code,
                    code_value=requested_code,
                )
            )
        except ValueError:
            pass
    legacy_code = str(data.get("internal_name") or record.internal_name or record.slug).strip()
    if legacy_code:
        try:
            candidates.add(
                _forms_subdivisions._derived_subdivision_compose_code(
                    country_code=country_code,
                    parent_code=parent_code,
                    code_value=legacy_code,
                )
            )
        except ValueError:
            pass
    return country_code, {code for code in candidates if code}


def _derived_subdivision_edit_link_indexes(
    country_codes: set[str] | None,
) -> tuple[
    dict[tuple[str, str], list[tuple[str, str]]],
    dict[tuple[str, str], list[tuple[str, str]]],
    dict[str, dict],
]:
    normalized_codes = {
        query_parameters.normalize_country_key(code)
        for code in (country_codes or set())
        if query_parameters.normalize_country_key(code)
    }
    if not normalized_codes:
        return {}, {}, {}
    records = list(
        DerivedSubdivision.objects.filter(source_country_code__in=normalized_codes).order_by(
            "source_country_code",
            "slug",
        )
    )
    records_by_country: dict[str, list[tuple[DerivedSubdivision, dict]]] = {}
    for record in records:
        source_code = query_parameters.normalize_country_key(record.source_country_code)
        if not source_code:
            continue
        records_by_country.setdefault(source_code, []).append((record, _derived_subdivision_record_toml(record)))

    code_links: dict[tuple[str, str], list[tuple[str, str]]] = {}
    name_links: dict[tuple[str, str], list[tuple[str, str]]] = {}
    metadata_by_href: dict[str, dict] = {}
    for source_code, country_records in records_by_country.items():
        code_counts = Counter(
            _derived_subdivision_requested_code(record, data)
            for record, data in country_records
        )
        name_counts = Counter(
            name_key
            for record, data in country_records
            for name_key in _derived_subdivision_edit_name_keys(record, data)
        )
        for record, data in country_records:
            materialized_code = _derived_subdivision_materialized_code(record, data, code_counts)
            if not materialized_code:
                continue
            link_codes = [materialized_code]
            requested_code = _derived_subdivision_requested_code(record, data)
            if requested_code and code_counts[requested_code] == 1 and requested_code not in link_codes:
                link_codes.append(requested_code)
            legacy_code = str(data.get("internal_name") or record.internal_name or record.slug).strip()
            if legacy_code and legacy_code not in link_codes:
                link_codes.append(legacy_code)
            if legacy_code:
                parent_code = _forms_subdivisions._derived_subdivision_clean_parent_code(data.get("parent_code"), country_code=source_code)
                try:
                    legacy_materialized_code = _forms_subdivisions._derived_subdivision_compose_code(
                        country_code=source_code,
                        parent_code=parent_code,
                        code_value=legacy_code,
                    )
                except ValueError:
                    legacy_materialized_code = ""
                if legacy_materialized_code and legacy_materialized_code not in link_codes:
                    link_codes.append(legacy_materialized_code)
            href = reverse(
                "ciudades_del_mundo:derived_subdivision_edit",
                kwargs={
                    "country_code": source_code,
                    "subdivision_slug": _derived_subdivision_entry_slug(record),
                },
            )
            metadata_by_href[href] = _derived_subdivision_edit_metadata(record, data, href=href)
            for code in link_codes:
                code_links.setdefault((source_code, source_code), []).append((code, href))
            for name_key in _derived_subdivision_edit_name_keys(record, data):
                if name_counts[name_key] == 1:
                    name_links.setdefault((source_code, source_code), []).append((name_key, href))
    return code_links, name_links, metadata_by_href


def _derived_subdivision_edit_link_index(country_codes: set[str] | None) -> dict[tuple[str, str], list[tuple[str, str]]]:
    code_links, _name_links, _metadata = _derived_subdivision_edit_link_indexes(country_codes)
    return code_links


def _derived_subdivision_edit_metadata(record: DerivedSubdivision, data: dict, *, href: str) -> dict:
    try:
        level = _forms_subdivisions._derived_subdivision_level(data.get("level") or 1)
    except ValueError:
        level = 1
    entity_type = str(data.get("entity_type") or record.entity_type or data.get("generic_name") or "").strip()
    return {
        "href": href,
        "delete_url": reverse(
            "ciudades_del_mundo:derived_subdivision_delete",
            kwargs={
                "country_code": query_parameters.normalize_country_key(record.source_country_code),
                "subdivision_slug": _derived_subdivision_entry_slug(record),
            },
        ),
        "clone_url": reverse(
            "ciudades_del_mundo:derived_subdivision_clone",
            kwargs={
                "country_code": query_parameters.normalize_country_key(record.source_country_code),
                "subdivision_slug": _derived_subdivision_entry_slug(record),
            },
        ),
        "name": str(data.get("name") or record.name or data.get("internal_name") or record.internal_name or "").strip(),
        "level": level,
        "type_text": entity_type or "-",
        "source_country_code": query_parameters.normalize_country_key(record.source_country_code),
        "content": record.content,
        "updated_at": record.updated_at,
    }


def _derived_subdivision_edit_name_keys(record: DerivedSubdivision, data: dict) -> set[str]:
    keys = set()
    for value in (data.get("name"), record.name, data.get("internal_name"), record.internal_name):
        key = _queries_groups._group_name_match_key(str(value or ""))
        if key:
            keys.add(key)
    return keys


def _derived_subdivision_edit_href_for_area(
    link_index: dict[tuple[str, str], list[tuple[str, str]]],
    name_index: dict[tuple[str, str], list[tuple[str, str]]],
    source_codes: set[str],
    *,
    new_area_code: str,
    area_code: str | None,
    area_name: str | None = None,
) -> str:
    return _derived_subdivision_edit_match_for_area(
        link_index,
        name_index,
        source_codes,
        new_area_code=new_area_code,
        area_code=area_code,
        area_name=area_name,
    ).get("href", "")


def _derived_subdivision_edit_match_for_area(
    link_index: dict[tuple[str, str], list[tuple[str, str]]],
    name_index: dict[tuple[str, str], list[tuple[str, str]]],
    source_codes: set[str],
    *,
    new_area_code: str,
    area_code: str | None,
    area_name: str | None = None,
) -> dict:
    area_key = _queries_browser._division_row_code_key(area_code)
    area_name_key = _queries_groups._group_name_match_key(str(area_name or ""))
    for source_code in sorted(query_parameters.normalize_country_key(code) for code in source_codes):
        entries = link_index.get((source_code, new_area_code), [])
        if area_key:
            for code, href in entries:
                if _queries_browser._division_row_code_key(code) == area_key:
                    return {"href": href, "use_definition_metadata": True}
            for code, href in sorted(entries, key=lambda item: len(_queries_browser._division_row_code_key(item[0])), reverse=True):
                code_key = _queries_browser._division_row_code_key(code)
                if code_key and area_key.startswith(f"{code_key}-"):
                    return {"href": href, "use_definition_metadata": False}
        if area_name_key:
            for name_key, href in name_index.get((source_code, new_area_code), []):
                if name_key == area_name_key:
                    return {"href": href, "use_definition_metadata": True}
    return {"href": "", "use_definition_metadata": False}


def _derived_subdivision_seed_paths_for_country(paths: list[Path], country_code: str) -> list[Path]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return paths
    return [path for path in paths if _derived_subdivision_seed_country_from_path(path) == country_code]


def _derived_subdivision_seed_country_from_path(path: Path) -> str:
    path = Path(path)
    if path.parent.name == "subdivisions":
        return query_parameters.normalize_country_key(path.stem)
    try:
        if path.parent.parent.name == "subdivisions":
            return query_parameters.normalize_country_key(path.parent.name)
    except IndexError:
        return ""
    return ""


def _derived_subdivision_counts_by_country(country_codes: set[str] | None = None) -> dict[str, int]:
    country_codes = {
        query_parameters.normalize_country_key(code)
        for code in (country_codes or set())
        if query_parameters.normalize_country_key(code)
    }
    try:
        queryset = DerivedSubdivision.objects.values("source_country_code").annotate(total=Count("slug"))
        if country_codes:
            queryset = queryset.filter(source_country_code__in=country_codes)
        return {
            query_parameters.normalize_country_key(row["source_country_code"]): int(row["total"] or 0)
            for row in queryset
            if query_parameters.normalize_country_key(row["source_country_code"])
        }
    except (OperationalError, ProgrammingError):
        return {}


def _derived_subdivision_record_slug(country_code: str, subdivision_slug: str) -> str:
    return _forms_recipes._normalize_recipe_slug(
        f"{query_parameters.normalize_country_key(country_code)}_{_queries_groups._group_entry_slug(subdivision_slug)}"
        if query_parameters.normalize_country_key(country_code)
        else _queries_groups._group_entry_slug(subdivision_slug)
    )


def _derived_subdivision_entry_slug(record: DerivedSubdivision) -> str:
    country_code = query_parameters.normalize_country_key(record.source_country_code)
    if country_code and record.slug.startswith(f"{country_code}_"):
        return _queries_groups._group_entry_slug(record.slug[len(country_code) + 1 :])
    if record.internal_name:
        return _queries_groups._group_entry_slug(record.internal_name)
    return _queries_groups._group_entry_slug(record.slug)


def _derived_subdivision_clone_internal_and_code(record: DerivedSubdivision, data: dict) -> tuple[str, str, str]:
    country_code = query_parameters.normalize_country_key(data.get("source_country_code") or record.source_country_code)
    source_key = _queries_subdivision_sources._derived_subdivision_source_key(record, data)
    base_internal = _queries_groups._group_internal_name(f"{source_key}_COPIA")
    base_entry_slug = _queries_groups._group_entry_slug(base_internal)
    suffix_index = 1
    candidate_internal = base_internal
    candidate_entry_slug = base_entry_slug
    candidate_slug = _derived_subdivision_record_slug(country_code, candidate_entry_slug)
    while DerivedSubdivision.objects.filter(slug=candidate_slug).exists():
        suffix_index += 1
        candidate_internal = _queries_groups._group_internal_name(f"{base_internal}_{suffix_index}")
        candidate_entry_slug = _queries_groups._group_entry_slug(candidate_internal)
        candidate_slug = _derived_subdivision_record_slug(country_code, candidate_entry_slug)
    base_code = derived_code_piece(data.get("code") or record.code or source_key)
    suffix = "COPIA" if suffix_index <= 1 else f"COPIA-{suffix_index}"
    clone_code = f"{base_code}-{suffix}" if base_code else suffix
    return candidate_internal, clone_code, candidate_slug


def _derived_subdivision_for_country(country_code: str, subdivision_slug: str) -> DerivedSubdivision | None:
    country_code = query_parameters.normalize_country_key(country_code)
    subdivision_slug = _queries_groups._group_entry_slug(subdivision_slug)
    record_slug = _derived_subdivision_record_slug(country_code, subdivision_slug)
    record = DerivedSubdivision.objects.filter(slug=record_slug).first()
    if record:
        return record
    direct = DerivedSubdivision.objects.filter(slug=subdivision_slug, source_country_code__iexact=country_code).first()
    if direct:
        return direct
    queryset = DerivedSubdivision.objects.filter(source_country_code__iexact=country_code).order_by("slug")
    try:
        internal_name = _queries_groups._group_internal_name(subdivision_slug)
    except ValueError:
        internal_name = ""
    if internal_name:
        record = queryset.filter(internal_name__iexact=internal_name).first()
        if record:
            return record
    requested_code = _forms_subdivisions._derived_subdivision_code_piece(subdivision_slug)
    if requested_code:
        record = queryset.filter(code__iexact=requested_code).first()
        if record:
            return record
    for candidate in queryset:
        data = _derived_subdivision_record_toml(candidate)
        names = [
            data.get("entry_slug"),
            data.get("internal_name"),
            data.get("slug"),
            candidate.internal_name,
            candidate.code,
        ]
        for name in names:
            if not str(name or "").strip():
                continue
            try:
                if _queries_groups._group_entry_slug(str(name)) == subdivision_slug:
                    return candidate
            except ValueError:
                continue
    return None
