"""Clear scraped AdminArea rows for one SQL scraping config."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.translation import gettext as _

from ciudades_del_mundo.infrastructure.django.admin_area_deletion import (
    SQLITE_SAFE_DELETE_BATCH_SIZE,
    AdminAreaDeletionProgress,
    delete_admin_area_country,
)
from ciudades_del_mundo.infrastructure.django.visual_asset_deletion import (
    VisualAssetDeletionResult,
    delete_visual_assets_for_country,
)
from ciudades_del_mundo.models import AdminArea, ScrapingConfig
from ciudades_del_mundo.web.task_progress import write_config_progress


CLEAR_DELETE_BATCH_SIZE = SQLITE_SAFE_DELETE_BATCH_SIZE


def _delete_admin_area_country(
    country_code: str,
    *,
    batch_size: int | None = None,
    on_batch=None,
) -> tuple[int, int]:
    """Delete one source country's AdminArea rows in explicit safe batches."""
    return delete_admin_area_country(
        country_code,
        batch_size=batch_size or CLEAR_DELETE_BATCH_SIZE,
        on_batch=on_batch,
    )


def _resolve_country_code(identifier: str) -> tuple[str, list[str]]:
    """Resolve a CLI identifier to the source AdminArea CountryCode.

    The clear operation is a data operation over ``AdminArea.country_code``. The
    SQL config slug is only a UI/config identifier, so it is accepted as a
    fallback for existing shortcuts and old background tasks.
    """
    country_records = list(
        ScrapingConfig.objects.filter(country_code__iexact=identifier)
        .only("slug", "country_code")
        .order_by("slug")
    )
    if country_records:
        country_code = country_records[0].country_code
        return country_code, [record.slug for record in country_records]

    slug_record = (
        ScrapingConfig.objects.filter(slug=identifier)
        .only("slug", "country_code")
        .first()
    )
    if not slug_record:
        raise CommandError(_("No hay datos"))

    country_code = slug_record.country_code or slug_record.slug
    slugs = list(
        ScrapingConfig.objects.filter(country_code__iexact=country_code)
        .order_by("slug")
        .values_list("slug", flat=True)
    )
    if slug_record.slug not in slugs:
        slugs.append(slug_record.slug)
    return country_code, slugs


class Command(BaseCommand):
    help = _("Clears scraped AdminArea rows for a SQL scraping config CountryCode.")

    def _write(self, message, *, style=None):
        self.stdout.write(style(message) if style else message)
        self.stdout.flush()

    def _log_clear_batch(self, progress: AdminAreaDeletionProgress) -> None:
        self._write(
            _(
                "Limpiando: lote=%(batch)s borradas=%(deleted)s/%(total)s "
                "relaciones_limpiadas=%(relations)s"
            )
            % {
                "batch": progress.batch_number,
                "deleted": progress.deleted,
                "total": progress.total,
                "relations": progress.references_cleared,
            }
        )

    def add_arguments(self, parser):
        parser.add_argument(
            "country_code",
            help=_(
                "CountryCode/AdminArea.country_code a limpiar. "
                "Tambien acepta el slug de una configuracion SQL por compatibilidad."
            ),
        )

    def handle(self, *args, **options):
        identifier = str(options["country_code"] or "").strip()
        if not identifier:
            raise CommandError(_("No hay datos"))

        country_code, affected_slugs = _resolve_country_code(identifier)
        for slug in affected_slugs:
            write_config_progress(slug, "clearing")
        batch_size = CLEAR_DELETE_BATCH_SIZE
        total_before_delete = AdminArea.objects.filter(country_code=country_code).count()
        self._write(
            _("Limpiando: country_code=%(country_code)s filas=%(total)s lote=%(batch_size)s")
            % {
                "country_code": country_code,
                "total": total_before_delete,
                "batch_size": batch_size,
            }
        )

        asset_result = VisualAssetDeletionResult()
        try:
            with transaction.atomic():
                asset_result = delete_visual_assets_for_country(
                    country_code,
                    aliases=affected_slugs,
                )
                self._write(
                    _(
                        "Limpiando assets: country_code=%(country_code)s "
                        "assets=%(assets)s traducciones_assets=%(translations)s archivos_media=%(files)s"
                    )
                    % {
                        "country_code": country_code,
                        "assets": asset_result.assets,
                        "translations": asset_result.translations,
                        "files": asset_result.local_files,
                    }
                )
                total, deleted = _delete_admin_area_country(
                    country_code,
                    batch_size=batch_size,
                    on_batch=self._log_clear_batch,
                )
                ScrapingConfig.objects.filter(slug__in=affected_slugs).update(
                    is_valid=False, validation_error=""
                )
        finally:
            for slug in affected_slugs:
                write_config_progress(slug, "pending")

        self._write(
            f"{_('Limpiar')}: country_code={country_code} "
            f"configs={','.join(affected_slugs)} rows={total} deleted={deleted} "
            f"assets={asset_result.assets} traducciones_assets={asset_result.translations} "
            f"archivos_media={asset_result.local_files}"
        )
