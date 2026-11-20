"""Composition of the alternative strategy with SQL, tasks and JSON provider."""
import time

from ciudades_del_mundo.application.ai.territorial import propose
from ciudades_del_mundo.application.ai.local_territorial import LocalTerritorialEngine
from ciudades_del_mundo.application.territorial_reconciliation import compare_entities
from ciudades_del_mundo.domain.territorial_reconciliation import ReconciliationError, parse_reference
from ciudades_del_mundo.infrastructure.ai.json_provider import OpenAICompatibleJsonProvider
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore, json_data
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import ControlledTerritorialDiscovery, content_hash
from ciudades_del_mundo.models import TerritorialRun, WebTask


class ReconciliationCancelled(ReconciliationError):
    pass


def task_checkpoint(task_id: str):
    if task_id:
        task = WebTask.objects.filter(pk=task_id).first()
        if task is None or task.cancel_requested or task.status in ("cancelled", "failed"):
            raise ReconciliationCancelled("task: cancelled or interrupted; no territorial data applied.")


def effective_status(run) -> str:
    if run.status in ("pending", "running", "validated") and run.task_id and run.task:
        if run.task.cancel_requested or run.task.status == "cancelled":
            return "cancelled"
        if run.task.status == "failed":
            return "interrupted"
    return run.status


def execute_run(run_id: str, *, provider=None, discovery=None, checkpoint=None, progress=lambda status: None, engine="local"):
    if engine not in ("local", "provider"):
        raise ReconciliationError("Unknown territorial AI engine; choose local or provider.")
    store = DjangoTerritorialStore()
    run = TerritorialRun.objects.select_related("reference", "evidence", "task").get(pk=run_id)
    checkpoint = checkpoint or (lambda: task_checkpoint(run.task_id))
    if run.status in ("validated", "applied"):
        return run
    if run.status != "pending":
        raise ReconciliationError("run: terminal proposal; create a new run.")
    if not store.claim_run(run.pk):
        raise ReconciliationError("run: another worker already claimed this proposal.")
    started = time.monotonic()
    provider_calls = 0
    try:
        checkpoint()
        # Local inference is the default. A remote provider must be explicit.
        provider = provider or (LocalTerritorialEngine() if engine == "local" else OpenAICompatibleJsonProvider.from_env())
        if isinstance(provider, OpenAICompatibleJsonProvider):
            provider.timeout = min(45, provider.timeout)
            provider.max_completion_tokens = 16_000
        run.status = "running"
        store.save_run(run, ["status", "updated_at"])
        progress("observing")
        if run.evidence_id is None:
            document = (discovery or ControlledTerritorialDiscovery()).discover(parse_reference(run.reference.content), checkpoint=checkpoint)
            checkpoint()
            run.evidence = store.import_evidence(document)
            store.save_run(run, ["evidence", "updated_at"])
        store.verify_inputs(run)
        inputs = store.snapshot(run)
        run.inputs = inputs
        run.inputs_hash = content_hash(inputs)
        store.save_run(run, ["inputs", "inputs_hash", "updated_at"])
        progress("proposing")

        def capture(response):
            run.plan = response if isinstance(response, dict) else {"invalid_response": response}
            store.save_run(run, ["plan", "updated_at"])

        def count_call():
            nonlocal provider_calls
            provider_calls += 1

        run.plan = propose(provider, run.reference.content,
            {k: v for k, v in run.evidence.content.items() if k != "pages"}, run.evidence.version,
            checkpoint=checkpoint, on_response=capture, on_call=count_call)
        checkpoint()
        progress("validating")
        entities = store.compiled(run)
        if content_hash(store.snapshot(run)) != run.inputs_hash:
            raise ReconciliationError("run: inputs changed during proposal; create a new run.")
        run.comparison = json_data(compare_entities(store.baseline(inputs), entities))
        run.plan_hash = content_hash(run.plan)
        run.status = "validated"
        run.diagnostics = []
    except Exception as exc:
        run.status = "cancelled" if isinstance(exc, ReconciliationCancelled) else "invalid"
        run.diagnostics = [{"type": type(exc).__name__, "reason": str(exc)}]
        raise
    finally:
        is_local = isinstance(provider, LocalTerritorialEngine) or (provider is None and engine == "local")
        run.metrics = {"model": getattr(provider, "model", None),
            "engine": "local" if is_local else "provider",
            "usage": getattr(provider, "usage_history", None),
            "engine_calls": provider_calls,
            "provider_calls": 0 if is_local else provider_calls,
            "elapsed_seconds": time.monotonic() - started,
            "limits": {"calls": 6, "retry": 1, "provider_timeout_seconds": getattr(provider, "timeout", None),
                "output_tokens_per_call": getattr(provider, "max_completion_tokens", None), "input_chars": 500_000}}
        store.save_run(run, ["plan", "plan_hash", "status", "diagnostics", "comparison", "metrics", "updated_at"])
        progress(run.status)
    return run
