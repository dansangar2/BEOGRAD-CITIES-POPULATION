"""Explicit migration improvement, separate from API equivalence tests."""
import sqlite3

from django.db import OperationalError
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from ciudades_del_mundo.urls import urlpatterns as project_urls

from ciudades_del_mundo.web.middleware import DatabaseBusyMiddleware


def busy_view(request):
    raise OperationalError("database is locked")


urlpatterns = [path("busy/", busy_view), *project_urls]


class DatabaseBusyNegotiationTests(SimpleTestCase):
    def test_both_json_consumers_receive_controlled_503(self):
        for headers in ({"HTTP_ACCEPT": "application/json"}, {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}):
            with self.subTest(headers=headers), override_settings(ROOT_URLCONF=__name__):
                response = Client().get("/busy/", **headers)
                self.assertEqual(response.status_code, 503)
                self.assertIn("error", response.json())
                self.assertNotIn("<!DOCTYPE", response.content.decode())

    def test_browser_response_keeps_the_busy_template(self):
        with override_settings(ROOT_URLCONF=__name__):
            response = Client().get("/busy/", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 503)
        self.assertIn("text/html", response["Content-Type"])

    def test_driver_code_takes_precedence_over_message(self):
        middleware = DatabaseBusyMiddleware(lambda request: HttpResponse())
        request = RequestFactory().get("/busy/", HTTP_ACCEPT="application/json")
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | (2 << 8)):
            cause = sqlite3.OperationalError("driver detail")
            cause.sqlite_errorcode = code
            error = OperationalError("driver detail")
            error.__cause__ = cause
            self.assertEqual(middleware.process_exception(request, error).status_code, 503)
        cause.sqlite_errorcode = sqlite3.SQLITE_ERROR
        self.assertIsNone(middleware.process_exception(request, error))
        self.assertIsNone(middleware.process_exception(request, ValueError("database is locked")))
