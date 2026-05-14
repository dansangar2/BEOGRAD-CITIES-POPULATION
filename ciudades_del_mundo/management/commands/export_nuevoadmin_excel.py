"""Export `NuevoAdminArea` hierarchies to Excel workbooks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.application.export_nuevo_admin_areas import ExportNuevoAdminAreasToExcel
from ciudades_del_mundo.infrastructure.django.nuevo_admin_area_export_repository import (
    DjangoNuevoAdminAreaExportRepository,
)
from ciudades_del_mundo.infrastructure.excel import SimpleXlsxWriter
from ciudades_del_mundo.models import NuevoAdminArea


class Command(BaseCommand):
    help = (
        "Exporta NuevoAdminArea a un archivo Excel .xlsx jerarquico. "
        "Mantiene una fila por ruta del pais a la hoja y bloques de columnas por nivel."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--country-id",
            required=True,
            help="ID logico del pais/imperio en NuevoAdminArea. Ej: first-spanish-empire.",
        )
        parser.add_argument(
            "--max-level",
            type=int,
            default=None,
            help="Nivel maximo de NuevoAdminArea a incluir.",
        )
        parser.add_argument(
            "--name",
            default=None,
            help="Nombre base del fichero Excel, sin extension.",
        )
        parser.add_argument(
            "--output-dir",
            default=".",
            help="Directorio donde se escribira el .xlsx.",
        )

    def handle(self, *args, **opts):
        country_id = opts["country_id"]
        max_level = opts["max_level"]
        output_dir = Path(opts["output_dir"])

        try:
            root = NuevoAdminArea.objects.only("id", "country_code").get(id=country_id)
        except NuevoAdminArea.DoesNotExist:
            raise CommandError(
                f"No existe NuevoAdminArea con id='{country_id}'. "
                "Ejecuta antes build_new_subdivisions."
            )

        base_name = opts["name"]
        if not base_name:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{root.country_code}_{stamp}"
        output_path = output_dir / f"{base_name}.xlsx"

        exporter = ExportNuevoAdminAreasToExcel(
            repository=DjangoNuevoAdminAreaExportRepository(),
            writer=SimpleXlsxWriter(),
        )

        try:
            result = exporter.run(
                country_id=country_id,
                max_level=max_level,
                output_path=output_path,
            )
        except ValueError as exc:
            raise CommandError(str(exc))

        if result.rows == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"No se encontraron subdivisiones para country_id='{country_id}'."
                )
            )
            return

        levels = ", ".join(str(level) for level in result.levels)
        self.stdout.write(
            self.style.SUCCESS(
                f"Excel generado: {result.path} ({result.rows} filas, niveles: {levels})"
            )
        )
