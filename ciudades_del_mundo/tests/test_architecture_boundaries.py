"""Architecture boundary checks for the hexagonal core."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HexagonalBoundaryTests(unittest.TestCase):
    def test_web_functions_are_not_redefined_in_the_same_scope(self):
        violations = []
        for path in (PROJECT_ROOT / "web").rglob("*.py"):
            violations.extend(f"{path.relative_to(PROJECT_ROOT)}:{item}" for item in
                              _function_redefinitions(ast.parse(path.read_text(encoding="utf-8"))))
        self.assertEqual(violations, [])

    def test_redefinition_check_respects_scopes_and_control_flow(self):
        tree = ast.parse("def f(): pass\nif True:\n def f(): pass\nclass A:\n def f(self): pass\nclass B:\n async def f(self): pass\n")
        self.assertEqual(_function_redefinitions(tree), ["3:f"])

    def test_queries_do_not_import_http_or_handler_adapters(self):
        violations = []
        forbidden = ("django.http", "django.shortcuts", "ciudades_del_mundo.web.handlers", "ciudades_del_mundo.web.http")
        for path in sorted((PROJECT_ROOT / "web" / "queries").rglob("*.py")):
            for imported in _absolute_imports(path):
                if _matches_any(imported, forbidden):
                    violations.append(f"{path.name} imports {imported}")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and any(arg.arg == "request" for arg in node.args.args):
                    violations.append(f"{path.name}.{node.name} receives an HTTP request")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "save", "delete", "create", "update_or_create", "get_or_create", "bulk_create", "bulk_update",
                }:
                    violations.append(f"{path.name} calls mutation {node.func.attr}")
        self.assertEqual(violations, [])

    def test_area_selectors_do_not_depend_on_web(self):
        path = PROJECT_ROOT / "infrastructure/django/area_selectors.py"
        self.assertFalse(any(_matches_any(name, ("ciudades_del_mundo.web",)) for name in _absolute_imports(path)))

    def test_mutation_adapters_do_not_import_web(self):
        for relative in ("services/derived_config_mutations.py", "services/scraping_config_tasks.py"):
            with self.subTest(module=relative):
                self.assertFalse(any(_matches_any(name, ("ciudades_del_mundo.web",))
                                     for name in _absolute_imports(PROJECT_ROOT / relative)))

    def test_internal_code_does_not_depend_on_the_compatibility_facade(self):
        for path in PROJECT_ROOT.rglob("*.py"):
            if path.name in {"views.py", "test_web_modularity.py"} or "__pycache__" in path.parts:
                continue
            with self.subTest(module=str(path.relative_to(PROJECT_ROOT))):
                self.assertFalse(any(_matches_any(name, ("ciudades_del_mundo.web.views",))
                                     for name in _absolute_imports(path)))

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
            for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
                for imported in _absolute_imports(path):
                    if _matches_any(imported, forbidden_prefixes):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {imported}")

        self.assertEqual(violations, [])

    def test_application_does_not_define_boundary_protocols(self):
        violations: list[str] = []

        for path in sorted((PROJECT_ROOT / "application").rglob("*.py")):
            for class_name in _protocol_class_names(path):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} declares Protocol {class_name}")

        self.assertEqual(violations, [])


def _function_redefinitions(tree: ast.AST) -> list[str]:
    """Control-flow blocks share a scope; classes/functions introduce one."""
    violations = []

    def scope(root):
        names = set()

        def visit(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in names:
                    violations.append(f"{node.lineno}:{node.name}")
                names.add(node.name)
                scope(node)
            elif isinstance(node, ast.ClassDef):
                scope(node)
            else:
                for child in ast.iter_child_nodes(node):
                    visit(child)

        for child in ast.iter_child_nodes(root):
            visit(child)

    scope(tree)
    return violations


def _absolute_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = ".".join((PROJECT_ROOT.name, *path.relative_to(PROJECT_ROOT).parent.parts))
                module = resolve_name("." * node.level + module, package)
            if module:
                imports.append(module)
                # `from ciudades_del_mundo import models` must also be checked.
                imports.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return imports


def _protocol_class_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if any(_base_name(base) == "Protocol" for base in node.bases):
            names.append(node.name)
    return names


def _base_name(base) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Subscript):
        return _base_name(base.value)
    return ""


def _matches_any(imported: str, prefixes: tuple[str, ...]) -> bool:
    return any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in prefixes)


class ImportResolutionTests(unittest.TestCase):
    def test_boundary_check_finds_a_violation_in_a_nested_package(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            root = Path(directory) / "ciudades_del_mundo"
            for package in ("domain", "ports", "application"):
                (root / package).mkdir(parents=True)
            path = root / "application" / "nested" / "use_case.py"
            path.parent.mkdir()
            path.write_text("from ... import models\n", encoding="utf-8")
            with patch(f"{__name__}.PROJECT_ROOT", root):
                with self.assertRaises(AssertionError):
                    HexagonalBoundaryTests().test_domain_ports_and_application_do_not_import_outer_adapters()
    def test_nested_relative_and_package_member_imports(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            root = Path(directory) / "ciudades_del_mundo"
            path = root / "application" / "nested" / "use_case.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "from ... import models\nfrom ...infrastructure import django\n"
                "from .. import sibling\nfrom ciudades_del_mundo import services\n",
                encoding="utf-8",
            )
            with patch(f"{__name__}.PROJECT_ROOT", root):
                imports = _absolute_imports(path)
            self.assertIn("ciudades_del_mundo.models", imports)
            self.assertIn("ciudades_del_mundo.infrastructure.django", imports)
            self.assertIn("ciudades_del_mundo.application.sibling", imports)
            self.assertIn("ciudades_del_mundo.services", imports)

    def test_relative_imports_in_package_init_use_the_package(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            root = Path(directory) / "ciudades_del_mundo"
            path = root / "domain" / "nested" / "__init__.py"
            path.parent.mkdir(parents=True)
            path.write_text("from . import rules\nfrom ...models import AdminArea\n", encoding="utf-8")
            with patch(f"{__name__}.PROJECT_ROOT", root):
                imports = _absolute_imports(path)
            self.assertIn("ciudades_del_mundo.domain.nested.rules", imports)
            self.assertIn("ciudades_del_mundo.models", imports)
