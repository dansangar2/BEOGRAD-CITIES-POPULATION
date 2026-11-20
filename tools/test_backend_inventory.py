"""Static route ownership must describe the maintained runtime sources."""
import ast
import unittest

from tools import inventory_java_api


class BackendInventoryTests(unittest.TestCase):
    def test_every_python_route_is_maintained_and_has_an_existing_owner(self):
        routes = inventory_java_api.inventory()['routes']
        self.assertTrue(routes)
        for route in routes:
            self.assertEqual(route['python_state'], 'maintained; active in Python mode')
            if 'source' in route:
                path, line = route['source'].rsplit(':', 1)
                source = inventory_java_api.ROOT / path
                self.assertTrue(source.is_file(), path)
                self.assertIn(int(line), [node.lineno for node in ast.parse(source.read_text(encoding='utf-8-sig')).body
                                         if isinstance(node, ast.FunctionDef)])

    def test_java_allowlist_and_pending_routes_have_distinct_owners(self):
        routes = inventory_java_api.inventory()['routes']
        implemented = {route['name'] for route in routes if route['java_status'].startswith('native Java;')}
        self.assertEqual(implemented, inventory_java_api.java_route_names())
        for route in routes:
            if route['name'] not in implemented:
                self.assertIn('Django owner in Java mode', route['java_status'])


if __name__ == '__main__':
    unittest.main()
