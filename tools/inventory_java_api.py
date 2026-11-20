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
WEB = ROOT / "old-backend-python/ciudades_del_mundo/web"
IMPLEMENTED = {"api_country_summary", "dashboard_population_data", "api_derived_summary", "dashboard_derived_data", "config_export_data_csv"}


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
        record = {"name": name, "url": "/" + route, "owner": owner, "python_state": "archived and disabled", "java_status": "disabled; Python HTTP archived"}
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
                "security": "Historical Django middleware evidence; the Python endpoint is disabled. Native Java routes own their HTTP security.",
                "calls": sorted({ast.unparse(n.func) for n in calls}),
                "direct_effects_observed": sorted({ast.unparse(n.func) for n in calls if re.search(r"\.(save|delete|update|create|start|cancel|write|unlink|mkdir)$", ast.unparse(n.func))}),
                "imports": imports,
                "consumers": [p for p, text in consumers.items() if name in text or (route and route in text)],
                "existing_test_references": [p for p, text in test_sources.items() if name in text or (route and route in text)],
                "equivalence": "EquivalenceTest with frozen synthetic SQL/HTTP contracts; no Python execution (nine languages, seven methods, Accept variants)" if name in IMPLEMENTED else "disabled; pending Java implementation and characterization",
            })
        routes.append(record)
    return {
        "scope": "Historical Python web urlpatterns. Python HTTP is archived and disabled; only the five implemented Java routes remain active.",
        "limitations": "Historical static evidence only; pending routes are disabled, not migrated or proxied. No archived code is executed by the normal build/startup.",
        "platform_routes": ["/admin/ (disabled)", "/i18n/ (disabled)", "/static/ (Python UI disabled)", "/media/ (native Java, configured root containment)"],
        "new_operational_routes": [
            {"method": "GET", "url": "/internal/ready/", "owner": "org.beograd.web.OperationsController.ready", "origin": "public loopback JVM", "response": "JSON status ready or error, 200/503", "effects": "Read-only Java readiness checks; no SQL writes"},
            {"method": "GET", "url": "/internal/live", "owner": "org.beograd.web.OperationsController.live", "origin": "public loopback JVM", "response": "JSON status up, 200", "effects": "None"},
            {"method": "GET", "url": "/internal/ready", "owner": "org.beograd.web.OperationsController.ready", "origin": "public loopback JVM", "response": "JSON status ready or error, 200/503", "effects": "Read-only SQL dependency checks"},
            {"method": "POST", "url": "/internal/shutdown", "owner": "org.beograd.web.OperationsController.stop", "origin": "public loopback JVM", "response": "202 accepted or 404", "permissions": "Per-launch ephemeral shutdown token", "effects": "Graceful JVM shutdown; no SQL writes"},
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
        rows = ["# Inventario de rutas para la migración Java", "", 
                "Evidencia estática regenerable con `py -B tools/inventory_java_api.py --output docs/architecture/java-api-inventory.json --markdown docs/architecture/java-api-inventory.md`.", "",
                "107 rutas Python históricas. La API antigua está archivada y deshabilitada; las rutas pendientes no se ejecutan ni se reenvían a Django. Solo están activas las cinco rutas Java implementadas, además de comprobaciones operativas y media. Las pruebas Java usan contratos congelados, nueve idiomas y siete métodos HTTP sin ejecutar Python.", "",
                "Los detalles de entradas, respuestas, errores, middleware, llamadas, consumidores y referencias de pruebas están en [el JSON](java-api-inventory.json). `sin restricción observada` significa que el controlador no limita el método; CSRF se aplica a métodos inseguros. Las ramas son comprobaciones estáticas, no una declaración OpenAPI inferida.", "",
                "| URL | Métodos/ramas observados | Tipo de respuesta | Controlador | Java |",
                "| --- | --- | --- | --- | --- |"]
        for route in result['routes']:
            rows.append(f"| `{route['url']}` | {', '.join(route.get('method_branches', [])) or 'sin restricción observada'} | {', '.join(route.get('classification', ['redirect']))} | `{route['owner']}` | {'Implementado en Java' if route['name'] in IMPLEMENTED else 'Deshabilitada'} |")
        args.markdown.write_text('\n'.join(rows) + '\n', encoding='utf-8')


if __name__ == "__main__":
    main()
