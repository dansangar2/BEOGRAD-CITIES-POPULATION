"""Explicit, reviewed JSON-to-SQL reference import; no implicit seed loading."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore


class Command(BaseCommand):
    help = "Import a versioned, human-reviewed territorial reference JSON into SQL."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--reviewed-by", required=True)

    def handle(self, *args, **options):
        try:
            document = json.loads(Path(options["path"]).read_text(encoding="utf-8"))
            record = DjangoTerritorialStore().import_reference(document, reviewed_by=options["reviewed_by"])
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"reference={record.pk} country={record.country} version={record.version} hash={record.content_hash}")
