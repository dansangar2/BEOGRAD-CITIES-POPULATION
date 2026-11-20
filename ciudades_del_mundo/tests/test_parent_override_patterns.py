from types import SimpleNamespace
import unittest

from ciudades_del_mundo.application.citypopulation_linking import apply_runtime_config_extensions
from ciudades_del_mundo.domain import ScrapedAdminArea
from ciudades_del_mundo.services.scraping_config_extensions import parse_runtime_config_extensions


class ParentOverridePatternTests(unittest.TestCase):
    def test_full_code_pattern_derives_each_parent_and_restores_the_configured_level(self):
        extension = parse_runtime_config_extensions(r"""
[[parent_overrides]]
code_pattern = '([0-9]{8})x.*'
parent_code_template = '\1'
level = 4
exclude_codes = ['12345678xskip']
""")
        config = SimpleNamespace(country_code='example', **extension)
        rows = [
            ScrapedAdminArea(code='12345678x0', name='A', level=4, country_code='example', parent_code='district'),
            ScrapedAdminArea(code='12345678x0001', name='B', level=5, country_code='example', parent_code='12345678x0'),
            ScrapedAdminArea(code='87654321x0', name='C', level=4, country_code='example', parent_code='other'),
            ScrapedAdminArea(code='12345678', name='Municipality', level=3, country_code='example'),
            ScrapedAdminArea(code='prefix12345678x0', name='Unrelated', level=5, country_code='example'),
            ScrapedAdminArea(code='12345678xskip', name='Excluded', level=5, country_code='example'),
        ]
        actual = apply_runtime_config_extensions(config, rows)
        self.assertEqual([(r.parent_code, r.level) for r in actual[:3]],
                         [('12345678', 4), ('12345678', 4), ('87654321', 4)])
        self.assertEqual(actual[3:], rows[3:])
        self.assertEqual([r.name for r in actual], [r.name for r in rows])

    def test_literal_parent_patterns_allow_later_historical_boundary_overrides(self):
        config = SimpleNamespace(country_code='example', **parse_runtime_config_extensions(r"""
[[parent_overrides]]
code_pattern = '([0-9]{8})x.*'
parent_code_template = '\1'
level = 4
[[parent_overrides]]
code_pattern = '12345678x.*'
parent_code = '87654321'
"""))
        row = ScrapedAdminArea(code='12345678x0', name='Locality', level=5, country_code='example')
        actual = apply_runtime_config_extensions(config, [row])[0]
        self.assertEqual((actual.parent_code, actual.level), ('87654321', 4))

    def test_invalid_patterns_and_templates_are_rejected_when_loading_config(self):
        for body in ["code_pattern = '['\nlevel = 4",
                     "parent_code_template = 'x'",
                     "code_pattern = '(x)'\nparent_code_template = '\\2'",
                     "code_pattern = '(x)'\nparent_code_template = '\\1'\nparent_code = 'other'"]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_runtime_config_extensions('[[parent_overrides]]\n' + body)
