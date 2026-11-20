"""Regression checks for the compatibility facade and ordered static sources."""
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.urls import reverse, resolve

from ciudades_del_mundo.web import views
from ciudades_del_mundo.web.handlers import configs, dashboard
from ciudades_del_mundo.web.queries import new_countries
from tools.build_web_assets import STATIC_ROOT, render_bundles


class WebModularityTests(SimpleTestCase):
    def test_url_handlers_are_the_same_objects_as_compatibility_exports(self):
        for name in ("dashboard", "config_list", "task_list", "new_country_list", "group_list"):
            with self.subTest(name=name):
                self.assertIs(resolve(reverse(f"ciudades_del_mundo:{name}")).func, getattr(views, name))

    def test_owner_patch_points_restore_without_using_the_facade(self):
        original = new_countries._new_country_config_preview_metric_values
        replacement = Mock(return_value={})
        with patch("ciudades_del_mundo.web.queries.new_countries._new_country_config_preview_metric_values", replacement):
            self.assertIs(new_countries._new_country_config_preview_metric_values, replacement)
        self.assertIs(new_countries._new_country_config_preview_metric_values, original)

    def test_patches_are_local_to_the_consuming_adapter(self):
        original = dashboard.render
        with patch("ciudades_del_mundo.web.handlers.dashboard.render") as replacement:
            self.assertIs(dashboard.render, replacement)
            self.assertIs(configs.render, original)
        self.assertIs(dashboard.render, original)
        self.assertIs(configs.render, original)

    def test_compatibility_exports_only_public_http_handlers(self):
        from types import ModuleType

        self.assertIs(type(views), ModuleType)
        self.assertFalse(hasattr(views, "_BINDINGS"))
        self.assertFalse(hasattr(views, "render"))
        self.assertFalse(hasattr(views, "_new_country_config_preview_metric_values"))
        for name in views.__all__:
            self.assertTrue(getattr(views, name).__module__.startswith("ciudades_del_mundo.web.handlers."))

    def test_bundles_match_ordered_sources(self):
        for name, content in render_bundles().items():
            with self.subTest(bundle=name):
                self.assertEqual((STATIC_ROOT / name).read_text(encoding="utf-8"), content)

    def test_startup_and_static_versions_are_preserved(self):
        script = render_bundles()["app.js"]
        startup = script[script.index('  document.addEventListener("DOMContentLoaded"'):]
        self.assertLess(startup.index('"select2"'), startup.index('"editor de configuraci'))
        self.assertEqual(script.count('document.addEventListener("DOMContentLoaded"'), 1)
        base = (Path(__file__).resolve().parents[1] / "templates/ciudades_del_mundo/base.html").read_text(encoding="utf-8")
        self.assertEqual(base.count("20261015-country-summary-batch"), 2)
