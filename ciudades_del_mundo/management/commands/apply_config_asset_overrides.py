"""Apply manually configured visual-asset corrections for one SQL config."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ciudades_del_mundo.management.commands.scrape_subdivisions_with_assets import (
    _apply_configured_wikidata_asset_overrides,
    _config_data_for_slug,
    _mirror_country_assets_to_root_admin_areas,
)
from ciudades_del_mundo.services.scraping_configs import export_scraping_configs_to_toml
from ciudades_del_mundo.services.visual_assets import visual_asset_tables_exist
from ciudades_del_mundo.web.task_progress import write_config_progress


class Command(BaseCommand):
    help = "Guarda/aplica las correcciones manuales de banderas y escudos de una configuración."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug de la configuración SQL a corregir.")
        parser.add_argument(
            "--no-export",
            action="store_true",
            help="No exportar el TOML después de aplicar las correcciones.",
        )

    def handle(self, *args, **options):
        slug = str(options["slug"] or "").strip()
        if not slug:
            raise CommandError("Debes indicar un slug.")
        if not visual_asset_tables_exist():
            raise CommandError("La tabla de assets visuales no existe. Ejecuta migraciones.")

        config_data = _config_data_for_slug(slug)
        country_code = str(config_data.get("country_code") or slug)
        write_config_progress(slug, "populating", detail="Guardando correcciones de escudos y banderas")

        applied = _apply_configured_wikidata_asset_overrides(
            slug,
            country_code,
            config_data=config_data,
            logger=self.stdout.write,
        )
        mirrored = _mirror_country_assets_to_root_admin_areas(country_code, logger=self.stdout.write)

        exported_count = 0
        if not options.get("no_export"):
            exported = export_scraping_configs_to_toml(force=True, slugs=[slug])
            exported_count = len(exported or []) if not isinstance(exported, bool) else int(exported)

        write_config_progress(slug, "populated", detail="Correcciones de assets guardadas")
        self.stdout.write(
            self.style.SUCCESS(
                f"Correcciones aplicadas: {applied}. Assets raíz sincronizados: {mirrored}. TOML exportado: {exported_count}."
            )
        )
