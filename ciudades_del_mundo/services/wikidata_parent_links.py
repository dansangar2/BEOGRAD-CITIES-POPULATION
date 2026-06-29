"""Resolve missing AdminArea parents from Wikidata administrative links."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import json
import re
import unicodedata
from urllib.parse import unquote, urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ciudades_del_mundo.domain import ScrapedAdminArea


WIKIDATA_PARENT_BATCH_LIMIT = 500
WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
_USER_AGENT = "BEOGRAD-CITIES-POPULATION/1.0 (local data maintenance; Wikidata parent repair)"


@dataclass(frozen=True)
class WikidataParentCandidate:
    """One direct administrative parent returned by Wikidata."""

    child_qid: str
    parent_qid: str
    parent_label: str = ""


def repair_entities_with_wikidata_parent_links(
    country_code: str,
    entities: list[ScrapedAdminArea] | tuple[ScrapedAdminArea, ...],
    *,
    logger=None,
) -> list[ScrapedAdminArea]:
    """Fetch P131 for currently unlinked rows and link to already scraped parents.

    Wikidata is only a repair source here.  It may identify the missing parent,
    but the parent must already exist in the CityPopulation scrape by QID or by
    a unique ``parentLabel`` match inside the same country.
    """

    rows = list(entities)
    targets = _entities_needing_parent_repair(country_code, rows)
    qids = _unique_qids(entity.data_wd for entity in targets)
    if not qids:
        return rows

    try:
        candidates = fetch_wikidata_parent_candidates(qids, logger=logger)
    except Exception as exc:  # noqa: BLE001 - repair should not hide the final validation error.
        _log(logger, f"[wikimedia] padres Wikidata no disponibles ({exc}); se validara con CityPopulation")
        return rows

    repaired = link_entities_with_wikidata_parent_candidates(country_code, rows, candidates)
    repaired_count = _changed_parent_count(rows, repaired)
    _log(
        logger,
        f"[wikimedia] padres Wikidata: candidatos={len(candidates)}, reparados={repaired_count}",
    )
    return repaired


def fetch_wikidata_parent_candidates(
    wikidata_ids: list[str] | tuple[str, ...],
    *,
    logger=None,
) -> list[WikidataParentCandidate]:
    qids = _unique_qids(wikidata_ids)
    candidates: list[WikidataParentCandidate] = []
    for start in range(0, len(qids), WIKIDATA_PARENT_BATCH_LIMIT):
        batch = qids[start : start + WIKIDATA_PARENT_BATCH_LIMIT]
        if not batch:
            continue
        payload = _wikidata_sparql_json(_wikidata_parent_query(batch), timeout=60)
        rows = payload.get("results", {}).get("bindings", []) or []
        for row in rows:
            child_qid = _qid_from_binding(row, "item")
            parent_qid = _qid_from_binding(row, "parent")
            if not child_qid or not parent_qid:
                continue
            candidates.append(
                WikidataParentCandidate(
                    child_qid=child_qid,
                    parent_qid=parent_qid,
                    parent_label=str(row.get("parentLabel", {}).get("value") or "").strip(),
                )
            )
        _log(
            logger,
            f"[wikimedia] padres Wikidata lote {start // WIKIDATA_PARENT_BATCH_LIMIT + 1}: "
            f"qids={len(batch)}, filas={len(rows)}",
        )
    return candidates


def link_entities_with_wikidata_parent_candidates(
    country_code: str,
    entities: list[ScrapedAdminArea] | tuple[ScrapedAdminArea, ...],
    candidates: list[WikidataParentCandidate] | tuple[WikidataParentCandidate, ...],
) -> list[ScrapedAdminArea]:
    rows = list(entities)
    scoped = [entity for entity in rows if str(entity.country_code) == str(country_code)]
    if not scoped:
        return rows

    by_code = {str(entity.code): entity for entity in scoped}
    existing_codes = set(by_code)
    root = _preferred_root(country_code, scoped)
    parents_by_child_qid: dict[str, list[WikidataParentCandidate]] = {}
    for candidate in candidates:
        child_qid = _normalize_qid(candidate.child_qid)
        parent_qid = _normalize_qid(candidate.parent_qid)
        if not child_qid or not parent_qid:
            continue
        parents_by_child_qid.setdefault(child_qid, []).append(
            WikidataParentCandidate(child_qid=child_qid, parent_qid=parent_qid, parent_label=candidate.parent_label)
        )

    by_qid: dict[str, list[ScrapedAdminArea]] = {}
    by_name: dict[str, list[ScrapedAdminArea]] = {}
    for entity in scoped:
        qid = _normalize_qid(entity.data_wd)
        if qid:
            by_qid.setdefault(qid, []).append(entity)
        name = _normalized_text(entity.name)
        if name:
            by_name.setdefault(name, []).append(entity)

    updated: list[ScrapedAdminArea] = []
    for entity in rows:
        if str(entity.country_code) != str(country_code):
            updated.append(entity)
            continue
        if not _needs_parent_repair(entity, existing_codes, root):
            updated.append(entity)
            continue
        child_qid = _normalize_qid(entity.data_wd)
        if not child_qid:
            updated.append(entity)
            continue
        parent = _best_parent_for_entity(
            entity,
            parents_by_child_qid.get(child_qid, []),
            by_qid=by_qid,
            by_name=by_name,
            by_code=by_code,
            scoped=scoped,
        )
        if parent is None:
            updated.append(entity)
            continue
        updated.append(replace(entity, parent_code=str(parent.code), level=int(parent.level) + 1))
    return updated


def _best_parent_for_entity(
    entity: ScrapedAdminArea,
    candidates: list[WikidataParentCandidate],
    *,
    by_qid: dict[str, list[ScrapedAdminArea]],
    by_name: dict[str, list[ScrapedAdminArea]],
    by_code: dict[str, ScrapedAdminArea],
    scoped: list[ScrapedAdminArea],
) -> ScrapedAdminArea | None:
    forced_level = _forced_parent_level(entity.annotations)
    possible: list[tuple[tuple[int, int, int], ScrapedAdminArea]] = []
    for candidate in candidates:
        parent_qid = _normalize_qid(candidate.parent_qid)
        qid_matches = by_qid.get(parent_qid, [])
        label_matches: list[ScrapedAdminArea] = []
        label_key = _normalized_text(candidate.parent_label)
        if label_key:
            raw_label_matches = by_name.get(label_key, [])
            if len({str(item.code) for item in raw_label_matches}) == 1:
                label_matches = raw_label_matches
        for parent in [*qid_matches, *label_matches]:
            if str(parent.code) == str(entity.code):
                continue
            if _parent_chain_reaches(parent, str(entity.code), by_code):
                continue
            source_score = 2 if _normalize_qid(parent.data_wd) == parent_qid else 1
            forced_score = 1 if forced_level is not None and int(parent.level) == forced_level else 0
            possible.append(((forced_score, int(parent.level), source_score), parent))
    for parent in _url_scope_parent_candidates(entity, scoped):
        if str(parent.code) == str(entity.code):
            continue
        if _parent_chain_reaches(parent, str(entity.code), by_code):
            continue
        forced_score = 1 if forced_level is not None and int(parent.level) == forced_level else 0
        possible.append(((forced_score, int(parent.level), 0), parent))
    if not possible:
        return None
    possible.sort(key=lambda item: item[0], reverse=True)
    return possible[0][1]


def _url_scope_parent_candidates(entity: ScrapedAdminArea, scoped: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    tokens = _url_scope_tokens(entity)
    if not tokens:
        return []
    candidates_by_token: dict[str, list[ScrapedAdminArea]] = {}
    for candidate in scoped:
        name = _normalized_text(candidate.name)
        if not name or name not in tokens:
            continue
        if int(candidate.level) < 0:
            continue
        candidates_by_token.setdefault(name, []).append(candidate)

    candidates: list[ScrapedAdminArea] = []
    for token in tokens:
        rows = candidates_by_token.get(token, [])
        if len({str(row.code) for row in rows}) == 1:
            candidates.extend(rows)
    return candidates


def _entities_needing_parent_repair(country_code: str, entities: list[ScrapedAdminArea]) -> list[ScrapedAdminArea]:
    scoped = [entity for entity in entities if str(entity.country_code) == str(country_code)]
    codes = {str(entity.code) for entity in scoped}
    root = _preferred_root(country_code, scoped)
    return [entity for entity in scoped if _needs_parent_repair(entity, codes, root) and _normalize_qid(entity.data_wd)]


def _needs_parent_repair(entity: ScrapedAdminArea, existing_codes: set[str], root: ScrapedAdminArea | None) -> bool:
    if int(entity.level) == 0:
        return False
    parent_code = str(entity.parent_code or "").strip()
    if not parent_code:
        return root is None or str(entity.code) != str(root.code)
    return parent_code == str(entity.code) or parent_code not in existing_codes


def _preferred_root(country_code: str, entities: list[ScrapedAdminArea]) -> ScrapedAdminArea | None:
    roots = [entity for entity in entities if int(entity.level) == 0 and not entity.parent_code]
    if not roots:
        return None
    for entity in roots:
        if str(entity.code).casefold() == str(country_code).casefold():
            return entity
    return roots[0]


def _parent_chain_reaches(parent: ScrapedAdminArea, target_code: str, by_code: dict[str, ScrapedAdminArea]) -> bool:
    seen: set[str] = set()
    current: ScrapedAdminArea | None = parent
    while current is not None:
        code = str(current.code)
        if code == target_code:
            return True
        if code in seen or not current.parent_code:
            return False
        seen.add(code)
        current = by_code.get(str(current.parent_code))
    return False


def _wikidata_parent_query(qids: list[str]) -> str:
    values = " ".join(f"wd:{qid}" for qid in qids)
    return f"""
SELECT ?item ?parent ?parentLabel WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P131 ?parent .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,es,nl,fr,de,it,pt". }}
}}
""".strip()


def _wikidata_sparql_json(query: str, *, timeout: int = 60) -> dict:
    data = urlencode({"query": query, "format": "json"}).encode("utf-8")
    request = Request(
        WIKIDATA_SPARQL_ENDPOINT,
        data=data,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS Wikidata endpoint.
        return json.loads(response.read().decode("utf-8"))


def _qid_from_binding(row: dict, key: str) -> str:
    value = str(row.get(key, {}).get("value") or "").strip()
    if not value:
        return ""
    return _normalize_qid(value.rsplit("/", 1)[-1])


def _unique_qids(values) -> list[str]:
    qids: list[str] = []
    seen: set[str] = set()
    for value in values:
        qid = _normalize_qid(value)
        if qid and qid not in seen:
            qids.append(qid)
            seen.add(qid)
    return qids


def _normalize_qid(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.rsplit("/", 1)[-1].upper()
    return text if re.fullmatch(r"Q\d+", text) else ""


def _normalized_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def _url_scope_tokens(entity: ScrapedAdminArea) -> tuple[str, ...]:
    url = str(entity.url or "").strip()
    if not url:
        return ()
    path = unquote(urlparse(url).path or "")
    if not path:
        return ()
    generic = {
        "en",
        "admin",
        "cities",
        "city",
        "places",
        "localities",
        "population",
        "www citypopulation de",
        _normalized_text(entity.country_code),
        _normalized_text(entity.code),
        _normalized_text(entity.name),
    }
    tokens: list[str] = []
    for segment in path.split("/"):
        normalized = _normalized_text(segment)
        if (
            len(normalized) >= 3
            and normalized not in generic
            and not normalized.replace(" ", "").isdigit()
            and not normalized.split()[0].isdigit()
            and normalized not in tokens
        ):
            tokens.append(normalized)
    return tuple(tokens)


def _forced_parent_level(annotations: str | None) -> int | None:
    match = re.search(r"Forced parent level:\s*(\d+)", str(annotations or ""))
    return int(match.group(1)) if match else None


def _changed_parent_count(before: list[ScrapedAdminArea], after: list[ScrapedAdminArea]) -> int:
    total = 0
    for old, new in zip(before, after, strict=False):
        if old.parent_code != new.parent_code or int(old.level) != int(new.level):
            total += 1
    return total


def _log(logger, message: str) -> None:
    if callable(logger):
        logger(message)
