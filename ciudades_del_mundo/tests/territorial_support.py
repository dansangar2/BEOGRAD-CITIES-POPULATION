"""Independent synthetic reference and test provider; no production seed."""
from copy import deepcopy
import json
from pathlib import Path

from ciudades_del_mundo.domain.admin_area import ScrapedAdminArea
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import freeze_pages

FIXTURE = Path(__file__).parent / "fixtures/territorial_spain.json"


def frozen_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def reference_document():
    # These declared branches/relations are independent of baseline output.
    # Numbers and codes are synthetic; no claim of official Spanish coverage.
    entities = []
    declarations = [
        ("spain", "Spain", 0, None),
        ("01", "Andalucía", 1, "spain"), ("02", "Madrid", 1, "spain"),
        ("03", "Ceuta", 1, "spain"), ("04", "Melilla", 1, "spain"),
        ("0101", "Sevilla", 2, "01"), ("0102", "Cádiz", 2, "01"), ("0201", "Madrid", 2, "02"),
        ("03__spain_ceuta__repeat2", "Ceuta", 2, "03"), ("04__spain_melilla__repeat2", "Melilla", 2, "04"),
        ("0101001", "Sevilla", 3, "0101"), ("0102001", "Cádiz", 3, "0102"), ("0201001", "Madrid", 3, "0201"),
        ("03001", "Ceuta", 3, "03__spain_ceuta__repeat2"), ("04001", "Melilla", 3, "04__spain_melilla__repeat2"),
        ("010100101", "Sevilla", 4, "0101001"), ("010100102", "Sevilla parte", 4, "0101001"),
        ("010200101", "Cádiz", 4, "0102001"), ("010200102", "Cádiz parte", 4, "0102001"),
        ("020100101", "Madrid", 4, "0201001"), ("020100102", "Madrid parte", 4, "0201001"),
        ("0300101", "Ceuta", 4, "03001"), ("0400101", "Melilla", 4, "04001"),
    ]
    roles = ["country", "community", "province", "municipality", "locality"]
    for code, name, level, parent in declarations:
        entity = {"id": "ref_" + code, "code": code, "names": [name], "role": roles[level],
                  "parent": "ref_" + parent if parent else None}
        if "__repeat" in code:
            entity["repeat_of"] = "ref_" + parent
        entities.append(entity)
    return {"schema_version": 1, "country": "spain", "version": "synthetic-branches-v1",
        "provenance": "Synthetic test reference declared in territorial_support.py; NOT official territorial data.",
        "scope": "Only frozen synthetic Andalucía, Madrid, Ceuta and Melilla branches. Not a national acceptance dataset.",
        "entry_path": "spain/", "roles": [{"id": role, "level": i} for i, role in enumerate(roles)],
        "entities": entities, "exceptions": [{"id": "autonomous_city_repetition", "entities": [
            "ref_03__spain_ceuta__repeat2", "ref_04__spain_melilla__repeat2"],
            "reason": "Preserve explicit community and province roles on consecutive levels, matching the existing Spain recipe.",
            "provenance": "Existing subdivisions/spain.toml repeat.infosection=2; synthetic acceptance only."}]}


def evidence_document():
    return freeze_pages("spain", "spain/", frozen_fixture()["pages"])


def valid_proposal(reference, evidence, version):
    nodes = []
    levels = {r["id"]: r["level"] for r in reference["roles"]}
    for entity in reference["entities"]:
        source = [row for row in evidence["rows"] if row["entity"]["name"] in entity["names"] and (
            row["entity"]["code"] == entity["code"] or (
                row["entity"]["code"] == reference["country"] and row["entity"]["level"] == 0
                and entity["role"] in ("country", "community")))]
        # Selection is a provider double, deliberately simple and inspectable.
        # Never read baseline to manufacture a matching proposal.
        source.sort(key=lambda row: ("/admin/" not in row["entity"]["url"], row["id"]))
        nodes.append({"reference_id": entity["id"], "evidence_ids": [row["id"] for row in source],
            "role": entity["role"], "level": levels[entity["role"]], "parent": entity["parent"],
            "reason": "Exact source identity and reference branch; page-local levels are mapped to the declared territorial role.",
            "reference_ids": [entity["id"], *(e["id"] for e in reference["exceptions"] if entity["id"] in e["entities"])]})
    return {"schema_version": 1, "country": reference["country"], "reference_version": reference["version"],
        "evidence_version": version, "nodes": nodes, "unresolved": [], "discrepancies": []}


class SimulatedProvider:
    model = "offline-double"
    usage_history = None

    def __init__(self, response=None):
        self.response = response
        self.calls = 0

    def complete_json(self, *, system_prompt, payload, image_url=""):
        self.calls += 1
        if self.response is not None:
            return deepcopy(self.response)
        return valid_proposal(payload["reference"], payload["evidence"], payload["evidence_version"])


def baseline_entities():
    return [ScrapedAdminArea(**row) for row in frozen_fixture()["baseline"]]
