"""Data selectors stay transport-independent; handlers preserve HTTP contracts."""
from unittest.mock import patch
from io import BytesIO

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase

from ciudades_del_mundo.infrastructure.django.area_selectors import filtered_admin_areas, visible_admin_areas
from ciudades_del_mundo.models import AdminArea, DerivedCountry
from ciudades_del_mundo.ports.query_errors import DataNotFoundError
from ciudades_del_mundo.web import http
from ciudades_del_mundo.web.handlers import dashboard
from ciudades_del_mundo.web.queries import new_countries
from ciudades_del_mundo.web.queries import discovery
from ciudades_del_mundo.infrastructure.scraping.discovery_http import read_discovery_html


class HttpAdapterTests(SimpleTestCase):
    def test_discovery_transport_passes_timeout_headers_and_preserves_decoding(self):
        with patch("ciudades_del_mundo.infrastructure.scraping.discovery_http.urlopen", return_value=BytesIO(b"<html>offline</html>")) as opened:
            self.assertEqual(read_discovery_html("https://example.test/", timeout=8, user_agent="Test"), "<html>offline</html>")
        self.assertEqual(opened.call_args.kwargs["timeout"], 8)
        self.assertEqual(opened.call_args.args[0].get_header("User-agent"), "Test")

    def test_discovery_preserves_the_translated_error_boundary(self):
        with patch("ciudades_del_mundo.web.queries.discovery.read_discovery_html", side_effect=OSError("offline")):
            with self.assertRaisesRegex(ValueError, "offline"):
                discovery._read_citypopulation_html("https://example.test/")

    def test_query_params_copy_selected_lists_without_losing_multi_values(self):
        request = RequestFactory().get("/countries/", {"country": ["aa", "bb"], "q": "Name", "level": ["1", "2"]})
        params = http.query_params(request, list_fields=("country", "level"))
        self.assertIs(type(params), dict)
        self.assertEqual(params, {"country": ["aa", "bb"], "q": "Name", "level": ["1", "2"]})

    def test_handler_converts_data_error_to_404_preserving_the_message(self):
        with patch("ciudades_del_mundo.web.queries.browser._country_detail_payload", side_effect=DataNotFoundError("missing")):
            with patch("ciudades_del_mundo.infrastructure.django.area_selectors.visible_admin_areas") as areas:
                areas.return_value.filter.return_value.exists.return_value = True
                with self.assertRaisesMessage(Http404, "missing"):
                    dashboard.api_country_detail(RequestFactory().get("/api/countries/aa/"), "aa")


class SqlSelectorTests(TestCase):
    def test_selector_accepts_plain_data_and_keeps_country_level_merge_and_sort_filters(self):
        for country, code, level, population in [("aa", "root", 0, 100), ("aa", "one", 1, 20),
                                                 ("aa", "two", 1, 30), ("bb", "other", 1, 90)]:
            AdminArea.objects.create(id=f"{country}_{code}", country_code=country, code=code, name=code,
                                     level=level, pop_latest=population)
        hidden = AdminArea.objects.create(id="aa_hidden", country_code="aa", code="hidden", name="hidden", level=1)
        AdminArea.objects.filter(pk=hidden.pk).update(city_merge_status=3)
        rows = filtered_admin_areas({"country": ["aa"], "level": ["1"], "sort": "population_desc"})
        self.assertEqual(list(rows.values_list("code", flat=True)), ["two", "one"])
        self.assertEqual(visible_admin_areas().count(), 4)

    def test_missing_query_raises_a_data_exception_without_http(self):
        with self.assertRaises(DataNotFoundError):
            new_countries.get_derived_country("missing")
        country = DerivedCountry.objects.create(slug="aa", name="AA")
        self.assertEqual(new_countries.get_derived_country("aa"), country)
        with self.assertRaises(DataNotFoundError):
            new_countries.get_derived_country_config(country, "missing")
