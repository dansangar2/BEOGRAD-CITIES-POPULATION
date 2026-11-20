"""Public-origin behavior stays with Django while use cases run in Java."""
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from django.conf import settings
from django.http import HttpResponse
from django.test import Client, SimpleTestCase, override_settings

from ciudades_del_mundo.web.java_routing import JavaApiRoutingMiddleware


class Upstream(BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json', 'X-Request-ID': 'native-java'}


@override_settings(BEOGRAD_JAVA_ENABLED=True, BEOGRAD_JAVA_PORT=8001)
class JavaRoutingTests(SimpleTestCase):
    def setUp(self):
        self.settings_override = override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware'])
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_public_origin_cookies_language_and_query_remain_consistent(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'fr'
        with patch('ciudades_del_mundo.web.java_routing.build_opener') as opener:
            opener.return_value.open.return_value = Upstream(b'{"type":"donut"}')
            response = client.get('/api/countries/?a=one&a=two', HTTP_X_BEOGRAD_LANGUAGE='spoofed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Request-ID'], 'native-java')
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8001/api/countries/?a=one&a=two')
        self.assertEqual(request.get_header('X-beograd-language'), 'fr')
        self.assertFalse(request.has_header('Cookie'))

    def test_missing_java_never_executes_the_python_api(self):
        with patch('ciudades_del_mundo.web.java_routing.build_opener') as opener, patch('ciudades_del_mundo.web.queries.browser._country_summary_payload') as old:
            opener.return_value.open.side_effect = URLError('offline')
            response = Client().get('/api/countries/')
        self.assertEqual(response.status_code, 503)
        old.assert_not_called()

    def test_csrf_rejects_unsafe_requests_before_forwarding(self):
        with patch('ciudades_del_mundo.web.java_routing.build_opener') as opener:
            response = Client(enforce_csrf_checks=True).post('/api/derived/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        opener.assert_not_called()

    def test_valid_csrf_is_forwarded_for_java_validation(self):
        client = Client(enforce_csrf_checks=True)
        token = 'a' * 32
        client.cookies[settings.CSRF_COOKIE_NAME] = token
        with patch('ciudades_del_mundo.web.java_routing.build_opener') as opener:
            opener.return_value.open.return_value = Upstream(b'{"type":"bar"}')
            response = client.post('/api/derived/', {'csrfmiddlewaretoken': token})
        self.assertEqual(response.status_code, 200)
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('X-csrftoken'), token)
        self.assertEqual(request.get_header('Cookie'), 'csrftoken=' + token)

    def test_missing_slash_keeps_django_redirect(self):
        with patch('ciudades_del_mundo.web.java_routing.build_opener') as opener:
            response = Client().get('/api/countries')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/api/countries/')
        opener.assert_not_called()

    def test_native_busy_response_preserves_html_and_json_negotiation(self):
        from urllib.error import HTTPError
        for headers in ({'HTTP_ACCEPT': 'text/html'}, {'HTTP_ACCEPT': 'application/json'}, {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}):
            upstream = HTTPError('http://127.0.0.1/', 503, 'Busy', {'Content-Type': 'application/json', 'X-Beograd-Database-Busy': '1'}, BytesIO(b'{"error":"busy"}'))
            with self.subTest(headers=headers), patch('ciudades_del_mundo.web.java_routing.build_opener') as opener:
                opener.return_value.open.side_effect = upstream
                response = Client().get('/api/countries/', **headers)
                self.assertEqual(response.status_code, 503)
                self.assertIn('text/html' if headers.get('HTTP_ACCEPT') == 'text/html' else 'application/json', response['Content-Type'])

    def test_html_and_pending_api_routes_keep_their_original_owners(self):
        from django.test import RequestFactory
        from django.urls import resolve
        middleware = JavaApiRoutingMiddleware(lambda request: HttpResponse())
        for path in ('/countries/', '/api/tasks/', '/api/countries/xx/', '/groups/source-data/'):
            request = RequestFactory().get(path)
            request.resolver_match = resolve(path)
            self.assertIsNone(middleware.process_view(request, None, (), {}))
