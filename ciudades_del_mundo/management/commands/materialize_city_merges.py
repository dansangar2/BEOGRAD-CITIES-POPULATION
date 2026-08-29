"""Apply configured city unifications to already populated AdminArea rows."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext as _

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.models import ScrapingConfig
from ciudades_del_mundo.services.configured_city_materializer import materialize_configured_cities_for_slug


class Command(BaseCommand):
    help = _("Materializa unificaciones SQL [[cities]] en AdminArea sin scraping.")

    def add_arguments(self, parser):
        parser.add_argument(
            "slugs",
            nargs="*",
            help=_("Slugs de configuracion opcionales. Si se omiten, procesa todas las configs SQL con [[cities]]."),
        )

    def handle(self, *args, **options):
        slugs = list(options.get("slugs") or [])
        if not slugs:
            slugs = list(
                ScrapingConfig.objects.filter(cities_count__gt=0).order_by("slug").values_list("slug", flat=True)
            )
        if not slugs:
            raise CommandError(_("No se encontraron configs SQL con ciudades configuradas."))

        failed = []
        for slug in slugs:
            try:
                write_lock = sqlite_write_lock_if_needed()
                if write_lock is None:
                    result = materialize_configured_cities_for_slug(slug)
                else:
                    with write_lock:
                        result = materialize_configured_cities_for_slug(slug)
            except (OperationalError, ProgrammingError, ValueError) as exc:
                failed.append((slug, str(exc)))
                self.stderr.write(self.style.ERROR(f"ERROR {slug}: {exc}"))
                continue

            detail = (
                f"configs={result.configs_total}, applied={result.configs_applied}, "
                f"created={result.created}, updated={result.updated}, rows={result.rows_loaded}"
            )
            if result.skipped_reason:
                detail = f"{detail}, skipped={result.skipped_reason}"
            self.stdout.write(self.style.SUCCESS(f"OK {slug}: {detail}"))

        if failed:
            raise CommandError(_("%(count)s materializacion(es) de ciudades fallaron.") % {"count": len(failed)})
