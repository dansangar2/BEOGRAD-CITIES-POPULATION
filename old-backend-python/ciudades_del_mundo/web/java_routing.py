"""Explicit migration allowlist. Django remains the public origin and UI owner.

process_view runs after Django's CSRF middleware; cookies, locale, permissions
and unsafe-method protection keep their existing owners. Java implements the
listed use cases natively. Pending routes continue to execute their real owners.
"""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import get_language
from ciudades_del_mundo.web.http import _wants_json
from ciudades_del_mundo.web.middleware import database_busy_response

JAVA_ROUTE_NAMES = frozenset({
    "api_country_summary", "dashboard_population_data", "api_derived_summary", "dashboard_derived_data", "config_export_data_csv",
})


class JavaApiRoutingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if not getattr(settings, "BEOGRAD_JAVA_ENABLED", False) or not match or match.namespace != "ciudades_del_mundo" or match.url_name not in JAVA_ROUTE_NAMES:
            return None
        # Fixed loopback target, never a URL from query/body/header input. No proxy env.
        port = int(settings.BEOGRAD_JAVA_PORT)
        if not 1 <= port <= 65535:
            raise ValueError("BEOGRAD_JAVA_PORT must be 1..65535")
        headers = {"Accept": request.headers.get("accept", "*/*"), "X-Beograd-Language": get_language() or "es"}
        if request.headers.get("x-requested-with"):
            headers["X-Requested-With"] = request.headers["x-requested-with"]
        url = f"http://127.0.0.1:{port}{request.get_full_path()}"
        upstream_request = Request(url, method=request.method, headers=headers)
        # Migrated use cases are read-only, even for historically accepted POSTs.
        try:
            upstream = build_opener(ProxyHandler({})).open(upstream_request, timeout=10)
        except HTTPError as exc:
            upstream = exc
        except (URLError, TimeoutError, OSError):
            # No fallback to Python: outages must be visible and must not duplicate work.
            return HttpResponse(status=503)
        with upstream:
            if upstream.headers.get("X-Beograd-Database-Busy") == "1" and not _wants_json(request):
                # Existing HTML error page belongs to Django's UI. JSON errors are native Java.
                return database_busy_response(request)
            response = HttpResponse(upstream.read(), status=upstream.status, content_type=upstream.headers.get("Content-Type", "application/octet-stream"))
            for key in ("Content-Disposition", "X-Request-ID", "Allow", "Location"):
                value = upstream.headers.get(key)
                if value is not None:
                    response[key] = value
        return response
