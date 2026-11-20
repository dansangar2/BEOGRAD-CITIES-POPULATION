"""Explicit CLI for retained Python use cases; never starts an HTTP worker."""
import json
from django.core.management.base import BaseCommand, CommandError
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore
from ciudades_del_mundo.models import TerritorialRun
from ciudades_del_mundo.services.territorial_reconciliation import execute_run, task_checkpoint, effective_status
from ciudades_del_mundo.web.task_progress import current_task_id, write_config_progress


class Command(BaseCommand):
    help = "Create, review or explicitly apply a SQL territorial proposal; HTTP integration is separate."

    def add_arguments(self, parser):
        parser.add_argument("country_or_run")
        parser.add_argument("--new", action="store_true", help="Interpret the argument as a country and persist a new run.")
        parser.add_argument("--ai-engine", choices=("local", "provider"), default="local")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--evidence-id", type=int)
        group.add_argument("--discover", action="store_true", help="Explicitly allow live same-country discovery.")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--plan-hash")
        parser.add_argument("--show", action="store_true", help="Read stored inputs, plan, differences and diagnostics without provider calls.")

    def handle(self, *args, **options):
        store = DjangoTerritorialStore()
        try:
            if options["new"]:
                if options["apply"] or options["show"] or not (options["evidence_id"] or options["discover"]):
                    raise CommandError("--new requires --evidence-id or --discover and cannot apply/show an unreviewed proposal.")
                run = store.create_run(options["country_or_run"], evidence_id=options["evidence_id"], discover=options["discover"])
            else:
                run = TerritorialRun.objects.select_related("reference", "evidence", "task").filter(pk=options["country_or_run"]).first()
                if run is None:
                    raise CommandError("Unknown territorial run. Use --new with a country and an evidence source.")
            self.stdout.write(f"run={run.pk}")

            def checkpoint():
                task_checkpoint(current_task_id())

            def progress(status):
                self.stdout.write(f"territorial run={run.pk} phase={status}")
                write_config_progress(run.country, status, detail=run.pk)

            if options["show"]:
                evidence = run.evidence.content if run.evidence_id else {}
                self.stdout.write(json.dumps({"id": run.pk, "status": effective_status(run), "expected": run.reference.content,
                    "observed": {k: v for k, v in evidence.items() if k != "pages"}, "plan": run.plan,
                    "plan_hash": run.plan_hash, "inputs_hash": run.inputs_hash, "differences": run.comparison,
                    "diagnostics": run.diagnostics, "metrics": run.metrics, "result": run.result}, ensure_ascii=False, indent=2))
            elif options["apply"]:
                if not options["plan_hash"]:
                    raise CommandError("--apply requires the reviewed --plan-hash.")
                result = store.apply(run.pk, checkpoint=checkpoint, expected_plan_hash=options["plan_hash"])
                self.stdout.write(json.dumps(result))
            else:
                run = execute_run(run.pk, checkpoint=checkpoint, progress=progress, engine=options["ai_engine"])
                self.stdout.write(f"status={run.status} plan_hash={run.plan_hash}")
        except (ValueError, RuntimeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
