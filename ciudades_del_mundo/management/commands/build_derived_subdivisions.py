"""Build ``NuevoAdminArea`` rows from SQL ``DerivedSubdivision`` recipes."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.services.derived_subdivision_builder import (
    build_derived_subdivisions_for_country,
)


class Command(BaseCommand):
    help = (
        "Construye nuevas divisiones NuevoAdminArea desde las recetas SQL "
        "DerivedSubdivision de un pais."
    )

    def add_arguments(self, parser):
        parser.add_argument("country_code", help="Pais fuente, por ejemplo spain.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Borra y reconstruye las nuevas divisiones existentes de ese pais.",
        )
        parser.add_argument(
            "--population-year",
            type=int,
            default=None,
            help="Anio base para source_population_indices.toml.",
        )

    def handle(self, *args, **options):
        try:
            result = build_derived_subdivisions_for_country(
                options["country_code"],
                force=options["force"],
                source_population_year=options.get("population_year"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Construidas %(built)s nuevas divisiones para %(country)s "
                "desde %(records)s receta(s). Ciudad mayor actualizada en %(updated)s fila(s)."
                % {
                    "built": result.built,
                    "country": result.country_code,
                    "records": result.records,
                    "updated": result.most_populated_updated,
                }
            )
        )
