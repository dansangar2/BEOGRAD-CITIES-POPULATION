"""Select group definitions, members and metrics from SQL."""

from __future__ import annotations
from ciudades_del_mundo.ports.query_errors import DataNotFoundError

from ciudades_del_mundo.application import query_parameters as query_parameters
from ciudades_del_mundo.infrastructure.django import area_selectors as area_selectors

import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
import tomllib
import unicodedata
from django.db import OperationalError, ProgrammingError
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services.visual_assets import commons_file_url
from ciudades_del_mundo.services.derived_config_seeds import bundled_subdivision_group_paths
from ciudades_del_mundo.web.forms import recipes as _forms_recipes
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web.queries import subdivisions as _queries_subdivisions
from ciudades_del_mundo.web import view_support as _view_support


def _group_new_area_country_codes_by_source(country_codes: set[str] | None = None) -> dict[str, set[str]]:
    country_codes = {
        query_parameters.normalize_country_key(code)
        for code in (country_codes or set())
        if query_parameters.normalize_country_key(code)
    }
    if not country_codes:
        return {}

    # Keep the direct country_code mapping for derived rows whose code matches
    # the source country, then add explicit new-country config mappings.
    result: dict[str, set[str]] = {code: {code} for code in country_codes}
    try:
        for row in DerivedCountry.objects.filter(source_country_code__in=country_codes).values(
            "source_country_code",
            "slug",
        ):
            source_code = query_parameters.normalize_country_key(row.get("source_country_code"))
            derived_code = query_parameters.normalize_country_key(row.get("slug"))
            if source_code and derived_code:
                result.setdefault(source_code, set()).add(derived_code)

        for row in (
            DerivedCountryConfig.objects.filter(
                Q(source_country_code__in=country_codes)
                | Q(country__source_country_code__in=country_codes)
            )
            .values(
                "source_country_code",
                "derived_country_code",
                "country_id",
                "country__source_country_code",
            )
            .order_by("source_country_code", "derived_country_code", "country_id")
        ):
            source_code = query_parameters.normalize_country_key(row.get("source_country_code")) or query_parameters.normalize_country_key(
                row.get("country__source_country_code")
            )
            derived_code = query_parameters.normalize_country_key(row.get("derived_country_code")) or query_parameters.normalize_country_key(
                row.get("country_id")
            )
            if source_code and derived_code:
                result.setdefault(source_code, set()).add(derived_code)
    except (OperationalError, ProgrammingError):
        pass
    return result


def _group_country_cards(
    groups: list[SubdivisionGroup],
    *,
    seed_records: list[dict],
    subdivision_seed_paths: list[Path],
) -> list[dict]:
    groups_by_country: dict[str, list[SubdivisionGroup]] = {}
    fallback_groups: list[SubdivisionGroup] = []
    for group in groups:
        codes = _subdivision_group_record_country_codes(group)
        if not codes:
            fallback_groups.append(group)
            continue
        for code in codes:
            groups_by_country.setdefault(code, []).append(group)

    cards = []
    visible_group_slugs: set[str] = set()
    country_rows = _queries_browser._admin_root_population_rows(
        detail_route="ciudades_del_mundo:api_country_detail",
        include_visual_assets=True,
    )
    visible_country_codes = {
        str(country.get("code") or "").strip().lower()
        for country in country_rows
        if str(country.get("code") or "").strip()
    }
    derived_subdivision_counts_by_country = _queries_subdivisions._derived_subdivision_counts_by_country(visible_country_codes)
    seed_counts_by_country: dict[str, int] = {}
    fallback_seed_count = 0
    for seed in seed_records:
        codes = set(seed.get("country_codes") or set())
        visible_codes = codes & visible_country_codes
        if not visible_codes:
            fallback_seed_count += 1
            continue
        for code in visible_codes:
            seed_counts_by_country[code] = seed_counts_by_country.get(code, 0) + 1

    subdivision_seed_counts_by_country: dict[str, int] = {}
    fallback_subdivision_seed_count = 0
    for path in subdivision_seed_paths:
        country_code = _queries_subdivisions._derived_subdivision_seed_country_from_path(path)
        if country_code and country_code in visible_country_codes:
            subdivision_seed_counts_by_country[country_code] = subdivision_seed_counts_by_country.get(country_code, 0) + 1
        else:
            fallback_subdivision_seed_count += 1

    for country in country_rows:
        code = str(country.get("code") or "").strip().lower()
        if not code:
            continue
        country_groups = groups_by_country.pop(code, [])
        visible_group_slugs.update(group.slug for group in country_groups)
        group_entries = _subdivision_group_entries_for_country(country_groups, code)
        seed_count = seed_counts_by_country.pop(code, 0)
        subdivision_seed_count = subdivision_seed_counts_by_country.pop(code, 0)
        cards.append(
            {
                "key": code,
                "code": code,
                "label": country.get("label") or code,
                "population": int(country.get("population") or 0),
                "area_km2": country.get("area_km2"),
                "population_text": _group_metric_text(country.get("population")),
                "area_text": _group_metric_text(country.get("area_km2")),
                "flag_url": _stored_visual_asset_image_url(country.get("flag_asset")),
                "detail_url": reverse(
                    "ciudades_del_mundo:group_country_detail_data",
                    kwargs={"country_code": code},
                ),
                "groups": [],
                "group_count": len(group_entries),
                "level_rows": [],
                "division_rows": [],
                "seed_count": seed_count,
                "subdivision_seed_count": subdivision_seed_count,
                "subdivision_count": derived_subdivision_counts_by_country.get(code, 0),
                "export_url": reverse(
                    "ciudades_del_mundo:group_export_toml",
                    kwargs={"slug": code},
                ),
                "new_group_url": reverse(
                    "ciudades_del_mundo:group_entry_new",
                    kwargs={"country_code": code},
                ),
                "subdivision_export_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_export_toml",
                    kwargs={"slug": code},
                ),
                "new_subdivision_url": reverse(
                    "ciudades_del_mundo:derived_subdivision_new",
                    kwargs={"country_code": code},
                ),
            }
        )

    unmatched_groups = list(fallback_groups)
    for code in sorted(groups_by_country):
        unmatched_groups.extend(
            group for group in groups_by_country[code] if group.slug not in visible_group_slugs
        )
    unmatched_seed_count = fallback_seed_count
    unmatched_subdivision_seed_count = fallback_subdivision_seed_count + sum(subdivision_seed_counts_by_country.values())
    if unmatched_groups or unmatched_seed_count:
        fallback_key = "__other__"
        fallback_entries = []
        for group in unmatched_groups:
            for entry in _subdivision_group_record_entries(group, fallback_key):
                fallback_entries.append(
                    {
                        **entry,
                        "href": reverse(
                            "ciudades_del_mundo:group_edit",
                            kwargs={"slug": group.slug},
                        ),
                    }
                )
        cards.append(
            {
                "key": fallback_key,
                "code": "",
                "label": _("Otros paises"),
                "groups": fallback_entries,
                "group_count": len(fallback_entries),
                "level_rows": [],
                "division_rows": [],
                "population": None,
                "area_km2": None,
                "population_text": "-",
                "area_text": "-",
                "flag_url": "",
                "detail_url": "",
                "new_group_url": reverse("ciudades_del_mundo:group_new"),
                "seed_count": unmatched_seed_count,
                "subdivision_seed_count": unmatched_subdivision_seed_count,
                "subdivision_count": 0,
                "subdivision_export_url": "",
                "new_subdivision_url": reverse("ciudades_del_mundo:derived_subdivision_list"),
            }
        )
    return cards


def _group_country_detail_payload(country_code: str) -> dict:
    country_code = query_parameters.normalize_country_key(country_code)
    division_rows = _group_derived_subdivision_definition_rows(country_code)
    group_entries = _subdivision_group_entries_for_country(_subdivision_groups_for_country(country_code), country_code)
    return {
        "ok": True,
        "country_code": country_code,
        "groups": _group_entries_payload(group_entries),
        "level_rows": _group_level_rows_from_division_rows(division_rows),
        "division_rows": _group_division_rows_payload(division_rows),
    }


def _group_derived_subdivision_definition_rows(country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    rows = list(_queries_browser._pending_derived_subdivision_rows_by_country({country_code}).get(country_code, []))
    rows.sort(key=lambda row: (int(row.get("level") or 0), str(row.get("name") or ""), str(row.get("id") or "")))
    return rows


def _group_entries_payload(entries: list[dict]) -> list[dict]:
    return [
        {
            "internal_name": str(entry.get("internal_name") or ""),
            "municipality_count": int(entry.get("municipality_count") or 0),
            "municipality_count_text": str(entry.get("municipality_count_text") or "0"),
            "href": str(entry.get("href") or ""),
            "search_text": " ".join(
                str(value or "")
                for value in (
                    entry.get("slug"),
                    entry.get("internal_name"),
                    entry.get("name"),
                    entry.get("municipality_count"),
                )
            ),
        }
        for entry in entries
    ]


def _group_level_rows_from_division_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in rows:
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        entry = grouped.setdefault(level, {"level": level, "types": set(), "count": 0})
        type_text = str(row.get("type_text") or "").strip()
        if type_text and type_text != "-":
            entry["types"].add(type_text)
        entry["count"] += 1
    level_rows = []
    for level, entry in sorted(grouped.items()):
        type_names = sorted(entry["types"])
        type_text = ", ".join(type_names[:3])
        if len(type_names) > 3:
            type_text = f"{type_text}, +{len(type_names) - 3}"
        level_rows.append(
            {
                "level": level,
                "label": f"L{level}",
                "type_text": type_text or "-",
                "count": int(entry["count"] or 0),
                "count_text": _group_metric_text(entry["count"]),
            }
        )
    return level_rows


def _group_division_rows_payload(rows: list[dict]) -> list[dict]:
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "level": int(row.get("level") or 0),
            "type_text": str(row.get("type_text") or "-"),
            "type_level_text": str(row.get("type_level_text") or row.get("type_text") or "-"),
            "area_text": str(row.get("area_text") or "-"),
            "population_text": str(row.get("population_text") or "-"),
            "search_text": str(row.get("search_text") or ""),
            "href": str(row.get("href") or ""),
            "clone_url": str(row.get("clone_url") or ""),
            "delete_url": str(row.get("delete_url") or ""),
        }
        for row in rows
    ]


def _group_level_rows_by_country(country_codes: set[str] | None = None) -> dict[str, list[dict]]:
    new_area_codes_by_country = _group_new_area_country_codes_by_source(country_codes)
    new_area_country_codes = {
        derived_code
        for derived_codes in new_area_codes_by_country.values()
        for derived_code in derived_codes
        if derived_code
    }
    if not new_area_country_codes:
        return {}
    countries_by_new_area_code: dict[str, set[str]] = {}
    for source_code, derived_codes in new_area_codes_by_country.items():
        for derived_code in derived_codes:
            countries_by_new_area_code.setdefault(derived_code, set()).add(source_code)
    try:
        rows = list(
            NuevoAdminArea.objects.filter(country_code__in=new_area_country_codes, level__gt=0)
            .values("country_code", "level", "entity_type")
            .annotate(total=Count("id"), population=Sum("pop_latest"))
            .order_by("country_code", "level", "entity_type")
        )
    except (OperationalError, ProgrammingError):
        return {}

    grouped: dict[tuple[str, int], dict] = {}
    for row in rows:
        new_area_code = query_parameters.normalize_country_key(row.get("country_code"))
        source_codes = countries_by_new_area_code.get(new_area_code, set())
        if not source_codes:
            continue
        try:
            level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            continue
        for code in source_codes:
            key = (code, level)
            entry = grouped.setdefault(
                key,
                {
                    "country_code": code,
                    "level": level,
                    "types": set(),
                    "count": 0,
                    "population": 0,
                },
            )
            entity_type = str(row.get("entity_type") or "").strip()
            if entity_type:
                entry["types"].add(entity_type)
            entry["count"] += int(row.get("total") or 0)
            entry["population"] += int(row.get("population") or 0)

    by_country: dict[str, list[dict]] = {}
    for (code, level), row in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        type_names = sorted(row["types"])
        type_text = ", ".join(type_names[:3])
        if len(type_names) > 3:
            type_text = f"{type_text}, +{len(type_names) - 3}"
        by_country.setdefault(code, []).append(
            {
                "country_code": code,
                "level": level,
                "label": f"L{level}",
                "type_text": type_text or "-",
                "count": row["count"],
                "count_text": _group_metric_text(row["count"]),
                "population_text": _group_metric_text(row["population"]),
                "new_group_url": (
                    reverse("ciudades_del_mundo:group_entry_new", kwargs={"country_code": code})
                    + f"?include_level={level}"
                ),
            }
        )
    return by_country


def _group_division_rows_by_country(country_codes: set[str] | None = None) -> dict[str, list[dict]]:
    new_area_codes_by_country = _group_new_area_country_codes_by_source(country_codes)
    new_area_country_codes = {
        derived_code
        for derived_codes in new_area_codes_by_country.values()
        for derived_code in derived_codes
        if derived_code
    }
    if not new_area_country_codes:
        return {}
    edit_link_index, edit_name_index, edit_metadata = _queries_subdivisions._derived_subdivision_edit_link_indexes(country_codes)
    countries_by_new_area_code: dict[str, set[str]] = {}
    for source_code, derived_codes in new_area_codes_by_country.items():
        for derived_code in derived_codes:
            countries_by_new_area_code.setdefault(derived_code, set()).add(source_code)
    try:
        areas = list(
            NuevoAdminArea.objects.filter(country_code__in=new_area_country_codes, level__gt=0)
            .only("id", "country_code", "code", "name", "level", "entity_type", "area_km2", "pop_latest", "updated_at")
            .order_by("country_code", "level", "name", "id")
        )
    except (OperationalError, ProgrammingError):
        return {}

    by_country: dict[str, list[dict]] = {}
    for area in areas:
        new_area_code = query_parameters.normalize_country_key(area.country_code)
        source_codes = countries_by_new_area_code.get(new_area_code, set())
        if not source_codes:
            continue
        edit_match = _queries_subdivisions._derived_subdivision_edit_match_for_area(
            edit_link_index,
            edit_name_index,
            source_codes,
            new_area_code=new_area_code,
            area_code=area.code,
            area_name=area.name,
        )
        href = edit_match.get("href", "")
        sql_metadata = edit_metadata.get(href, {}) if edit_match.get("use_definition_metadata") and href else {}
        delete_metadata = edit_metadata.get(href, {}) if href else {}
        try:
            level = int(sql_metadata.get("level") if sql_metadata else area.level or 0)
        except (TypeError, ValueError):
            level = 0
        raw_type = str(sql_metadata.get("type_text") if sql_metadata else area.entity_type or "").strip()
        display_name = str(sql_metadata.get("name") if sql_metadata else area.name or "").strip() or "-"
        type_text = raw_type or "-"
        type_level_text = type_text
        area_value = area.area_km2
        population_value = area.pop_latest
        if (
            sql_metadata
            and sql_metadata.get("updated_at")
            and area.updated_at
            and sql_metadata["updated_at"] > area.updated_at
        ):
            preview_area, preview_population = _queries_subdivisions._derived_subdivision_preview_metric_values(
                str(sql_metadata.get("source_country_code") or ""),
                str(sql_metadata.get("content") or ""),
            )
            area_value = preview_area if preview_area is not None else area_value
            population_value = preview_population if preview_population is not None else population_value
        area_text = _group_metric_text(area_value)
        population_text = _group_metric_text(population_value)
        search_text = " ".join(
            str(value or "")
            for value in (
                area.name,
                display_name,
                raw_type,
                type_text,
                level,
                area_text,
                population_text,
            )
        )
        row = {
            "id": area.id,
            "name": display_name,
            "level": level,
            "type_text": type_text,
            "type_level_text": type_level_text,
            "area_text": area_text,
            "population_text": population_text,
            "search_text": search_text,
            "href": href,
            "delete_url": str(delete_metadata.get("delete_url") or ""),
            "clone_url": str(delete_metadata.get("clone_url") or ""),
        }
        for code in sorted(source_codes):
            by_country.setdefault(code, []).append(row)
    materialized_hrefs = {
        str(row.get("href") or "")
        for rows in by_country.values()
        for row in rows
        if str(row.get("href") or "")
    }
    for code, rows in _queries_browser._pending_derived_subdivision_rows_by_country(
        country_codes,
        exclude_hrefs=materialized_hrefs,
    ).items():
        by_country.setdefault(code, []).extend(rows)
    for rows in by_country.values():
        rows.sort(key=lambda row: (int(row.get("level") or 0), str(row.get("name") or ""), str(row.get("id") or "")))
    return by_country


def _subdivision_group_seed_records(paths: list[Path] | None = None) -> list[dict]:
    return [
        {
            "path": path,
            "slug": path.stem,
            "country_codes": _subdivision_group_seed_country_codes(path),
        }
        for path in (paths if paths is not None else bundled_subdivision_group_paths())
    ]


def _subdivision_group_seed_paths_for_country(paths: list[Path], country_code: str) -> list[Path]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return paths
    return [
        seed["path"]
        for seed in _subdivision_group_seed_records(paths)
        if country_code in seed.get("country_codes", set())
    ]


def _subdivision_group_seed_country_codes(path: Path) -> set[str]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return set()
    return _subdivision_group_content_country_codes(content)


def _subdivision_group_record_country_codes(group: SubdivisionGroup) -> set[str]:
    code = query_parameters.normalize_country_key(group.source_country_code)
    if code:
        return {code}
    return _subdivision_group_content_country_codes(group.content)


def _subdivision_groups_for_country(country_code: str) -> list[SubdivisionGroup]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    groups = []
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if country_code in _subdivision_group_record_country_codes(group):
            groups.append(group)
    return groups


def _subdivision_group_content_country_codes(content: str) -> set[str]:
    try:
        data = tomllib.loads(content or "")
    except tomllib.TOMLDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()

    explicit = query_parameters.normalize_country_key(data.get("source_country_code"))
    selection = data.get("selection")
    if not explicit and isinstance(selection, dict):
        explicit = query_parameters.normalize_country_key(selection.get("source_country_code"))
    if explicit:
        return {explicit}

    legacy = data.get("legacy")
    legacy_source_ref = _subdivision_group_legacy_source_reference_country_code(data.get("source_python"))
    if legacy_source_ref:
        return {legacy_source_ref}

    legacy_source = str(legacy.get("python_source") or "") if isinstance(legacy, dict) else ""
    return _subdivision_group_legacy_spec_country_codes(legacy_source)


def _subdivision_group_legacy_source_reference_country_code(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidates = []
    for pattern in (
        r"historical_divisions[\\/\.]([A-Za-z][A-Za-z0-9_]*)",
        r"new_subdivisions[\\/\.]([A-Za-z][A-Za-z0-9_]*)",
    ):
        candidates.extend(match.group(1) for match in re.finditer(pattern, text))
    if not candidates:
        candidates.append(Path(text).stem or text.rsplit(".", 1)[-1])
    aliases = {
        "spain": "spain",
        "spanish": "spain",
        "france": "france",
        "french": "france",
        "italy": "italy",
        "italian": "italy",
        "portugal": "portugal",
        "morocco": "morocco",
        "colombia": "colombia",
        "cuba": "cuba",
        "guatemala": "guatemala",
        "mexico": "mexico",
        "equatorialguinea": "equatorialguinea",
        "western_sahara": "westernsahara",
        "westernsahara": "westernsahara",
    }
    for candidate in candidates:
        slug = re.sub(r"[^a-z0-9_]+", "_", str(candidate or "").casefold()).strip("_")
        for prefix, country_code in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if slug == prefix or slug.startswith(f"{prefix}_"):
                return country_code
    return ""


def _subdivision_group_legacy_spec_country_codes(source: str) -> set[str]:
    if not str(source or "").strip():
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    codes: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DIVISIONS" for target in node.targets):
            continue
        for country_code in _queries_browser._legacy_division_spec_country_codes_from_node(node.value):
            normalized = query_parameters.normalize_country_key(country_code)
            if normalized:
                codes.add(normalized)
    return codes


def _group_metric_text(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _group_source_level_options(country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    if not country_code:
        return []
    try:
        level_rows = list(
                area_selectors.group_source_admin_areas()
                .filter(country_code__iexact=country_code)
                .exclude(level=0)
                .values("level", "entity_type")
                .annotate(count=Count("id"))
                .order_by("level")
            )
        level_counts: dict[int, int] = {}
        level_types: dict[int, dict[str, int]] = {}
        for row in level_rows:
            if row["level"] is None:
                continue
            level = int(row["level"])
            level_counts[level] = level_counts.get(level, 0) + int(row["count"] or 0)
            entity_type = str(row.get("entity_type") or "").strip()
            if entity_type:
                type_counts = level_types.setdefault(level, {})
                type_counts[entity_type] = type_counts.get(entity_type, 0) + int(row["count"] or 0)
    except (OperationalError, ProgrammingError, ValueError):
        return []
    if not level_counts:
        return []
    max_level = _group_effective_max_source_level(level_counts, level_types=level_types)
    options = []
    for row in _forms_scraping._source_level_filter_options_for_country(country_code):
        try:
            row_level = int(row.get("value"))
        except (TypeError, ValueError):
            continue
        if row_level < max_level:
            options.append(row)
    return options


def _group_effective_max_source_level(level_counts: dict[int, int], *, level_types: dict[int, dict[str, int]] | None = None) -> int:
    levels = sorted(level for level, count in level_counts.items() if count > 0)
    if not levels:
        return 0
    level_types = level_types or {}
    while len(levels) >= 2 and _group_level_is_lowest_place_only(level_types.get(levels[-1], set())):
        levels.pop()
    effective_max = levels[-1]
    while len(levels) >= 2:
        deepest = levels[-1]
        previous = levels[-2]
        deepest_count = int(level_counts.get(deepest) or 0)
        previous_count = int(level_counts.get(previous) or 0)
        if previous_count < 100 or deepest_count / previous_count >= 0.01:
            break
        effective_max = previous
        levels.pop()
    return effective_max


def _group_level_is_lowest_place_only(entity_types) -> bool:
    if not entity_types:
        return False
    lowest_tokens = ("locality", "municipality seat", "seat", "place", "settlement")
    if isinstance(entity_types, dict):
        total = sum(int(count or 0) for count in entity_types.values())
        if total <= 0:
            return False
        lowest_total = 0
        for entity_type, count in entity_types.items():
            normalized = re.sub(r"[^a-z0-9 ]+", " ", str(entity_type or "").casefold()).strip()
            if any(token in normalized for token in lowest_tokens):
                lowest_total += int(count or 0)
        return lowest_total / total >= 0.95
    normalized_types = [
        re.sub(r"[^a-z0-9 ]+", " ", str(entity_type or "").casefold()).strip()
        for entity_type in entity_types
    ]
    return all(any(token in entity_type for token in lowest_tokens) for entity_type in normalized_types)


def _group_source_section_options(country_code: str, level: str | int, *, parent_id: str = "") -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return []
    try:
        queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code, level=level_int)
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        rows = list(
            queryset
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    child_counts = _queries_browser._admin_area_child_counts([str(row["id"]) for row in rows], group_source_only=True)
    sections = []
    for row in rows:
        row_id = str(row["id"])
        children_count = child_counts.get(row_id, 0)
        if children_count <= 0:
            continue
        name = _queries_browser._display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
        entity_type = _queries_browser._entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        sections.append(
            {
                "id": row_id,
                "code": str(row.get("code") or ""),
                "name": name,
                "label": label,
                "level": int(row.get("level") or 0),
                "entity_type": entity_type,
                "parent_id": str(row.get("parent_id") or ""),
                "children_count": children_count,
            }
        )
    return sections


def _group_source_root_payload(country_code: str) -> dict | None:
    root = _queries_browser._source_country_root_for_code(country_code)
    if root is None:
        return None
    children_count = _queries_browser._admin_area_child_counts([str(root.id)], group_source_only=True).get(str(root.id), 0)
    if children_count <= 0:
        return None
    name = _queries_browser._area_display_name(root)
    entity_type = _queries_browser._entity_type_label(root.entity_type, country_code=root.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(root.id),
        "value": str(root.id),
        "code": str(root.code or ""),
        "name": name,
        "label": label,
        "level": 0,
        "entity_type": entity_type,
        "parent_id": "",
        "ancestor_ids": [],
        "children_count": children_count,
    }


def _group_source_child_options(country_code: str, parent_id: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    if not country_code or not parent_id:
        return []
    try:
        rows = list(
            area_selectors.group_source_admin_areas()
            .filter(parent_id=parent_id, country_code__iexact=country_code)
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id", "city_merge_status")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    direct_child_levels = sorted({int(row.get("level") or 0) for row in rows if int(row.get("level") or 0) > 0})
    if len(direct_child_levels) > 1:
        direct_child_level = direct_child_levels[0]
        rows = [
            row
            for row in rows
            if int(row.get("level") or 0) == direct_child_level
            or int(row.get("city_merge_status") or 0) == int(AdminArea.CityMergeStatus.UNIFIED)
        ]
    children = []
    for row in rows:
        name = _queries_browser._display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
        entity_type = _queries_browser._entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
        label = name
        if entity_type:
            label = f"{label} ({entity_type})"
        children.append(
            {
                "id": str(row["id"]),
                "code": str(row.get("code") or ""),
                "name": name,
                "raw_name": str(row.get("name") or ""),
                "label": label,
                "level": int(row.get("level") or 0),
                "entity_type": entity_type,
                "parent_id": str(row.get("parent_id") or ""),
                "city_merge_status": int(row.get("city_merge_status") or 0),
            }
        )
    return children


def _group_source_descendant_options(country_code: str, parent_ids: list[str]) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    current_parent_ids = [str(parent_id).strip() for parent_id in parent_ids if str(parent_id or "").strip()]
    if not country_code or not current_parent_ids:
        return []
    seen_ids = set(current_parent_ids)
    ancestor_paths = {parent_id: [] for parent_id in current_parent_ids}
    descendants = []
    depth_guard = 0
    while current_parent_ids and depth_guard < 20:
        depth_guard += 1
        try:
            rows = list(
                area_selectors.group_source_admin_areas()
                .filter(parent_id__in=current_parent_ids, country_code__iexact=country_code)
                .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
                .order_by("level", "name", "code", "id")
            )
        except (OperationalError, ProgrammingError, ValueError):
            return []
        next_parent_ids = []
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids:
                continue
            parent_id = str(row.get("parent_id") or "")
            ancestor_ids = [parent_id] + ancestor_paths.get(parent_id, [])
            row["ancestor_ids"] = ancestor_ids
            descendants.append(row)
            seen_ids.add(row_id)
            next_parent_ids.append(row_id)
            ancestor_paths[row_id] = ancestor_ids
        current_parent_ids = next_parent_ids
    return [_group_source_item_payload_from_row(row) for row in descendants]


def _group_source_item_level_options(country_code: str) -> list[dict]:
    return _group_source_level_options(country_code)


def _group_source_item_options(country_code: str, level: str | int, *, parent_id: str = "") -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    parent_id = str(parent_id or "").strip()
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return []
    try:
        queryset = area_selectors.group_source_admin_areas().filter(country_code__iexact=country_code, level=level_int)
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        rows = list(
            queryset
            .values("id", "country_code", "code", "name", "level", "entity_type", "parent_id")
            .order_by("name", "code", "id")
        )
    except (OperationalError, ProgrammingError, ValueError):
        return []
    return [_group_source_item_payload_from_row(row) for row in rows]


def _group_source_item_payload_from_row(row: dict) -> dict:
    name = _queries_browser._display_name(row.get("name", ""), row.get("name", ""), country_code=row.get("country_code", ""))
    entity_type = _queries_browser._entity_type_label(row.get("entity_type"), country_code=row.get("country_code"))
    label = f"{name} ({entity_type})" if entity_type else name
    return {
        "id": str(row["id"]),
        "value": str(row["id"]),
        "country_code": query_parameters.normalize_country_key(row.get("country_code")),
        "code": str(row.get("code") or ""),
        "name": name,
        "raw_name": str(row.get("name") or ""),
        "label": label,
        "level": int(row.get("level") or 0),
        "entity_type": entity_type,
        "parent_id": str(row.get("parent_id") or ""),
        "ancestor_ids": [str(value) for value in row.get("ancestor_ids") or [] if str(value or "").strip()],
    }


def _group_entry_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError(_("El nombre interno es obligatorio."))
    if not _view_support.RECIPE_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El nombre interno debe empezar por una letra y usar letras, numeros o guion bajo."))
    return slug


def _group_internal_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
    if not text:
        raise ValueError(_("El nombre interno es obligatorio."))
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", text):
        raise ValueError(_("El nombre interno debe empezar por una letra y usar letras, numeros o guion bajo."))
    return text.upper()


def _group_entry_display_name(internal_name: str) -> str:
    return str(internal_name or "").replace("_", " ").title()


def _subdivision_group_record_entries(group: SubdivisionGroup, country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    legacy_entries = _queries_browser._legacy_group_assignments(group.content)
    if legacy_entries:
        for entry in legacy_entries:
            for block in entry["blocks"]:
                if not block.get("country_code"):
                    block["country_code"] = country_code
        return legacy_entries
    entry = _queries_browser._stored_group_entry_from_record(group, country_code)
    return [entry] if entry else []


def _subdivision_group_entries_for_country(groups: list[SubdivisionGroup], country_code: str) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    entries_by_slug: dict[str, dict] = {}
    for group in groups:
        for entry in _subdivision_group_record_entries(group, country_code):
            municipality_count = sum(len(_group_block_selected_names(block)) for block in (entry.get("blocks") or []))
            entry = {**entry, "href": reverse(
                "ciudades_del_mundo:group_entry_edit",
                kwargs={"country_code": country_code, "group_slug": entry["slug"]},
            )}
            entry["municipality_count"] = municipality_count
            entry["municipality_count_text"] = _group_metric_text(municipality_count)
            # Standalone SQL rows win over virtual legacy rows with the same route id.
            if entry["slug"] not in entries_by_slug or not entry.get("is_legacy"):
                entries_by_slug[entry["slug"]] = entry
    return sorted(entries_by_slug.values(), key=lambda item: (item["internal_name"], item["name"]))


def _group_entry_for_country(country_code: str, group_slug: str, *, hydrate: bool = True) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    group_slug = _group_entry_slug(group_slug)
    record_slug = _queries_browser._country_group_record_slug(country_code, group_slug)
    record = SubdivisionGroup.objects.filter(slug=record_slug).first()
    if record:
        entry = _queries_browser._stored_group_entry_from_record(record, country_code)
        return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    direct_record = SubdivisionGroup.objects.filter(slug=group_slug).first()
    if direct_record and country_code in _subdivision_group_record_country_codes(direct_record):
        entry = _queries_browser._stored_group_entry_from_record(direct_record, country_code)
        return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    candidates = list(SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"))
    for group in candidates:
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            if entry["slug"] == group_slug or entry.get("record_slug") == group_slug:
                return _group_entry_hydrated_from_sql(entry, country_code) if hydrate else entry
    return None


def _group_duplicate_entry(
    *,
    country_code: str,
    group_slug: str,
    exclude_record_slug: str = "",
    exclude_entry_slug: str = "",
) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    group_slug = _group_entry_slug(group_slug)
    exclude_record_slug = str(exclude_record_slug or "").strip()
    exclude_entry_slug = _group_entry_slug(exclude_entry_slug) if str(exclude_entry_slug or "").strip() else ""
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if exclude_record_slug and group.slug == exclude_record_slug:
            continue
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            entry_slug = _group_entry_slug(entry.get("slug") or "")
            if exclude_entry_slug and entry_slug == exclude_entry_slug:
                continue
            if entry_slug == group_slug:
                return entry
    return None


def _group_existing_key_options(
    *,
    country_code: str,
    exclude_record_slug: str = "",
    exclude_entry_slug: str = "",
) -> list[dict]:
    country_code = query_parameters.normalize_country_key(country_code)
    rows = []
    seen: set[str] = set()
    for group in SubdivisionGroup.objects.order_by("source_country_code", "name", "slug"):
        if exclude_record_slug and group.slug == exclude_record_slug:
            continue
        if country_code not in _subdivision_group_record_country_codes(group):
            continue
        for entry in _subdivision_group_record_entries(group, country_code):
            try:
                slug = _group_entry_slug(entry.get("slug") or "")
            except ValueError:
                continue
            if exclude_entry_slug and slug == exclude_entry_slug:
                continue
            if slug in seen:
                continue
            seen.add(slug)
            rows.append({"slug": slug, "internal_name": str(entry.get("internal_name") or "").strip()})
    return rows


def _group_entry_hydrated_from_sql(entry: dict | None, country_code: str) -> dict | None:
    if not entry:
        return None
    blocks = []
    for block in entry.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_country = query_parameters.normalize_country_key(block.get("country_code") or country_code)
        raw_names = [str(name).strip() for name in (block.get("names") or []) if str(name or "").strip()]
        sections = _group_sections_from_payload(block.get("sections") or [])
        if raw_names:
            resolved = _group_sections_for_flat_names(block_country, raw_names)
            existing_section_ids = {section.get("area_id") for section in sections}
            sections.extend(
                section
                for section in resolved["sections"]
                if section.get("area_id") and section.get("area_id") not in existing_section_ids
            )
            raw_names = resolved["unresolved_names"]
        blocks.append({**block, "country_code": block_country, "names": raw_names, "sections": sections})
    return {**entry, "blocks": blocks}


def _group_sections_for_flat_names(country_code: str, names: list[str]) -> dict:
    country_code = query_parameters.normalize_country_key(country_code)
    cleaned_names = [str(name).strip() for name in names if str(name or "").strip()]
    wanted_keys = [_group_name_match_key(name) for name in cleaned_names]
    wanted_set = {key for key in wanted_keys if key}
    if not country_code or not wanted_set:
        return {"sections": [], "unresolved_names": names}

    try:
        condition = Q()
        unique_names = sorted(set(cleaned_names))
        if len(unique_names) <= 250:
            for name in unique_names:
                condition |= Q(name__iexact=name) | Q(code__iexact=name) | Q(id=name)
        else:
            condition = Q(name__in=unique_names) | Q(code__in=unique_names) | Q(id__in=unique_names)
        rows = list(
            area_selectors.group_source_admin_areas()
            .filter(country_code__iexact=country_code)
            .exclude(level=0)
            .filter(condition)
            .select_related("parent")
            .only(
                "id",
                "country_code",
                "code",
                "name",
                "level",
                "entity_type",
                "parent_id",
                "parent__id",
                "parent__country_code",
                "parent__code",
                "parent__name",
                "parent__level",
                "parent__entity_type",
                "parent__parent_id",
            )
        )
    except (OperationalError, ProgrammingError, ValueError):
        return {"sections": [], "unresolved_names": names}

    by_name: dict[str, list[AdminArea]] = {}
    for area in rows:
        key = _group_name_match_key(area.name)
        if key in wanted_set:
            by_name.setdefault(key, []).append(area)

    candidate_ids = [str(area.id) for candidates in by_name.values() for area in candidates]
    child_counts = _queries_browser._admin_area_child_counts(candidate_ids)
    selected_by_parent: dict[str, dict] = {}
    unresolved = []
    for original_name, name_key in zip(cleaned_names, wanted_keys):
        candidates = by_name.get(name_key) or []
        if not candidates:
            unresolved.append(original_name)
            continue
        area = sorted(candidates, key=lambda item: _group_area_name_candidate_rank(item, child_counts))[0]
        parent = area.parent
        if parent is None:
            unresolved.append(original_name)
            continue
        section = selected_by_parent.setdefault(parent.id, _group_section_payload_from_area(parent))
        section["selected"].append({"id": str(area.id), "name": _queries_browser._area_display_name(area)})

    return {
        "sections": list(selected_by_parent.values()),
        "unresolved_names": unresolved,
    }


def _group_area_name_candidate_rank(area: AdminArea, child_counts: dict[str, int]) -> tuple[int, int, int, str, str]:
    entity_type = str(area.entity_type or "").casefold()
    is_lowest_place = int(_group_level_is_lowest_place_only({entity_type}))
    has_children = int(child_counts.get(str(area.id), 0) > 0)
    return (
        -has_children,
        is_lowest_place,
        -(int(area.level or 0)),
        str(area.name or ""),
        str(area.id),
    )


def _group_section_payload_from_area(area: AdminArea) -> dict:
    label = _queries_browser._area_display_name(area)
    entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
    if entity_type:
        label = f"{label} ({entity_type})"
    metadata = _group_admin_area_metadata(str(area.id))
    return {
        "area_id": str(area.id),
        "area_name": _queries_browser._area_display_name(area),
        "area_label": label,
        "level": int(area.level or 0),
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": metadata.get("ancestor_ids") or [],
        "selected": [],
    }


def _group_name_match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


def _stored_visual_asset_image_url(asset: dict | None) -> str:
    if not isinstance(asset, dict):
        return ""
    for key in ("image_url", "remote_url", "local_url"):
        value = str(asset.get(key) or "").strip()
        if value:
            return value
    commons_filename = str(asset.get("commons_filename") or "").strip()
    if commons_filename:
        return commons_file_url(commons_filename, width=900)
    return ""


def _group_seed_file_slug_for_record(group: SubdivisionGroup | None) -> str:
    if not group:
        return ""
    entry = _queries_browser._stored_group_entry_from_record(group, query_parameters.normalize_country_key(group.source_country_code))
    if entry:
        return _group_entry_slug(entry.get("slug") or entry.get("internal_name") or group.slug)
    country_code = query_parameters.normalize_country_key(group.source_country_code)
    if country_code and group.slug.startswith(f"{country_code}_"):
        return _group_entry_slug(group.slug[len(country_code) + 1 :])
    return _group_entry_slug(group.slug)


def _group_record_edit_url(group: SubdivisionGroup) -> str:
    country_codes = sorted(_subdivision_group_record_country_codes(group))
    country_code = country_codes[0] if country_codes else query_parameters.normalize_country_key(group.source_country_code)
    if country_code:
        entries = _subdivision_group_record_entries(group, country_code)
        if entries:
            return reverse(
                "ciudades_del_mundo:group_entry_edit",
                kwargs={"country_code": country_code, "group_slug": entries[0]["slug"]},
            )
    return reverse("ciudades_del_mundo:group_edit", kwargs={"slug": group.slug})


def _source_country_options_with_current(current_code: str) -> list[dict[str, str]]:
    current_code = str(current_code or "").strip().lower()
    options = _queries_browser._derived_source_country_options()
    if current_code and current_code not in {option["value"] for option in options}:
        options.append(
            {
                "value": current_code,
                "label": _queries_browser._display_name("", current_code, country_code=current_code),
                "root_id": "",
            }
        )
    return options


def _group_city_entry_for_country(country_code: str, city_slug: str) -> dict | None:
    country_code = query_parameters.normalize_country_key(country_code)
    city_slug = str(city_slug or "").strip()
    if not country_code or not city_slug:
        return None
    lookup_slug = _group_city_lookup_slug(city_slug)
    if not lookup_slug:
        return None
    code_piece = _group_internal_name(f"CITY_{lookup_slug}")
    return {
        "slug": _group_entry_slug(code_piece),
        "internal_name": code_piece,
        "name": code_piece,
        "blocks": [{"country_code": country_code, "names": []}],
        "record": None,
        "record_slug": "",
        "is_legacy": True,
    }


def _group_city_lookup_slug(value: object) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().casefold()).strip("_-")


def _group_blocks_for_context(value: str, *, fallback_country_code: str) -> list[dict]:
    try:
        return _group_blocks_from_json(value, fallback_country_code=fallback_country_code)
    except ValueError:
        return [{"country_code": fallback_country_code, "names": []}]


def _group_sections_from_payload(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    sections = []
    seen_area_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        area_id = str(item.get("area_id") or item.get("id") or "").strip()
        if not area_id or area_id in seen_area_ids:
            continue
        seen_area_ids.add(area_id)
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        metadata = _group_admin_area_metadata(area_id)
        raw_selected = item.get("selected")
        selected = []
        if isinstance(raw_selected, list):
            for selected_item in raw_selected:
                if isinstance(selected_item, dict):
                    selected_id = str(selected_item.get("id") or "").strip()
                    selected_name = str(selected_item.get("name") or selected_item.get("label") or "").strip()
                else:
                    selected_id = ""
                    selected_name = str(selected_item or "").strip()
                if selected_name:
                    selected.append({"id": selected_id, "name": selected_name,
                                     "operation": "subtract" if isinstance(selected_item, dict) and selected_item.get("operation") == "subtract" else "add"})
        else:
            selected_names = item.get("selected_names") or []
            selected_ids = item.get("selected_ids") or []
            if not isinstance(selected_names, list):
                selected_names = []
            if not isinstance(selected_ids, list):
                selected_ids = []
            for index, selected_name in enumerate(selected_names):
                selected_name = str(selected_name or "").strip()
                if selected_name:
                    selected.append(
                        {
                            "id": str(selected_ids[index] or "").strip() if index < len(selected_ids) else "",
                            "name": selected_name,
                            "operation": "subtract" if index < len(item.get("selected_operations") or []) and item["selected_operations"][index] == "subtract" else "add",
                        }
                    )
        ancestor_ids = item.get("ancestor_ids") or metadata.get("ancestor_ids") or []
        if not isinstance(ancestor_ids, list):
            ancestor_ids = []
        sections.append(
            {
                "area_id": area_id,
                "area_name": str(item.get("area_name") or item.get("name") or metadata.get("name") or "").strip(),
                "area_label": str(item.get("area_label") or metadata.get("label") or item.get("area_name") or item.get("name") or "").strip(),
                "level": level,
                "parent_id": str(item.get("parent_id") or metadata.get("parent_id") or "").strip(),
                "ancestor_ids": [str(ancestor_id) for ancestor_id in ancestor_ids if str(ancestor_id or "").strip()],
                "selected": selected,
            }
        )
    return sections


def _group_admin_area_metadata(area_id: str) -> dict:
    area_id = str(area_id or "").strip()
    if not area_id:
        return {}
    try:
        area = area_selectors.visible_admin_areas().select_related("parent").filter(id=area_id).first()
    except (OperationalError, ProgrammingError, ValueError):
        return {}
    if area is None:
        return {}
    name = _queries_browser._area_display_name(area)
    entity_type = _queries_browser._entity_type_label(area.entity_type, country_code=area.country_code)
    label = f"{name} ({entity_type})" if entity_type else name
    ancestor_ids = []
    parent = area.parent
    guard = 0
    while parent is not None and guard < 20:
        ancestor_ids.append(str(parent.id))
        parent = parent.parent
        guard += 1
    return {
        "name": name,
        "label": label,
        "parent_id": str(area.parent_id or ""),
        "ancestor_ids": ancestor_ids,
    }


def _group_block_selected_names(block: dict) -> list[str]:
    names = []
    seen: set[str] = set()
    for name in block.get("names") or []:
        clean_name = str(name or "").strip()
        if clean_name and clean_name not in seen:
            seen.add(clean_name)
            names.append(clean_name)
    for section in block.get("sections") or []:
        for selected in section.get("selected") or []:
            clean_name = str(selected.get("name") or "").strip() if isinstance(selected, dict) else str(selected or "").strip()
            if clean_name and clean_name not in seen:
                seen.add(clean_name)
                names.append(clean_name)
    return names


def _group_selected_names_from_blocks(blocks: list[dict]) -> list[str]:
    names = []
    seen: set[str] = set()
    for block in blocks:
        for name in _group_block_selected_names(block):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _group_blocks_with_required_country(blocks: list[dict], *, fallback_country_code: str) -> list[dict]:
    fallback_country_code = query_parameters.normalize_country_key(fallback_country_code)
    grouped: dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        country_code = query_parameters.normalize_country_key(block.get("country_code") or fallback_country_code)
        if not country_code:
            continue
        grouped_block = grouped.setdefault(country_code, {"country_code": country_code, "names": [], "sections": []})
        grouped_block["names"].extend(str(name).strip() for name in (block.get("names") or []) if str(name or "").strip())
        grouped_block["sections"].extend(_group_sections_from_payload(block.get("sections") or []))
    if fallback_country_code not in grouped:
        grouped[fallback_country_code] = {"country_code": fallback_country_code, "names": [], "sections": []}
    ordered_codes = [fallback_country_code] + sorted(code for code in grouped if code != fallback_country_code)
    return [grouped[code] for code in ordered_codes]


def _group_blocks_from_json(value: str, *, fallback_country_code: str) -> list[dict]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido: %(error)s") % {"error": exc}) from exc
    if not isinstance(payload, list):
        raise ValueError(_("Los grupos de la agrupacion no son JSON valido."))
    blocks = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        country_code = query_parameters.normalize_country_key(item.get("country_code") or fallback_country_code)
        raw_names = item.get("names") or []
        if isinstance(raw_names, str):
            names = raw_names.splitlines()
        elif isinstance(raw_names, list):
            names = raw_names
        else:
            names = []
        blocks.append(
            {
                "country_code": country_code,
                "names": [str(name).strip() for name in names if str(name or "").strip()],
                "sections": _group_sections_from_payload(item.get("sections") or []),
            }
        )
    return _group_blocks_with_required_country(blocks, fallback_country_code=fallback_country_code)


def get_subdivision_group(slug: str) -> SubdivisionGroup:
    slug = _forms_recipes._normalize_recipe_slug(slug)
    try:
        return SubdivisionGroup.objects.get(slug=slug)
    except SubdivisionGroup.DoesNotExist as exc:
        raise DataNotFoundError(_("No existe el grupo '%(slug)s'.") % {"slug": slug}) from exc
