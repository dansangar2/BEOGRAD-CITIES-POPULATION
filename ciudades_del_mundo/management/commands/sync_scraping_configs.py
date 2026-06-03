"""Synchronize bundled subdivision TOML files into the SQL config table."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ciudades_del_mundo.services.scraping_configs import sync_scraping_configs_from_toml


class Command(BaseCommand):
    help = "Imports ciudades_del_mundo/subdivisions/*.toml into the SQL ScrapingConfig table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing SQL configs with the current TOML file contents.",
        )

    def handle(self, *args, **options):
        imported = sync_scraping_configs_from_toml(
            force=bool(options["force"]),
            only_if_empty=not bool(options["force"]),
        )
        self.stdout.write(self.style.SUCCESS(f"Imported {imported} scraping config(s)."))
