"""Synchronize per-country TOML seed files with the SQL config table."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError, ProgrammingError

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.scraping_configs import (
    export_scraping_configs_to_toml,
    sync_scraping_configs_from_toml,
)


class Command(BaseCommand):
    help = "Import/export per-country subdivisions TOML seeds and SQL ScrapingConfig rows."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Optional config slugs to synchronize.")
        direction = parser.add_mutually_exclusive_group()
        direction.add_argument(
            "--from-toml",
            action="store_true",
            help="Import ciudades_del_mundo/subdivisions/*.toml seed files into SQL. This is the default.",
        )
        direction.add_argument(
            "--to-toml",
            action="store_true",
            help="Export SQL configs back to ciudades_del_mundo/subdivisions/*.toml seed files.",
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

            write_lock = sqlite_write_lock_if_needed()
            if write_lock is None:
                imported = sync_scraping_configs_from_toml(
                    force=bool(options["force"]),
                    only_if_empty=not bool(options["force"]) and not slugs,
                    slugs=slugs,
                )
            else:
                with write_lock:
                    imported = sync_scraping_configs_from_toml(
                        force=bool(options["force"]),
                        only_if_empty=not bool(options["force"]) and not slugs,
                        slugs=slugs,
                    )
            self.stdout.write(self.style.SUCCESS(f"Imported {imported} scraping config(s)."))
        except (OperationalError, ProgrammingError) as exc:
            message = str(exc)
            if (
                "ciudades_del_mundo_scrapingconfig" in message
                or "schema_version" in message
                or "scrape_types" in message
                or "pages_config" in message
            ):
                raise CommandError(
                    "La tabla de configuración no está migrada. Ejecuta primero: py manage.py migrate "
                    "y después repite sync_scraping_configs."
                ) from exc
            raise
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
