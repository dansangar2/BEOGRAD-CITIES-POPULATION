"""Validate subdivision TOML files before running expensive scrape jobs."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.infrastructure.scraping import PythonScrapingConfigRepository


class Command(BaseCommand):
    help = "Validates subdivision scraping configs from TOML."

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
            raise CommandError("No subdivision configs found.")

        failed = []
        for slug in countries:
            try:
                config = repository.get(slug)
            except Exception as exc:
                failed.append((slug, str(exc)))
                self.stderr.write(self.style.ERROR(f"ERROR {slug}: {exc}"))
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"OK {slug}: pages={len(config.pages)}, cities={len(config.cities)}, "
                    f"representation={'yes' if config.representation else 'no'}"
                )
            )

        if failed:
            raise CommandError(f"{len(failed)} subdivision config(s) failed validation.")
