# app/utils/citypop.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple, Literal
from urllib.parse import urljoin, urlparse
import datetime as _dt
import re
import time

import requests
from bs4 import BeautifulSoup, Tag
from collections import deque

# ==============================
# Modelo
# ==============================
@dataclass
class Entity:
    entity_id: str
    name: str
    level: Optional[int]                # 0..5 si se sabe; None si no
    entity_type: Optional[str]          # "Autonomous Community", "Province", "Municipality", ...
    area_km2: Optional[float]
    density: Optional[float]
    pop_latest: Optional[int]
    pop_latest_date: Optional[str]      # "YYYY-MM-DD" (última col. visible, C o R)
    last_census_year: Optional[int]     # == year(pop_latest_date)
    parent_id: Optional[str]
    parent_name: Optional[str]
    country_code: str
    url: Optional[str]

    @property
    def pk(self) -> str:
        return f"{self.country_code}_{self.entity_id}"

    def to_dict(self) -> Dict:
        return asdict(self)


# ==============================
# Scraper
# ==============================
class CityPopulationScraper:
    """
    Soporta:
      - Páginas con tabla id="tl" (clásica, con tbodys adminX).
      - Páginas con tabla id="ts" (listados planos, ej. municipios).
    Preferencia de tabla configurable:
      - prefer_table="ts"  → si existe #ts, se usa esa; si no, #tl.
      - prefer_table="tl"  → al revés.
      - prefer_table="auto" → usa #ts si existe, si no #tl.

    last_census_year SIEMPRE = año de pop_latest_date (última col. visible, sea C o R).

    Nuevo:
      - lowest_level (int): nivel mínimo a asignar cuando no puede inferirse el nivel
        de la fila. Por defecto 1.
    """

    TBODY_LEVEL_MAP = {"admin0": 0, "admin1": 1, "admin2": 2, "admin3": 3, "admin4": 4, "admin5": 5}

    BASE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    def __init__(self, debug: bool = False, parser: str = "lxml"):
        self.debug = debug
        self.parser = parser if parser in ("lxml", "html.parser") else "html.parser"
        self._session = requests.Session()
        self._session.headers.update(self.BASE_HEADERS)

    # ---------- API ----------
    def scrape_table(
        self,
        url: str,
        country_code: str,
        prefer_table: Literal["auto", "ts", "tl"] = "auto",
        lowest_level: int = 1,  # ← nivel mínimo del scraping (por defecto 1)
    ) -> List[Entity]:
        """
        Lee la tabla principal de la URL y devuelve entidades.

        lowest_level:
          - Si no se puede inferir el nivel de la fila, se usará este valor.
          - Por defecto es 1.
        """
        html = self._get(url)
        soup = BeautifulSoup(html, self.parser)

        tl = soup.find("table", id="tl")
        ts = soup.find("table", id="ts")

        table = None
        if prefer_table == "ts":
            table = ts or tl
        elif prefer_table == "tl":
            table = tl or ts
        else:  # auto
            table = ts or tl

        if not table:
            if self.debug:
                print("[scrape_table] No se encontró <table id='ts'|'tl'> en:", url)
            return []

        # --- Metadatos población / fecha visible
        last_visible_pop_idx, last_visible_date = self._detect_last_visible_pop_column(table)
        last_year_from_visible = self._year_from_date(last_visible_date)

        entities: List[Entity] = []

        if table.get("id") == "ts":
            # --------- Variante TS (municipios u otros listados planos)
            tbody = table.find("tbody")
            if not tbody:
                return []

            # Si la cabecera incluye una columna administrativa (p.ej., Provincia), antes forzábamos 3.
            # Ahora usamos lowest_level para que sea configurable por país/escenario.
            has_radm = bool(table.find("th", class_=lambda c: c and "radm" in c.split()))

            for tr in tbody.find_all("tr", recursive=False):
                ent = self._parse_tr_ts(
                    tr=tr,
                    last_visible_pop_idx=last_visible_pop_idx,
                    last_visible_date=last_visible_date,
                    default_last_census_year=last_year_from_visible,
                    country_code=country_code,
                    base_url=self._base_for_urljoin(url),
                    has_radm=has_radm,
                )
                if ent:
                    if ent.level is None:
                        ent.level = lowest_level
                    entities.append(ent)

        else:
            # --------- Variante TL (clásica con adminX)
            tbodies = table.find_all("tbody", recursive=False)
            has_admin_tbodies = any(self._level_from_tbody_class(t) is not None for t in tbodies)

            if has_admin_tbodies:
                parent_stack: Dict[int, Tuple[str, str]] = {}
                for tbody in tbodies:
                    level = self._level_from_tbody_class(tbody)
                    if level is None or level == 0:
                        continue
                    for tr in tbody.find_all("tr", recursive=False):
                        ent = self._parse_tr_tl(
                            tr=tr,
                            explicit_level=level,
                            last_visible_pop_idx=last_visible_pop_idx,
                            last_visible_date=last_visible_date,
                            default_last_census_year=last_year_from_visible,
                            country_code=country_code,
                            base_url=self._base_for_urljoin(url),
                        )
                        if not ent:
                            continue
                        parent_tuple = parent_stack.get(level - 1)
                        if parent_tuple:
                            ent.parent_id, ent.parent_name = parent_tuple
                        else:
                            ent.parent_id = None
                            ent.parent_name = None
                        parent_stack[level] = (ent.entity_id, ent.name)
                        entities.append(ent)
            else:
                tbody = table.find("tbody")
                if not tbody:
                    return []
                for tr in tbody.find_all("tr", recursive=False):
                    inferred_level = self._infer_level_from_tr(tr)
                    ent = self._parse_tr_tl(
                        tr=tr,
                        explicit_level=inferred_level,
                        last_visible_pop_idx=last_visible_pop_idx,
                        last_visible_date=last_visible_date,
                        default_last_census_year=last_year_from_visible,
                        country_code=country_code,
                        base_url=self._base_for_urljoin(url),
                    )
                    if ent:
                        if ent.level is None:
                            ent.level = lowest_level
                        entities.append(ent)

        # Post-proceso de seguridad: rellena cualquier None
        for e in entities:
            if e.level is None:
                e.level = lowest_level

        if self.debug:
            print(f"[scrape_table] {url} -> tabla #{table.get('id')} -> {len(entities)} entidades (min={lowest_level})")
        return entities

    def get_children(
        self,
        entity: Entity,
        target_level: Optional[int] = None,
        lowest_level: Optional[int] = None,  # opcional para forzar el mínimo en hijos
    ) -> List[Entity]:
        """
        Carga la página de la entidad y devuelve sus hijos.
        - Si la página tiene #ts con columna radm → hijos nivel (lowest_level si se pasa, si no 3/parent+1).
        - Si #tl con adminX → hijos a ese nivel.
        - Si no puede inferir, usa (lowest_level) o (parent.level + 1) si se conoce el nivel del padre.
        """
        if not entity.url:
            return []

        html = self._get(entity.url)
        soup = BeautifulSoup(html, self.parser)

        tl = soup.find("table", id="tl")
        ts = soup.find("table", id="ts")
        table = ts or tl  # al buscar hijos, nos interesa antes #ts si existe

        if not table:
            if self.debug:
                print("[get_children] Sin tablas en:", entity.url)
            return []

        last_visible_pop_idx, last_visible_date = self._detect_last_visible_pop_column(table)
        last_year_from_visible = self._year_from_date(last_visible_date)

        results: List[Entity] = []

        if table.get("id") == "ts":
            tbody = table.find("tbody")
            has_radm = bool(table.find("th", class_=lambda c: c and "radm" in c.split()))
            if tbody:
                for tr in tbody.find_all("tr", recursive=False):
                    child = self._parse_tr_ts(
                        tr=tr,
                        last_visible_pop_idx=last_visible_pop_idx,
                        last_visible_date=last_visible_date,
                        default_last_census_year=last_year_from_visible,
                        country_code=entity.country_code,
                        base_url=self._base_for_urljoin(entity.url),
                        has_radm=has_radm,
                    )
                    if not child:
                        continue

                    # Nivel en TS:
                    if child.level is None:
                        if lowest_level is not None:
                            child.level = lowest_level
                        elif has_radm:
                            child.level = (entity.level + 1) if entity.level is not None else 3
                        else:
                            child.level = (entity.level + 1) if entity.level is not None else None

                    # Padre
                    child.parent_id = entity.entity_id
                    child.parent_name = entity.name

                    results.append(child)

        else:
            tbodies = table.find_all("tbody", recursive=False)
            has_admin_tbodies = any(self._level_from_tbody_class(t) is not None for t in tbodies)
            if has_admin_tbodies:
                for tbody in tbodies:
                    lvl = self._level_from_tbody_class(tbody)
                    if lvl is None or lvl == 0:
                        continue
                    for tr in tbody.find_all("tr", recursive=False):
                        child = self._parse_tr_tl(
                            tr=tr,
                            explicit_level=lvl,
                            last_visible_pop_idx=last_visible_pop_idx,
                            last_visible_date=last_visible_date,
                            default_last_census_year=last_year_from_visible,
                            country_code=entity.country_code,
                            base_url=self._base_for_urljoin(entity.url),
                        )
                        if not child:
                            continue
                        if child.level is None:
                            if lowest_level is not None:
                                child.level = lowest_level
                            elif entity.level is not None:
                                child.level = entity.level + 1
                        child.parent_id = entity.entity_id
                        child.parent_name = entity.name
                        results.append(child)
            else:
                tbody = table.find("tbody")
                if tbody:
                    for tr in tbody.find_all("tr", recursive=False):
                        inferred_level = self._infer_level_from_tr(tr)
                        child = self._parse_tr_tl(
                            tr=tr,
                            explicit_level=inferred_level,
                            last_visible_pop_idx=last_visible_pop_idx,
                            last_visible_date=last_visible_date,
                            default_last_census_year=last_year_from_visible,
                            country_code=entity.country_code,
                            base_url=self._base_for_urljoin(entity.url),
                        )
                        if not child:
                            continue
                        if child.level is None:
                            if lowest_level is not None:
                                child.level = lowest_level
                            elif entity.level is not None:
                                child.level = entity.level + 1
                        child.parent_id = entity.entity_id
                        child.parent_name = entity.name
                        results.append(child)

        # Si se pidió lowest_level, asegura que ningún hijo quede con None
        if lowest_level is not None:
            for c in results:
                if c.level is None:
                    c.level = lowest_level

        if target_level is None:
            return results

        # BFS para bajar más niveles
        if entity.level is None:
            return [e for e in results if e.level == target_level]

        if target_level <= entity.level:
            return []

        frontier = [e for e in results if (e.level or -1) > entity.level and (e.level or -1) <= target_level]
        all_found = list(frontier)

        while frontier and max((e.level or -1) for e in all_found) < target_level:
            nxt: List[Entity] = []
            for node in frontier:
                if not node.url:
                    continue
                kids = self.get_children(node, target_level=None, lowest_level=lowest_level)
                for c in kids:
                    if c.parent_id is None:
                        c.parent_id, c.parent_name = node.entity_id, node.name
                    if c.level is None and node.level is not None:
                        c.level = node.level + 1
                all_found.extend(kids)
                nxt.extend(kids)
            frontier = [e for e in nxt if (e.level or -1) < target_level]

        return [e for e in all_found if e.level == target_level]

    # ---------- helpers ----------
    def _get(self, url: str) -> str:
        """
        Descarga HTML con cabeceras/Referer y reintentos básicos.
        """
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"
        self._session.headers["Referer"] = referer

        attempts = 2
        last_exc = None
        for i in range(attempts):
            try:
                resp = self._session.get(url, timeout=30)
                if resp.status_code == 403:
                    raise requests.HTTPError("403 Forbidden (posible bloqueo).")
                resp.raise_for_status()
                text = resp.text
                if "enable JavaScript" in text and "Cloudflare" in text:
                    raise requests.HTTPError("Página protegida por JS/Cloudflare.")
                return text
            except Exception as exc:
                last_exc = exc
                if self.debug:
                    print(f"[_get] intento {i+1}/{attempts} error: {exc}")
                time.sleep(0.8)
        raise last_exc if last_exc else RuntimeError("Fallo al descargar.")

    def _base_for_urljoin(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def _level_from_tbody_class(self, tbody: Tag) -> Optional[int]:
        for cls in (tbody.get("class") or []):
            if cls in self.TBODY_LEVEL_MAP:
                return self.TBODY_LEVEL_MAP[cls]
        return None

    def _infer_level_from_tr(self, tr: Tag) -> Optional[int]:
        onclick = tr.get("onclick", "") or ""
        m = re.search(r"adm(\d+)", onclick)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None

    def _detect_last_visible_pop_column(self, table: Tag) -> Tuple[int, Optional[str]]:
        """
        Devuelve:
          - índice lógico (0-based) entre celdas rpop de la última columna visible
          - fecha "YYYY-MM-DD" de esa columna (si se pudo leer de thead).
        """
        thead = table.find("thead")
        if not thead:
            return -1, None

        pop_ths = [th for th in thead.find_all("th") if "rpop" in (th.get("class") or [])]
        last_visible_idx = -1
        last_date = None
        logical_idx = -1

        for th in pop_ths:
            logical_idx += 1
            style = (th.get("style") or "").replace(" ", "").lower()
            if "display:table-cell" in style:
                last_visible_idx = logical_idx
                last_date = th.get("data-coldate") or last_date

        if last_visible_idx == -1 and pop_ths:
            last_visible_idx = len(pop_ths) - 1
            last_date = pop_ths[-1].get("data-coldate") or last_date

        return last_visible_idx, last_date

    def _parse_tr_tl(
        self,
        tr: Tag,
        explicit_level: Optional[int],
        last_visible_pop_idx: int,
        last_visible_date: Optional[str],
        default_last_census_year: Optional[int],
        country_code: str,
        base_url: str,
    ) -> Optional[Entity]:
        tds = tr.find_all("td", recursive=False)
        if not tds:
            return None

        main_td = next((td for td in tds if "rname" in (td.get("class") or [])), None)
        if not main_td:
            return None

        name = self._extract_name(main_td)

        raw_id = main_td.get("id", "")
        entity_id = raw_id[1:] if raw_id.startswith("i") else (raw_id or None)
        if not entity_id:
            onclick = tr.get("onclick", "") or main_td.get("onclick", "")
            m = re.search(r"sym\('([^']+)'\)", onclick) or re.search(r"symArea\('([^']+)'", onclick)
            if m:
                entity_id = m.group(1)
        if not entity_id:
            return None

        status_td = tr.find("td", class_="rstatus")
        entity_type = status_td.get_text(" ", strip=True) if status_td else None

        area_km2 = self._safe_float(main_td.get("data-area"))
        density = self._safe_float(main_td.get("data-density"))

        pop_latest = self._extract_last_visible_population(tr, last_visible_pop_idx)

        url = None
        sc_td = tr.find("td", class_="sc")
        if sc_td:
            a = sc_td.find("a", href=True)
            if a and a["href"]:
                url = urljoin(base_url, a["href"])

        level = explicit_level
        lcy = self._year_from_date(last_visible_date) or default_last_census_year

        return Entity(
            entity_id=entity_id,
            name=name,
            level=level,
            entity_type=entity_type,
            area_km2=area_km2,
            density=density,
            pop_latest=pop_latest,
            pop_latest_date=last_visible_date,
            last_census_year=lcy,
            parent_id=None,
            parent_name=None,
            country_code=country_code,
            url=url,
        )

    def _parse_tr_ts(
        self,
        tr: Tag,
        last_visible_pop_idx: int,
        last_visible_date: Optional[str],
        default_last_census_year: Optional[int],
        country_code: str,
        base_url: str,
        has_radm: bool,
    ) -> Optional[Entity]:
        tds = tr.find_all("td", recursive=False)
        if not tds:
            return None

        main_td = next((td for td in tds if "rname" in (td.get("class") or [])), None)
        if not main_td:
            return None

        name = self._extract_name(main_td)

        raw_id = main_td.get("id", "")
        entity_id = raw_id[1:] if raw_id.startswith("i") else (raw_id or None)
        if not entity_id:
            onclick = tr.get("onclick", "") or main_td.get("onclick", "")
            m = re.search(r"sym\('([^']+)'\)", onclick)
            if m:
                entity_id = m.group(1)
        if not entity_id:
            return None

        status_td = tr.find("td", class_="rstatus")
        entity_type = status_td.get_text(" ", strip=True) if status_td else None

        area_km2 = self._safe_float(main_td.get("data-area"))
        density = self._safe_float(main_td.get("data-density"))
        pop_latest = self._extract_last_visible_population(tr, last_visible_pop_idx)

        url = None
        sc_td = tr.find("td", class_="sc")
        if sc_td:
            a = sc_td.find("a", href=True)
            if a and a["href"]:
                url = urljoin(base_url, a["href"])

        # En TS normalmente no hay admX en onclick.
        # level=None aquí; lo fijamos fuera (scrape_table/get_children) usando lowest_level.
        level = None
        lcy = self._year_from_date(last_visible_date) or default_last_census_year

        return Entity(
            entity_id=entity_id,
            name=name,
            level=level,
            entity_type=entity_type,
            area_km2=area_km2,
            density=density,
            pop_latest=pop_latest,
            pop_latest_date=last_visible_date,
            last_census_year=lcy,
            parent_id=None,
            parent_name=None,
            country_code=country_code,
            url=url,
        )

    # --------- util parsing ----------
    def _extract_name(self, td: Tag) -> str:
        span = td.find("span", attrs={"itemprop": "name"})
        if span and span.get_text(strip=True):
            return span.get_text(strip=True)
        spans = td.find_all("span", attrs={"itemprop": "name"})
        if spans:
            return spans[0].get_text(strip=True)
        return td.get_text(" ", strip=True)

    def _extract_last_visible_population(self, tr: Tag, last_visible_pop_idx: int) -> Optional[int]:
        pops = [td for td in tr.find_all("td", recursive=False) if "rpop" in (td.get("class") or [])]
        if not pops:
            return None
        idx = last_visible_pop_idx if (0 <= last_visible_pop_idx < len(pops)) else len(pops) - 1
        raw = pops[idx].get_text(strip=True)
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None

    def _safe_float(self, s: Optional[str]) -> Optional[float]:
        if s is None:
            return None
        try:
            return float(s.replace(",", "."))
        except Exception:
            return None

    def _year_from_date(self, s: Optional[str]) -> Optional[int]:
        if not s:
            return None
        try:
            return _dt.date.fromisoformat(s[:10]).year
        except Exception:
            m = re.search(r"(\d{4})", s)
            return int(m.group(1)) if m else None

    # === NUEVO: crawler jerárquico adaptativo ===
    def crawl_hierarchy(
        self,
        root_url: str,
        country_code: str,
        *,
        target_level: int = 3,
        prefer_table: Literal["auto", "ts", "tl"] = "auto",
        lowest_level: int = 1,
        debug: bool = None,
    ) -> List[Entity]:
        """
        Carga la página raíz (normalmente /<country>/admin/) y recorre hacia abajo
        hasta target_level usando los enlaces "→" de cada entidad.
        - Deduplica por Entity.pk
        - Respeta lowest_level para filas sin nivel inferible
        - Funciona con páginas #tl y #ts (municipios/comunas)
        """
        if debug is None:
            debug = self.debug

        # 1) nivel raíz
        root_entities = self.scrape_table(
            url=root_url,
            country_code=country_code,
            prefer_table=prefer_table,
            lowest_level=lowest_level,
        )
        if debug:
            print(f"[crawl] raíz: {root_url} -> {len(root_entities)} entidades")

        # 2) si ya alcanza el target_level, devolvemos todo
        seen: Dict[str, Entity] = {}
        queue: deque[Entity] = deque()

        for e in root_entities:
            # Normaliza niveles desconocidos en raíz
            if e.level is None:
                e.level = lowest_level
            seen[e.pk] = e
            queue.append(e)

        # 3) BFS descendente hasta target_level
        self._bfs_descend(queue, seen, target_level, lowest_level, debug)

        # 4) filtra si quieres “solo” el nivel objetivo; si no, devuelve todo el árbol
        return list(seen.values())

    def _bfs_descend(
        self,
        queue: "deque[Entity]",
        seen: Dict[str, Entity],
        target_level: int,
        lowest_level: int,
        debug: bool,
    ) -> None:
        """
        Recorre descendiendo hijos con get_children(...) hasta target_level.
        Deduplica por pk y actualiza parent_id/parent_name si aparecen.
        """
        while queue:
            parent = queue.popleft()

            # si el padre ya es del nivel objetivo, no seguimos desde él
            if parent.level is not None and parent.level >= target_level:
                continue

            if not parent.url:
                continue

            try:
                children = self.get_children(
                    parent,
                    target_level=None,   # traemos todos los hijos directos de esa página
                    lowest_level=lowest_level,
                )
            except Exception as exc:
                if debug:
                    print(f"[crawl] get_children fallo en {parent.name} ({parent.url}): {exc}")
                continue

            if debug:
                print(f"[crawl] {parent.name} -> {len(children)} hijos")

            for child in children:
                # Asegura nivel mínimo
                if child.level is None:
                    child.level = (parent.level + 1) if (parent.level is not None) else lowest_level

                # Evita “subir” por enlaces raros
                if parent.level is not None and child.level is not None:
                    if child.level <= parent.level:
                        # corrige si la página “ts” no marcó bien el nivel
                        child.level = parent.level + 1

                # Deduplicación y merge suave
                pk = child.pk
                if pk in seen:
                    # preferimos conservar parent_id si estaba vacío
                    old = seen[pk]
                    if old.parent_id is None and child.parent_id is not None:
                        old.parent_id = child.parent_id
                        old.parent_name = child.parent_name
                    # si el viejo no tenía nivel y el nuevo sí, actualizamos
                    if old.level is None and child.level is not None:
                        old.level = child.level
                    # si el viejo no tenía url y el nuevo sí, actualizamos
                    if not old.url and child.url:
                        old.url = child.url
                else:
                    seen[pk] = child
                    # Solo seguimos bajando si aún no llegamos al target_level
                    if child.level is None or child.level < target_level:
                        queue.append(child)

# ==============================
# Ejemplo de uso rápido
# ==============================
# if __name__ == "__main__":
#     scraper = CityPopulationScraper(debug=True, parser="lxml")
#
#     # --- Andalucía: preferir MUNICIPIOS (#ts)
#     url_and = "https://www.citypopulation.de/en/spain/andalucia/"
#     municipios_flat = scraper.scrape_table(url_and, country_code="ES", prefer_table="ts")
#     print("Filas municipios (ts):", len(municipios_flat), "ej:", municipios_flat[:1])
#
#     # Si quieres forzar PROVINCIAS (#tl) en la misma página:
#     provincias = scraper.scrape_table(url_and, country_code="ES", prefer_table="tl")
#     print("Filas provincias (tl):", len(provincias), "ej:", provincias[:1])
#
#     # --- Obtener hijos con niveles correctos:
#     andalucia = Entity(
#         entity_id="iAND", name="Andalucía", level=1, entity_type="Autonomous Community",
#         area_km2=None, density=None, pop_latest=None, pop_latest_date=None,
#         last_census_year=None, parent_id=None, parent_name=None,
#         country_code="ES", url=url_and
#     )
#     hijos = scraper.get_children(andalucia)  # detecta #ts y pone level=3
#     print("Hijos de Andalucía (esperado nivel 3):", {e.level for e in hijos})