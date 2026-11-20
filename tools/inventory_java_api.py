"""Inventory HTTP owners without importing Django or opening the database.

The evidence is deliberately static: observed expressions are not inferred
response schemas. Runtime equivalence belongs in separate characterization tests.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "ciudades_del_mundo/web"


def java_route_names() -> set[str]:
    """Read the runtime allowlist without importing Django."""
    tree = ast.parse((WEB / "java_routing.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "JAVA_ROUTE_NAMES" for n in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise ValueError("Missing JAVA_ROUTE_NAMES")


IMPLEMENTED = java_route_names()


def inventory() -> dict:
    handlers = {}
    for path in sorted((WEB / "handlers").glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        imports = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                handlers[f"{path.stem}.{node.name}"] = (path, source, node, imports)
    consumers = {}
    for folder, suffixes in (("templates", (".html",)), ("static/ciudades_del_mundo/src", (".js",))):
        for path in (ROOT / "ciudades_del_mundo" / folder).rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                consumers[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8-sig")
    test_sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8-sig")
                    for p in (ROOT / "ciudades_del_mundo/tests").glob("test_*.py")}
    routes = []
    tree = ast.parse((WEB / "urls.py").read_text(encoding="utf-8-sig"))
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "path":
            continue
        route = ast.literal_eval(call.args[0])
        owner = ast.unparse(call.args[1])
        name = next((ast.literal_eval(k.value) for k in call.keywords if k.arg == "name"), "")
        record = {"name": name, "url": "/" + route, "owner": owner, "python_state": "maintained; active in Python mode", "java_status": "Django owner in Java mode; native Java implementation pending"}
        if name in IMPLEMENTED:
            record["java_status"] = "native Java; seven HTTP methods characterized against synthetic Django schema"
        if owner in handlers:
            path, source, node, imports = handlers[owner]
            nodes = list(ast.walk(node))
            calls = [n for n in nodes if isinstance(n, ast.Call)]
            responses = [ast.get_source_segment(source, n) for n in calls
                         if ast.unparse(n.func) in {"JsonResponse", "HttpResponse", "HttpResponseNotAllowed", "redirect", "render", "Http404"}]
            request_values = sorted({ast.unparse(n) for n in calls if re.match(r"request\.(GET|POST|FILES|headers)\.", ast.unparse(n.func))})
            methods = sorted({n.value for n in nodes if isinstance(n, ast.Constant)
                              and n.value in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")})
            kinds = []
            for kind, token in (("json", "JsonResponse"), ("html", "render"), ("download", "HttpResponse"), ("redirect", "redirect")):
                if any(ast.unparse(n.func) == token for n in calls):
                    kinds.append(kind)
            if not kinds:
                kinds = ["delegated-helper; inspect owner"]
            record.update({
                "source": f"{path.relative_to(ROOT).as_posix()}:{node.lineno}",
                "classification": kinds,
                "method_branches": methods,
                "method_note": "Branches observed; no decorator restricting methods. CSRF middleware applies to unsafe methods.",
                "inputs_observed": request_values,
                "responses_and_errors_observed": responses,
                "permissions": [ast.unparse(n) for n in node.decorator_list],
                "security": "Django middleware protects the public origin in both modes. Native Java routes also own their HTTP security.",
                "calls": sorted({ast.unparse(n.func) for n in calls}),
                "direct_effects_observed": sorted({ast.unparse(n.func) for n in calls if re.search(r"\.(save|delete|update|create|start|cancel|write|unlink|mkdir)$", ast.unparse(n.func))}),
                "imports": imports,
                "consumers": [p for p, text in consumers.items() if name in text or (route and route in text)],
                "existing_test_references": [p for p, text in test_sources.items() if name in text or (route and route in text)],
                "equivalence": "EquivalenceTest with frozen synthetic SQL/HTTP contracts; no Python execution (nine languages, seven methods, Accept variants)" if name in IMPLEMENTED else "Django implementation active in both modes; native Java characterization pending",
            })
        routes.append(record)
    return {
        "scope": "Maintained Python web urlpatterns. Python serves all routes; Java mode forwards only JAVA_ROUTE_NAMES to native Java.",
        "limitations": "Static evidence, not runtime equivalence. Pending Java routes retain Django owners. Java migration remains incomplete.",
        "platform_routes": ["/admin/ (Django)", "/i18n/ (Django)", "/static/ (Django UI)", "/media/ (Django public origin; Java also serves configured media)"],
        "new_operational_routes": [
            {"method": "GET", "url": "/internal/ready/", "owner": "ciudades_del_mundo.local_urls.ready", "origin": "public loopback Django in both modes", "response": "JSON status ready or error, 200/503", "effects": "Read-only Django readiness checks; no SQL writes"},
            {"method": "GET", "url": "/internal/live", "owner": "org.beograd.web.OperationsController.live", "origin": "internal loopback JVM in Java mode", "response": "JSON status up, 200", "effects": "None"},
            {"method": "GET", "url": "/internal/ready", "owner": "org.beograd.web.OperationsController.ready", "origin": "internal loopback JVM in Java mode", "response": "JSON status ready or error, 200/503", "effects": "Read-only SQL dependency checks"},
            {"method": "POST", "url": "/internal/shutdown", "owner": "org.beograd.web.OperationsController.stop", "origin": "internal loopback JVM in Java mode", "response": "202 accepted or 404", "permissions": "Per-launch ephemeral shutdown token", "effects": "Graceful JVM shutdown; no SQL writes"},
        ],
        "routes": routes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    result = inventory()
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(f"{len(result['routes'])} routes: {args.output}")
    else:
        print(content)
    if args.markdown:
        rows = ["# Inventario de rutas de los backends Java y Python", "",
                "Evidencia estática regenerable con `py -B tools/inventory_java_api.py --output docs/architecture/java-api-inventory.json --markdown docs/architecture/java-api-inventory.md`.", "",
                f"{len(result['routes'])} rutas Python mantenidas. Python sirve todas las rutas; en modo Java se reenvían {len(IMPLEMENTED)} rutas de la allowlist a la JVM. Las pendientes mantienen su propietario Django. La migración Java continúa incompleta. Este inventario es evidencia estática; no acredita equivalencia en ejecución.", "",
                "Los detalles de entradas, respuestas, errores, middleware, llamadas, consumidores y referencias de pruebas están en [el JSON](java-api-inventory.json). `sin restricción observada` significa que el controlador no limita el método; CSRF se aplica a métodos inseguros. Las ramas son comprobaciones estáticas, no una declaración OpenAPI inferida.", "",
                "| URL | Métodos/ramas observados | Tipo de respuesta | Controlador | Java |",
                "| --- | --- | --- | --- | --- |"]
        for route in result['routes']:
            rows.append(f"| `{route['url']}` | {', '.join(route.get('method_branches', [])) or 'sin restricción observada'} | {', '.join(route.get('classification', ['redirect']))} | `{route['owner']}` | {'Implementado en Java' if route['name'] in IMPLEMENTED else 'Django; implementación Java pendiente'} |")
        args.markdown.write_text('\n'.join(rows) + '\n', encoding='utf-8')


if __name__ == "__main__":
    main()
