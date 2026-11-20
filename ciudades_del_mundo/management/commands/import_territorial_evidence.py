"""Explicit import of frozen HTML pages; observations are parsed locally."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from ciudades_del_mundo.domain.territorial_reconciliation import parse_reference
from ciudades_del_mundo.infrastructure.django.territorial_reconciliation import DjangoTerritorialStore
from ciudades_del_mundo.infrastructure.scraping.territorial_evidence import freeze_pages


class Command(BaseCommand):
    help = 'Import frozen pages JSON {"https://www.citypopulation.de/en/country/.../": "<html>"} into SQL.'

    def add_arguments(self, parser):
        parser.add_argument("country")
        parser.add_argument("path")

    def handle(self, *args, **options):
        try:
            store = DjangoTerritorialStore()
            reference = parse_reference(store.reference(options["country"]))
            pages = json.loads(Path(options["path"]).read_text(encoding="utf-8"))
            record = store.import_evidence(freeze_pages(reference.country, reference.entry_path, pages))
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"evidence={record.pk} country={record.country} version={record.version}")
