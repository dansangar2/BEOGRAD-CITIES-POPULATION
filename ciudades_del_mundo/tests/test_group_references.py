import unittest

from ciudades_del_mundo.application.group_references import replace_group_references


class GroupReferenceRulesTests(unittest.TestCase):
    def test_renames_and_deduplicates_source_capital_and_child_references_only(self):
        data = {"code": "keep-code", "population": 123, "include": [{"groups": ["old", "OLD", "new", "Other"]}],
                "capital_groups": [{"group": "old"}],
                "children": [{"subtract": [{"groups": ["OLD"]}], "capital_groups": [{"group_key": "old"}]}]}
        self.assertTrue(replace_group_references(data, old_key="OLD", new_key="NEW", normalize_key=str.upper))
        self.assertEqual(data["include"][0]["groups"], ["NEW", "Other"])
        self.assertEqual(data["capital_groups"][0]["group"], "NEW")
        self.assertEqual(data["children"][0]["subtract"][0]["groups"], ["NEW"])
        self.assertEqual(data["children"][0]["capital_groups"][0]["group_key"], "NEW")
        self.assertEqual((data["code"], data["population"]), ("keep-code", 123))
        self.assertFalse(replace_group_references(data, old_key="OLD", new_key="NEW", normalize_key=str.upper))

    def test_supplied_validation_errors_are_not_replaced_or_swallowed(self):
        def validate(value):
            raise ValueError("existing translated validation message")

        with self.assertRaisesRegex(ValueError, "existing translated validation message"):
            replace_group_references({"include": [{"groups": ["bad"]}]}, old_key="OLD", new_key="NEW", normalize_key=validate)
