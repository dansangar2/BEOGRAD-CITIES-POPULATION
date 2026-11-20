"""Opt-in bridge from the scraping entrypoint to territorial AI reconciliation.

AI produces a reviewable plan. Existing parser/linker/repository adapters still
own observations and application; this module never replaces the scraper.
"""
from ciudades_del_mundo.domain.territorial_reconciliation import ReconciliationError
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore
from ciudades_del_mundo.models import TerritorialRun
from ciudades_del_mundo.services.territorial_reconciliation import effective_status, execute_run


class AiScrapingStrategy:
    def __init__(self, *, provider=None, discovery=None, engine="local"):
        self.store = DjangoTerritorialStore()
        self.provider = provider
        self.discovery = discovery
        self.engine = engine

    def propose(self, country: str, *, evidence_id=None, discover=False,
                checkpoint=None, progress=lambda status: None):
        if bool(evidence_id) == bool(discover):
            raise ReconciliationError("AI scraping requires exactly one frozen evidence ID or explicit discovery.")
        run = self.store.create_run(country, evidence_id=evidence_id, discover=discover)
        return execute_run(run.pk, provider=self.provider, discovery=self.discovery,
            checkpoint=checkpoint, progress=progress, engine=self.engine)

    def _run(self, run_id: str, country: str | None):
        run = TerritorialRun.objects.select_related("reference", "evidence", "task").filter(pk=run_id).first()
        if run is None:
            raise ReconciliationError(f"Unknown territorial run {run_id}.")
        if country is not None and run.country != country:
            raise ReconciliationError("Territorial run belongs to another country.")
        return run

    def review(self, run_id: str, *, country: str | None = None) -> dict:
        run = self._run(run_id, country)
        evidence = run.evidence.content if run.evidence_id else {}
        return {"id": run.pk, "country": run.country, "status": effective_status(run),
            "expected": run.reference.content,
            "observed": {k: v for k, v in evidence.items() if k != "pages"},
            "plan": run.plan, "plan_hash": run.plan_hash, "inputs_hash": run.inputs_hash,
            "differences": run.comparison, "diagnostics": run.diagnostics,
            "metrics": run.metrics, "result": run.result}

    def apply(self, run_id: str, *, plan_hash: str, country: str | None = None,
              checkpoint=lambda: None) -> dict:
        if not plan_hash:
            raise ReconciliationError("AI scraping application requires the reviewed plan hash.")
        run = self._run(run_id, country)
        return self.store.apply(run.pk, expected_plan_hash=plan_hash, checkpoint=checkpoint)
