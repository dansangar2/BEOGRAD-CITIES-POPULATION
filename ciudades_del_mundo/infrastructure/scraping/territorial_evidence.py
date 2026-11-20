"""Bounded same-country discovery, freezing and reuse of the existing parser."""
from dataclasses import asdict
from hashlib import sha256
import json
import re
import time
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain.territorial_reconciliation import ENGINE_VERSION, ReconciliationError
from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
from ciudades_del_mundo.infrastructure.scraping.citypopulation_sections import CityPopulationSectionParser


BASE_URL = "https://www.citypopulation.de/en/"
MAX_PAGE_BYTES = 4_000_000
MAX_TOTAL_BYTES = 30_000_000


def content_hash(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def controlled_url(value: str, entry_path: str) -> str:
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "www.citypopulation.de" or parsed.query
            or parsed.fragment or not path.startswith("/en/" + entry_path)
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
            or any(p in (".", "..") for p in path.split("/"))):
        raise ReconciliationError(f"discovery: URL outside the country allowlist: {value!r}.")
    if not path.endswith(("/", ".html")):
        raise ReconciliationError(f"discovery: non-page URL: {value!r}.")
    return "https://www.citypopulation.de" + path


class _CountryRedirects(HTTPRedirectHandler):
    def __init__(self, entry_path):
        self.entry_path = entry_path

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        controlled_url(newurl, self.entry_path)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ControlledTerritorialDiscovery:
    def __init__(self, *, max_pages=100, timeout=20, deadline_seconds=240, fetch=None):
        self.max_pages = min(150, max(1, max_pages))
        self.timeout = min(30, max(1, timeout))
        self.deadline_seconds = min(300, max(1, deadline_seconds))
        self.fetch = fetch

    def discover(self, reference, *, checkpoint=lambda: None) -> dict:
        entry = reference.entry_path
        pending = [controlled_url(BASE_URL + entry, entry)]
        pages = {}
        total_bytes = 0
        deadline = time.monotonic() + self.deadline_seconds
        opener = build_opener(_CountryRedirects(entry))
        while pending:
            checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(pages) >= self.max_pages:
                raise ReconciliationError(f"discovery: page/time limit reached; pending={pending[:10]}. Evidence is incomplete.")
            url = pending.pop(0)
            if self.fetch:
                html = self.fetch(url)
            else:
                request = Request(url, headers={"User-Agent": "BEOGRAD-territorial-reconciliation/1"})
                with opener.open(request, timeout=min(self.timeout, remaining)) as response:
                    controlled_url(response.geturl(), entry)
                    raw = response.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    raise ReconciliationError(f"discovery: page byte limit: {url}.")
                html = raw.decode("utf-8")
            checkpoint()
            size = len(html.encode("utf-8"))
            total_bytes += size
            if size > MAX_PAGE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ReconciliationError("discovery: page/total byte limit exceeded.")
            pages[url] = html
            for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
                target = urljoin(url, anchor["href"]).split("#", 1)[0]
                try:
                    target = controlled_url(target, entry)
                except ReconciliationError:
                    continue
                if target not in pages and target not in pending:
                    pending.append(target)
        return freeze_pages(reference.country, entry, pages)


def freeze_pages(country: str, entry_path: str, pages: dict[str, str]) -> dict:
    """Page-local section levels are observations, not assumed territorial roles."""
    if not isinstance(pages, dict) or not pages or len(pages) > 150:
        raise ReconciliationError("evidence.pages: expected 1..150 frozen pages.")
    if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", country) or any(not isinstance(url, str) for url in pages):
        raise ReconciliationError("evidence: invalid country or page URL identifiers.")
    parser = CityPopulationSectionParser(CityPopulationClient())
    rows = []
    frozen = []
    total = 0
    for url, html in sorted(pages.items()):
        controlled_url(url, entry_path)
        if not isinstance(html, str) or len(html.encode()) > MAX_PAGE_BYTES:
            raise ReconciliationError(f"evidence: invalid/oversized HTML at {url}.")
        total += len(html.encode())
        if total > MAX_TOTAL_BYTES:
            raise ReconciliationError("evidence: total byte limit exceeded.")
        page_id = sha256(url.encode()).hexdigest()[:16]
        frozen.append({"id": page_id, "url": url, "html": html, "hash": sha256(html.encode()).hexdigest()})
        entities = parser.parse(html=html, url=url, country_code=country, system="citiesadmin", base_level=0, page=None)
        for index, entity in enumerate(entities):
            section = next((line for line in entity.annotations.splitlines() if line.startswith("CityPopulation section:")), "unclassified")
            rows.append({"id": f"{page_id}:{index}", "page": page_id, "block": section, "entity": asdict(entity)})
    if not rows:
        raise ReconciliationError("evidence: no parsed entities; pages alone cannot justify an apply.")
    return json.loads(json.dumps({"schema_version": 1, "engine": ENGINE_VERSION, "country": country,
        "entry_path": entry_path, "pages": frozen, "rows": rows}, default=str))


def verify_evidence(document: dict) -> None:
    """Reject edited observations, parser drift and missing frozen source pages."""
    try:
        rebuilt = freeze_pages(document["country"], document["entry_path"], {p["url"]: p["html"] for p in document["pages"]})
    except (KeyError, TypeError) as exc:
        raise ReconciliationError("evidence: invalid frozen page document.") from exc
    if content_hash(rebuilt) != content_hash(document):
        raise ReconciliationError("evidence: observations differ from frozen HTML/current parser; import a new version.")
