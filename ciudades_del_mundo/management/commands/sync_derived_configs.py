"""Import derived-country and subdivision-group TOML seeds into SQL."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.services.derived_config_seeds import (
    bundled_derived_subdivision_paths,
    bundled_new_country_config_paths,
    bundled_subdivision_group_paths,
    import_derived_subdivision_path_records,
    import_new_country_config_path,
    import_subdivision_group_path_records,
)


class Command(BaseCommand):
    help = _(
        "Importa semillas TOML de new_country_configs/, subdivision_groups/groups/<pais>.toml "
        "y subdivision_groups/subdivisions/<pais>.toml en SQL."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "section",
            choices=["new-countries", "groups", "subdivisions"],
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
        paths = self._seed_paths(section, slugs)
        if slugs:
            missing = []
            for slug in slugs:
                matches = self._seed_paths(section, [slug])
                if not matches:
                    missing.append(slug)
            if missing:
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
                record = import_new_country_config_path(path, force=force)
                self.stdout.write(f"[new-countries] {record.country_id}/{record.slug}")
                imported += 1
            elif section == "groups":
                records = import_subdivision_group_path_records(path, force=force)
                for record in records:
                    self.stdout.write(f"[groups] {record.slug}")
                imported += len(records)
            else:
                records = import_derived_subdivision_path_records(path, force=force)
                for record in records:
                    self.stdout.write(f"[subdivisions] {record.slug}")
                imported += len(records)
        return imported

    def _seed_paths(self, section, slugs):
        if section == "new-countries":
            return bundled_new_country_config_paths(slugs)
        if section == "groups":
            return bundled_subdivision_group_paths(slugs)
        return bundled_derived_subdivision_paths(slugs)
