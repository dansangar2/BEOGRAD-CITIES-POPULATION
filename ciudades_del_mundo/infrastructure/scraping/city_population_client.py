from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import Tag

from ciudades_del_mundo.ports import ScrapingPageNotFoundError


@dataclass
class CityPopulationEntity:
    entity_id: str
    name: str
    level: int | None
    entity_type: str | None
    area_km2: float | None
    density: float | None
    pop_latest: int | None
    pop_latest_date: str | None
    last_census_year: int | None
    parent_id: str | None
    parent_name: str | None
    country_code: str
    url: str | None


class CityPopulationClient:
    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; CityPopulationClient/2.0; +https://example.org)",
        "Accept-Language": "en,es;q=0.9",
    }

    def __init__(self, debug: bool = False, parser: str = "lxml"):
        self.debug = debug
        self.parser = parser if parser in ("lxml", "html.parser") else "html.parser"
        self._session = requests.Session()
        self._session.headers.update(self.BASE_HEADERS)

    def get(self, url: str) -> str:
        parsed = urlparse(url)
        self._session.headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        attempts = 2
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._session.get(url, timeout=30)
                if response.status_code == 404:
                    raise ScrapingPageNotFoundError(url)
                if response.status_code == 403:
                    raise requests.HTTPError("403 Forbidden.")
                response.raise_for_status()
                text = response.text
                if "enable JavaScript" in text and "Cloudflare" in text:
                    raise requests.HTTPError("CityPopulation page is protected by JavaScript/Cloudflare.")
                if "Check for Humans" in text or "Sorry, your access has" in text:
                    raise requests.HTTPError("CityPopulation requires human verification.")
                return text
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, ScrapingPageNotFoundError):
                    raise
                if self.debug:
                    print(f"[citypopulation] attempt {attempt + 1}/{attempts} failed: {exc}")
                time.sleep(0.8)

        raise last_exc if last_exc else RuntimeError("Failed to download CityPopulation page.")

    def base_for_urljoin(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def detect_last_visible_pop_column(self, table: Tag) -> tuple[int, str | None]:
        thead = table.find("thead")
        if not thead:
            return -1, None

        pop_headers = [th for th in thead.find_all("th") if "rpop" in (th.get("class") or [])]
        last_visible_idx = -1
        last_date = None
        logical_idx = -1

        for th in pop_headers:
            logical_idx += 1
            style = (th.get("style") or "").replace(" ", "").lower()
            if "display:table-cell" in style:
                last_visible_idx = logical_idx
                last_date = th.get("data-coldate") or last_date

        if last_visible_idx == -1 and pop_headers:
            last_visible_idx = len(pop_headers) - 1
            last_date = pop_headers[-1].get("data-coldate") or last_date

        return last_visible_idx, last_date

    def parse_tr_tl(
        self,
        tr: Tag,
        explicit_level: int | None,
        last_visible_pop_idx: int,
        last_visible_date: str | None,
        default_last_census_year: int | None,
        country_code: str,
        base_url: str,
    ) -> CityPopulationEntity | None:
        main_td = self._main_name_cell(tr)
        if not main_td:
            return None

        entity_id = self._entity_id(main_td, tr, allow_sym_area=True)
        if not entity_id:
            return None

        status_td = tr.find(["td", "th"], class_="rstatus")
        area_km2 = self.safe_float(main_td.get("data-area"))
        if area_km2 is None:
            area_cell = tr.find(["td", "th"], class_="rarea")
            area_km2 = self.safe_float_text(area_cell.get_text(" ", strip=True)) if area_cell else None
        pop_latest = self._extract_last_visible_population(tr, last_visible_pop_idx)
        return CityPopulationEntity(
            entity_id=entity_id,
            name=self._extract_name(main_td),
            level=explicit_level,
            entity_type=status_td.get_text(" ", strip=True) if status_td else None,
            area_km2=area_km2,
            density=self._density_or_calculated(main_td.get("data-density"), pop_latest, area_km2),
            pop_latest=pop_latest,
            pop_latest_date=last_visible_date,
            last_census_year=self.year_from_date(last_visible_date) or default_last_census_year,
            parent_id=None,
            parent_name=None,
            country_code=country_code,
            url=self._row_url(tr, base_url),
        )

    def parse_tr_ts(
        self,
        tr: Tag,
        last_visible_pop_idx: int,
        last_visible_date: str | None,
        default_last_census_year: int | None,
        country_code: str,
        base_url: str,
        has_radm: bool,
    ) -> CityPopulationEntity | None:
        main_td = self._main_name_cell(tr)
        if not main_td:
            return None

        entity_id = self._entity_id(main_td, tr, allow_sym_area=False)
        if not entity_id:
            return None

        status_td = tr.find(["td", "th"], class_="rstatus")
        entity_type = status_td.get_text(" ", strip=True) if status_td else main_td.get("data-status")
        area_km2 = self.safe_float(main_td.get("data-area"))
        pop_latest = self._extract_last_visible_population(tr, last_visible_pop_idx)
        return CityPopulationEntity(
            entity_id=entity_id,
            name=self._extract_name(main_td),
            level=None,
            entity_type=entity_type,
            area_km2=area_km2,
            density=self._density_or_calculated(main_td.get("data-density"), pop_latest, area_km2),
            pop_latest=pop_latest,
            pop_latest_date=last_visible_date,
            last_census_year=self.year_from_date(last_visible_date) or default_last_census_year,
            parent_id=None,
            parent_name=None,
            country_code=country_code,
            url=self._row_url(tr, base_url),
        )

    def safe_float(self, value: Optional[str]) -> float | None:
        if value is None:
            return None
        try:
            return float(value.replace(",", "."))
        except Exception:
            return None

    def safe_float_text(self, value: str | None) -> float | None:
        if not value:
            return None
        normalized = re.sub(r"[^\d,.-]", "", value)
        if not normalized or normalized in {"-", ".", ","}:
            return None

        has_comma = "," in normalized
        has_dot = "." in normalized
        if has_comma and has_dot:
            decimal_sep = "," if normalized.rfind(",") > normalized.rfind(".") else "."
            thousands_sep = "." if decimal_sep == "," else ","
            normalized = normalized.replace(thousands_sep, "").replace(decimal_sep, ".")
        elif has_comma:
            parts = normalized.split(",")
            normalized = "".join(parts) if len(parts[-1]) == 3 else normalized.replace(",", ".")

        try:
            return float(normalized)
        except Exception:
            return None

    def _density_or_calculated(
        self,
        raw_density: str | None,
        population: int | None,
        area_km2: float | None,
    ) -> float | None:
        density = self.safe_float(raw_density)
        if density is not None:
            return density
        if population is None or area_km2 in (None, 0):
            return None
        return population / area_km2

    def year_from_date(self, value: str | None) -> int | None:
        if not value:
            return None
        try:
            return dt.date.fromisoformat(value[:10]).year
        except Exception:
            match = re.search(r"(\d{4})", value)
            return int(match.group(1)) if match else None

    def _main_name_cell(self, tr: Tag) -> Tag | None:
        cells = tr.find_all(["td", "th"], recursive=False)
        return next((cell for cell in cells if "rname" in (cell.get("class") or [])), None)

    def _entity_id(self, main_td: Tag, tr: Tag, *, allow_sym_area: bool) -> str | None:
        raw_id = main_td.get("id", "")
        entity_id = raw_id[1:] if raw_id.startswith("i") else (raw_id or None)
        if entity_id:
            return entity_id

        onclick = tr.get("onclick", "") or main_td.get("onclick", "")
        patterns = [r"sym\('([^']+)'\)"]
        if allow_sym_area:
            patterns.append(r"symArea\('([^']+)'")
        for pattern in patterns:
            match = re.search(pattern, onclick)
            if match:
                return match.group(1)
        return None

    def _row_url(self, tr: Tag, base_url: str) -> str | None:
        sc_td = tr.find("td", class_="sc")
        if not sc_td:
            return None
        anchor = sc_td.find("a", href=True)
        return urljoin(base_url, anchor["href"]) if anchor and anchor["href"] else None

    def _extract_name(self, td: Tag) -> str:
        span = td.find("span", attrs={"itemprop": "name"})
        if span and span.get_text(strip=True):
            return span.get_text(strip=True)
        spans = td.find_all("span", attrs={"itemprop": "name"})
        if spans:
            return spans[0].get_text(strip=True)
        return td.get_text(" ", strip=True)

    def _extract_last_visible_population(self, tr: Tag, last_visible_pop_idx: int) -> int | None:
        pops = [cell for cell in tr.find_all(["td", "th"], recursive=False) if "rpop" in (cell.get("class") or [])]
        if not pops:
            return None
        idx = last_visible_pop_idx if 0 <= last_visible_pop_idx < len(pops) else len(pops) - 1
        digits = re.sub(r"[^\d]", "", pops[idx].get_text(strip=True))
        return int(digits) if digits else None
