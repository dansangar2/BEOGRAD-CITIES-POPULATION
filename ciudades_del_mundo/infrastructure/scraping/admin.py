"""Scraper for CityPopulation pages using the hierarchical admin table layout."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.base import BaseCityPopulationScraper


class CityPopulationAdminScraper(BaseCityPopulationScraper):
    """Scrape admin pages where nested levels live in `table#tl`."""

    html_format = "admin"

    def scrape_html(self, html: str, url: str, country_code: str, level: int) -> list[ScrapedAdminArea]:
        soup = BeautifulSoup(html, self._client.parser)
        table = soup.find("table", id="tl")
        root = self._parse_root(soup, country_code=country_code, level=level, url=url)

        entities = [root] if root else []
        if not table:
            return entities

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

            entity_level = level + relative_level
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

                parent = parent_stack.get(entity_level - 1)
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
                )
                if root and entity.code == root.code and entity.level == root.level:
                    continue

                entities.append(entity)
                parent_stack[entity.level] = entity
                for stacked_level in [stacked_level for stacked_level in parent_stack if stacked_level > entity.level]:
                    del parent_stack[stacked_level]

        return entities

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
        last_census_year = self._client.year_from_date(pop_latest_date)
        area_node = section.find(attrs={"data-area": True})
        density_node = section.find(attrs={"data-density": True})

        return ScrapedAdminArea(
            code=country_code,
            name=name,
            level=level,
            country_code=country_code,
            entity_type=entity_type,
            area_km2=self._client.safe_float(area_node.get("data-area")) if area_node else None,
            density=self._client.safe_float(density_node.get("data-density")) if density_node else None,
            pop_latest=pop_latest,
            pop_latest_date=pop_latest_date,
            last_census_year=last_census_year,
            url=url,
        )

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
        )

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
