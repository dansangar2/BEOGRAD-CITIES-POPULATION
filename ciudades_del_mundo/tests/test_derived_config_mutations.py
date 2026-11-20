from django.test import TestCase

from ciudades_del_mundo.models import NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services import derived_config_mutations as mutations


class DerivedMutationTests(TestCase):
    def test_branch_deletion_preserves_root_siblings_and_other_countries(self):
        root = NuevoAdminArea.objects.create(id="aa", code="aa", name="AA", country_code="aa", level=0)
        branch = NuevoAdminArea.objects.create(id="aa-1", code="1", name="One", country_code="aa", level=1, parent=root)
        NuevoAdminArea.objects.create(id="aa-11", code="11", name="Child", country_code="aa", level=2, parent=branch)
        sibling = NuevoAdminArea.objects.create(id="aa-2", code="2", name="Two", country_code="aa", level=1, parent=root)
        other = NuevoAdminArea.objects.create(id="bb", code="1", name="BB", country_code="bb", level=0)
        self.assertEqual(mutations._delete_materialized_rows_for_new_country_config(country_code="aa", code_candidates=["1"]), 2)
        self.assertEqual(set(NuevoAdminArea.objects.values_list("pk", flat=True)), {root.pk, sibling.pk, other.pk})
        self.assertEqual(mutations._delete_materialized_rows_for_new_country_config(country_code="aa", code_candidates=["aa"]), 0)

    def test_subdivision_deletion_is_idempotent_and_country_scoped(self):
        root = NuevoAdminArea.objects.create(id="aa", code="aa", name="AA", country_code="aa", level=0)
        NuevoAdminArea.objects.create(id="aa-1", code="1", name="One", country_code="aa", level=1, parent=root)
        NuevoAdminArea.objects.create(id="bb", code="1", name="BB", country_code="bb", level=0)
        self.assertEqual(mutations._delete_materialized_rows_for_derived_subdivision(country_code="aa", code_candidates=["1"]), 1)
        self.assertEqual(mutations._delete_materialized_rows_for_derived_subdivision(country_code="aa", code_candidates=["1"]), 0)
        self.assertEqual(set(NuevoAdminArea.objects.values_list("pk", flat=True)), {"aa", "bb"})

    def test_group_rename_preserves_other_countries_exclusions_and_partial_keys(self):
        original = 'OLD = ["One"]\nIGNORE_OLD = ["Two"]\n'
        for slug, country in (("aa-group", "aa"), ("bb-group", "bb"), ("aa-excluded", "aa")):
            SubdivisionGroup.objects.create(slug=slug, name=slug, source_country_code=country, content=original)
        arguments = dict(country_code="aa", old_key="OLD", new_key="NEW", exclude_slugs={"aa-excluded"},
                         group_country_codes=lambda record: [record.source_country_code])
        self.assertEqual(mutations._rename_subdivision_group_content_references(**arguments), 1)
        self.assertEqual(SubdivisionGroup.objects.get(pk="aa-group").content, original.replace('OLD = ["One"]', 'NEW = ["One"]'))
        self.assertEqual(SubdivisionGroup.objects.get(pk="bb-group").content, original)
        self.assertEqual(SubdivisionGroup.objects.get(pk="aa-excluded").content, original)
        self.assertEqual(mutations._rename_subdivision_group_content_references(**arguments), 0)
