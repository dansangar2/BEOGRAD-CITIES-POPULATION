from django.apps import AppConfig
from django.db import OperationalError, ProgrammingError
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate

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
    except (OperationalError, ProgrammingError):
        # The command may be running against a DB that is not yet usable. The
        # config page/bootstrap endpoint will retry during a real request.
        return
