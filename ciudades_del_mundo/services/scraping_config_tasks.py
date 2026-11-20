"""Reusable scraping config tasks responsibilities."""
from __future__ import annotations

from ciudades_del_mundo.infrastructure.django.sqlite_write_lock import sqlite_write_lock_if_needed
from ciudades_del_mundo.models import ScrapingConfig
from ciudades_del_mundo.services.scraping_configs import bundled_toml_config_paths, ensure_initial_scraping_configs, scraping_config_table_exists, sync_scraping_configs_from_toml
from django.db import OperationalError, ProgrammingError
from django.utils.translation import gettext as _


def _import_missing_scraping_config_from_toml(slug: str) -> tuple[bool, str]:
    """Import the matching TOML seed for a missing SQL config row."""
    if not scraping_config_table_exists():
        return _ensure_config_available_for_task(slug, import_from_toml=False)
    if not bundled_toml_config_paths([slug]):
        return _ensure_config_available_for_task(slug, import_from_toml=False)
    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            sync_scraping_configs_from_toml(force=False, only_if_empty=False, slugs=[slug])
        else:
            with write_lock:
                sync_scraping_configs_from_toml(force=False, only_if_empty=False, slugs=[slug])
        if ScrapingConfig.objects.filter(slug=slug).exists():
            return True, ""
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
    except Exception as exc:  # noqa: BLE001 - surfaced to AJAX instead of a debug page.
        return False, _("No se pudo importar el TOML: %(error)s") % {"error": exc}
    return _ensure_config_available_for_task(slug, import_from_toml=False)


def _ensure_bulk_configs_available_for_task() -> tuple[bool, str]:
    """Bootstrap bundled TOML configs before bulk config actions when SQL is empty."""
    if not scraping_config_table_exists():
        return False, ""
    try:
        if ScrapingConfig.objects.exists():
            return True, ""
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}

    try:
        write_lock = sqlite_write_lock_if_needed()
        if write_lock is None:
            ensure_initial_scraping_configs(force=False)
        else:
            with write_lock:
                ensure_initial_scraping_configs(force=False)
    except (OperationalError, ProgrammingError) as exc:
        return False, _(
            "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
        ) % {"error": exc}
    except Exception as exc:  # noqa: BLE001 - surfaced to AJAX instead of a debug page.
        return False, _("No se pudo importar el TOML: %(error)s") % {"error": exc}
    return True, ""


def _ensure_config_available_for_task(slug: str, *, import_from_toml: bool = False) -> tuple[bool, str]:
    """Ensure a SQL config can be used before launching validate/scrape."""
    if scraping_config_table_exists():
        try:
            if ScrapingConfig.objects.filter(slug=slug).exists():
                return True, ""
        except (OperationalError, ProgrammingError) as exc:
            return False, _(
                "No se pudo comprobar la tabla de configuraciones. Ejecuta 'py manage.py migrate'. Detalle: %(error)s"
            ) % {"error": exc}
        if import_from_toml:
            return _import_missing_scraping_config_from_toml(slug)

    return False, _(
        "No existe la configuración SQL '%(slug)s'. Ejecuta "
        "'py manage.py sync_scraping_configs %(slug)s' para importarla desde seeds TOML."
    ) % {"slug": slug}
