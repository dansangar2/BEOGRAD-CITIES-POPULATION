"""Architecture boundary checks for the hexagonal core."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HexagonalBoundaryTests(unittest.TestCase):
    def test_domain_ports_and_application_do_not_import_outer_adapters(self):
        forbidden_by_package = {
            "domain": (
                "django",
                "ciudades_del_mundo.application",
                "ciudades_del_mundo.infrastructure",
                "ciudades_del_mundo.management",
                "ciudades_del_mundo.models",
                "ciudades_del_mundo.services",
                "ciudades_del_mundo.web",
            ),
            "ports": (
                "django",
                "ciudades_del_mundo.application",
                "ciudades_del_mundo.infrastructure",
                "ciudades_del_mundo.management",
                "ciudades_del_mundo.models",
                "ciudades_del_mundo.services",
                "ciudades_del_mundo.web",
            ),
            "application": (
                "django",
                "ciudades_del_mundo.infrastructure",
                "ciudades_del_mundo.management",
                "ciudades_del_mundo.models",
                "ciudades_del_mundo.services",
                "ciudades_del_mundo.web",
            ),
        }
        violations: list[str] = []

        for package, forbidden_prefixes in forbidden_by_package.items():
            for path in sorted((PROJECT_ROOT / package).glob("*.py")):
                for imported in _absolute_imports(path):
                    if _matches_any(imported, forbidden_prefixes):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")

        self.assertEqual(violations, [])


def _absolute_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append(node.module)
    return imports


def _matches_any(imported: str, prefixes: tuple[str, ...]) -> bool:
    return any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in prefixes)
