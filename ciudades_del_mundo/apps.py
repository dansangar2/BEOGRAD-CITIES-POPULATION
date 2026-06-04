from django.apps import AppConfig
from django.db import OperationalError, ProgrammingError
from django.db.backends.signals import connection_created
from django.core.signals import request_started
from django.db.models.signals import post_migrate
import os
import threading


class CiudadesDelMundoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ciudades_del_mundo'
    verbose_name = 'Ciudades del Mundo'

    def ready(self):
        connection_created.connect(_configure_sqlite_connection, dispatch_uid="ciudades_sqlite_pragmas")
        post_migrate.connect(
            _sync_scraping_configs_after_migrate,
            sender=self,
            dispatch_uid="ciudades_sync_scraping_configs_after_migrate",
        )
        # This repair job can write many visual-asset rows/files. Running it
        # automatically on the first request competes with long scrape tasks on
        # SQLite and can make them fail with "database is locked". Keep it opt-in
        # for local maintenance instead of starting it silently while the UI is
        # being used.
        if os.environ.get("CIUDADES_ENABLE_ASSET_STARTUP_REPAIR") == "1":
            request_started.connect(
                _repair_visual_asset_files_on_first_request,
                dispatch_uid="ciudades_repair_visual_asset_files_on_first_request",
            )


def _configure_sqlite_connection(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout = 30000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
    except OperationalError:
        # A running scrape/build may briefly lock SQLite while a new web
        # connection opens. The middleware handles the request-level retry path.
        pass


def _sync_scraping_configs_after_migrate(sender, **kwargs):
    """Seed bundled TOML configs after migrations, not during app initialization."""
    try:
        from ciudades_del_mundo.services.scraping_configs import ensure_initial_scraping_configs

        ensure_initial_scraping_configs(force=False)
        try:
            from ciudades_del_mundo.services.visual_assets import ensure_missing_local_asset_files

            ensure_missing_local_asset_files(limit=None)
        except Exception:
            pass
    except (OperationalError, ProgrammingError):
        # The command may be running against a DB that is not yet usable. The
        # config page/bootstrap endpoint will retry during a real request.
        return


_VISUAL_ASSET_REPAIR_STARTED = False


def _repair_visual_asset_files_on_first_request(sender, **kwargs):
    """Repair missing local flag/coat files after startup, outside app init."""
    global _VISUAL_ASSET_REPAIR_STARTED
    if _VISUAL_ASSET_REPAIR_STARTED or os.environ.get("CIUDADES_DISABLE_ASSET_STARTUP_REPAIR") == "1":
        return
    _VISUAL_ASSET_REPAIR_STARTED = True

    def _run():
        try:
            from django.db import close_old_connections
            from ciudades_del_mundo.services.visual_assets import ensure_visual_assets_after_startup_async

            close_old_connections()
            ensure_visual_assets_after_startup_async()
        except (OperationalError, ProgrammingError):
            return
        except Exception:
            # Startup repair must never prevent the web UI from loading.
            return

    threading.Thread(target=_run, name="visual-asset-startup-repair", daemon=True).start()
