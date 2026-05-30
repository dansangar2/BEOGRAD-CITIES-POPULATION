import ast
import unittest

from ciudades_del_mundo.web.views import (
    _render_recipe_from_form,
    _validate_config_text,
    _validate_recipe_text,
)


class WebInterfaceHelperTests(unittest.TestCase):
    def test_validate_config_text_accepts_minimal_toml(self):
        _validate_config_text(
            "testland",
            """
LEGAL_SUBDIVISION = 2

[[pages]]
source = "admin"
path = ["admin"]
lowest_level = 0
""",
        )

    def test_recipe_form_renders_importable_python_with_numeric_dat_keys(self):
        content = _render_recipe_from_form(
            {
                "slug": "testland",
                "root_name": "Testland",
                "source_country": "spain",
                "municipal_level": "3",
                "representation_level": "2",
                "representation_total": "100",
                "representation_min": "1",
                "divisions_json": """
[
  {
    "name": "Provincia",
    "code": "PRO",
    "dat": {"2": ["Madrid"]}
  }
]
""",
            }
        )

        _validate_recipe_text(content, filename="testland.py")
        tree = ast.parse(content)
        divisions_node = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "DIVISIONS" for target in node.targets)
        )
        divisions = ast.literal_eval(divisions_node)
        self.assertEqual(divisions[0]["dat"], {2: ["Madrid"]})
