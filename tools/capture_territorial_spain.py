"""Explicit synthetic acceptance capture; never invoked by runtime or tests.

Uses the traditional scraper unchanged, offline, before reconciliation. All
numbers/codes here are test data, not national statistics or an official catalog.
Re-capturing the baseline requires explicit --write and review of its diff.
"""
import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.tests.isolated_settings")


def row(code, name, status, population, *, parent="", qid=""):
    return (f'<tr><td class="rname" id="i{code}" data-wd="{qid}"><span itemprop="name">{name}</span></td>'
        f'<td class="rstatus">{status}</td>' + (f'<td class="radm">{parent}</td>' if parent else "")
        + f'<td class="rpop">{population}</td></tr>')


def table(body, footer="", table_id="tl"):
    parent = '<th class="radm">Municipality</th>' if table_id == "ts" else ""
    return (f'<table id="{table_id}"><thead><tr><th>Name</th><th>Status</th>{parent}'
        '<th class="rpop" data-coldate="2021-01-01">Population</th></tr></thead>'
        f'{body}<tfoot>{footer}</tfoot></table>')


def capture():
    import django
    django.setup()
    from ciudades_del_mundo.application.scrape_admin_areas import ScrapeAdminAreas
    from ciudades_del_mundo.infrastructure.scraping import CityPopulationAdminScraper, CityPopulationCitiesScraper
    from ciudades_del_mundo.infrastructure.scraping.city_population_client import CityPopulationClient
    from ciudades_del_mundo.services.scraping_configs import parse_scraping_job_config
    from ciudades_del_mundo.tests.territorial_support import reference_document

    pages = {}
    country = row("ES", "Spain", "Country", 1000, qid="Q29")
    national = ""
    for region_code, name, provinces in (
        ("01", "Andalucía", [("0101", "Sevilla"), ("0102", "Cádiz")]),
        ("02", "Madrid", [("0201", "Madrid")]),
        ("03", "Ceuta", []), ("04", "Melilla", []),
    ):
        national += '<tbody class="admin1">' + row(region_code, name, "Autonomous Community", 200) + '</tbody>'
        if provinces:
            national += '<tbody class="admin2">' + ''.join(row(code, n, "Province", 100) for code, n in provinces) + '</tbody>'
    pages["spain/admin"] = table(national, country)
    for path, province_code, province_name, municipality_code, municipality_name in (
        ("seville", "0101", "Sevilla", "0101001", "Sevilla"),
        ("cadiz", "0102", "Cádiz", "0102001", "Cádiz"),
        ("madrid", "0201", "Madrid", "0201001", "Madrid"),
    ):
        municipality = row(municipality_code, municipality_name, "Municipality", 70, qid="Q2807" if path == "madrid" else "")
        status = "Locality (part)" if path == "madrid" else "Locality"
        cities = row(municipality_code + "01", municipality_name, status, 40, parent=municipality_name,
                     qid="Q2807" if path == "madrid" else "")
        cities += row(municipality_code + "02", municipality_name + " parte", status, 30, parent=municipality_name,
                      qid="Q2807" if path == "madrid" else "")
        pages["spain/localities/" + path] = table('<tbody class="adm">' + row(province_code, province_name, "Province", 100) + '</tbody><tbody>' + municipality + '</tbody>') + table('<tbody>' + cities + '</tbody>', table_id="ts")
    # Existing Spain semantics: city pages repeat their root at L1/L2, then
    # municipality at L3. Separate locality pages supply observed L4 rows.
    for path, code, municipality_code in (("ceuta", "03", "03001"), ("melilla", "04", "04001")):
        name = path.title()
        pages["spain/" + path] = table('<tbody>' + row(municipality_code, name, "Municipality", 70) + '</tbody>', row(code, name, "Autonomous City", 70))
        repeated_code = f"{code}__spain_{path}__repeat2"
        pages["spain/localities/" + path] = table('<tbody class="adm">' + row(repeated_code, name, "Autonomous City", 70) + '</tbody><tbody>' + row(municipality_code, name, "Municipality", 70) + '</tbody>') + table('<tbody>' + row(municipality_code + "01", name, "Locality", 70, parent=name) + '</tbody>', table_id="ts")

    # Fixture-specific explicit import of the existing Spain recipe. Paths are
    # restricted to these frozen synthetic pages, with identical section rules.
    content = (ROOT / "ciudades_del_mundo/subdivisions/spain.toml").read_text(encoding="utf-8")
    config = parse_scraping_job_config("spain", content)
    selected = []
    for page in config.pages:
        if page.path in pages:
            selected.append(page)
    # Sevilla's production slug is sevilla, whereas this fixture uses seville.
    sevilla = next(p for p in config.pages if p.path == "spain/localities/sevilla")
    selected.append(replace(sevilla, path="spain/localities/seville"))
    config = replace(config, pages=selected)
    frozen = {config.base_url + path + "/": html for path, html in pages.items()}

    class MemoryRepository:
        def save_many(self, country_code, entities):
            self.entities = entities
            return len(entities), 0

        def delete_missing(self, country_code, ids):
            return 0

    repo = MemoryRepository()
    with patch.object(CityPopulationClient, "get", side_effect=frozen.__getitem__):
        try:
            ScrapeAdminAreas(repo, [CityPopulationAdminScraper(), CityPopulationCitiesScraper()]).run(config)
        except Exception as exc:
            for entity in getattr(exc, "entities", ()):
                print("UNLINKED", entity)
            raise
    return {"notice": "SYNTHETIC representative branches; codes/populations are test data, not an official Spanish reference.",
        "pages": frozen, "reference": reference_document(), "traditional_content": content,
        "captured_owners": {name: sha256((ROOT / "ciudades_del_mundo" / name).read_bytes()).hexdigest()
            for name in ("application/scrape_admin_areas.py", "application/citypopulation_linking.py", "infrastructure/scraping/citypopulation_sections.py")},
        "selected_paths": [p.path for p in selected],
        "baseline": [asdict(e) for e in sorted(repo.entities, key=lambda e: (e.level, e.code))]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True)
    parser.parse_args()
    data = capture()
    target = ROOT / "ciudades_del_mundo/tests/fixtures/territorial_spain.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Captured {len(data['baseline'])} rows on {len(data['pages'])} synthetic frozen pages: {target}")
    for e in data["baseline"]:
        print(e["code"], e["name"], e["level"], e["parent_code"])


if __name__ == "__main__":
    main()
