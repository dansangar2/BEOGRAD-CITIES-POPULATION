from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import tomllib
import unicodedata
from typing import Iterable
import unittest


DEFAULT_LOCALITY_ENTITY_TYPES = ("Locality", "Municipality seat")


@dataclass(frozen=True)
class CountryDataAreaRecord:
    id: str
    country_code: str
    name: str
    level: int
    parent_id: str | None = None
    entity_type: str | None = None
    pop_latest: int | None = None


@dataclass(frozen=True)
class CountryDataTotals:
    regions: int | None = None
    provinces: int | None = None
    municipalities: int | None = None
    localities: int | None = None


@dataclass(frozen=True)
class CountryDataContract:
    schema_version: int
    country_code: str
    country_name: str
    region_level: int
    province_level: int
    municipality_level: int
    locality_levels: tuple[int, ...]
    locality_entity_types: tuple[str, ...]
    locality_min_population: int | None
    totals: CountryDataTotals
    regions: dict[str, tuple[str, ...]]
    province_municipality_counts: dict[tuple[str, str], int]
    province_municipalities: dict[tuple[str, str], tuple[str, ...]]
    municipality_localities: dict[tuple[str, str, str], tuple[str, ...]]
    routes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class CountryDataIssue:
    code: str
    message: str


def parse_country_data_contract(text: str) -> CountryDataContract:
    data = tomllib.loads(text)
    schema_version = int(data.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError(f"Unsupported country data schema_version: {schema_version}.")

    country_code = _required_str(data, "country_code")
    country_name = _required_str(data, "country_name")
    levels = data.get("levels") or {}
    totals = data.get("totals") or {}
    return CountryDataContract(
        schema_version=schema_version,
        country_code=country_code,
        country_name=country_name,
        region_level=int(levels.get("region", 1)),
        province_level=int(levels.get("province", 2)),
        municipality_level=int(levels.get("municipality", 3)),
        locality_levels=tuple(int(level) for level in levels.get("localities", (4,))),
        locality_entity_types=tuple(
            str(item) for item in data.get("locality_entity_types", DEFAULT_LOCALITY_ENTITY_TYPES)
        ),
        locality_min_population=_optional_int(data.get("locality_min_population")),
        totals=CountryDataTotals(
            regions=_optional_int(totals.get("regions")),
            provinces=_optional_int(totals.get("provinces")),
            municipalities=_optional_int(totals.get("municipalities")),
            localities=_optional_int(totals.get("localities")),
        ),
        regions=_parse_named_lists(data.get("regions") or {}, section="regions"),
        province_municipality_counts=_parse_path_counts(
            data.get("province_municipality_counts") or {},
            section="province_municipality_counts",
            parts=2,
        ),
        province_municipalities=_parse_path_lists(
            data.get("province_municipalities") or {},
            section="province_municipalities",
            parts=2,
        ),
        municipality_localities=_parse_path_lists(
            data.get("municipality_localities") or {},
            section="municipality_localities",
            parts=3,
        ),
        routes=tuple(_as_string_tuple(item.get("path"), "routes.path") for item in data.get("routes", ())),
    )


def validate_country_data_contract_consistency(contract: CountryDataContract) -> list[CountryDataIssue]:
    issues: list[CountryDataIssue] = []
    _add_duplicate_list_issues("regions", contract.regions, issues)
    _add_duplicate_list_issues("province_municipalities", contract.province_municipalities, issues)
    _add_duplicate_list_issues("municipality_localities", contract.municipality_localities, issues)
    _add_province_municipality_count_issues(contract, issues)

    _expect_total("regions", contract.totals.regions, len(contract.regions), issues)
    _expect_total("provinces", contract.totals.provinces, sum(len(items) for items in contract.regions.values()), issues)
    _expect_total(
        "municipalities",
        contract.totals.municipalities,
        sum(len(items) for items in contract.province_municipalities.values()),
        issues,
    )
    if contract.province_municipality_counts:
        _expect_total(
            "municipalities",
            contract.totals.municipalities,
            sum(contract.province_municipality_counts.values()),
            issues,
        )
    _expect_total(
        "localities",
        contract.totals.localities,
        sum(len(items) for items in contract.municipality_localities.values()),
        issues,
    )

    region_names = set(contract.regions)
    province_keys = set(contract.province_municipalities)
    municipality_keys = set(contract.municipality_localities)
    for region, province in sorted(province_keys):
        if region not in region_names:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-PROVINCE-PARENT",
                    f"{region} > {province} is declared without a matching region.",
                )
            )
        elif province not in contract.regions[region]:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-PROVINCE-LIST",
                    f"{region} > {province} is not listed under [regions].",
                )
            )
    for region, provinces in sorted(contract.regions.items()):
        for province in provinces:
            if (region, province) not in province_keys:
                issues.append(
                    CountryDataIssue(
                        "CD-CONTRACT-MISSING-PROVINCE-SECTION",
                        f"{region} > {province} is listed under [regions] but has no [province_municipalities] entry.",
                    )
                )
    for region, province, municipality in sorted(municipality_keys):
        if (region, province) not in province_keys:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-MUNICIPALITY-PARENT",
                    f"{region} > {province} > {municipality} is declared without a matching province.",
                )
            )
        elif municipality not in contract.province_municipalities[(region, province)]:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-MUNICIPALITY-LIST",
                    f"{region} > {province} > {municipality} is not listed under [province_municipalities].",
                )
            )
    for (region, province), municipalities in sorted(contract.province_municipalities.items()):
        for municipality in municipalities:
            if (region, province, municipality) not in municipality_keys:
                issues.append(
                    CountryDataIssue(
                        "CD-CONTRACT-MISSING-MUNICIPALITY-SECTION",
                        (
                            f"{region} > {province} > {municipality} is listed under "
                            "[province_municipalities] but has no [municipality_localities] entry."
                        ),
                    )
                )

    route_counts = Counter(contract.routes)
    for route, count in sorted(route_counts.items()):
        if count > 1:
            issues.append(CountryDataIssue("CD-CONTRACT-DUPLICATE-ROUTE", f"Duplicate route: {_format_path(route)}."))
        _add_route_consistency_issues(contract, route, issues)
    return issues


def build_country_data_snapshot(
    *,
    country_code: str,
    country_name: str,
    records: Iterable[CountryDataAreaRecord],
    region_level: int = 1,
    province_level: int = 2,
    municipality_level: int = 3,
    locality_levels: tuple[int, ...] = (4,),
    locality_entity_types: tuple[str, ...] = DEFAULT_LOCALITY_ENTITY_TYPES,
    locality_min_population: int | None = None,
    routes: tuple[tuple[str, ...], ...] = (),
) -> CountryDataContract:
    scoped = [record for record in records if record.country_code == country_code]
    by_id = {record.id: record for record in scoped}
    children_by_parent: dict[str, list[CountryDataAreaRecord]] = {}
    for record in scoped:
        if record.parent_id:
            children_by_parent.setdefault(record.parent_id, []).append(record)

    regions: dict[str, tuple[str, ...]] = {}
    for region in _sort_records(record for record in scoped if record.level == region_level):
        provinces = [
            province.name
            for province in _sort_records(
                record
                for record in children_by_parent.get(region.id, ())
                if record.level == province_level and record.parent_id == region.id
            )
        ]
        regions[region.name] = tuple(provinces)

    province_municipalities: dict[tuple[str, str], tuple[str, ...]] = {}
    for province in _sort_records(record for record in scoped if record.level == province_level):
        region = by_id.get(province.parent_id or "")
        if region is None or region.level != region_level:
            continue
        municipalities = [
            municipality.name
            for municipality in _sort_records(
                record
                for record in children_by_parent.get(province.id, ())
                if record.level == municipality_level and record.parent_id == province.id
            )
        ]
        province_municipalities[(region.name, province.name)] = tuple(municipalities)

    locality_type_set = set(locality_entity_types)
    municipality_localities: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for municipality in _sort_records(record for record in scoped if record.level == municipality_level):
        province = by_id.get(municipality.parent_id or "")
        region = by_id.get(province.parent_id or "") if province else None
        if province is None or region is None or province.level != province_level or region.level != region_level:
            continue
        localities = [
            locality.name
            for locality in _sort_records(
                record
                for record in children_by_parent.get(municipality.id, ())
                if record.parent_id == municipality.id
                and record.level in locality_levels
                and (not locality_type_set or str(record.entity_type or "") in locality_type_set)
                and _meets_min_population(record, locality_min_population)
            )
        ]
        municipality_localities[(region.name, province.name, municipality.name)] = tuple(localities)

    totals = CountryDataTotals(
        regions=len(regions),
        provinces=sum(len(items) for items in regions.values()),
        municipalities=sum(len(items) for items in province_municipalities.values()),
        localities=sum(len(items) for items in municipality_localities.values()),
    )
    return CountryDataContract(
        schema_version=1,
        country_code=country_code,
        country_name=country_name,
        region_level=region_level,
        province_level=province_level,
        municipality_level=municipality_level,
        locality_levels=tuple(locality_levels),
        locality_entity_types=tuple(locality_entity_types),
        locality_min_population=locality_min_population,
        totals=totals,
        regions=regions,
        province_municipality_counts={key: len(values) for key, values in province_municipalities.items()},
        province_municipalities=province_municipalities,
        municipality_localities=municipality_localities,
        routes=routes,
    )


def validate_country_data_contract(
    contract: CountryDataContract,
    records: Iterable[CountryDataAreaRecord],
) -> list[CountryDataIssue]:
    records = list(records)
    issues = validate_country_data_contract_consistency(contract)
    _add_parent_integrity_issues(contract.country_code, records, issues)
    snapshot = build_country_data_snapshot(
        country_code=contract.country_code,
        country_name=contract.country_name,
        records=records,
        region_level=contract.region_level,
        province_level=contract.province_level,
        municipality_level=contract.municipality_level,
        locality_levels=contract.locality_levels,
        locality_entity_types=contract.locality_entity_types,
        locality_min_population=contract.locality_min_population,
        routes=contract.routes,
    )

    _compare_total("regions", contract.totals.regions, snapshot.totals.regions, issues)
    _compare_total("provinces", contract.totals.provinces, snapshot.totals.provinces, issues)
    _compare_total("municipalities", contract.totals.municipalities, snapshot.totals.municipalities, issues)
    _compare_total("localities", contract.totals.localities, snapshot.totals.localities, issues)
    _compare_maps("regions", contract.regions, snapshot.regions, issues)
    _compare_counts(
        "province_municipality_counts",
        contract.province_municipality_counts,
        snapshot.province_municipality_counts,
        issues,
    )
    _compare_maps("province_municipalities", contract.province_municipalities, snapshot.province_municipalities, issues)
    _compare_maps("municipality_localities", contract.municipality_localities, snapshot.municipality_localities, issues)
    _compare_routes(contract, records, issues)
    return issues


def _required_str(data: dict, key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"country data requires {key}.")
    return value


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_named_lists(raw: dict, *, section: str) -> dict[str, tuple[str, ...]]:
    parsed: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        parsed[str(key)] = _as_string_tuple(value, f"{section}.{key}")
    return parsed


def _parse_path_lists(raw: dict, *, section: str, parts: int) -> dict[tuple[str, ...], tuple[str, ...]]:
    parsed: dict[tuple[str, ...], tuple[str, ...]] = {}
    for key, value in raw.items():
        path = tuple(part.strip() for part in str(key).split(">"))
        if len(path) != parts or any(not part for part in path):
            raise ValueError(f"{section}.{key} must have {parts} path parts separated by '>'.")
        parsed[path] = _as_string_tuple(value, f"{section}.{key}")
    return parsed


def _parse_path_counts(raw: dict, *, section: str, parts: int) -> dict[tuple[str, ...], int]:
    parsed: dict[tuple[str, ...], int] = {}
    for key, value in raw.items():
        path = tuple(part.strip() for part in str(key).split(">"))
        if len(path) != parts or any(not part for part in path):
            raise ValueError(f"{section}.{key} must have {parts} path parts separated by '>'.")
        parsed[path] = int(value)
    return parsed


def _as_string_tuple(value, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list.")
    items = tuple(str(item).strip() for item in value)
    if any(not item for item in items):
        raise ValueError(f"{context} contains an empty name.")
    return items


def _meets_min_population(record: CountryDataAreaRecord, minimum: int | None) -> bool:
    if minimum is None:
        return True
    return record.pop_latest is not None and int(record.pop_latest) >= minimum


def _sort_records(records: Iterable[CountryDataAreaRecord]) -> list[CountryDataAreaRecord]:
    return sorted(records, key=lambda item: _sort_key(item.name))


def _sort_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_marks).casefold().strip()


def _name_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _add_duplicate_list_issues(section: str, mapping: dict, issues: list[CountryDataIssue]) -> None:
    for key, values in sorted(mapping.items(), key=lambda item: str(item[0])):
        counts = Counter(_name_key(value) for value in values)
        duplicates = sorted(value for value, count in counts.items() if count > 1)
        if duplicates:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-DUPLICATE-NAME",
                    f"{section}.{_format_key(key)} has duplicate names: {', '.join(duplicates)}.",
                )
            )


def _add_province_municipality_count_issues(
    contract: CountryDataContract,
    issues: list[CountryDataIssue],
) -> None:
    for key, expected in sorted(contract.province_municipality_counts.items()):
        if key not in contract.province_municipalities:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-COUNT-PARENT",
                    f"province_municipality_counts.{_format_key(key)} has no [province_municipalities] entry.",
                )
            )
            continue
        actual = len(contract.province_municipalities[key])
        if expected != actual:
            issues.append(
                CountryDataIssue(
                    "CD-CONTRACT-COUNT",
                    f"province_municipality_counts.{_format_key(key)}={expected} but list contains {actual}.",
                )
            )
    for key in sorted(set(contract.province_municipalities) - set(contract.province_municipality_counts)):
        issues.append(
            CountryDataIssue(
                "CD-CONTRACT-MISSING-COUNT",
                f"province_municipality_counts.{_format_key(key)} is missing.",
            )
        )


def _add_route_consistency_issues(
    contract: CountryDataContract,
    route: tuple[str, ...],
    issues: list[CountryDataIssue],
) -> None:
    if len(route) != 5:
        issues.append(
            CountryDataIssue(
                "CD-CONTRACT-ROUTE-SHAPE",
                f"Route must have 5 parts: {_format_path(route)}.",
            )
        )
        return
    country, region, province, municipality, locality = route
    if country != contract.country_name:
        issues.append(
            CountryDataIssue(
                "CD-CONTRACT-ROUTE-COUNTRY",
                f"Route starts with {country}, expected {contract.country_name}: {_format_path(route)}.",
            )
        )
    if region not in contract.regions:
        issues.append(CountryDataIssue("CD-CONTRACT-ROUTE-REGION", f"Route region is missing: {_format_path(route)}."))
        return
    if province not in contract.regions[region]:
        issues.append(
            CountryDataIssue("CD-CONTRACT-ROUTE-PROVINCE", f"Route province is missing: {_format_path(route)}.")
        )
        return
    province_key = (region, province)
    if municipality not in contract.province_municipalities.get(province_key, ()):
        issues.append(
            CountryDataIssue("CD-CONTRACT-ROUTE-MUNICIPALITY", f"Route municipality is missing: {_format_path(route)}.")
        )
        return
    municipality_key = (region, province, municipality)
    if locality not in contract.municipality_localities.get(municipality_key, ()):
        issues.append(
            CountryDataIssue("CD-CONTRACT-ROUTE-LOCALITY", f"Route locality is missing: {_format_path(route)}.")
        )


def _expect_total(name: str, expected: int | None, actual: int, issues: list[CountryDataIssue]) -> None:
    if expected is not None and expected != actual:
        issues.append(
            CountryDataIssue(
                "CD-CONTRACT-TOTAL",
                f"[totals].{name}={expected} but declared lists contain {actual}.",
            )
        )


def _compare_total(name: str, expected: int | None, actual: int | None, issues: list[CountryDataIssue]) -> None:
    if expected is not None and expected != actual:
        issues.append(
            CountryDataIssue(
                "CD-DATA-TOTAL",
                f"{name}: expected {expected}, found {actual}.",
            )
        )


def _compare_counts(section: str, expected: dict, actual: dict, issues: list[CountryDataIssue]) -> None:
    if not expected:
        return
    expected_keys = set(expected)
    actual_keys = set(actual)
    for key in sorted(expected_keys - actual_keys, key=_format_key):
        issues.append(CountryDataIssue("CD-DATA-MISSING-COUNT-PARENT", f"{section}.{_format_key(key)} is missing."))
    for key in sorted(expected_keys & actual_keys, key=_format_key):
        expected_count = expected[key]
        actual_count = actual[key]
        if expected_count != actual_count:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-COUNT",
                    f"{section}.{_format_key(key)} expected {expected_count}, found {actual_count}.",
                )
            )


def _add_parent_integrity_issues(
    country_code: str,
    records: list[CountryDataAreaRecord],
    issues: list[CountryDataIssue],
) -> None:
    scoped = [record for record in records if record.country_code == country_code]
    by_id = {record.id: record for record in scoped}
    for record in sorted(scoped, key=lambda item: (int(item.level), _sort_key(item.name), item.id)):
        if int(record.level) <= 0:
            continue
        if not record.parent_id:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-MISSING-PARENT-LINK",
                    f"{record.name} ({record.id}, L{record.level}) has no parent.",
                )
            )
            continue
        if record.parent_id == record.id:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-SELF-PARENT-LINK",
                    f"{record.name} ({record.id}, L{record.level}) points to itself as parent.",
                )
            )
            continue
        if record.parent_id not in by_id:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-UNKNOWN-PARENT-LINK",
                    f"{record.name} ({record.id}, L{record.level}) points to missing parent {record.parent_id}.",
                )
            )


def _compare_maps(section: str, expected: dict, actual: dict, issues: list[CountryDataIssue]) -> None:
    expected_keys = set(expected)
    actual_keys = set(actual)
    for key in sorted(expected_keys - actual_keys, key=_format_key):
        issues.append(CountryDataIssue("CD-DATA-MISSING-PARENT", f"{section}.{_format_key(key)} is missing."))
    for key in sorted(actual_keys - expected_keys, key=_format_key):
        issues.append(CountryDataIssue("CD-DATA-EXTRA-PARENT", f"{section}.{_format_key(key)} is unexpected."))
    for key in sorted(expected_keys & actual_keys, key=_format_key):
        expected_values = set(expected[key])
        actual_values = set(actual[key])
        missing = sorted(expected_values - actual_values, key=_sort_key)
        extra = sorted(actual_values - expected_values, key=_sort_key)
        if missing:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-MISSING-NAME",
                    f"{section}.{_format_key(key)} is missing: {', '.join(missing[:20])}{_more(missing)}.",
                )
            )
        if extra:
            issues.append(
                CountryDataIssue(
                    "CD-DATA-EXTRA-NAME",
                    f"{section}.{_format_key(key)} has unexpected names: {', '.join(extra[:20])}{_more(extra)}.",
                )
            )


def _compare_routes(
    contract: CountryDataContract,
    records: Iterable[CountryDataAreaRecord],
    issues: list[CountryDataIssue],
) -> None:
    if not contract.routes:
        return
    scoped = [record for record in records if record.country_code == contract.country_code]
    by_id = {record.id: record for record in scoped}
    route_counts = Counter(_record_route(record, by_id) for record in scoped)
    for route in contract.routes:
        count = route_counts.get(route, 0)
        if count == 0:
            issues.append(CountryDataIssue("CD-DATA-MISSING-ROUTE", f"Missing route: {_format_path(route)}."))
        elif count > 1:
            issues.append(CountryDataIssue("CD-DATA-DUPLICATE-ROUTE", f"Duplicate route: {_format_path(route)}."))


def _record_route(record: CountryDataAreaRecord, by_id: dict[str, CountryDataAreaRecord]) -> tuple[str, ...]:
    route: list[str] = []
    current: CountryDataAreaRecord | None = record
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        route.append(current.name)
        current = by_id.get(current.parent_id or "")
    return tuple(reversed(route))


def _format_key(key) -> str:
    if isinstance(key, tuple):
        return _format_path(key)
    return str(key)


def _format_path(parts: Iterable[str]) -> str:
    return " > ".join(str(part) for part in parts)


def _more(items: list[str]) -> str:
    return f" and {len(items) - 20} more" if len(items) > 20 else ""


SPAIN_COUNTRY_NAME = "Espa\u00f1a"
SPAIN_TOTAL_MUNICIPALITIES = 8131
SPAIN_TOTAL_LOCALITIES_GE_20 = 29509

SPAIN_REGIONS = {
    "Andaluc\u00eda": (
        "Almer\u00eda",
        "C\u00e1diz",
        "C\u00f3rdoba",
        "Granada",
        "Huelva",
        "Ja\u00e9n",
        "M\u00e1laga",
        "Sevilla",
    ),
    "Arag\u00f3n": ("Huesca", "Teruel", "Zaragoza"),
    "Asturias": ("Asturias",),
    "Canarias": ("Las Palmas", "Santa Cruz de Tenerife"),
    "Cantabria": ("Cantabria",),
    "Castilla y Le\u00f3n": (
        "\u00c1vila",
        "Burgos",
        "Le\u00f3n",
        "Palencia",
        "Salamanca",
        "Segovia",
        "Soria",
        "Valladolid",
        "Zamora",
    ),
    "Castilla-La Mancha": ("Albacete", "Ciudad Real", "Cuenca", "Guadalajara", "Toledo"),
    "Catalu\u00f1a": ("Barcelona", "Girona", "Lleida", "Tarragona"),
    "Ceuta": ("Ceuta",),
    "Comunitat Valenciana": ("Alicante", "Castell\u00f3n", "Valencia"),
    "Extremadura": ("Badajoz", "C\u00e1ceres"),
    "Galicia": ("A Coru\u00f1a", "Lugo", "Ourense", "Pontevedra"),
    "Illes Balears": ("Illes Balears",),
    "La Rioja": ("La Rioja",),
    "Madrid": ("Madrid",),
    "Melilla": ("Melilla",),
    "Murcia": ("Murcia",),
    "Navarra": ("Navarra",),
    "Pa\u00eds Vasco": ("Araba", "Bizkaia", "Gipuzkoa"),
}

SPAIN_PROVINCE_MUNICIPALITY_COUNTS = {
    ("Andaluc\u00eda", "Almer\u00eda"): 103,
    ("Andaluc\u00eda", "C\u00e1diz"): 45,
    ("Andaluc\u00eda", "C\u00f3rdoba"): 77,
    ("Andaluc\u00eda", "Granada"): 174,
    ("Andaluc\u00eda", "Huelva"): 80,
    ("Andaluc\u00eda", "Ja\u00e9n"): 97,
    ("Andaluc\u00eda", "M\u00e1laga"): 103,
    ("Andaluc\u00eda", "Sevilla"): 106,
    ("Arag\u00f3n", "Huesca"): 202,
    ("Arag\u00f3n", "Teruel"): 236,
    ("Arag\u00f3n", "Zaragoza"): 293,
    ("Asturias", "Asturias"): 78,
    ("Canarias", "Las Palmas"): 34,
    ("Canarias", "Santa Cruz de Tenerife"): 54,
    ("Cantabria", "Cantabria"): 102,
    ("Castilla y Le\u00f3n", "\u00c1vila"): 248,
    ("Castilla y Le\u00f3n", "Burgos"): 371,
    ("Castilla y Le\u00f3n", "Le\u00f3n"): 211,
    ("Castilla y Le\u00f3n", "Palencia"): 191,
    ("Castilla y Le\u00f3n", "Salamanca"): 362,
    ("Castilla y Le\u00f3n", "Segovia"): 209,
    ("Castilla y Le\u00f3n", "Soria"): 183,
    ("Castilla y Le\u00f3n", "Valladolid"): 225,
    ("Castilla y Le\u00f3n", "Zamora"): 248,
    ("Castilla-La Mancha", "Albacete"): 87,
    ("Castilla-La Mancha", "Ciudad Real"): 102,
    ("Castilla-La Mancha", "Cuenca"): 238,
    ("Castilla-La Mancha", "Guadalajara"): 288,
    ("Castilla-La Mancha", "Toledo"): 204,
    ("Catalu\u00f1a", "Barcelona"): 311,
    ("Catalu\u00f1a", "Girona"): 221,
    ("Catalu\u00f1a", "Lleida"): 231,
    ("Catalu\u00f1a", "Tarragona"): 184,
    ("Ceuta", "Ceuta"): 1,
    ("Comunitat Valenciana", "Alicante"): 141,
    ("Comunitat Valenciana", "Castell\u00f3n"): 135,
    ("Comunitat Valenciana", "Valencia"): 266,
    ("Extremadura", "Badajoz"): 165,
    ("Extremadura", "C\u00e1ceres"): 223,
    ("Galicia", "A Coru\u00f1a"): 93,
    ("Galicia", "Lugo"): 67,
    ("Galicia", "Ourense"): 92,
    ("Galicia", "Pontevedra"): 61,
    ("Illes Balears", "Illes Balears"): 67,
    ("La Rioja", "La Rioja"): 174,
    ("Madrid", "Madrid"): 179,
    ("Melilla", "Melilla"): 1,
    ("Murcia", "Murcia"): 45,
    ("Navarra", "Navarra"): 272,
    ("Pa\u00eds Vasco", "Araba"): 50,
    ("Pa\u00eds Vasco", "Bizkaia"): 113,
    ("Pa\u00eds Vasco", "Gipuzkoa"): 88,
}

SPAIN_ROUTES = (
    (SPAIN_COUNTRY_NAME, "Andaluc\u00eda", "Sevilla", "Sevilla", "Sevilla"),
    (SPAIN_COUNTRY_NAME, "Andaluc\u00eda", "C\u00e1diz", "Algeciras", "Algeciras"),
    (SPAIN_COUNTRY_NAME, "Ceuta", "Ceuta", "Ceuta", "Benz\u00fa"),
    (SPAIN_COUNTRY_NAME, "Arag\u00f3n", "Zaragoza", "Zaragoza", "Zaragoza"),
    (SPAIN_COUNTRY_NAME, "Asturias", "Asturias", "Oviedo", "Oviedo"),
    (SPAIN_COUNTRY_NAME, "Illes Balears", "Illes Balears", "Palma", "Palma"),
    (SPAIN_COUNTRY_NAME, "Canarias", "Las Palmas", "Las Palmas de Gran Canaria", "Las Palmas de Gran Canaria"),
    (SPAIN_COUNTRY_NAME, "Cantabria", "Cantabria", "Santander", "Santander"),
    (SPAIN_COUNTRY_NAME, "Castilla-La Mancha", "Toledo", "Toledo", "Toledo"),
    (SPAIN_COUNTRY_NAME, "Castilla y Le\u00f3n", "Valladolid", "Valladolid", "Valladolid"),
    (SPAIN_COUNTRY_NAME, "Catalu\u00f1a", "Barcelona", "Barcelona", "Barcelona"),
    (SPAIN_COUNTRY_NAME, "Madrid", "Madrid", "Madrid", "Madrid"),
    (SPAIN_COUNTRY_NAME, "Comunitat Valenciana", "Valencia", "Val\u00e8ncia", "Val\u00e8ncia"),
    (SPAIN_COUNTRY_NAME, "Extremadura", "Badajoz", "M\u00e9rida", "M\u00e9rida"),
    (SPAIN_COUNTRY_NAME, "Galicia", "Lugo", "Lugo", "Lugo"),
    (SPAIN_COUNTRY_NAME, "La Rioja", "La Rioja", "Logro\u00f1o", "Logro\u00f1o"),
    (SPAIN_COUNTRY_NAME, "Murcia", "Murcia", "Murcia", "Murcia"),
    (SPAIN_COUNTRY_NAME, "Navarra", "Navarra", "Tudela", "Tudela"),
    (SPAIN_COUNTRY_NAME, "Pa\u00eds Vasco", "Bizkaia", "Bilbao", "Bilbao"),
    (SPAIN_COUNTRY_NAME, "Melilla", "Melilla", "Melilla", "Melilla"),
)


class CountryDataContractTests(unittest.TestCase):
    def test_spain_contract_data_uses_scraped_coofficial_names_and_expected_totals(self):
        contract = _spain_test_contract()

        self.assertEqual(contract.country_code, "spain")
        self.assertEqual(contract.country_name, SPAIN_COUNTRY_NAME)
        self.assertEqual(contract.totals.regions, 19)
        self.assertEqual(contract.totals.provinces, 52)
        self.assertEqual(contract.totals.municipalities, SPAIN_TOTAL_MUNICIPALITIES)
        self.assertEqual(contract.totals.localities, SPAIN_TOTAL_LOCALITIES_GE_20)
        self.assertEqual(len(contract.province_municipality_counts), 52)
        self.assertEqual(sum(contract.province_municipality_counts.values()), SPAIN_TOTAL_MUNICIPALITIES)
        self.assertEqual(contract.province_municipality_counts[("Andaluc\u00eda", "Sevilla")], 106)
        self.assertEqual(contract.province_municipality_counts[("Madrid", "Madrid")], 179)
        self.assertEqual(contract.province_municipality_counts[("Ceuta", "Ceuta")], 1)
        self.assertEqual(len(contract.routes), 20)
        self.assertEqual(validate_country_data_contract_consistency(contract), [])
        self.assertIn(
            (SPAIN_COUNTRY_NAME, "Comunitat Valenciana", "Valencia", "Val\u00e8ncia", "Val\u00e8ncia"),
            contract.routes,
        )
        self.assertIn(
            (SPAIN_COUNTRY_NAME, "Illes Balears", "Illes Balears", "Palma", "Palma"),
            contract.routes,
        )
        self.assertIn(
            (SPAIN_COUNTRY_NAME, "Madrid", "Madrid", "Madrid", "Madrid"),
            contract.routes,
        )

    def test_spain_generated_records_match_contract_and_have_parents(self):
        contract = _spain_test_contract()
        records = _records_from_contract(contract)

        self.assertEqual(validate_country_data_contract(contract, records), [])

    def test_validator_accepts_matching_country_hierarchy(self):
        contract = parse_country_data_contract(
            """
schema_version = 1
country_code = "testland"
country_name = "Testland"
locality_min_population = 20
locality_entity_types = ["Locality", "Municipality seat"]

[levels]
region = 1
province = 2
municipality = 3
localities = [4]

[totals]
regions = 1
provinces = 1
municipalities = 1
localities = 2

[regions]
North = ["North Province"]

[province_municipality_counts]
"North > North Province" = 1

[province_municipalities]
"North > North Province" = ["Springfield"]

[municipality_localities]
"North > North Province > Springfield" = ["Springfield", "West Springfield"]

[[routes]]
path = ["Testland", "North", "North Province", "Springfield", "West Springfield"]
"""
        )
        records = [
            CountryDataAreaRecord("root", "testland", "Testland", 0),
            CountryDataAreaRecord("region", "testland", "North", 1, "root", "Region"),
            CountryDataAreaRecord("province", "testland", "North Province", 2, "region", "Province"),
            CountryDataAreaRecord("municipality", "testland", "Springfield", 3, "province", "Municipality"),
            CountryDataAreaRecord("seat", "testland", "Springfield", 4, "municipality", "Municipality seat", 100),
            CountryDataAreaRecord("locality", "testland", "West Springfield", 4, "municipality", "Locality", 20),
            CountryDataAreaRecord("tiny", "testland", "Tiny Hamlet", 4, "municipality", "Locality", 19),
        ]

        self.assertEqual(validate_country_data_contract(contract, records), [])

    def test_validator_reports_missing_municipality_and_route(self):
        contract = parse_country_data_contract(
            """
schema_version = 1
country_code = "testland"
country_name = "Testland"

[levels]
region = 1
province = 2
municipality = 3
localities = [4]

[totals]
regions = 1
provinces = 1
municipalities = 1
localities = 0

[regions]
North = ["North Province"]

[province_municipality_counts]
"North > North Province" = 1

[province_municipalities]
"North > North Province" = ["Springfield"]

[municipality_localities]
"North > North Province > Springfield" = []

[[routes]]
path = ["Testland", "North", "North Province", "Springfield"]
"""
        )
        records = [
            CountryDataAreaRecord("root", "testland", "Testland", 0),
            CountryDataAreaRecord("region", "testland", "North", 1, "root", "Region"),
            CountryDataAreaRecord("province", "testland", "North Province", 2, "region", "Province"),
        ]

        issues = validate_country_data_contract(contract, records)

        self.assertTrue(_has_issue(issues, "CD-DATA-TOTAL", "municipalities"))
        self.assertTrue(_has_issue(issues, "CD-DATA-MISSING-NAME", "Springfield"))
        self.assertTrue(_has_issue(issues, "CD-DATA-MISSING-ROUTE", "Springfield"))

    def test_validator_reports_wrong_declared_counts_and_national_totals(self):
        contract = parse_country_data_contract(
            """
schema_version = 1
country_code = "testland"
country_name = "Testland"

[levels]
region = 1
province = 2
municipality = 3
localities = [4]

[totals]
regions = 1
provinces = 1
municipalities = 2
localities = 3

[regions]
North = ["North Province"]

[province_municipality_counts]
"North > North Province" = 2

[province_municipalities]
"North > North Province" = ["Springfield"]

[municipality_localities]
"North > North Province > Springfield" = ["Springfield", "West Springfield"]
"""
        )

        issues = validate_country_data_contract_consistency(contract)

        self.assertTrue(_has_issue(issues, "CD-CONTRACT-COUNT", "North > North Province"))
        self.assertTrue(_has_issue(issues, "CD-CONTRACT-TOTAL", "[totals].municipalities"))
        self.assertTrue(_has_issue(issues, "CD-CONTRACT-TOTAL", "[totals].localities"))

    def test_validator_reports_routes_not_declared_in_hierarchy_lists(self):
        contract = parse_country_data_contract(
            """
schema_version = 1
country_code = "testland"
country_name = "Testland"

[levels]
region = 1
province = 2
municipality = 3
localities = [4]

[totals]
regions = 1
provinces = 1
municipalities = 1
localities = 1

[regions]
North = ["North Province"]

[province_municipality_counts]
"North > North Province" = 1

[province_municipalities]
"North > North Province" = ["Springfield"]

[municipality_localities]
"North > North Province > Springfield" = ["Springfield"]

[[routes]]
path = ["Testland", "North", "North Province", "Springfield", "Missing Locality"]
"""
        )

        issues = validate_country_data_contract_consistency(contract)

        self.assertTrue(_has_issue(issues, "CD-CONTRACT-ROUTE-LOCALITY", "Missing Locality"))

    def test_validator_reports_non_root_rows_without_parent(self):
        contract = parse_country_data_contract(
            """
schema_version = 1
country_code = "testland"
country_name = "Testland"

[levels]
region = 1
province = 2
municipality = 3
localities = [4]

[totals]
regions = 1
provinces = 0
municipalities = 0
localities = 0

[regions]
North = []

[province_municipality_counts]

[province_municipalities]

[municipality_localities]
"""
        )
        records = [
            CountryDataAreaRecord("root", "testland", "Testland", 0),
            CountryDataAreaRecord("region", "testland", "North", 1, None, "Region"),
        ]

        issues = validate_country_data_contract(contract, records)

        self.assertTrue(_has_issue(issues, "CD-DATA-MISSING-PARENT-LINK", "North"))


def _spain_test_contract() -> CountryDataContract:
    route_municipalities_by_province: dict[tuple[str, str], list[str]] = {}
    route_localities_by_municipality: dict[tuple[str, str, str], list[str]] = {}
    for _, region, province, municipality, locality in SPAIN_ROUTES:
        route_municipalities_by_province.setdefault((region, province), [])
        if municipality not in route_municipalities_by_province[(region, province)]:
            route_municipalities_by_province[(region, province)].append(municipality)
        route_localities_by_municipality.setdefault((region, province, municipality), [])
        if locality not in route_localities_by_municipality[(region, province, municipality)]:
            route_localities_by_municipality[(region, province, municipality)].append(locality)

    province_municipalities: dict[tuple[str, str], tuple[str, ...]] = {}
    for key, count in SPAIN_PROVINCE_MUNICIPALITY_COUNTS.items():
        fixed = tuple(route_municipalities_by_province.get(key, ()))
        if len(fixed) > count:
            raise AssertionError(f"Too many fixed municipalities for {key}.")
        filler_count = count - len(fixed)
        fillers = tuple(f"{key[1]} municipio test {index:03d}" for index in range(1, filler_count + 1))
        province_municipalities[key] = fixed + fillers

    municipality_localities: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for (region, province), municipalities in province_municipalities.items():
        for municipality in municipalities:
            municipality_localities[(region, province, municipality)] = tuple(
                route_localities_by_municipality.get((region, province, municipality), ())
            )

    current_localities = sum(len(items) for items in municipality_localities.values())
    extra_localities = SPAIN_TOTAL_LOCALITIES_GE_20 - current_localities
    if extra_localities < 0:
        raise AssertionError("Spain route localities exceed the expected total.")
    sevilla_key = ("Andaluc\u00eda", "Sevilla", "Sevilla")
    municipality_localities[sevilla_key] = municipality_localities[sevilla_key] + tuple(
        f"Sevilla localidad test {index:05d}" for index in range(1, extra_localities + 1)
    )

    return CountryDataContract(
        schema_version=1,
        country_code="spain",
        country_name=SPAIN_COUNTRY_NAME,
        region_level=1,
        province_level=2,
        municipality_level=3,
        locality_levels=(4, 5),
        locality_entity_types=("Locality", "Municipality seat"),
        locality_min_population=20,
        totals=CountryDataTotals(
            regions=len(SPAIN_REGIONS),
            provinces=sum(len(provinces) for provinces in SPAIN_REGIONS.values()),
            municipalities=SPAIN_TOTAL_MUNICIPALITIES,
            localities=SPAIN_TOTAL_LOCALITIES_GE_20,
        ),
        regions=SPAIN_REGIONS,
        province_municipality_counts=SPAIN_PROVINCE_MUNICIPALITY_COUNTS,
        province_municipalities=province_municipalities,
        municipality_localities=municipality_localities,
        routes=SPAIN_ROUTES,
    )


def _records_from_contract(contract: CountryDataContract) -> list[CountryDataAreaRecord]:
    records = [CountryDataAreaRecord("spain", "spain", contract.country_name, 0)]
    region_ids: dict[str, str] = {}
    province_ids: dict[tuple[str, str], str] = {}
    municipality_ids: dict[tuple[str, str, str], str] = {}

    for region_index, region in enumerate(contract.regions, start=1):
        region_id = f"region-{region_index:02d}"
        region_ids[region] = region_id
        records.append(CountryDataAreaRecord(region_id, "spain", region, 1, "spain", "Autonomous Community"))

    for province_index, ((region, province), municipalities) in enumerate(contract.province_municipalities.items(), start=1):
        province_id = f"province-{province_index:02d}"
        province_ids[(region, province)] = province_id
        records.append(CountryDataAreaRecord(province_id, "spain", province, 2, region_ids[region], "Province"))
        for municipality_index, municipality in enumerate(municipalities, start=1):
            municipality_id = f"municipality-{province_index:02d}-{municipality_index:03d}"
            municipality_ids[(region, province, municipality)] = municipality_id
            records.append(CountryDataAreaRecord(municipality_id, "spain", municipality, 3, province_id, "Municipality"))

    locality_counter = 0
    for key, localities in contract.municipality_localities.items():
        for locality in localities:
            locality_counter += 1
            entity_type = "Municipality seat" if locality == key[2] else "Locality"
            records.append(
                CountryDataAreaRecord(
                    f"locality-{locality_counter:05d}",
                    "spain",
                    locality,
                    4,
                    municipality_ids[key],
                    entity_type,
                    20,
                )
            )
    return records


def _has_issue(issues: list[CountryDataIssue], code: str, text: str) -> bool:
    return any(issue.code == code and text in issue.message for issue in issues)
