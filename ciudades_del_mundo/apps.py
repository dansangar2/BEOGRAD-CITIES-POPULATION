from django.apps import AppConfig
from django.db import OperationalError
from django.db.backends.signals import connection_created

class CiudadesDelMundoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ciudades_del_mundo'
    verbose_name = 'Ciudades del Mundo'

    def ready(self):
        connection_created.connect(_configure_sqlite_connection, dispatch_uid="ciudades_sqlite_pragmas")


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
