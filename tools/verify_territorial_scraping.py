"""Offline acceptance across separate CLI processes and disposable SQLite.

Tests the production local expert system by default. --engine provider tests
the external adapter with simulated HTTP/model responses. Every subprocess
denies socket connections. No project database or paid provider is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "ciudades_del_mundo/tests/fixtures/territorial_spain.json"

# Deliberately test-only bootstrap: production has no import hook or alternative
# provider selected by a test environment variable. This code is only passed to
# disposable verification processes by this tool.
BOOTSTRAP = r'''
import io
import json
import os
import socket
import sys

def offline(*args, **kwargs):
    raise AssertionError("Network connections are forbidden in territorial acceptance")

socket.socket.connect = offline
socket.socket.connect_ex = offline
import django
django.setup()
from django.core.management import call_command
from django.utils import timezone
from ciudades_del_mundo.infrastructure.ai import json_provider
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore, json_data
from ciudades_del_mundo.application.territorial_reconciliation import compare_entities
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import content_hash
from ciudades_del_mundo.models import AdminArea, ScrapingConfig, TerritorialRun, WebTask
from ciudades_del_mundo.tests.territorial_support import baseline_entities, valid_proposal, reference_document

spec = json.load(sys.stdin)
requests = []

def simulated_transport(request, *, timeout):
    if spec["engine"] != "provider":
        raise AssertionError("The local engine must not call an HTTP provider")
    if request.full_url != "https://territorial-provider.invalid/v1/chat/completions":
        raise AssertionError("Unexpected provider endpoint")
    body = json.loads(request.data)
    payload = json.loads(body["messages"][-1]["content"])
    if "pages" in payload["evidence"] or "baseline" in payload:
        raise AssertionError("The model must not receive raw HTML or the regression baseline")
    requests.append({"timeout": timeout, "output_limit": body.get("max_completion_tokens")})
    if spec.get("invalid_response"):
        content = {"schema_version": 999}
    elif not payload["tool_results"]:
        content = {"tool": "reference", "ids": [payload["reference"]["entities"][0]["id"]]}
    else:
        content = valid_proposal(payload["reference"], payload["evidence"], payload["evidence_version"])
    # Usage is intentionally absent: this transport double cannot measure tokens.
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode())

json_provider.urlopen = simulated_transport
output = io.StringIO()
try:
    action = spec.get("action", "command")
    if action == "seed_existing":
        AdminArea.objects.create(id="spain_unobserved", country_code="spain", code="unobserved",
            name="Synthetic pre-existing unobserved row", level=4)
        ScrapingConfig.objects.update_or_create(slug="spain", defaults={"country_code": "spain",
            "content": "invalid traditional recipe, preserved for isolation acceptance",
            "content_hash": "intentionally uncached", "is_valid": False})
        result = {}
    elif action == "inspect":
        run = TerritorialRun.objects.select_related("reference", "evidence").order_by("-created_at").first()
        areas = json_data(list(AdminArea.objects.order_by("id").values()))
        configs = json_data(list(ScrapingConfig.objects.order_by("pk").values()))
        result = {"area_count": len(areas), "areas_hash": content_hash(areas),
            "configs_hash": content_hash(configs), "unobserved_retained": any(e["id"] == "spain_unobserved" for e in areas)}
        if run:
            store = DjangoTerritorialStore()
            expected = baseline_entities()
            expected_ids = {e.id for e in expected}
            actual = [e for e in store.baseline(store.snapshot(run)) if e.id in expected_ids]
            result.update({"run": run.pk, "status": run.status, "plan_hash": run.plan_hash,
                "reference_hash": run.reference.content_hash, "evidence_version": run.evidence.version,
                "differences": json_data(compare_entities(expected, actual)), "result": run.result,
                "approved_plan_matches": run.approved_plan == run.plan,
                "metrics": run.metrics, "diagnostics": run.diagnostics})
    elif action == "change_inputs":
        ScrapingConfig.objects.filter(pk="spain").update(content="concurrent traditional configuration change")
        result = {}
    elif action in ("unmatched_reference", "restore_reference"):
        document = reference_document()
        document["version"] += "-unmatched" if action == "unmatched_reference" else "-restored"
        if action == "unmatched_reference":
            document["entities"][0]["names"] = ["Synthetic unmatched country identity"]
        DjangoTerritorialStore().import_reference(document, reviewed_by="synthetic acceptance fault scenario")
        result = {}
    elif action == "cancel_next":
        task = WebTask.objects.create(id="acceptance-cancel", key="territorial:spain", label="test",
            status="cancelled", cancel_requested=True, created_at=timezone.now())
        result = {"task": task.pk}
    else:
        # Raise if AI routing tries to load a pre-existing TOML recipe. The
        # invalid SQL recipe above remains present throughout the verification.
        if spec["args"] and spec["args"][0] == "scrape_subdivisions":
            import ciudades_del_mundo.management.commands.scrape_subdivisions as command_owner
            def forbidden_recipe(*args, **kwargs):
                raise AssertionError("AI routing must not instantiate the traditional config repository")
            command_owner.PythonScrapingConfigRepository = forbidden_recipe
        call_command(*spec["args"], stdout=output, stderr=output)
        result = {"stdout": output.getvalue()}
    print(json.dumps({"ok": True, "result": result, "transport_calls": len(requests)}))
except Exception as exc:
    print(json.dumps({"ok": False, "type": type(exc).__name__, "error": str(exc),
        "stdout": output.getvalue(), "transport_calls": len(requests)}))
    sys.exit(1)
'''


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify(python: str, engine: str = "local") -> dict:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="territorial acceptance ") as directory:
        temporary = Path(directory)
        settings = temporary / "territorial_acceptance_settings.py"
        settings.write_text(
            "from ciudades_del_mundo.tests.isolated_settings import *\n"
            f"BASE_DIR = __import__('pathlib').Path({str(temporary)!r})\n"
            f"DATABASES['default']['NAME'] = {str(temporary / 'acceptance.sqlite3')!r}\n",
            encoding="utf-8")
        reference_path = temporary / "reviewed reference.json"
        pages_path = temporary / "frozen pages.json"
        reference_path.write_text(json.dumps(fixture["reference"]), encoding="utf-8")
        pages_path.write_text(json.dumps(fixture["pages"]), encoding="utf-8")
        environment = os.environ.copy()
        environment.update({"PYTHONDONTWRITEBYTECODE": "1", "DJANGO_SETTINGS_MODULE": settings.stem,
            "BEOGRAD_DJANGO_SECRET_KEY": "disposable-territorial-acceptance-only"})
        if engine == "provider":
            environment.update({"CIUDADES_AI_API_KEY": "simulated-key", "CIUDADES_AI_MODEL": "simulated-transport",
                "CIUDADES_AI_BASE_URL": "https://territorial-provider.invalid/v1"})
        else:
            for name in ("CIUDADES_AI_API_KEY", "CIUDADES_AI_MODEL", "CIUDADES_AI_BASE_URL"):
                environment.pop(name, None)
        # Never inherit task IDs/paths that could touch a user's running task.
        for name in ("CIUDADES_WEB_TASK_ID", "CIUDADES_WEB_TASK_PROGRESS_PATH"):
            environment.pop(name, None)
        environment["PYTHONPATH"] = os.pathsep.join([str(temporary), str(ROOT)])
        subprocesses = 0

        def run(spec, *, expect_success=True, task=None):
            nonlocal subprocesses
            env = environment.copy()
            spec["engine"] = engine
            if task:
                env["CIUDADES_WEB_TASK_ID"] = task
            process = subprocess.run([python, "-B", "-c", BOOTSTRAP], cwd=ROOT, env=env,
                input=json.dumps(spec), text=True, encoding="utf-8", capture_output=True, timeout=90)
            subprocesses += 1
            try:
                result = json.loads(process.stdout)
            except ValueError as exc:
                raise AssertionError(f"Verification process failed: {process.stderr[-3000:]}") from exc
            require(bool(result["ok"]) == expect_success, f"Unexpected verification result: {result}")
            require((process.returncode == 0) == expect_success, f"Unexpected process exit: {result}")
            return result

        def command(*args, **kwargs):
            if args and args[0] == "scrape_subdivisions":
                args = (*args, "--ai-engine", engine)
            return run({"args": list(args)}, **kwargs)

        def inspect():
            return run({"action": "inspect"})["result"]

        command("migrate", "--noinput", "--verbosity", "0")
        command("import_territorial_reference", str(reference_path), "--reviewed-by", "synthetic acceptance reviewer")
        imported = command("import_territorial_evidence", "spain", str(pages_path))["result"]["stdout"]
        evidence_id = imported.split("evidence=", 1)[1].split()[0]
        run({"action": "seed_existing"})
        before = inspect()
        generated = command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", evidence_id)
        require(generated["transport_calls"] == (2 if engine == "provider" else 0), "Unexpected HTTP provider calls")
        pending = inspect()
        require(pending["status"] == "validated", "Proposal was not validated")
        require(pending["areas_hash"] == before["areas_hash"], "Proposal modified current areas")
        run_id, plan_hash = pending["run"], pending["plan_hash"]
        review = command("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", run_id)
        require(review["transport_calls"] == 0, "Review called the provider")
        review_payload = json.loads(review["result"]["stdout"])
        require(review_payload["expected"] == fixture["reference"], "Reviewed reference changed")
        require(review_payload["plan_hash"] == plan_hash, "Reviewed plan hash changed")
        rejected = command("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", run_id,
            "--plan-hash", "unreviewed-hash", expect_success=False)
        require("approved" in rejected["error"], "Unreviewed plan did not have a concrete rejection")
        require(inspect()["areas_hash"] == before["areas_hash"], "Rejected plan changed current areas")
        apply_args = ("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", run_id, "--plan-hash", plan_hash)
        applied_result = command(*apply_args)
        require(applied_result["transport_calls"] == 0, "Application called the provider")
        applied = inspect()
        require(applied["status"] == "applied" and applied["approved_plan_matches"], "Approved plan not committed")
        require(not applied["differences"], f"Committed SQL differs from the captured traditional result: {applied['differences']}")
        require(applied["unobserved_retained"] and applied["result"]["deleted"] == 0, "Unobserved data was deleted")
        require(applied["configs_hash"] == before["configs_hash"], "Traditional configuration changed")
        repeated_result = command(*apply_args)
        repeated = inspect()
        require(repeated["areas_hash"] == applied["areas_hash"], "Idempotent application wrote areas/timestamps again")
        require(repeated_result["result"]["stdout"] == applied_result["result"]["stdout"], "Repeated application changed its result")
        if engine == "provider":
            invalid = run({"args": ["scrape_subdivisions", "spain", "--strategy", "ai", "--ai-engine", "provider", "--evidence-id", evidence_id],
                "invalid_response": True}, expect_success=False)
            require(invalid["transport_calls"] == 1, "Invalid proposal was unexpectedly retried")
        else:
            run({"action": "unmatched_reference"})
            invalid = command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", evidence_id, expect_success=False)
            require(invalid["transport_calls"] == 0, "Unresolved local inference used a provider")
        invalid_state = inspect()
        require(invalid_state["status"] == "invalid" and invalid_state["diagnostics"], "Invalid response lacks persisted diagnostics")
        require(invalid_state["areas_hash"] == applied["areas_hash"], "Invalid response changed areas")
        if engine == "local":
            run({"action": "restore_reference"})
        command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", evidence_id)
        stale = inspect()
        run({"action": "change_inputs"})
        rejected = command("scrape_subdivisions", "spain", "--strategy", "ai", "--territorial-run", stale["run"],
            "--plan-hash", stale["plan_hash"], expect_success=False)
        require("changed" in rejected["error"], "Concurrent input change was not detected")
        require(inspect()["areas_hash"] == applied["areas_hash"], "Stale inputs caused area writes")
        cancelled_task = run({"action": "cancel_next"})["result"]["task"]
        cancelled = command("scrape_subdivisions", "spain", "--strategy", "ai", "--evidence-id", evidence_id,
            task=cancelled_task, expect_success=False)
        require(cancelled["transport_calls"] == 0, "Cancelled task called provider")
        cancelled_state = inspect()
        require(cancelled_state["status"] == "cancelled", "Cancellation was not persisted")
        require(cancelled_state["areas_hash"] == applied["areas_hash"], "Cancellation modified areas")
        return {"passed": True, "mode": ("offline; production local symbolic engine; no simulated inference" if engine == "local"
            else "offline; real CLI and provider adapter; simulated HTTP/model"),
            "database": "disposable SQLite, deleted after verification", "subprocesses": subprocesses,
            "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "scope": fixture["reference"]["scope"], "frozen_pages": len(fixture["pages"]),
            "expected_entities": len(fixture["baseline"]), "committed_entities": applied["result"]["found"],
            "entity_differences": applied["differences"], "reference_hash": applied["reference_hash"],
            "evidence_version": applied["evidence_version"],
            "checks": {"review_before_apply": True,
                "no_manual_recipe_dependency": True, "traditional_config_preserved": True,
                "unobserved_rows_retained": True, "idempotent_apply": True, "invalid_or_unresolved_plan_rejected": True,
                "stale_inputs_rejected": True, "unreviewed_hash_rejected": True, "cancellation": True},
            "local_engine_tested": engine == "local", "real_provider_tested": False,
            "actual_tokens": None, "actual_cost": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--engine", choices=("local", "provider"), default="local")
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.python, arguments.engine), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
