"""Discover and classify CityPopulation routes for explicit config generation."""

from __future__ import annotations


from dataclasses import dataclass
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from ciudades_del_mundo.infrastructure.scraping.discovery_http import read_discovery_html
from bs4 import BeautifulSoup
from django.utils.translation import gettext as _
from ciudades_del_mundo.infrastructure.scraping.page_types import CityPopulationPageProfile, CityPopulationPageType, detect_citypopulation_page_profile
from ciudades_del_mundo.web.forms import scraping as _forms_scraping
from ciudades_del_mundo.web.queries import browser as _queries_browser
from ciudades_del_mundo.web import view_support as _view_support


@dataclass(frozen=True)
class CityPopulationRouteCandidate:
    """One route discovered in a CityPopulation country index."""

    path: str
    title: str = ""
    description: str = ""
    section: str = ""
    category: str = "other"


@dataclass(frozen=True)
class CityPopulationPageProbe:
    """Structural facts detected after reading a candidate CityPopulation page."""

    path: str
    url: str
    title: str
    h1: str
    headings: tuple[str, ...]
    profile: CityPopulationPageProfile
    table_ids: tuple[str, ...]
    row_count: int
    tl_rows: int
    ts_rows: int
    has_population: bool
    category: str = "other"


@dataclass(frozen=True)
class CityPopulationGeneratedPage:
    """TOML-ready page specification generated from a probed route."""

    path: str
    source: str
    lowest_level: int
    category: str = "other"
    include_infosection: bool = True
    include_major_subdivision: bool = True
    include_minor_subdivision: bool = True
    include_cities: bool = True


def _generate_citypopulation_config(slug: str) -> tuple[str, list[str]]:
    """Generate a TOML draft by probing the real CityPopulation country pages.

    The older helper only listed links found in ``/en/<slug>/`` and guessed the
    scraper type from the path.  This version opens the country index, selects
    routes that usually contain the official administrative / locality data,
    fetches those pages, detects their real table structure, and then writes a
    DB-ready TOML draft grouped by compatible page hints.
    """
    url = f"https://www.citypopulation.de/en/{slug}/"
    log = [_('Entrando en %(url)s') % {"url": url}]
    root_html = _read_citypopulation_html(url)
    root_probe = _citypopulation_page_probe(
        slug=slug,
        path="",
        html=root_html,
        url=url,
        category="root",
    )
    root_name = _citypopulation_root_name(root_probe, slug)

    candidates = _citypopulation_route_candidates(slug, root_html, base_url=url)
    selected = _select_citypopulation_candidates(candidates)
    log.append(
        _('%(total)s rutas encontradas; %(selected)s rutas útiles seleccionadas para comprobar.')
        % {"total": len(candidates), "selected": len(selected)}
    )

    probes: list[CityPopulationPageProbe] = []
    if _citypopulation_probe_has_data(root_probe):
        probes.append(root_probe)
        log.append(_('La página raíz contiene tablas o sección informativa útil.'))

    for candidate in selected:
        if len(probes) >= _view_support.CITYPOPULATION_DISCOVERY_MAX_PAGES:
            log.append(
                _('Límite de comprobación alcanzado (%(limit)s páginas).')
                % {"limit": _view_support.CITYPOPULATION_DISCOVERY_MAX_PAGES}
            )
            break
        candidate_url = _citypopulation_url_for_path(slug, candidate.path)
        try:
            html = _read_citypopulation_html(candidate_url)
        except (OSError, URLError, ValueError) as exc:
            log.append(_('Omitida %(path)s: %(error)s') % {"path": candidate.path or "/", "error": exc})
            continue
        probe = _citypopulation_page_probe(
            slug=slug,
            path=candidate.path,
            html=html,
            url=candidate_url,
            category=candidate.category,
        )
        if not _citypopulation_probe_has_data(probe):
            log.append(_('Omitida %(path)s: no se detectaron tablas de población útiles.') % {"path": candidate.path})
            continue
        probes.append(probe)

    if not probes:
        raise ValueError(_('No se encontraron páginas de población útiles para %(slug)s.') % {"slug": slug})

    generated_pages = _generated_pages_from_citypopulation_probes(probes, candidates)
    if not generated_pages:
        raise ValueError(_('No se pudo construir una configuración válida para %(slug)s.') % {"slug": slug})

    content = _render_citypopulation_generated_config(
        slug=slug,
        name=root_name,
        pages=generated_pages,
        legal_subdivision=_infer_citypopulation_legal_subdivision(generated_pages, probes),
    )
    log.append(_('Páginas TOML generadas: %(count)s') % {"count": len(generated_pages)})
    return content, log


def _read_citypopulation_html(url: str) -> str:
    try:
        return read_discovery_html(
            url, timeout=_view_support.CITYPOPULATION_DISCOVERY_TIMEOUT,
            user_agent=_view_support.CITYPOPULATION_GENERATOR_USER_AGENT,
        )
    except (OSError, URLError) as exc:
        raise ValueError(_('No se pudo leer CityPopulation: %(error)s') % {"error": exc}) from exc


def _citypopulation_route_candidates(slug: str, html: str, *, base_url: str) -> list[CityPopulationRouteCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(class_="cindex") or soup
    seen: set[str] = set()
    candidates: list[CityPopulationRouteCandidate] = []
    for link in root.find_all("a", href=True):
        path = _citypopulation_path_from_href(slug, link.get("href") or "", base_url=base_url)
        if path is None or path in seen:
            continue
        title_node = link.find("h3")
        title = _clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        description = _clean_text(" ".join(node.get_text(" ", strip=True) for node in link.find_all("p")[:2]))
        section = _citypopulation_previous_heading(link)
        category = _citypopulation_candidate_category(path, title=title, description=description, section=section)
        if category == "excluded":
            continue
        seen.add(path)
        candidates.append(
            CityPopulationRouteCandidate(
                path=path,
                title=title,
                description=description,
                section=section,
                category=category,
            )
        )
    return candidates


def _citypopulation_path_from_href(slug: str, href: str, *, base_url: str) -> str | None:
    href = str(href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc and parsed.netloc not in {"www.citypopulation.de", "citypopulation.de"}:
        return None
    marker = f"/en/{slug}/"
    path = parsed.path
    if marker not in path:
        return None
    relative = path.split(marker, 1)[1].strip("/")
    if not relative:
        return ""
    if relative.startswith(("maps/", "search/", "help/")):
        return None
    if ".." in relative or relative.endswith((".html", ".htm")):
        return None
    return relative


def _citypopulation_url_for_path(slug: str, path: str) -> str:
    path = str(path or "").strip("/")
    if path:
        return f"https://www.citypopulation.de/en/{slug}/{path}/"
    return f"https://www.citypopulation.de/en/{slug}/"


def _citypopulation_candidate_category(path: str, *, title: str = "", description: str = "", section: str = "") -> str:
    value = str(path or "").strip("/").casefold()
    text = f"{value} {title} {description} {section}".casefold()
    segments = [segment for segment in value.split("/") if segment]
    if not value:
        return "root"
    excluded_segments = {
        "agglo",
        "urbanareas",
        "metro",
        "combmetro",
        "ua",
        "census",
        "settlements",
        "townships",
        "map",
        "maps",
        "search",
        "help",
    }
    if any(segment in excluded_segments for segment in segments):
        return "excluded"
    if any(word in text for word in ("top 10", "urban area", "agglomeration", "metropolitan", "micropolitan")):
        return "excluded"
    if value in {"admin", "reg/admin", "prov/admin", "states/admin", "state/admin", "province/admin"}:
        return "main_admin"
    if value in {"cities", "city", "towns"}:
        return "main_cities"
    if value.endswith("/admin") or "/admin/" in value:
        return "regional_admin"
    if value.startswith(("localities/", "places/", "towns/")):
        return "localities"
    if value.startswith("cities/"):
        return "regional_cities"
    if not segments or len(segments) == 1:
        return "regional_detail"
    if "admin" in segments:
        return "regional_admin"
    return "other"


def _citypopulation_previous_heading(node) -> str:
    for previous in node.find_all_previous(["h2", "h1"]):
        text = _clean_text(previous.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _select_citypopulation_candidates(
    candidates: list[CityPopulationRouteCandidate],
) -> list[CityPopulationRouteCandidate]:
    candidate_by_path = {candidate.path: candidate for candidate in candidates}
    usable = [candidate for candidate in candidates if not _is_redundant_citypopulation_coded_admin(candidate, candidate_by_path)]
    caps = {
        "main_admin": 12,
        "main_cities": 6,
        "regional_admin": 90,
        "regional_detail": 80,
        "regional_cities": 40,
        "localities": 110,
        "other": 25,
        "root": 1,
    }
    order = ["main_admin", "main_cities", "regional_admin", "regional_detail", "regional_cities", "localities", "other"]
    selected: list[CityPopulationRouteCandidate] = []
    seen: set[str] = set()
    for category in order:
        rows = [candidate for candidate in usable if candidate.category == category]
        rows.sort(key=lambda candidate: _citypopulation_candidate_sort_key(candidate))
        for candidate in rows[: caps.get(category, 20)]:
            if candidate.path in seen:
                continue
            selected.append(candidate)
            seen.add(candidate.path)
            if len(selected) >= _view_support.CITYPOPULATION_DISCOVERY_MAX_PAGES:
                return selected
    return selected


def _citypopulation_candidate_sort_key(candidate: CityPopulationRouteCandidate) -> tuple[int, str]:
    path = candidate.path.casefold()
    title_bonus = 0 if candidate.title else 1
    if path in {"admin", "cities", "reg/admin", "prov/admin", "states/admin"}:
        title_bonus -= 2
    return (title_bonus, _natural_sort_text(path))


def _is_redundant_citypopulation_coded_admin(
    candidate: CityPopulationRouteCandidate,
    candidate_by_path: dict[str, CityPopulationRouteCandidate],
) -> bool:
    """Skip URL variants like ``admin/01__foo`` when a cleaner route exists."""
    path = candidate.path.strip("/")
    match = re.search(r"(?:^|/)admin/[^/]*__(?P<name>[^/]+)$", path, flags=re.IGNORECASE)
    if not match:
        return False
    coded_key = _route_key(match.group("name"))
    if not coded_key:
        return False
    for other_path in candidate_by_path:
        if other_path == candidate.path:
            continue
        parts = [part for part in other_path.strip("/").split("/") if part]
        aliases = []
        if parts:
            aliases.append(parts[0])
            aliases.append(parts[-1])
            if parts[-1] == "admin" and len(parts) > 1:
                aliases.append(parts[-2])
        if any(_route_key(alias) == coded_key for alias in aliases):
            return True
    return False


def _route_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _natural_sort_text(value: str) -> str:
    return re.sub(r"\d+", lambda match: match.group(0).zfill(8), str(value or ""))


def _citypopulation_page_probe(
    *,
    slug: str,
    path: str,
    html: str,
    url: str,
    category: str,
) -> CityPopulationPageProbe:
    soup = BeautifulSoup(html, "html.parser")
    profile = detect_citypopulation_page_profile(soup)
    title_node = soup.find("title")
    h1_node = soup.find("h1")
    table_ids: list[str] = []
    row_count = 0
    tl_rows = 0
    ts_rows = 0
    has_population = False
    for table in soup.find_all("table"):
        table_id = str(table.get("id") or "")
        if table_id:
            table_ids.append(table_id)
        data_rows = sum(len(tbody.find_all("tr", recursive=False)) for tbody in table.find_all("tbody", recursive=False))
        if table_id in {"tl", "ts"}:
            row_count += data_rows
        if table_id == "tl":
            tl_rows = data_rows
        if table_id == "ts":
            ts_rows = data_rows
        if table.find(class_=lambda value: value and "rpop" in value.split()):
            has_population = True
    headings = tuple(
        _clean_text(node.get_text(" ", strip=True))
        for node in soup.find_all("h2")[:8]
        if _clean_text(node.get_text(" ", strip=True))
    )
    return CityPopulationPageProbe(
        path=path.strip("/"),
        url=url,
        title=_clean_text(title_node.get_text(" ", strip=True) if title_node else ""),
        h1=_clean_text(h1_node.get_text(" ", strip=True) if h1_node else slug),
        headings=headings,
        profile=profile,
        table_ids=tuple(table_ids),
        row_count=row_count,
        tl_rows=tl_rows,
        ts_rows=ts_rows,
        has_population=has_population,
        category=category,
    )


def _citypopulation_probe_has_data(probe: CityPopulationPageProbe) -> bool:
    if probe.profile.page_type == CityPopulationPageType.UNKNOWN:
        return False
    if probe.row_count > 0 and probe.has_population:
        return True
    return probe.profile.page_type == CityPopulationPageType.INFOSECTION and probe.profile.has_root


def _citypopulation_root_name(probe: CityPopulationPageProbe, slug: str) -> str:
    if probe.h1 and probe.h1.casefold() != slug.casefold():
        return probe.h1
    return _queries_browser._display_name("", slug, country_code=slug) or slug


def _generated_pages_from_citypopulation_probes(
    probes: list[CityPopulationPageProbe],
    candidates: list[CityPopulationRouteCandidate],
) -> list[CityPopulationGeneratedPage]:
    candidate_paths = {candidate.path for candidate in candidates}
    generated: list[CityPopulationGeneratedPage] = []
    seen: set[tuple] = set()
    for probe in probes:
        spec = _generated_page_from_citypopulation_probe(probe, candidate_paths=candidate_paths)
        if spec is None:
            continue
        key = (
            spec.path,
            spec.source,
            spec.lowest_level,
            spec.include_infosection,
            spec.include_major_subdivision,
            spec.include_minor_subdivision,
            spec.include_cities,
        )
        if key in seen:
            continue
        seen.add(key)
        generated.append(spec)
    return sorted(generated, key=_generated_page_sort_key)


def _generated_page_from_citypopulation_probe(
    probe: CityPopulationPageProbe,
    *,
    candidate_paths: set[str],
) -> CityPopulationGeneratedPage | None:
    source = _citypopulation_source_for_probe(probe)
    category = probe.category
    lowest_level = _citypopulation_lowest_level_for_probe(probe, candidate_paths=candidate_paths)
    include_infosection = True
    include_major_subdivision = True
    include_cities = True

    if category == "localities":
        include_infosection = False
        if probe.profile.has_ts:
            include_major_subdivision = False
            include_cities = True
        if source == "admin":
            source = "cities"
    elif category == "regional_detail" and f"{probe.path}/admin" in candidate_paths and probe.profile.has_ts:
        include_major_subdivision = False
        include_cities = True
        if source == "admin":
            source = "cities"
    elif category == "regional_cities" and probe.profile.has_ts and probe.profile.has_tl:
        include_major_subdivision = False
        include_cities = True
        if source == "admin":
            source = "cities"
    return CityPopulationGeneratedPage(
        path=probe.path,
        source=source,
        lowest_level=lowest_level,
        category=category,
        include_infosection=include_infosection,
        include_major_subdivision=include_major_subdivision,
        include_minor_subdivision=include_minor_subdivision,
        include_cities=include_cities,
    )


def _citypopulation_source_for_probe(probe: CityPopulationPageProbe) -> str:
    return "admin" if probe.profile.preferred_html_format == "admin" else "cities"


def _citypopulation_lowest_level_for_probe(
    probe: CityPopulationPageProbe,
    *,
    candidate_paths: set[str],
) -> int:
    path = probe.path.strip("/")
    category = probe.category
    if not path or category in {"main_admin", "main_cities"}:
        return 0
    if category == "regional_admin":
        # National admin variants like states/admin are roots; region/admin pages
        # start at the region/federal-subject level.
        if path in {"reg/admin", "prov/admin", "states/admin", "state/admin", "province/admin"}:
            return 0
        return 1
    if category == "regional_detail":
        if f"{path}/admin" in candidate_paths:
            return 2
        return 1
    if category == "regional_cities":
        return 1
    if category == "localities":
        return 3
    return 1


def _generated_page_sort_key(page: CityPopulationGeneratedPage) -> tuple[int, int, str]:
    order = {
        "root": 0,
        "main_cities": 1,
        "main_admin": 2,
        "regional_admin": 3,
        "regional_detail": 4,
        "regional_cities": 5,
        "localities": 6,
        "other": 7,
    }
    return (order.get(page.category, 99), page.lowest_level, _natural_sort_text(page.path))


def _render_citypopulation_generated_config(
    *,
    slug: str,
    name: str,
    pages: list[CityPopulationGeneratedPage],
    legal_subdivision: int | None,
) -> str:
    grouped: dict[tuple, list[str]] = {}
    page_by_key: dict[tuple, CityPopulationGeneratedPage] = {}
    for page in pages:
        key = (
            page.source,
            page.lowest_level,
            page.category,
            page.include_infosection,
            page.include_major_subdivision,
            page.include_minor_subdivision,
            page.include_cities,
        )
        grouped.setdefault(key, []).append(page.path)
        page_by_key[key] = page

    lines = [f"name = {_queries_browser._toml_string(name)}", f"country_code = {_queries_browser._toml_string(slug)}"]
    if legal_subdivision is not None:
        lines.append(f"LEGAL_SUBDIVISION = {int(legal_subdivision)}")
    lines.append("scrape_schema_version = 2")
    lines.append("")

    for key in sorted(grouped, key=lambda item: _generated_page_sort_key(page_by_key[item])):
        page = page_by_key[key]
        paths = sorted(grouped[key], key=_natural_sort_text)
        if page.source == "admin":
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "minor_subdivision": bool(page.include_minor_subdivision),
            }
        elif page.source == "citiesadmin":
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "minor_subdivision": bool(page.include_minor_subdivision),
                "cities": bool(page.include_cities),
            }
        else:
            include = {
                "infosection": bool(page.include_infosection),
                "major_subdivision": bool(page.include_major_subdivision),
                "cities": bool(page.include_cities),
            }
        lines.append("[[pages]]")
        lines.append(f"source = {_queries_browser._toml_string(page.source)}")
        lines.append(f"path = {_forms_scraping._toml_array(paths)}")
        if int(page.lowest_level) != _inferred_generated_page_level(slug, page.source, paths[0] if paths else ""):
            lines.append(f"force_highest_level = {int(page.lowest_level)}")
        lines.append(f"include = {_forms_scraping._toml_scalar_inline_table(include)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _inferred_generated_page_level(slug: str, source: str, path: str) -> int:
    segments = [segment for segment in str(path or "").strip("/").split("/") if segment]
    slug_key = str(slug or "").strip("/").casefold()
    if segments and slug_key and segments[0].casefold() == slug_key:
        segments = segments[1:]
    normalized_source = str(source or "").strip().lower()
    if not segments:
        return 0
    if segments == ["admin"]:
        return 0
    if normalized_source in {"cities", "citiesadmin"} and segments == ["cities"]:
        return 0
    if segments[0] in {"cities", "localities", "places", "towns"}:
        return 1
    return 1


def _infer_citypopulation_legal_subdivision(
    pages: list[CityPopulationGeneratedPage],
    probes: list[CityPopulationPageProbe],
) -> int | None:
    if not pages:
        return None
    probe_by_path = {probe.path: probe for probe in probes}
    max_level = 0
    has_locality_layer = False
    for page in pages:
        probe = probe_by_path.get(page.path)
        estimated = page.lowest_level
        if probe and probe.profile.admin_levels:
            estimated = max(estimated, page.lowest_level + max(probe.profile.admin_levels))
        elif probe and probe.profile.has_tl and probe.profile.has_ts:
            estimated = max(estimated, page.lowest_level + 2)
        elif probe and (probe.profile.has_tl or probe.profile.has_ts):
            estimated = max(estimated, page.lowest_level + 1)
        max_level = max(max_level, estimated)
        if page.category in {"localities", "regional_detail", "regional_cities"} and page.include_cities:
            has_locality_layer = True
    if max_level <= 0:
        return None
    if has_locality_layer and max_level > 1:
        return min(5, max(1, max_level - 1))
    return min(5, max(1, max_level))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
