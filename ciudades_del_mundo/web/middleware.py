"""Web middleware for graceful operational failures."""

from __future__ import annotations

from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _


class DatabaseBusyMiddleware:
    """Return a controlled response when SQLite is temporarily locked."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise

            message = _(
                "La base de datos esta ocupada por una tarea de escritura. "
                "Espera unos segundos y vuelve a intentarlo."
            )
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"error": message}, status=503)

            return render(
                request,
                "ciudades_del_mundo/database_busy.html",
                {"message": message},
                status=503,
            )
