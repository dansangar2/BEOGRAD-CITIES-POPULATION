"""Web middleware for graceful operational failures."""

from __future__ import annotations

from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from ciudades_del_mundo.web.http import _wants_json


def _sqlite_is_busy(exc: OperationalError) -> bool:
    cause = exc.__cause__ or exc
    code = getattr(cause, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in {5, 6}  # SQLITE_BUSY / SQLITE_LOCKED, including extended codes.
    return "database is locked" in str(exc).lower() or "database table is locked" in str(exc).lower()


def database_busy_response(request):
    message = _(
        "La base de datos esta ocupada por una tarea de escritura. "
        "Espera unos segundos y vuelve a intentarlo."
    )
    if _wants_json(request):
        return JsonResponse({"error": message}, status=503)
    return render(request, "ciudades_del_mundo/database_busy.html", {"message": message}, status=503)


class DatabaseBusyMiddleware:
    """Return a controlled response when SQLite is temporarily locked."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except OperationalError as exc:
            response = self.process_exception(request, exc)
            if response is None:
                raise
            return response

    def process_exception(self, request, exception):
        # Django converts view exceptions before __call__ regains control.
        if not isinstance(exception, OperationalError) or not _sqlite_is_busy(exception):
            return None

        return database_busy_response(request)
