from __future__ import annotations

from django.core.management import BaseCommand, CommandError, call_command

from ciudades_del_mundo.infrastructure.django.visual_asset_deletion import (
    delete_visual_assets_for_country,
)


class Command(BaseCommand):
    help = "Limpia los datos de un país y también sus recursos visuales asociados."

    def add_arguments(self, parser):
        parser.add_argument("country_code", help="Código del país a limpiar.")

    def handle(self, *args, **options):
        country_code = str(options.get("country_code") or "").strip()
        if not country_code:
            raise CommandError("Debes indicar un country_code.")

        # clear_config_data is now the canonical cleanup command and already
        # deletes visual assets.  This command remains as a compatibility alias
        # for existing queued tasks, buttons and scripts.
        call_command("clear_config_data", country_code)
        self.stdout.write(self.style.SUCCESS(f"OK {country_code}: datos y assets limpiados"))


def _delete_visual_assets_for_country(country_code: str) -> tuple[int, int]:
    """Backward-compatible helper kept for older tests/imports."""
    result = delete_visual_assets_for_country(country_code)
    return result.assets, result.translations
