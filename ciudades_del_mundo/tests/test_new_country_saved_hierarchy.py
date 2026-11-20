"""Country-wide historical recipes display their persisted SQL hierarchy."""
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea


class SavedCountryHierarchyTests(TestCase):
    def setUp(self):
        # The container slug and materialized country code need not match.
        self.country = DerivedCountry.objects.create(slug='spain', name='Spain', source_country_code='spain')
        self.config = DerivedCountryConfig.objects.create(
            country=self.country, slug='federal', name='Federal Republic', source_country_code='spain',
            derived_country_code='federal',
            content='kind = "derived_country_config"\nderived_country_code = "federal"\n[legacy]\npython = "DIVISIONS = []"\n',
        )
        self.root = NuevoAdminArea.objects.create(id='federal', country_code='federal', code='FED', name='Federal Republic', level=0)
        self.parent = NuevoAdminArea.objects.create(id='federal_parent', country_code='federal', code='FED-CAS', name='Castilla', entity_type='Corona', level=1, parent=self.root, pop_latest=500)
        self.sibling = NuevoAdminArea.objects.create(id='federal_sibling', country_code='federal', code='FED-ARA', name='Aragon', level=1, parent=self.root)
        NuevoAdminArea.objects.bulk_create([
            NuevoAdminArea(id=f'province_{i}', country_code='federal', code=f'FED-CAS-{i:02}', name=f'Province {i:02}', entity_type='Provincia', level=2, parent=self.parent, pop_latest=i)
            for i in range(25)
        ])
        self.capital = AdminArea.objects.create(id='capital', country_code='spain', code='MAD', name='Madrid', level=3)
        self.parent.capitals.add(self.capital)
        self.tree_url = '/api/new-country-containers/spain/config-tree/'

    def test_saved_hierarchy_is_identical_in_python_and_combined_java_modes(self):
        original = list(NuevoAdminArea.objects.order_by('id').values())
        for java in (False, True):
            with self.subTest(java=java), override_settings(
                BEOGRAD_JAVA_ENABLED=java, BEOGRAD_JAVA_PORT=8001,
                MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware'],
            ), patch('ciudades_del_mundo.web.java_routing.build_opener') as upstream:
                client = Client()
                detail = client.get('/new-countries/spain/spain/')
                levels = detail.context['tree_levels']
                self.assertEqual([(row['value'], row['count']) for row in levels], [(1, 2), (2, 25)])
                response = client.get(self.tree_url)
                self.assertEqual(response.status_code, 200)
                rows = response.json()['rows']
                self.assertEqual([row['entity_display_name'] for row in rows], ['Aragon', 'Castilla'])
                parent = rows[1]
                self.assertTrue(parent['has_children'])
                self.assertEqual(parent['subdivision_count'], 25)
                self.assertEqual(parent['capital'], ['Madrid'])
                self.assertEqual(parent['entity_population_text'], '500')
                self.assertFalse(parent['edit_url'])
                self.assertFalse(rows[0]['has_children'])
                children = client.get(self.tree_url, {'parent': parent['tree_id'], 'page_size': 100}).json()['rows']
                self.assertEqual(len(children), 25)
                self.assertTrue(all(row['tree_parent'] == parent['tree_id'] and row['depth'] == 1 and row['level'] == 2 for row in children))
                self.assertTrue(all(not row['has_children'] for row in children))
                upstream.assert_not_called()
        self.assertEqual(list(NuevoAdminArea.objects.order_by('id').values()), original)
        self.assertEqual(self.country.configs.count(), 1)

    def test_level_filter_paginates_all_saved_provinces(self):
        response = self.client.get(self.tree_url, {'level': 2, 'page': 2, 'page_size': 20})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['pagination']['total'], 25)
        self.assertEqual(len(data['rows']), 5)
        self.assertTrue(all(row['level'] == 2 and row['depth'] == 0 and not row['tree_parent'] for row in data['rows']))

    def test_parent_scope_does_not_cross_countries(self):
        other = NuevoAdminArea.objects.create(id='foreign', country_code='foreign', code='FOREIGN', name='Foreign', level=1)
        for parent in (f'materialized:spain:{other.id}', f'materialized:another:{self.parent.id}', 'config:spain:federal'):
            with self.subTest(parent=parent):
                response = self.client.get(self.tree_url, {'parent': parent})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['rows'], [])

    def test_unbuilt_bridge_recipe_keeps_its_configuration_row(self):
        NuevoAdminArea.objects.all().delete()
        response = self.client.get(self.tree_url)
        self.assertEqual(response.status_code, 200)
        rows = response.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['is_config_row'])
        self.assertEqual(rows[0]['tree_id'], 'config:spain:federal')

    def test_individual_declarative_configs_keep_their_editable_tree(self):
        self.config.content = 'kind = "derived_country_config"\n[[entities]]\nname = "Castilla"\ncode = "CAS"\nlevel = 1\n'
        self.config.save()
        rows = self.client.get(self.tree_url).json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['tree_id'], 'config:spain:federal')
        self.assertTrue(rows[0]['edit_url'])
