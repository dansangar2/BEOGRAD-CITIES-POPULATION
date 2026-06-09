"""Scraper for CityPopulation pages using the hierarchical admin table layout."""

from __future__ import annotations

import re

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper
from ciudades_del_mundo.infrastructure.scraping.page_config import (
    apply_root_config,
    configured_level,
    include_tables_for_page,
    should_include_table,
    table_levels_for_page,
)


class CityPopulationAdminScraper(BaseCityPopulationScraper):
    """Scrape admin pages where nested levels live in `table#tl`."""

    html_format = "admin"

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        return self._scrape_configured(html=html, url=url, country_code=country_code, level=level, page=None)

    def scrape_configured_html(self, html: str, url: str, country_code: str, page) -> list[ScrapedAdminArea]:
        return self._scrape_configured(
            html=html,
            url=url,
            country_code=country_code,
            level=page.lowest_level,
            page=page,
        )

    def _scrape_configured(
        self,
        *,
        html: str,
        url: str,
        country_code: str,
        level: int,
        page,
    ) -> list[ScrapedAdminArea]:
        soup, profile = self._soup_and_profile(html)
        table = soup.find("table", id="tl") if profile.has_tl else None
        root = self._parse_root(soup, country_code=country_code, level=level, url=url)
        if page is not None:
            root = apply_root_config(root, page=page, country_code=country_code, url=url, default_level=level)

        entities = [root] if root else []
        if not table:
            return entities

        table_levels = table_levels_for_page(page) if page is not None else {}
        include_tables = include_tables_for_page(page) if page is not None else ()
        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        last_year = self._client.year_from_date(last_pop_date)
        parent_stack: dict[int, ScrapedAdminArea] = {}
        if root:
            parent_stack[root.level] = root

        for tbody in table.find_all("tbody", recursive=False):
            relative_level = self._relative_level(tbody)
            if relative_level is None:
                continue

            entity_level = configured_level(
                table_levels,
                f"admin{relative_level}",
                str(relative_level),
                default=level + relative_level,
            )
            for tr in tbody.find_all("tr", recursive=False):
                parsed = self._client.parse_tr_tl(
                    tr=tr,
                    explicit_level=entity_level,
                    last_visible_pop_idx=last_pop_idx,
                    last_visible_date=last_pop_date,
                    default_last_census_year=last_year,
                    country_code=country_code,
                    base_url=self._client.base_for_urljoin(url),
                    visible_pop_columns=visible_pop_columns,
                )
                if not parsed:
                    continue

                parent = self._nearest_parent(parent_stack, entity_level)
                parent_code = parent.code if parent else None
                if parent_code is None and not root and level == 0 and entity_level == 1:
                    parent_code = country_code
                entity = ScrapedAdminArea(
                    code=parsed.entity_id,
                    name=parsed.name,
                    level=entity_level,
                    country_code=country_code,
                    entity_type=parsed.entity_type,
                    parent_code=parent_code,
                    area_km2=parsed.area_km2,
                    density=parsed.density,
                    pop_latest=parsed.pop_latest,
                    pop_latest_date=parsed.pop_latest_date,
                    last_census_year=parsed.last_census_year,
                    url=parsed.url,
                    data_wd=parsed.data_wd,
                )
                if root and entity.code == root.code and entity.level == root.level:
                    continue

                table_key = f"admin{relative_level}"
                if should_include_table(include_tables, table_key) or should_include_table(include_tables, str(relative_level)):
                    entities.append(entity)
                parent_stack[entity.level] = entity
                for stacked_level in [stacked_level for stacked_level in parent_stack if stacked_level > entity.level]:
                    del parent_stack[stacked_level]

        return entities

    def _nearest_parent(
        self,
        parent_stack: dict[int, ScrapedAdminArea],
        entity_level: int,
    ) -> ScrapedAdminArea | None:
        lower_levels = [level for level in parent_stack if level < entity_level]
        if not lower_levels:
            return None
        return parent_stack[max(lower_levels)]

    def _parse_root(
        self,
        soup: BeautifulSoup,
        *,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        section = soup.find(
            class_=lambda value: value and "infosection" in value and "mainsection" in value,
        ) or soup.find(class_=lambda value: value and "infosection" in value)
        if not section:
            return self._parse_tfoot_root(soup=soup, country_code=country_code, level=level, url=url)

        name_node = section.find(class_="infoname")
        name = self._clean_root_name(name_node.get_text(" ", strip=True)) if name_node else country_code
        entity_type = self._root_entity_type(section)

        pop_node = section.find(attrs={"data-newpop": True}) or section.find(attrs={"data-oldpop": True})
        pop_latest = int(pop_node.get("data-newpop") or pop_node.get("data-oldpop")) if pop_node else None
        pop_latest_date = (pop_node.get("data-newdate") or pop_node.get("data-olddate")) if pop_node else None
        if pop_latest is None:
            pop_latest, pop_latest_date = self._population_from_infosection_text(section)
        last_census_year = self._client.year_from_date(pop_latest_date)
        area_node = section.find(attrs={"data-area": True})
        density_node = section.find(attrs={"data-density": True})
        area_km2 = self._client.safe_float(area_node.get("data-area")) if area_node else None
        density = self._client.safe_float(density_node.get("data-density")) if density_node else None
        if area_km2 is None:
            area_km2 = self._metric_from_infosection_text(section, "area")
        if density is None:
            density = self._metric_from_infosection_text(section, "density")
        area_km2 = self._normalize_root_area(country_code, area_km2)
        if density is None and pop_latest is not None and area_km2 not in (None, 0):
            density = pop_latest / area_km2

        return ScrapedAdminArea(
            code=country_code,
            name=name,
            level=level,
            country_code=country_code,
            entity_type=entity_type,
            data_wd=self._root_data_wd(section, name=name, country_code=country_code),
            area_km2=area_km2,
            density=density,
            pop_latest=pop_latest,
            pop_latest_date=pop_latest_date,
            last_census_year=last_census_year,
            url=url,
        )

    def _population_from_infosection_text(self, section) -> tuple[int | None, str | None]:
        best_value = None
        best_date = None
        for node in section.find_all(class_="infotext", recursive=False):
            text = node.get_text(" ", strip=True)
            lowered = text.casefold()
            if "population" not in lowered or "annual population" in lowered or "density" in lowered:
                continue
            value_node = node.find(class_="val")
            value = self._client.safe_float_text(value_node.get_text(" ", strip=True) if value_node else text)
            if value is None:
                continue
            year_match = re.search(r"(\d{4})", text)
            date_value = f"{year_match.group(1)}-01-01" if year_match else None
            best_value = int(value)
            best_date = date_value
        return best_value, best_date

    def _metric_from_infosection_text(self, section, label: str) -> float | None:
        label = label.casefold()
        for node in section.find_all(class_="infotext", recursive=False):
            text = node.get_text(" ", strip=True)
            lowered = text.casefold()
            if label == "area":
                matches = "area" in lowered
            elif label == "density":
                matches = "density" in lowered
            else:
                matches = label in lowered
            if not matches:
                continue
            value_node = node.find(class_="val")
            value = self._client.safe_float_text(value_node.get_text(" ", strip=True) if value_node else text)
            if value is not None:
                return value
        return None

    def _normalize_root_area(self, country_code: str, area_km2: float | None) -> float | None:
        if country_code == "puertorico" and area_km2 is not None and area_km2 > 100000:
            return area_km2 / 100
        return area_km2

    def _relative_level(self, tbody) -> int | None:
        for class_name in tbody.get("class") or []:
            match = re.fullmatch(r"admin(\d+)", class_name)
            if match:
                return int(match.group(1))
        return None

    def _parse_tfoot_root(
        self,
        *,
        soup: BeautifulSoup,
        country_code: str,
        level: int,
        url: str,
    ) -> ScrapedAdminArea | None:
        if level != 0:
            return None

        table = soup.find("table", id="tl")
        tfoot = table.find("tfoot") if table else None
        tr = tfoot.find("tr") if tfoot else None
        if not table or not tr:
            return None

        last_pop_idx, last_pop_date = self._client.detect_last_visible_pop_column(table)
        visible_pop_columns = self._client.visible_pop_columns(table)
        parsed = self._client.parse_tr_tl(
            tr=tr,
            explicit_level=level,
            last_visible_pop_idx=last_pop_idx,
            last_visible_date=last_pop_date,
            default_last_census_year=self._client.year_from_date(last_pop_date),
            country_code=country_code,
            base_url=self._client.base_for_urljoin(url),
            visible_pop_columns=visible_pop_columns,
        )
        if not parsed:
            return None

        return ScrapedAdminArea(
            code=country_code,
            name=parsed.name,
            level=level,
            country_code=country_code,
            entity_type=parsed.entity_type,
            area_km2=parsed.area_km2,
            density=parsed.density,
            pop_latest=parsed.pop_latest,
            pop_latest_date=parsed.pop_latest_date,
            last_census_year=parsed.last_census_year,
            url=parsed.url,
            data_wd=parsed.data_wd,
        )

    def _root_data_wd(self, section, *, name: str, country_code: str) -> str:
        """Pick the data-wd that belongs to the infosection root, not a child row."""
        expected = {self._normalize_data_wd_name(name), self._normalize_data_wd_name(country_code)} - {""}
        candidates: list[tuple[int, str]] = []
        for element in section.find_all(attrs={"data-wd": True}):
            qid = self._normalize_data_wd_value(element.get("data-wd"))
            if not qid:
                continue
            text = element.get_text(" ", strip=True)
            data_wiki = str(element.get("data-wiki") or "")
            score = max(
                self._data_wd_name_score(text, expected),
                self._data_wd_name_score(data_wiki, expected),
            )
            if element is section:
                score += 30
            if element.find_parent(class_="infoname") or "infoname" in (element.get("class") or []):
                score += 20
            if score > 0:
                candidates.append((score, qid))
        if not candidates:
            qid = self._normalize_data_wd_value(section.get("data-wd"))
            return qid
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _normalize_data_wd_value(self, value: str | None) -> str:
        text = str(value or "").strip().upper()
        return text if re.fullmatch(r"Q\d+", text) else ""

    def _normalize_data_wd_name(self, value: str | None) -> str:
        text = str(value or "").casefold().replace("_", " ").replace("-", " ")
        text = re.sub(r"[^\w\s]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _data_wd_name_score(self, value: str | None, expected: set[str]) -> int:
        normalized = self._normalize_data_wd_name(value)
        if not normalized or not expected:
            return 0
        if normalized in expected:
            return 100
        for item in expected:
            if normalized in {f"{item} republic", f"republic of {item}", f"state of {item}", f"kingdom of {item}"}:
                return 95
            if normalized.startswith(f"{item} ") or normalized.startswith(f"{item}:"):
                return 70
        return 0

    def _clean_root_name(self, value: str) -> str:
        return re.sub(r"^Contents:\s*", "", value).strip()

    def _root_entity_type(self, section) -> str | None:
        for node in section.find_all(class_="infotext", recursive=False):
            if node.find(class_="val"):
                continue
            text = node.get_text(" ", strip=True)
            if text:
                return text
        return None
