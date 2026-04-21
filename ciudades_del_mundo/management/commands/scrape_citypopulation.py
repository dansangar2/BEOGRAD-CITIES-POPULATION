# ciudades_del_mundo/management/commands/scrape_citypopulation.py
import json
import re
import time
import unicodedata
from datetime import datetime
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum, Max

import importlib
import pkgutil
from ciudades_del_mundo.models import AdminArea
import ciudades_del_mundo.subdivisions as subdivisions_pkg


DEFAULT_BASE_URL = "https://www.citypopulation.de/en"

# -----------------------------
# PRESETS dinámicos
# -----------------------------

# Se rellenan dinámicamente a partir de todos los módulos en
# ciudades_del_mundo.subdivisions.* que tengan:
#   - DIVISIONS: dict con presets de subdivisión
#   - CITIES: lista o dict opcional con reglas de ciudades fusionadas
COUNTRY_PRESETS: dict[str, dict] = {}
MAKE_CITIES: dict[str, list[dict]] = {}

# 1) Recorrer todos los módulos dentro de ciudades_del_mundo.subdivisions
for module_info in pkgutil.iter_modules(subdivisions_pkg.__path__):
    mod_name = module_info.name  # p.ej. "spain", "france", "algeria", "morocco"
    full_name = f"{subdivisions_pkg.__name__}.{mod_name}"
    try:
        mod = importlib.import_module(full_name)
    except Exception:
        # Si un módulo falla al importar, lo ignoramos silenciosamente.
        continue

    # --- DIVISIONS ---
    divs = getattr(mod, "DIVISIONS", None)
    if isinstance(divs, dict):
        # Formato nuevo:
        #   en algeria.py: DIVISIONS = { "subdivision": {...} }
        # ⇒ COUNTRY_PRESETS["algeria"] = DIVISIONS
        COUNTRY_PRESETS[mod_name] = divs

    # --- CITIES (opcional) ---
    cities_cfg = getattr(mod, "CITIES", None)

    # Formato nuevo: CITIES = [ {...}, {...} ]
    if isinstance(cities_cfg, list):
        MAKE_CITIES[mod_name] = list(cities_cfg)

    # Compat con formato antiguo:
    #   CITIES = { "morocco": [ {...}, ... ], "spain": [ ... ] }
    elif isinstance(cities_cfg, dict):
        for country_code, city_list in cities_cfg.items():
            if not city_list:
                continue
            MAKE_CITIES.setdefault(country_code, []).extend(city_list)


# -----------------------------
# Helpers de parsing/limpieza
# -----------------------------

def reset_country_adminareas(country_code: str):
    """
    Elimina todos los AdminArea de un país antes de volver a poblar.
    """
    AdminArea.objects.filter(country_code=country_code).delete()


def _clean_int(s: str):
    if s is None:
        return None
    s = re.sub(r"[^\d\-]", "", s)
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _clean_decimal(s: str):
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def _clean_text(s: str):
    return re.sub(r"\s+", " ", (s or "")).strip()


def _norm_key(s: str) -> str:
    s = _clean_text(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("-", " ").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


def _slugify_citypop_name(name: str) -> str:
    s = _norm_key(name)
    s = s.replace(" ", "_").replace("'", "_")
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


def _title_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", " ").replace("-", " ").split())


def _singularize(title: str) -> str:
    title = _clean_text(title)
    irregular = {
        # Europa (FR/ES/EN/DE)
        "Arrondissements": "Arrondissement",
        "Arrondissementen": "Arrondissement",
        "Communes": "Commune",
        "Municipalities": "Municipality",
        "Municipalités": "Municipalité",
        "Municipios": "Municipio",
        "Municipis": "Municipi",
        "Provinces": "Province",
        "Provincias": "Provincia",
        "Départements": "Département",
        "Departments": "Department",
        "Regions": "Region",
        "Régions": "Région",
        "Counties": "County",
        "Districts": "District",
        "Cantons": "Canton",
        "Parishes": "Parish",
        "Comarcas": "Comarca",
        "Islands": "Island",
        "Islas": "Isla",
        "Autonomous Communities": "Autonomous Community",
        "Autonomous Regions": "Autonomous Region",
        "States": "State",
        "Länder": "Land",
        # África / Asia
        "Governorates": "Governorate",
        "Wilayas": "Wilaya",
        "Woredas": "Woreda",
        "Subprefectures": "Subprefecture",
        "Prefectures": "Prefecture",
        "Emirates": "Emirate",
        "Oblasts": "Oblast",
        "Rayons": "Rayon",
        "Krais": "Krai",
        "Raions": "Raion",
        "Municipalities & Towns": "Municipality",
        "Cities": "City",
        "Towns": "Town",
        "Villages": "Village",
    }
    return irregular.get(title, title[:-1] if title.endswith("s") else title)


def _clean_entity_type_text(s: str | None) -> str | None:
    if not s:
        return None
    s = _clean_text(s)
    # Eliminar prefijos de "contenido"
    s = re.sub(
        r"^(Contents?|Contenido|Contenidos?|Contenu|Inhalt|Conte[uú]do|[ÍI]ndice|Indice|Sumario)\s*[:•-]?\s*",
        "",
        s,
        flags=re.I,
    )
    # Si aún queda un "•", quedarnos con lo de la derecha
    if "•" in s:
        s = s.split("•")[-1].strip()
    return s


def _round4(x: Decimal | None):
    if x is None:
        return None
    try:
        return Decimal(x).quantize(Decimal("0.0001"))
    except Exception:
        return x


def fetch_html(url: str) -> BeautifulSoup:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CityPopScraper/1.5; +https://example.org)",
        "Accept-Language": "en,es;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_latest_dates_from_header(table_soup: BeautifulSoup):
    pop_latest_date = None
    last_census_year = None
    ths = table_soup.select("thead th.rpop")
    if ths:
        for th in reversed(ths):
            cold = th.get("data-coldate")
            if cold:
                try:
                    pop_latest_date = datetime.strptime(cold, "%Y-%m-%d").date()
                    break
                except Exception:
                    pass
        for th in reversed(ths):
            colhead = th.get("data-colhead") or ""
            if colhead.startswith("C "):
                m = re.search(r"(\d{4})", colhead)
                if m:
                    last_census_year = int(m.group(1))
                    break
    return pop_latest_date, last_census_year


# NUEVO: detectar unidad del área (p.ej. "A (hect)" → factor 0.01 para convertir a km²)
def parse_area_factor_from_table(table_soup: BeautifulSoup) -> Decimal:
    """
    Devuelve un factor multiplicativo para convertir el área a km².

    Casos:
    - "hect", "ha"   => hectáreas → km²  (x 0.01)
    - "m²", "m2"     => m² → km²         (x 1e-6)
    - "km²", "km2"   => ya km²           (x 1)
    - sin unidad     => asumimos km²     (x 1)
    """
    th = table_soup.select_one("thead th.rarea")
    if not th:
        return Decimal("1")

    unit_el = th.select_one("span.unit")
    unit_txt = _clean_text(unit_el.get_text(" ", strip=True) if unit_el else th.get_text(" ", strip=True)).lower()

    if "hect" in unit_txt or re.search(r"\bha\b", unit_txt):
        return Decimal("0.01")

    if "m²" in unit_txt or "m2" in unit_txt:
        return Decimal("0.000001")

    # km² / km2 / "km"
    if "km" in unit_txt:
        return Decimal("1")

    return Decimal("1")


def parse_totals_from_tfoot(table_soup: BeautifulSoup, area_factor: Decimal = Decimal("1")):
    area_km2 = None
    density = None
    pop_latest = None
    tf = table_soup.select_one("tfoot")
    if not tf:
        return area_km2, density, pop_latest

    cell_area = tf.select_one(".rarea")
    if cell_area:
        raw = _clean_decimal(cell_area.get_text(" ", strip=True))
        if raw is not None:
            try:
                area_km2 = raw * Decimal(area_factor)
            except Exception:
                area_km2 = raw

    cell_dens = tf.select_one(".rdens")
    if cell_dens:
        density = _clean_decimal(cell_dens.get_text(" ", strip=True))

    cells_pop = tf.select(".rpop")
    if cells_pop:
        # Tomar la última celda no vacía
        for td in reversed(cells_pop):
            pop_latest = _clean_int(td.get_text(" ", strip=True))
            if pop_latest is not None:
                break

    return area_km2, density, pop_latest


def parse_last_pop_from_row(tr):
    rpop_tds = tr.select("td.rpop")
    if not rpop_tds:
        return None
    # Si la última está vacía, tomar la anterior no vacía
    for td in reversed(rpop_tds):
        val = _clean_int(td.get_text(" ", strip=True))
        if val is not None:
            return val
    return None


def parse_area_from_row(tr, area_factor: Decimal = Decimal("1")):
    # 1) Celda .rarea si existe (ojo: algunas tablas están en hectáreas)
    td = tr.select_one("td.rarea")
    if td:
        raw = _clean_decimal(td.get_text(" ", strip=True))
        if raw is None:
            return None
        try:
            return raw * Decimal(area_factor)
        except Exception:
            return raw

    # 2) Citysection/arrondissement: <td class="noviz">2400</td> => dividir entre 100
    td_noviz = tr.select_one("td.noviz")
    if td_noviz:
        val = _clean_decimal(td_noviz.get_text(" ", strip=True))
        if val is not None:
            try:
                return (val / Decimal("100"))
            except Exception:
                pass

    # 3) data-* en rname (normalmente ya km²)
    td_rname = tr.select_one("td.rname")
    if td_rname and td_rname.has_attr("data-area"):
        return _clean_decimal(td_rname.get("data-area"))

    return None


def parse_density_from_row(tr):
    td = tr.select_one("td.rdens")
    if td:
        return _clean_decimal(td.get_text(" ", strip=True))
    td_rname = tr.select_one("td.rname")
    if td_rname and td_rname.has_attr("data-density"):
        return _clean_decimal(td_rname.get("data-density"))
    return None


def compute_density(pop: int | None, area: Decimal | None):
    if pop is None or area in (None, Decimal("0")):
        return None
    try:
        return (Decimal(pop) / area)
    except (DivisionByZero, InvalidOperation):
        return None


def extract_name_from_td(td_rname):
    spans = td_rname.select('span[itemprop="name"]')
    if spans:
        return _clean_text(spans[0].get_text(" ", strip=True))
    return _clean_text(td_rname.get_text(" ", strip=True))


def code_from_td(td_with_id):
    tid = td_with_id.get("id") or ""
    if tid.startswith("i"):
        return tid[1:]
    return tid or None


# NUEVO: extraer código numérico (p.ej. "05") del href "…/05__béni_mellal_khénifra/"
def code_from_href(href: str | None) -> str | None:
    if not href:
        return None
    # Tomar el último segmento con "__" y quedarnos con el prefijo numérico
    seg = href.strip("/").split("/")[-1]
    if "__" in seg:
        pref = seg.split("__", 1)[0]
        if re.fullmatch(r"\d{1,3}", pref):
            return pref
    # A veces el patrón puede venir con "?". Limpiamos y reintentamos.
    seg = seg.split("?", 1)[0]
    if "__" in seg:
        pref = seg.split("__", 1)[0]
        if re.fullmatch(r"\d{1,3}", pref):
            return pref
    return None


def split_current_and_parent_slug_from_href(href: str):
    if not href:
        return None, None
    parts = href.strip("/").split("/")
    idx = -1
    for i, p in enumerate(parts):
        if "__" in p:
            idx = i
            break
    if idx == -1:
        return None, None
    seg = parts[idx]
    cur_slug = seg.split("__", 1)[1].lower() if "__" in seg else None
    parent_slug = parts[idx - 1].lower() if idx - 1 >= 0 else None
    return cur_slug, parent_slug


# -----------------------------
# Scraper
# -----------------------------
class CityPopScraper:
    def __init__(self, country_code: str, base_url: str, sleeper: float = 1.0):
        self.country_code = country_code
        self.base_url = base_url.rstrip("/")
        self.sleep = sleeper

        self.slug_to_code_by_level = {}   # {level: {slug: code}}
        self.name_to_code_by_level = {}   # {level: {name_key: code}}
        self.pk_by_level_code = {i: {} for i in range(0, 10)}  # {level: {code: pk}}

        self.max_admin_level_seen = 0

        # Excepciones: (country, slug, target_level) -> función que devuelve parent_code
        self.exception_parent_resolver = {
            ("spain", "ceuta", 3): self._resolve_parent_ceuta_melilla,
            ("spain", "melilla", 3): self._resolve_parent_ceuta_melilla,
            ("france", "paris", 4): self._resolve_parent_paris,
        }

        # --- Soporte para MAKE_CITIES ---
        raw_cfg = MAKE_CITIES.get(country_code) or []

        # Queremos que SIEMPRE sea lista
        if isinstance(raw_cfg, dict):
            # Compat antigua: {"morocco": [ ... ]}
            if country_code in raw_cfg:
                raw_cfg = raw_cfg[country_code]
            else:
                raise ValueError(
                    f"MAKE_CITIES['{country_code}'] debe ser una lista de configs, "
                    f"pero es un dict sin clave '{country_code}'."
                )

        if not isinstance(raw_cfg, list):
            raise ValueError(
                f"MAKE_CITIES['{country_code}'] debe ser una lista de configs de ciudad, "
                f"no {type(raw_cfg).__name__}."
            )

        self.city_merge_cfg: list[dict] = raw_cfg

        # parent_code -> [reglas que se aplican bajo ese padre]
        self._city_merge_rules_by_parent: dict[str, list[dict]] = {}
        # (city_name, parent_code, level_distritos) -> buffer de métricas agregadas
        self._city_merge_buffer: dict[tuple[str, str | None, int], dict] = {}

        # Para saber qué reglas se han utilizado realmente
        self._city_merge_cfg_used: list[bool] = [False] * len(self.city_merge_cfg)
        self._city_merge_cfg_index: dict[int, int] = {
            id(cfg): idx for idx, cfg in enumerate(self.city_merge_cfg)
        }

        # Para validación de communes en cada cfg
        self._city_merge_cfg_expected_communes: list[set[str]] = []
        self._city_merge_cfg_matched_communes: list[set[str]] = []

        # Nivel base n extraído de "from": {n: ["..."]}
        # (o None si no está en ese formato)
        self._city_merge_cfg_from_level: list[int | None] = []

        for idx, cfg in enumerate(self.city_merge_cfg):
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"Config de MAKE_CITIES[{country_code}][{idx}] debe ser dict, "
                    f"no {type(cfg).__name__}."
                )
            if "city" not in cfg and "id" not in cfg:
                raise ValueError(
                    f"Config de MAKE_CITIES[{country_code}][{idx}] debe tener "
                    f"al menos 'city' o 'id'. Config: {cfg}"
                )

            # Preparar conjunto de comunas esperadas (normalizadas)
            communes = cfg.get("communes") or []
            if isinstance(communes, str):
                communes = [communes]
            expected_norm = {_norm_key(c) for c in communes if c}
            self._city_merge_cfg_expected_communes.append(expected_norm)
            self._city_merge_cfg_matched_communes.append(set())

            # Extraer n de from: {n: ["..."]} si existe
            from_cfg = cfg.get("from")
            base_level = None
            if isinstance(from_cfg, dict) and from_cfg:
                niveles = []
                for k in from_cfg.keys():
                    try:
                        niveles.append(int(k))
                    except (TypeError, ValueError):
                        continue
                if niveles:
                    base_level = min(niveles)
            self._city_merge_cfg_from_level.append(base_level)

    # ---------- Helpers MAKE_CITIES ----------

    def finalize_city_merges(self) -> int:
        """
        Crea AdminArea agregados para las ciudades definidas en MAKE_CITIES,
        usando los buffers construidos en _maybe_buffer_city_district.

        Comportamiento según 'id' en la config:

        - Si 'id' es un string: se crea un solo AdminArea en el MISMO nivel
          que los distritos/unidades agregadas (comportamiento clásico).

        - Si 'id' es una lista de strings y la regla tiene 'from' con un único
          nivel n (p.ej. {2: ["Wien"]}), se crean tantos AdminArea como ids:

              ids[0] -> nivel n + 1
              ids[1] -> nivel n + 2
              ids[2] -> nivel n + 3
              ...

          Todos con las mismas métricas agregadas. Cada uno cuelga
          jerárquicamente del anterior (cadena).
        """
        created = 0
        errores: list[str] = []

        for (city_name, parent_code, district_level), data in self._city_merge_buffer.items():
            area_km2 = data["area_km2"] or None
            pop_latest = data["pop_latest"] or None
            pop_latest_date = data["pop_latest_date"]
            last_census_year = data["last_census_year"]
            url = data.get("url")
            entity_type = data["entity_type"]
            cfg_idx = data.get("cfg_idx")

            if area_km2 is not None:
                area_km2 = _round4(area_km2)

            density = compute_density(pop_latest, area_km2) if area_km2 is not None else None

            cfg = self.city_merge_cfg[cfg_idx] if cfg_idx is not None else {}
            raw_ids = cfg.get("id") or cfg.get("city_code")

            # Normalizar ids
            if isinstance(raw_ids, str):
                ids_list = [raw_ids]
            elif isinstance(raw_ids, (list, tuple)):
                ids_list = [str(x) for x in raw_ids if str(x)]
            else:
                ids_list = []

            if not ids_list:
                # Fallback: generamos un id a partir del nombre
                slug = _slugify_citypop_name(city_name)
                ids_list = [f"city_{slug}"[:32]]

            if cfg_idx is not None:
                self._city_merge_cfg_used[cfg_idx] = True
                from_level = self._city_merge_cfg_from_level[cfg_idx]
            else:
                from_level = None

            # ---- Caso 1: comportamiento clásico (un único nivel) ----
            if len(ids_list) == 1 or from_level is None:
                city_code = ids_list[0]
                obj, was_created = self.save_area(
                    code=city_code,
                    name=city_name,
                    level=district_level,   # mismo nivel que los distritos
                    entity_type=entity_type,
                    parent_code=parent_code,
                    url=url,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=pop_latest,
                    pop_latest_date=pop_latest_date,
                    last_census_year=last_census_year,
                    set_parent=True,
                )
                if was_created:
                    created += 1
                continue

            # ---- Caso 2: lista de ids + from: {n: ...} → niveles n+1, n+2, ... ----
            chain_parent_code = parent_code  # primer nivel cuelga del padre original (provincia / Land)
            for offset, city_code in enumerate(ids_list, start=1):
                level = from_level + offset  # n+1, n+2, ...
                obj, was_created = self.save_area(
                    code=city_code,
                    name=city_name,
                    level=level,
                    entity_type=entity_type,
                    parent_code=chain_parent_code,
                    url=url,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=pop_latest,
                    pop_latest_date=pop_latest_date,
                    last_census_year=last_census_year,
                    set_parent=True,
                )
                if was_created:
                    created += 1
                # el siguiente nivel cuelga del código recién creado
                chain_parent_code = city_code

        # 2) Comprobar comunas faltantes por cada config de MAKE_CITIES
        for idx, cfg in enumerate(self.city_merge_cfg):
            expected = self._city_merge_cfg_expected_communes[idx]
            if not expected:
                # Esta regla no ha declarado comunas concretas → no hay comprobación estricta.
                continue

            matched = self._city_merge_cfg_matched_communes[idx]
            missing = expected - matched
            if not missing:
                continue

            label = cfg.get("city") or cfg.get("id") or f"config #{idx}"
            missing_list = ", ".join(sorted(missing))
            errores.append(f"{label}: faltan comunas [{missing_list}]")

        if errores:
            joined = "; ".join(errores)
            raise ValueError(
                f"Las reglas MAKE_CITIES para '{self.country_code}' no han encontrado todas "
                f"las comunas declaradas en 'communes'. Detalle: {joined}"
            )

        return created

    def _maybe_register_parent_for_city_merge(self, name: str, code: str, level: int):
        """
        Si este AdminArea coincide con algún 'from' en MAKE_CITIES,
        lo registramos como posible padre para esas reglas.

        Soporta:
        - "from": ["Nombre X", "Nombre Y"]
        - "from": "Nombre X"
        - "from": {2: ["Tanger - Assilah"], 3: ["Otra cosa"]}   ← nuevo
        """
        if not self.city_merge_cfg:
            return

        name_clean = _clean_text(name)
        name_norm = _norm_key(name)

        for cfg in self.city_merge_cfg:
            from_cfg = cfg.get("from")
            if not from_cfg:
                continue

            # --- from como dict {nivel: [nombres]} ---
            if isinstance(from_cfg, dict):
                for lvl_key, names in from_cfg.items():
                    try:
                        lvl_int = int(lvl_key)
                    except (TypeError, ValueError):
                        continue
                    # Solo aplica si el nivel actual coincide
                    if lvl_int != level:
                        continue

                    if isinstance(names, str):
                        names = [names]

                    for raw in names:
                        raw = str(raw or "").strip()
                        if not raw:
                            continue
                        raw_clean = _clean_text(raw)
                        raw_norm = _norm_key(raw)

                        if (
                            name_clean == raw_clean
                            or name_norm == raw_norm
                            or name_norm.endswith(raw_norm)
                            or code == raw
                        ):
                            rules = self._city_merge_rules_by_parent.setdefault(code, [])
                            if cfg not in rules:
                                rules.append(cfg)

            # --- Compat: from como lista/string ---
            else:
                if isinstance(from_cfg, (list, tuple)):
                    names_iter = from_cfg
                else:
                    names_iter = [from_cfg]

                for raw in names_iter:
                    raw = str(raw or "").strip()
                    if not raw:
                        continue
                    raw_clean = _clean_text(raw)
                    raw_norm = _norm_key(raw)

                    if (
                        name_clean == raw_clean
                        or name_norm == raw_norm
                        or name_norm.endswith(raw_norm)
                        or code == raw
                    ):
                        rules = self._city_merge_rules_by_parent.setdefault(code, [])
                        if cfg not in rules:
                            rules.append(cfg)

    def _maybe_buffer_city_district(
        self,
        *,
        code: str,
        name: str,
        level: int,
        entity_type: str | None,
        parent_code: str | None,
        url: str | None,
        area_km2: Decimal | None,
        density: Decimal | None,
        pop_latest: int | None,
        pop_latest_date,
        last_census_year: int | None,
    ) -> bool:
        """
        Si este registro debe fusionarse en una ciudad:
        - Por tipo (district_types: Arrondissement, etc.)
        - O por nombre explícito (communes: lista de nombres exactos)

        Devuelve True si se ha tratado como parte de una ciudad y
        NO debe guardarse como AdminArea separado.
        """
        if not parent_code:
            return False

        rules_for_parent = self._city_merge_rules_by_parent.get(parent_code)
        if not rules_for_parent:
            return False

        handled = False
        name_norm = _norm_key(name)
        etype_norm = _norm_key(entity_type) if entity_type else ""

        for cfg in rules_for_parent:
            # Tipos de distrito (Arrondissement, etc.)
            dtypes = cfg.get("district_types") or cfg.get("districtType") or []
            if isinstance(dtypes, str):
                dtypes = [dtypes]

            # Lista explícita de comunas / unidades
            communes = cfg.get("communes") or []
            if isinstance(communes, str):
                communes = [communes]

            # ¿Coincide por tipo?
            type_match = False
            if dtypes and etype_norm:
                for dt in dtypes:
                    if etype_norm == _norm_key(dt):
                        type_match = True
                        break

            # ¿Coincide por nombre explícito?
            name_match = False
            matched_communes_for_cfg: set[str] = set()
            if communes:
                for cname in communes:
                    c_norm = _norm_key(cname)
                    if name_norm == c_norm:
                        name_match = True
                        matched_communes_for_cfg.add(c_norm)

            # Lógica:
            #  - si hay district_types: incluimos si (tipo coincide) OR (nombre listado)
            #  - si NO hay district_types: incluimos solo si el nombre está listado
            if dtypes:
                if not (type_match or name_match):
                    continue
            else:
                if not name_match:
                    continue

            handled = True

            # Marcar esta cfg como USADA y registrar comunas encontradas
            cfg_idx = self._city_merge_cfg_index.get(id(cfg))
            if cfg_idx is not None:
                self._city_merge_cfg_used[cfg_idx] = True
                if matched_communes_for_cfg:
                    self._city_merge_cfg_matched_communes[cfg_idx].update(matched_communes_for_cfg)

            city_name = cfg.get("city") or name
            new_type = cfg.get("new_type") or cfg.get("newType") or "City"

            key = (city_name, parent_code, level)
            buf = self._city_merge_buffer.setdefault(
                key,
                dict(
                    city_name=city_name,
                    parent_code=parent_code,
                    level=level,
                    entity_type=new_type,
                    url=url,
                    area_km2=Decimal("0"),
                    pop_latest=0,
                    pop_latest_date=None,
                    last_census_year=None,
                    cfg_idx=cfg_idx,
                ),
            )

            # Acumular área
            if area_km2 is not None:
                try:
                    buf["area_km2"] = (buf["area_km2"] or Decimal("0")) + Decimal(area_km2)
                except Exception:
                    pass

            # Acumular población
            if pop_latest is not None:
                buf["pop_latest"] = (buf["pop_latest"] or 0) + int(pop_latest)

            # Fecha y censo más recientes
            if pop_latest_date and (
                buf["pop_latest_date"] is None or pop_latest_date > buf["pop_latest_date"]
            ):
                buf["pop_latest_date"] = pop_latest_date

            if last_census_year and (
                buf["last_census_year"] is None or last_census_year > buf["last_census_year"]
            ):
                buf["last_census_year"] = last_census_year

            # Preferimos una URL no vacía
            if url and not buf.get("url"):
                buf["url"] = url

        return handled

    # ---------- utilidades internas ----------

    def _pk(self, code: str) -> str:
        return f"{self.country_code}_{code}"

    def _index_name(self, level: int, name: str, code: str):
        d = self.name_to_code_by_level.setdefault(level, {})
        d[_clean_text(name)] = code
        d[_norm_key(name)] = code

    def _index_slug(self, level: int, name: str, href_slug: str | None, code: str):
        d = self.slug_to_code_by_level.setdefault(level, {})
        if href_slug:
            d[href_slug] = code
        d[_slugify_citypop_name(name)] = code

    def _is_country_row(self, name: str | None, code: str | None) -> bool:
        name_key = _norm_key(name or "")
        code_key = _norm_key(code or "")
        return name_key == self.country_code or code_key == self.country_code

    # --- NV0: país ---
    def ensure_country_area(self):
        code = self.country_code
        name = _title_from_slug(self.country_code)
        url = f"{self.base_url}/{self.country_code}/"
        pk = self._pk(code)

        existing = AdminArea.objects.filter(pk=pk).only("id").first()
        fields = dict(
            country_code=self.country_code,
            code=code,
            name=name,
            level=0,
            entity_type="Country",
            url=url,
            area_km2=None,
            density=None,
            pop_latest=None,
            pop_latest_date=None,
            last_census_year=None,
        )
        if existing:
            AdminArea.objects.filter(pk=pk).update(**fields)
            obj = AdminArea.objects.get(pk=pk)
        else:
            obj = AdminArea.objects.create(id=pk, parent_id=None, **fields)

        self.pk_by_level_code[0][code] = obj.id
        self._index_name(0, name, code)
        self._index_slug(0, name, None, code)

    def maybe_update_country_from_table(self, table_soup: BeautifulSoup, pop_latest_date, last_census_year):
        area_factor = parse_area_factor_from_table(table_soup)
        area_km2, density, pop_latest = parse_totals_from_tfoot(table_soup, area_factor=area_factor)
        if area_km2 is None and pop_latest is None and density is None:
            return
        pk = self._pk(self.country_code)
        updates = {}
        if area_km2 is not None:
            updates["area_km2"] = area_km2
        if pop_latest is not None:
            updates["pop_latest"] = pop_latest
        if density is not None:
            updates["density"] = _round4(density)
        if pop_latest_date:
            updates["pop_latest_date"] = pop_latest_date
        if last_census_year:
            updates["last_census_year"] = last_census_year
        if updates:
            AdminArea.objects.filter(pk=pk).update(**updates)

    def recompute_country_totals_from_db(self):
        qs = AdminArea.objects.filter(country_code=self.country_code, level=1)
        agg = qs.aggregate(
            sum_area=Sum("area_km2"),
            sum_pop=Sum("pop_latest"),
            latest_date=Max("pop_latest_date"),
            last_census=Max("last_census_year"),
        )
        updates = {}
        if agg.get("sum_area") is not None:
            updates["area_km2"] = agg["sum_area"]
        if agg.get("sum_pop") is not None:
            updates["pop_latest"] = agg["sum_pop"]
        if agg.get("sum_area") and agg.get("sum_pop"):
            try:
                updates["density"] = _round4(Decimal(agg["sum_pop"]) / Decimal(agg["sum_area"]))
            except (DivisionByZero, Exception):
                pass
        if agg.get("latest_date"):
            updates["pop_latest_date"] = agg["latest_date"]
        if agg.get("last_census"):
            updates["last_census_year"] = agg["last_census"]
        if updates:
            AdminArea.objects.filter(pk=self._pk(self.country_code)).update(**updates)

    # ---------- persistencia (upsert) ----------
    def save_area(
        self,
        *,
        code: str,
        name: str,
        level: int,
        entity_type: str | None = None,
        parent_code: str | None = None,
        url: str | None = None,
        area_km2: Decimal | None = None,
        density: Decimal | None = None,
        pop_latest: int | None = None,
        pop_latest_date: str | None = None,
        last_census_year: int | None = None,
        set_parent: bool = True,
    ):
        if not code:
            raise ValueError("save_area() requiere un 'code' no vacío")

        # 1) Registrar si este área es un posible 'from' de MAKE_CITIES
        self._maybe_register_parent_for_city_merge(name, code, level)

        # 2) Ver si este área debe fusionarse en una ciudad (no se guarda individualmente)
        if self._maybe_buffer_city_district(
            code=code,
            name=name,
            level=level,
            entity_type=entity_type,
            parent_code=parent_code,
            url=url,
            area_km2=area_km2,
            density=density,
            pop_latest=pop_latest,
            pop_latest_date=pop_latest_date,
            last_census_year=last_census_year,
        ):
            # No guardar en DB ni indexar; se usará solo en la ciudad agregada
            return None, False

        pk = self._pk(code)

        # Resolver parent_id si procede
        parent_id = None
        if set_parent:
            if parent_code:
                parent_id = self.pk_by_level_code.get(level - 1, {}).get(parent_code)
                if parent_id is None:
                    parent_obj = AdminArea.objects.filter(pk=self._pk(parent_code)).only("id").first()
                    if parent_obj:
                        parent_id = parent_obj.id
                        self.pk_by_level_code.setdefault(level - 1, {})[parent_code] = parent_id
            if level == 1 and parent_id is None:
                parent_id = self.pk_by_level_code.get(0, {}).get(self.country_code)

        # Redondear densidad a 4 decimales SIEMPRE
        density_rounded = _round4(density) if density is not None else None

        fields = dict(
            country_code=self.country_code,
            code=code,
            name=name,
            level=level,
            entity_type=entity_type,
            url=url,
            area_km2=area_km2,
            density=density_rounded,
            pop_latest=pop_latest,
            pop_latest_date=pop_latest_date,
            last_census_year=last_census_year,
        )

        existing = AdminArea.objects.filter(pk=pk).only("id", "parent_id").first()
        if existing:
            if set_parent:
                AdminArea.objects.filter(pk=pk).update(parent_id=parent_id, **fields)
            else:
                AdminArea.objects.filter(pk=pk).update(**fields)
            obj = AdminArea.objects.get(pk=pk)
            created = False
        else:
            obj = AdminArea.objects.create(id=pk, parent_id=(parent_id if set_parent else None), **fields)
            created = True

        self.pk_by_level_code.setdefault(level, {})[code] = obj.id
        self._index_name(level, name, code)
        self._index_slug(level, name, None, code)

        return obj, created

    def set_parent_only(self, level: int, code: str, parent_code: str | None):
        if not parent_code:
            return
        parent_id = self.pk_by_level_code.get(level - 1, {}).get(parent_code)
        if parent_id is None:
            parent_obj = AdminArea.objects.filter(pk=self._pk(parent_code)).only("id").first()
            if parent_obj:
                parent_id = parent_obj.id
                self.pk_by_level_code.setdefault(level - 1, {})[parent_code] = parent_id
        if parent_id:
            AdminArea.objects.filter(pk=self._pk(code)).update(parent_id=parent_id)

    # ---------- fallback para regiones completas desde /{country}/reg ----------
    def _parse_all_regions_from_reg(self, base_level: int):
        url = f"{self.base_url}/{self.country_code}/reg"
        soup = fetch_html(url)
        table = soup.select_one("section#adminareas table#tl")
        if not table:
            return 0

        pop_latest_date, last_census_year = parse_latest_dates_from_header(table)
        tbody = table.select_one("tbody.admin1")
        if not tbody:
            return 0

        saved = 0
        for tr in tbody.select("tr"):
            td_rname = tr.select_one("td.rname")
            td_status = tr.select_one("td.rstatus")
            td_sc = tr.select_one("td.sc a[itemprop='url']")
            if not (td_rname and td_status):
                continue

            details_href = td_sc.get("href") if td_sc else None
            code = code_from_td(td_rname) or code_from_href(details_href)
            name = extract_name_from_td(td_rname)

            if self._is_country_row(name, code):
                continue

            if not code:
                code = _slugify_citypop_name(name)

            entity_type = _clean_text(td_status.get_text(" ", strip=True))
            area_km2 = _clean_decimal(td_rname.get("data-area"))
            density = _clean_decimal(td_rname.get("data-density"))
            pop_latest = parse_last_pop_from_row(tr)
            full_url = urljoin(self.base_url + "/", details_href) if details_href else url

            cur_slug, _ = split_current_and_parent_slug_from_href(details_href or "")
            if not cur_slug:
                cur_slug = _slugify_citypop_name(name)

            self.save_area(
                code=code,
                name=name,
                level=base_level,
                entity_type=entity_type,
                parent_code=(self.country_code if base_level == 1 else None),
                url=full_url,
                area_km2=area_km2,
                density=density,
                pop_latest=pop_latest,
                pop_latest_date=pop_latest_date,
                last_census_year=last_census_year,
                set_parent=True,
            )
            self._index_slug(base_level, name, cur_slug, code)
            saved += 1

        if base_level == 1:
            self.maybe_update_country_from_table(table, pop_latest_date, last_census_year)

        self.max_admin_level_seen = max(self.max_admin_level_seen, base_level)
        time.sleep(self.sleep)
        return saved

    # ---------- parseo admin con cursor de padres ----------
    def scrape_admin_page(self, admin_path: str, base_level: int):
        admin_path = admin_path.strip("/")
        url = f"{self.base_url}/{self.country_code}/{admin_path}"
        soup = fetch_html(url)
        table = soup.select_one("section#adminareas table#tl, section#adminareas table#ts")
        if not table:
            raise CommandError(f"No se encontró <section id='adminareas'> con tabla #tl en {url}")

        pop_latest_date, last_census_year = parse_latest_dates_from_header(table)
        area_factor = parse_area_factor_from_table(table)

        used_region_fallback = False
        if admin_path.endswith("reg/admin"):
            tbodies_regions = table.select("tbody.admin1")
            count_regions = sum(len(tb.select("tr")) for tb in tbodies_regions)
            if count_regions < 2:
                got = self._parse_all_regions_from_reg(base_level)
                used_region_fallback = got > 0

        current_code_by_level = {}

        for tbody in table.select("tbody"):
            classes = tbody.get("class", [])
            k = None
            for c in classes:
                m = re.match(r"admin(\d+)", c)
                if m:
                    k = int(m.group(1))
                    break
            if k is None:
                continue

            target_level = base_level + (k - 1)

            # Incluso en fallback de reg/admin, si hay filas, usar SIEMPRE el id del td.rname
            if used_region_fallback and k == 1:
                for tr in tbody.select("tr"):
                    td_rname = tr.select_one("td.rname")
                    if not td_rname:
                        continue
                    code = code_from_td(td_rname)  # <- obligatorio desde id="iXX"
                    if not code:
                        continue  # si alguna fila no trae id, la saltamos para no duplicar
                    name = extract_name_from_td(td_rname)
                    current_code_by_level[target_level] = code
                    # opcional: indexar slug si hay enlace, pero el code SIEMPRE es el id
                    td_sc = tr.select_one("td.sc a[itemprop='url']")
                    cur_slug, _ = split_current_and_parent_slug_from_href(td_sc.get("href") if td_sc else "")
                    self._index_slug(base_level, name, cur_slug, code)
                continue

            for tr in tbody.select("tr"):
                td_rname = tr.select_one("td.rname")
                td_status = tr.select_one("td.rstatus")
                td_sc = tr.select_one("td.sc a[itemprop='url']")
                td_radm = tr.select_one("td.radm") or tr.select_one("td.radm.rarea")
                if not td_rname:
                    continue

                # Tomar SIEMPRE el code del id del <td.rname>
                code = code_from_td(td_rname)
                if not code:
                    # en asAdmin esperamos que siempre exista id; si no, se ignora la fila
                    continue

                name = extract_name_from_td(td_rname)
                if self._is_country_row(name, code):
                    continue

                raw_entity = _clean_text(td_status.get_text(" ", strip=True)) if td_status else None
                entity_type = _clean_entity_type_text(raw_entity) if raw_entity else None

                area_km2 = (
                    parse_area_from_row(tr, area_factor=area_factor)
                    or _clean_decimal(td_rname.get("data-area"))
                )
                density = parse_density_from_row(tr) or _clean_decimal(td_rname.get("data-density"))
                pop_latest = parse_last_pop_from_row(tr)
                if density is None:
                    density = compute_density(pop_latest, area_km2)

                details_href = td_sc.get("href") if td_sc else None
                full_url = urljoin(self.base_url + "/", details_href) if details_href else url

                # Solo usamos parent_slug para localizar el padre; el hijo mantiene code=id
                cur_slug, parent_slug = split_current_and_parent_slug_from_href(details_href or "")

                parent_code = None
                is_top_of_section = (k == 1 and base_level > 1)
                set_parent = not is_top_of_section

                if not is_top_of_section:
                    if parent_slug:
                        parent_map = self.slug_to_code_by_level.get(target_level - 1, {})
                        parent_code = parent_map.get(parent_slug)

                    if not parent_code and td_radm:
                        if td_radm.has_attr("data-admid"):
                            parent_code = str(td_radm["data-admid"])
                        else:
                            parent_name = _clean_text(td_radm.get_text(" ", strip=True))
                            parent_code = (
                                self.name_to_code_by_level.get(target_level - 1, {}).get(parent_name)
                                or self.name_to_code_by_level.get(target_level - 1, {}).get(_norm_key(parent_name))
                            )

                    if not parent_code:
                        parent_code = current_code_by_level.get(target_level - 1)

                    if target_level == 1 and not parent_code:
                        parent_code = self.country_code

                self.save_area(
                    code=code,
                    name=name,
                    level=target_level,
                    entity_type=entity_type,
                    parent_code=parent_code,
                    url=full_url,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=pop_latest,
                    pop_latest_date=pop_latest_date,
                    last_census_year=last_census_year,
                    set_parent=set_parent,
                )

                current_code_by_level[target_level] = code
                # indexar slug solo para lookup de padres; no afecta al code
                self._index_slug(target_level, name, cur_slug, code)

                self.max_admin_level_seen = max(self.max_admin_level_seen, target_level)

        if base_level == 1 and not used_region_fallback:
            self.maybe_update_country_from_table(table, pop_latest_date, last_census_year)

        time.sleep(self.sleep)

    # ---------- utilidades ultramar (Francia) ----------
    def _extract_table_h2_singular(self, soup: BeautifulSoup, section_id="adminareas") -> str | None:
        sec = soup.select_one(f"section#{section_id} h2")
        return _singularize(sec.get_text(" ", strip=True)) if sec else None

    def _ensure_upper_chain_and_link_child(
        self,
        child_level: int,
        child_code: str,
        child_name: str,
        url: str,
        area_km2=None,
        density=None,
        pop_latest=None,
        pop_latest_date=None,
        last_census_year=None,
    ):
        # Crea réplicas superiores hasta L1 solo si hacen falta. Métricas en L1.
        parent_for_child = None
        for L in range(child_level - 1, 0, -1):
            existing_code = (
                self.name_to_code_by_level.get(L, {}).get(_clean_text(child_name))
                or self.name_to_code_by_level.get(L, {}).get(_norm_key(child_name))
                or self.slug_to_code_by_level.get(L, {}).get(child_code)
            )
            if existing_code:
                parent_for_child = existing_code
                continue

            suf = f"__u{L}"
            if L == 1:
                suf = "__r1"
            replica_code = f"{child_code}{suf}"
            entity_type = "Region" if L == 1 else "Admin"

            self.save_area(
                code=replica_code,
                name=child_name,
                level=L,
                entity_type=entity_type,
                parent_code=(self.country_code if L == 1 else parent_for_child),
                url=url,
                area_km2=(area_km2 if L == 1 else None),
                density=(density if L == 1 else None),
                pop_latest=(pop_latest if L == 1 else None),
                pop_latest_date=(pop_latest_date if L == 1 else None),
                last_census_year=(last_census_year if L == 1 else None),
                set_parent=True,
            )
            self.slug_to_code_by_level.setdefault(L, {})[child_code] = replica_code
            self._index_name(L, child_name, replica_code)
            parent_for_child = replica_code

        if parent_for_child:
            self.set_parent_only(level=child_level, code=child_code, parent_code=parent_for_child)

    # ---------- páginas "full" (ultramar) ----------
    def scrape_full_territory(self, slug: str, base_level: int):
        slug = slug.strip("/").lower()
        url = f"{self.base_url}/{self.country_code}/{slug}"
        soup = fetch_html(url)

        # --- Adminareas (p.ej. Arrondissements dentro del departamento)
        table_admin = soup.select_one("section#adminareas table#tl")
        dep_name = None
        if table_admin:
            tfrow = table_admin.select_one("tfoot tr th.rname")
            if tfrow:
                dep_name = _clean_text(tfrow.get_text(" ", strip=True))
        if not dep_name:
            h2 = soup.select_one("section#adminareas h2")
            dep_name = _clean_text(h2.get_text(" ", strip=True)) if h2 else slug.split("/")[-1]

        dep_code = None
        if table_admin:
            tf_th = table_admin.select_one("tfoot tr th.rname[id]")
            if tf_th and tf_th.get("id"):
                dep_code = code_from_td(tf_th)
        if not dep_code:
            dep_code = slug.split("/")[-1]

        dep_pop_latest_date, dep_last_census_year = (None, None)
        dep_area, dep_density, dep_pop_latest = (None, None, None)
        if table_admin:
            dep_pop_latest_date, dep_last_census_year = parse_latest_dates_from_header(table_admin)
            area_factor = parse_area_factor_from_table(table_admin)
            t_area, t_dens, t_pop = parse_totals_from_tfoot(table_admin, area_factor=area_factor)
            dep_area = t_area
            dep_pop_latest = t_pop
            dep_density = t_dens if t_dens is not None else compute_density(dep_pop_latest, dep_area)

        dep_density_rounded = _round4(dep_density) if dep_density is not None else None
        self.save_area(
            code=dep_code,
            name=dep_name,
            level=base_level,
            entity_type="Department",
            parent_code=(self.country_code if base_level == 1 else None),
            url=url,
            area_km2=dep_area,
            density=dep_density_rounded,
            pop_latest=dep_pop_latest,
            pop_latest_date=dep_pop_latest_date,
            last_census_year=dep_last_census_year,
            set_parent=True,
        )
        self._index_slug(base_level, dep_name, dep_code, dep_code)
        self._index_name(base_level, dep_name, dep_code)

        if base_level > 1:
            self._ensure_upper_chain_and_link_child(
                child_level=base_level,
                child_code=dep_code,
                child_name=dep_name,
                url=url,
                area_km2=dep_area,
                density=dep_density_rounded,
                pop_latest=dep_pop_latest,
                pop_latest_date=dep_pop_latest_date,
                last_census_year=dep_last_census_year,
            )

        if table_admin:
            entity_from_h2 = self._extract_table_h2_singular(soup, "adminareas")
            pop_latest_date, last_census_year = parse_latest_dates_from_header(table_admin)
            area_factor = parse_area_factor_from_table(table_admin)
            for tr in table_admin.select("tbody tr"):
                td_rname = tr.select_one("td.rname")
                td_status = tr.select_one("td.rstatus")
                if not td_rname:
                    continue
                code = code_from_td(td_rname) or _slugify_citypop_name(extract_name_from_td(td_rname))
                name = extract_name_from_td(td_rname)

                raw_entity = _clean_text(td_status.get_text(" ", strip=True)) if td_status else None
                entity_type = _clean_entity_type_text(raw_entity) if raw_entity else (entity_from_h2 or "Admin")

                area_km2 = (
                    parse_area_from_row(tr, area_factor=area_factor)
                    or _clean_decimal(td_rname.get("data-area"))
                )
                pop_latest = parse_last_pop_from_row(tr)
                density = parse_density_from_row(tr) or _clean_decimal(td_rname.get("data-density"))
                if density is None:
                    density = compute_density(pop_latest, area_km2)

                self.save_area(
                    code=code,
                    name=name,
                    level=base_level + 1,
                    entity_type=entity_type,
                    parent_code=dep_code,
                    url=url,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=pop_latest,
                    pop_latest_date=pop_latest_date,
                    last_census_year=last_census_year,
                    set_parent=True,
                )
                self._index_name(base_level + 1, name, code)
                self._index_slug(base_level + 1, name, None, code)

        time.sleep(self.sleep)

        table_city = soup.select_one("section#citysection table#ts")
        if table_city:
            entity_from_h2 = self._extract_table_h2_singular(soup, "citysection")
            pop_latest_date, last_census_year = parse_latest_dates_from_header(table_city)
            area_factor = parse_area_factor_from_table(table_city)
            for tr in table_city.select("tbody tr"):
                td_rname = tr.select_one("td.rname")
                td_status = tr.select_one("td.rstatus")
                td_radm = tr.select_one("td.radm") or tr.select_one("td.radm.rarea")
                if not td_rname:
                    continue
                code = code_from_td(td_rname) or _slugify_citypop_name(extract_name_from_td(td_rname))
                name = extract_name_from_td(td_rname)

                raw_entity = _clean_text(td_status.get_text(" ", strip=True)) if td_status else None
                entity_type = _clean_entity_type_text(raw_entity) if raw_entity else (entity_from_h2 or "Commune")

                area_km2 = (
                    parse_area_from_row(tr, area_factor=area_factor)
                    or _clean_decimal(td_rname.get("data-area"))
                )
                pop_latest = parse_last_pop_from_row(tr)
                density = parse_density_from_row(tr) or _clean_decimal(td_rname.get("data-density"))
                if density is None:
                    density = compute_density(pop_latest, area_km2)

                parent_code = None
                if td_radm and td_radm.has_attr("data-admid"):
                    parent_code = str(td_radm["data-admid"])
                elif td_radm:
                    parent_name = _clean_text(td_radm.get_text(" ", strip=True))
                    parent_code = (
                        self.name_to_code_by_level.get(base_level + 1, {}).get(parent_name)
                        or self.name_to_code_by_level.get(base_level + 1, {}).get(_norm_key(parent_name))
                    )

                self.save_area(
                    code=code,
                    name=name,
                    level=base_level + 2,
                    entity_type=entity_type,
                    parent_code=parent_code or dep_code,
                    url=url,
                    area_km2=area_km2,
                    density=density,
                    pop_latest=pop_latest,
                    pop_latest_date=pop_latest_date,
                    last_census_year=last_census_year,
                    set_parent=True,
                )
        time.sleep(self.sleep)

    # ---------- Excepciones de parent_code (sin crear nada) ----------
    def _resolve_parent_ceuta_melilla(self, slug: str, target_level: int):
        l1_code = (
            self.slug_to_code_by_level.get(1, {}).get(slug)
            or self.name_to_code_by_level.get(1, {}).get(_title_from_slug(slug))
            or self.name_to_code_by_level.get(1, {}).get(_norm_key(_title_from_slug(slug)))
        )
        return l1_code

    def _resolve_parent_paris(self, slug: str, target_level: int):
        l3_code = (
            self.slug_to_code_by_level.get(3, {}).get(slug)
            or self.name_to_code_by_level.get(3, {}).get("Paris")
            or self.name_to_code_by_level.get(3, {}).get(_norm_key("Paris"))
        )
        return l3_code

    # ---------- páginas citysection (asNotAdmin) ----------
    def scrape_citysection(self, slug: str, target_level: int):
        slug = slug.strip("/").lower()
        url = f"{self.base_url}/{self.country_code}/{slug}"
        soup = fetch_html(url)
        table = soup.select_one("section#citysection table#ts")
        if not table:
            return 0

        pop_latest_date, last_census_year = parse_latest_dates_from_header(table)
        area_factor = parse_area_factor_from_table(table)
        saved = 0

        special_parent_code = None
        resolver = self.exception_parent_resolver.get((self.country_code, slug, target_level))
        if resolver:
            special_parent_code = resolver(slug, target_level)

        for tr in table.select("tbody tr"):
            td_rname = tr.select_one("td.rname")
            td_status = tr.select_one("td.rstatus")
            td_radm = tr.select_one("td.radm") or tr.select_one("td.radm.rarea")
            if not td_rname:
                continue

            code = code_from_td(td_rname) or _slugify_citypop_name(extract_name_from_td(td_rname))
            name = extract_name_from_td(td_rname)

            raw_entity = _clean_text(td_status.get_text(" ", strip=True)) if td_status else None
            entity_type = _clean_entity_type_text(raw_entity)

            area_km2 = (
                parse_area_from_row(tr, area_factor=area_factor)
                or _clean_decimal(td_rname.get("data-area"))
            )
            pop_latest = parse_last_pop_from_row(tr)
            density = parse_density_from_row(tr) or _clean_decimal(td_rname.get("data-density"))
            if density is None:
                density = compute_density(pop_latest, area_km2)

            parent_code = None
            if td_radm and td_radm.has_attr("data-admid"):
                parent_code = str(td_radm["data-admid"])
            elif td_radm:
                parent_name = _clean_text(td_radm.get_text(" ", strip=True))
                parent_code = (
                    self.name_to_code_by_level.get(target_level - 1, {}).get(parent_name)
                    or self.name_to_code_by_level.get(target_level - 1, {}).get(_norm_key(parent_name))
                )

            if not parent_code and special_parent_code:
                parent_code = special_parent_code

            self.save_area(
                code=code,
                name=name,
                level=target_level,
                entity_type=entity_type,
                parent_code=parent_code,
                url=url,
                area_km2=area_km2,
                density=density,
                pop_latest=pop_latest,
                pop_latest_date=pop_latest_date,
                last_census_year=last_census_year,
                set_parent=True,
            )
            saved += 1

        time.sleep(self.sleep)
        return saved


# -----------------------------
# Management command
# -----------------------------
class Command(BaseCommand):
    help = "Raspa CityPopulation y guarda AdminArea siguiendo *exactamente* el array 'subdivision'."

    def add_arguments(self, parser):
        parser.add_argument("--country", required=True, help="Slug del país (p.ej. spain, france, morocco)")
        parser.add_argument("--base-url", required=False, default=DEFAULT_BASE_URL,
                            help=f"Raíz del sitio (por defecto: {DEFAULT_BASE_URL})")
        parser.add_argument("--subdivision", required=False,
                            help="JSON con la estructura (usa comillas dobles).")
        parser.add_argument("--subdivision-file", required=False,
                            help="Fichero JSON con la estructura.")
        parser.add_argument("--sleep", type=float, default=1.0,
                            help="Segundos entre peticiones (cortesía).")

    @transaction.atomic
    def handle(self, *args, **options):
        country = options["country"].strip().strip("/")
        base_url = (options.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
        sleeper = float(options["sleep"])

        # NUEVO: borrar todos los AdminArea previos de ese país
        self.stdout.write(self.style.WARNING(
            f"Eliminando todas las AdminArea existentes para '{country}'..."
        ))
        reset_country_adminareas(country)

        # 1) Cargar 'subdivision'
        if options.get("subdivision_file"):
            try:
                with open(options["subdivision_file"], "r", encoding="utf-8") as f:
                    subdiv = json.load(f)
                self.stdout.write(self.style.NOTICE(
                    f"Usando --subdivision-file: {options['subdivision_file']}"
                ))
            except Exception as e:
                raise CommandError(f"No pude leer --subdivision-file: {e}")
        elif options.get("subdivision"):
            try:
                subdiv = json.loads(options["subdivision"])
                self.stdout.write(self.style.NOTICE("Usando --subdivision pasado por línea de comandos."))
            except Exception as e:
                raise CommandError(f"--subdivision debe ser JSON válido: {e}")
        else:
            preset = COUNTRY_PRESETS.get(country)
            if not preset:
                raise CommandError(
                    "No se pasó --subdivision/--subdivision-file y no hay preset para este país."
                )
            subdiv = preset["subdivision"]
            self.stdout.write(self.style.NOTICE(f"Usando preset incorporado para '{country}'."))

        scraper = CityPopScraper(country_code=country, base_url=base_url, sleeper=sleeper)

        # 0) Asegurar NV0 (país)
        scraper.ensure_country_area()

        # 2) Ejecutar secciones en orden 1..n
        for k, v in sorted(subdiv.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            sec = int(k)
            if not isinstance(v, dict):
                continue

            # ----- admin (cadena o lista) - compatible con antiguo 'asAdmin' -----
            admin_cfg = None
            if "admin" in v and v["admin"]:
                admin_cfg = v["admin"]
            elif "asAdmin" in v and v["asAdmin"]:
                # backward compatibility: use legacy key if present
                admin_cfg = v["asAdmin"]

            if admin_cfg:
                if isinstance(admin_cfg, (list, tuple)):
                    for raw_path in admin_cfg:
                        p = str(raw_path).strip().strip("/")
                        # Si es un slug de región sin '/', añadimos "/admin"
                        if p not in ("admin", "reg/admin") and "/" not in p:
                            admin_path = f"{p}/admin"
                        else:
                            admin_path = p
                        self.stdout.write(self.style.NOTICE(
                            f"[Sección {sec}] Scrape adminareas base L{sec}: {base_url}/{country}/{admin_path}"
                        ))
                        scraper.scrape_admin_page(admin_path, base_level=sec)
                else:
                    admin_path = str(admin_cfg).strip().strip("/")
                    self.stdout.write(self.style.NOTICE(
                        f"[Sección {sec}] Scrape adminareas base L{sec}: {base_url}/{country}/{admin_path}"
                    ))
                    scraper.scrape_admin_page(admin_path, base_level=sec)

            # ----- asNotAdmin (lista de slugs) -----
            if "asNotAdmin" in v and v["asNotAdmin"]:
                target_level = sec + 1
                regions = [s for s in v["asNotAdmin"] if s]
                for slug in regions:
                    slug = str(slug).strip().lower()
                    self.stdout.write(self.style.NOTICE(
                        f"[Sección {sec}] Scrape citysection (L{target_level}): {base_url}/{country}/{slug}"
                    ))
                    scraper.scrape_citysection(slug, target_level=target_level)

            # full
            full_list = v.get("full") or []
            if full_list:
                self.stdout.write(self.style.NOTICE(
                    f"[Sección {sec}] Procesando FULL para: {', '.join(full_list)}"
                ))
                for slug in full_list:
                    scraper.scrape_full_territory(slug, base_level=sec)

        # 3) Consolidar métricas del país sumando L1
        scraper.recompute_country_totals_from_db()

        # 3.bis) Crear las ciudades fusionadas configuradas en MAKE_CITIES
        created_cities = scraper.finalize_city_merges()
        if created_cities:
            self.stdout.write(self.style.SUCCESS(
                f"Ciudades fusionadas creadas: {created_cities}."
            ))

        self.stdout.write(self.style.SUCCESS(
            f"OK. Máximo nivel admin visto: L{scraper.max_admin_level_seen}."
        ))