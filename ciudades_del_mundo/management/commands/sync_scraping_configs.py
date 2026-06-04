"""Synchronize temporary TOML seed files with the SQL config table."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.services.scraping_configs import (
    export_scraping_configs_to_toml,
    sync_scraping_configs_from_toml,
)


class Command(BaseCommand):
    help = "Temporary bridge between bundled TOML seed files and SQL ScrapingConfig rows."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Optional config slugs to synchronize.")
        direction = parser.add_mutually_exclusive_group()
        direction.add_argument(
            "--from-toml",
            action="store_true",
            help="Import TOML seed files into SQL. This is the default.",
        )
        direction.add_argument(
            "--to-toml",
            action="store_true",
            help="Export SQL configs back to TOML seed files.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing destination configs.",
        )
        parser.add_argument(
            "--output-dir",
            default="",
            help="Optional output directory for --to-toml.",
        )

    def handle(self, *args, **options):
        slugs = list(options.get("slugs") or [])
        try:
            if options.get("to_toml"):
                exported = export_scraping_configs_to_toml(
                    force=bool(options["force"]),
                    slugs=slugs,
                    output_dir=options.get("output_dir") or None,
                )
                self.stdout.write(self.style.SUCCESS(f"Exported {exported} scraping config(s)."))
                return

            imported = sync_scraping_configs_from_toml(
                force=bool(options["force"]),
                only_if_empty=not bool(options["force"]) and not slugs,
                slugs=slugs,
            )
            self.stdout.write(self.style.SUCCESS(f"Imported {imported} scraping config(s)."))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
