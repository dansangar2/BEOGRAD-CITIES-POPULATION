"""Build Java's immutable text resource from the shipped gettext catalogs.

This is an explicit build bridge for UI translations, never a SQL/config seed
fallback. It does not initialize Django or open any database.
"""
from __future__ import annotations

import argparse
import ast
import gettext
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend-java/src/main/resources/text-catalog.json"


def literal_assignment(path: Path, name: str):
    source = path.read_text(encoding="utf-8-sig")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == name for n in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing literal {name} in {path}")


def content() -> str:
    languages = {}
    for path in sorted((ROOT / "locale").glob("*/LC_MESSAGES/django.mo")):
        with path.open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        languages[path.parents[1].name.replace("_", "-").lower()] = {
            k: v for k, v in catalog._catalog.items() if isinstance(k, str) and k and isinstance(v, str)
        }
    if not languages:
        raise ValueError("No compiled gettext catalogs found")
    data = {
        "languages": languages,
        "countries_es": literal_assignment(ROOT / "old-backend-python/ciudades_del_mundo/web/view_support.py", "COUNTRY_NAME_ES_BY_CODE"),
        "wikidata": literal_assignment(ROOT / "ciudades_del_mundo/infrastructure/wikidata_countries.py", "COUNTRY_WIKIDATA_IDS"),
        "spain_names": literal_assignment(ROOT / "old-backend-python/ciudades_del_mundo/web/spain_translations.py", "SPAIN_NAME_LABELS"),
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = content()
    if args.check:
        if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != result:
            raise SystemExit("Java text catalog needs rebuilding: py -B tools/build_java_catalog.py")
        print("Java text catalog matches shipped catalogs")
    else:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(result, encoding="utf-8")
        print(f"Wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
