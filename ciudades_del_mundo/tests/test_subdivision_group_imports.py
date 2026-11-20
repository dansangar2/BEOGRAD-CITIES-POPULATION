from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from unittest.mock import patch

from django.test import TestCase

from ciudades_del_mundo.models import DerivedSubdivision, SubdivisionGroup
from ciudades_del_mundo.services import derived_config_seeds as seeds


class SubdivisionGroupImportTests(TestCase):
    def test_explicit_import_adds_referenced_groups_and_preserves_custom_groups(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            group_root = root / 'groups'
            group_root.mkdir()
            (group_root / 'aa.toml').write_text(
                'source_country_code = "aa"\nMOVE = ["One"]\nCUSTOM = ["Seed"]\nUNUSED = ["Other"]\n', encoding='utf-8',
            )
            (group_root / 'bb.toml').write_text(
                'source_country_code = "bb"\nMOVE = ["Foreign"]\nCAPITAL = ["Capital"]\n', encoding='utf-8',
            )
            custom = SubdivisionGroup.objects.create(
                slug='aa_custom', name='Custom', source_country_code='aa',
                content='source_country_code = "aa"\nCUSTOM = ["Edited"]\n',
            )
            original = SubdivisionGroup.objects.filter(pk=custom.pk).values().get()
            path = root / 'aa.toml'
            path.write_text(
                'kind = "derived_subdivision"\ninternal_name = "PROVINCE"\nsource_country_code = "aa"\n'
                '[[include]]\nlevel = 2\ngroups = ["MOVE", "CUSTOM"]\n'
                '[[subtract]]\ncountry_code = "bb"\nlevel = 3\ngroups = ["MOVE"]\n'
                '[[children]]\nname = "Child"\nsource_country_code = "bb"\n'
                '[[children.capital_groups]]\nlevel = 3\ngroup = "CAPITAL"\n', encoding='utf-8',
            )
            with patch.object(seeds, 'SUBDIVISION_GROUP_SEED_ROOT', root):
                for _ in range(2):
                    records = seeds.import_derived_subdivision_path_records(path, force=True)
            self.assertEqual(len(records), 1)
            self.assertEqual(set(SubdivisionGroup.objects.values_list('slug', flat=True)), {'aa_move', 'aa_custom', 'bb_move', 'bb_capital'})
            self.assertEqual(SubdivisionGroup.objects.filter(pk='aa_custom').values().get(), original)
            self.assertIn('Foreign', SubdivisionGroup.objects.get(pk='bb_move').content)

    def test_missing_seed_does_not_break_existing_import_contract(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'aa.toml'
            path.write_text(
                'kind = "derived_subdivision"\ninternal_name = "PROVINCE"\nsource_country_code = "aa"\n'
                '[[include]]\nlevel = 3\ngroups = ["NOT_BUNDLED"]\n', encoding='utf-8',
            )
            with patch.object(seeds, 'SUBDIVISION_GROUP_SEED_ROOT', root):
                records = seeds.import_derived_subdivision_path_records(path)
            self.assertEqual(len(records), 1)
            self.assertFalse(SubdivisionGroup.objects.exists())

    def test_export_reimport_preserves_id_after_moving_to_source_country(self):
        record = DerivedSubdivision.objects.create(
            slug='old_province', internal_name='PROVINCE', name='Province', source_country_code='aa',
            content='kind = "derived_subdivision"\ninternal_name = "PROVINCE"\nsource_country_code = "aa"\n',
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'aa.toml'
            path.write_text(seeds._normalized_derived_subdivision_bundle_seed_content('aa', [record]), encoding='utf-8')
            self.assertEqual(seeds._parse_derived_subdivision_seed_records(path)[0]['slug'], 'old_province')
            imported = seeds.import_derived_subdivision_path_records(path, force=True)
        self.assertEqual([item.pk for item in imported], ['old_province'])
        self.assertEqual(DerivedSubdivision.objects.count(), 1)
        self.assertEqual(tomllib.loads(imported[0].content)['source_country_code'], 'aa')
