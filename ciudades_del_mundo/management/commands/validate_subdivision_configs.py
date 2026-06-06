"""Validate SQL-backed subdivision configs before running expensive scrape jobs."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository
from ciudades_del_mundo.models import ScrapingConfig
from ciudades_del_mundo.web.task_progress import write_config_progress
from ciudades_del_mundo.services.scraping_config_extensions import attach_runtime_config_extensions


class Command(BaseCommand):
    help = "Validates subdivision scraping configs stored in SQL."

    def _write(self, message, *, style=None, stderr=False):
        stream = self.stderr if stderr else self.stdout
        stream.write(style(message) if style else message)
        stream.flush()

    def add_arguments(self, parser):
        parser.add_argument(
            "countries",
            nargs="*",
            help="Optional config slugs to validate. If omitted, all configs are validated.",
        )

    def handle(self, *args, **options):
        repository = PythonScrapingConfigRepository()
        countries = options["countries"] or repository.list_slugs()
        if not countries:
            raise CommandError(
                "No SQL subdivision configs found. Run 'py manage.py sync_scraping_configs' to import temporary TOML seeds."
            )

        failed = []
        for slug in countries:
            write_config_progress(slug, "validating")
            try:
                config = repository.get(slug)
                attach_runtime_config_extensions([config])
            except Exception as exc:
                failed.append((slug, str(exc)))
                ScrapingConfig.objects.filter(slug=slug).update(is_valid=False, validation_error=str(exc))
                write_config_progress(slug, "failed", detail=str(exc))
                self._write(f"ERROR {slug}: {exc}", style=self.style.ERROR, stderr=True)
                continue

            ScrapingConfig.objects.filter(slug=slug).update(is_valid=True, validation_error="")
            write_config_progress(slug, "validated")
            self._write(
                f"OK {slug}: pages={len(config.pages)}, cities={len(config.cities)}, "
                f"representation={'yes' if config.representation else 'no'}",
                style=self.style.SUCCESS,
            )

        if failed:
            raise CommandError(f"{len(failed)} subdivision config(s) failed validation.")
