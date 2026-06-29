"""Import derived-country and subdivision-group TOML seeds into SQL."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.derived_config_seeds import (
    bundled_new_country_config_paths,
    bundled_subdivision_group_paths,
    import_new_country_config_seed,
    import_subdivision_group_seed,
)


class Command(BaseCommand):
    help = _("Importa semillas TOML de new_country_configs/ y subdivision_groups/ en SQL.")

    def add_arguments(self, parser):
        parser.add_argument(
            "section",
            choices=["new-countries", "groups"],
            help=_("Seccion SQL que se va a importar."),
        )
        parser.add_argument(
            "slugs",
            nargs="*",
            help=_("Slugs TOML concretos. Si se omiten, se importan todos los de la seccion."),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=_("Sobrescribe filas SQL existentes con el TOML semilla."),
        )

    def handle(self, *args, **options):
        section = options["section"]
        slugs = list(options.get("slugs") or [])
        force = bool(options.get("force"))
        paths = (
            bundled_new_country_config_paths(slugs)
            if section == "new-countries"
            else bundled_subdivision_group_paths(slugs)
        )
        if slugs and len(paths) != len(slugs):
            found = {path.stem for path in paths}
            missing = sorted(set(slugs) - found)
            raise CommandError(_("No existen semillas TOML para: %(slugs)s") % {"slugs": ", ".join(missing)})
        if not paths:
            self.stdout.write(self.style.WARNING(_("No hay semillas TOML para importar.")))
            return

        write_lock = sqlite_write_lock_if_needed()
        try:
            if write_lock is None:
                imported = self._import_paths(section, paths, force=force)
            else:
                with write_lock:
                    imported = self._import_paths(section, paths, force=force)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                _("Importadas %(count)s semilla(s) TOML en %(section)s.")
                % {"count": imported, "section": section}
            )
        )

    def _import_paths(self, section, paths, *, force: bool) -> int:
        imported = 0
        for path in paths:
            slug = path.stem
            if section == "new-countries":
                record = import_new_country_config_seed(slug, force=force)
                self.stdout.write(f"[new-countries] {record.country_id}/{record.slug}")
            else:
                record = import_subdivision_group_seed(slug, force=force)
                self.stdout.write(f"[groups] {record.slug}")
            imported += 1
        return imported
