from __future__ import annotations

from django.core.management import BaseCommand, CommandError

from ciudades_del_mundo.services.visual_assets import (
    ensure_missing_local_asset_files,
    ensure_visual_assets_for_admin_area,
    ensure_visual_assets_for_all_configs,
    ensure_visual_assets_for_config_slug,
    ensure_visual_assets_for_country_admin_areas,
    visual_asset_tables_exist,
)


class Command(BaseCommand):
    help = "Busca/descarga banderas, escudos y sellos persistidos en SQL para paises y subdivisiones."

    def add_arguments(self, parser):
        parser.add_argument("slug", nargs="?", help="Slug de una configuraciÃ³n concreta.")
        parser.add_argument("--all", action="store_true", help="Procesar todas las configuraciones SQL.")
        parser.add_argument("--admin-area", help="Procesar un AdminArea concreto por id.")
        parser.add_argument("--country-subdivisions", help="Procesar subdivisiones visibles de un pais por country_code.")
        parser.add_argument("--levels", default="", help="Niveles separados por coma para --country-subdivisions.")
        parser.add_argument(
            "--repair-local-only",
            action="store_true",
            help="No buscar nuevos assets; solo descargar ficheros locales que falten para assets ya guardados.",
        )
        parser.add_argument("--limit", type=int, default=None, help="LÃ­mite de configs o ficheros a procesar.")
        parser.add_argument("--no-download", action="store_true", help="No descargar imÃ¡genes; guardar solo URLs/metadatos.")

    def handle(self, *args, **options):
        if not visual_asset_tables_exist():
            raise CommandError("La tabla de assets visuales no existe. Ejecuta 'py manage.py migrate'.")

        if options.get("repair_local_only"):
            count = ensure_missing_local_asset_files(limit=options.get("limit"))
            self.stdout.write(self.style.SUCCESS(f"Ficheros locales reparados/descargados: {count}"))
            return

        if options.get("admin_area"):
            area_id = str(options["admin_area"])
            result = ensure_visual_assets_for_admin_area(area_id, download_missing=not options.get("no_download"))
            self.stdout.write(self.style.SUCCESS(result.as_log_line(f"admin_area:{area_id}")))
            return

        if options.get("country_subdivisions"):
            country_code = str(options["country_subdivisions"])
            result = ensure_visual_assets_for_country_admin_areas(
                country_code,
                download_missing=not options.get("no_download"),
                levels=_parse_levels(options.get("levels") or ""),
                limit=options.get("limit"),
            )
            self.stdout.write(self.style.SUCCESS(result.as_log_line(f"admin_areas:{country_code}")))
            return

        slug = options.get("slug")
        if slug:
            result = ensure_visual_assets_for_config_slug(slug, download_missing=not options.get("no_download"))
            self.stdout.write(self.style.SUCCESS(result.as_log_line(slug)))
            return

        if not options.get("all"):
            raise CommandError("Indica un slug, --all, --admin-area, --country-subdivisions o --repair-local-only.")

        result = ensure_visual_assets_for_all_configs(
            download_missing=not options.get("no_download"),
            limit=options.get("limit"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[assets] total: pages={result.scanned_pages}, found={result.found}, "
                f"downloaded={result.downloaded}, missing={result.missing}, errors={result.errors}"
            )
        )


def _parse_levels(value: str) -> tuple[int, ...] | None:
    if not value.strip():
        return None
    levels = []
    for item in value.split(","):
        item = item.strip()
        if item:
            levels.append(int(item))
    return tuple(levels)

