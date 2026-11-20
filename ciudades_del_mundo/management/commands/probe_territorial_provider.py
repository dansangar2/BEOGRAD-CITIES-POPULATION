"""Opt-in paid integration acceptance, intentionally outside the offline suite."""
import json
import os
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ciudades_del_mundo.application.territorial_reconciliation import compare_entities
from ciudades_del_mundo.infrastructure.ai.json_provider import OpenAICompatibleJsonProvider
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore, json_data
from ciudades_del_mundo.models import TerritorialRun
from ciudades_del_mundo.services.territorial_reconciliation import execute_run
from ciudades_del_mundo.tests.territorial_support import baseline_entities, evidence_document, reference_document


class Command(BaseCommand):
    help = "Paid opt-in acceptance against the same synthetic frozen Spain fixture and reference; in-memory SQL only."

    def add_arguments(self, parser):
        parser.add_argument("--allow-paid", action="store_true")

    def handle(self, *args, **options):
        if not options["allow_paid"] or os.environ.get("CIUDADES_TERRITORIAL_REAL_TEST") != "1":
            raise CommandError("Real provider test requires --allow-paid and CIUDADES_TERRITORIAL_REAL_TEST=1.")
        if connection.vendor != "sqlite" or str(connection.settings_dict["NAME"]) != ":memory:":
            raise CommandError("Use --settings=ciudades_del_mundo.tests.isolated_settings (disposable :memory: database only).")
        provider = OpenAICompatibleJsonProvider.from_env()
        call_command("migrate", interactive=False, verbosity=0, skip_checks=True)
        store = DjangoTerritorialStore()
        reference = store.import_reference(reference_document(), reviewed_by="synthetic acceptance fixture; not official")
        evidence = store.import_evidence(evidence_document())
        run = store.create_run(reference.country, evidence_id=evidence.pk)
        try:
            run = execute_run(run.pk, provider=provider)
            differences = json_data(compare_entities(baseline_entities(), store.compiled(run)))
            report = {"passed": not differences, "scope": reference.content["scope"],
                "reference_hash": reference.content_hash, "evidence_version": evidence.version,
                "differences": differences, "metrics": run.metrics}
        except Exception as exc:
            run = TerritorialRun.objects.get(pk=run.pk)
            self.stdout.write(json.dumps({"passed": False, "diagnostics": run.diagnostics, "metrics": run.metrics}, ensure_ascii=False))
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        if differences:
            raise CommandError("Provider proposal differs from the captured traditional fixture; inspect the entity-level report.")
