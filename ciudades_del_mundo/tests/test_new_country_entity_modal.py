"""Modal creation preserves saved hierarchies and supports optional sources."""
import json
import tomllib
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, DerivedSubdivision, NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services.new_country_entities import full_area_code


class NewCountryEntityModalTests(TestCase):
    def setUp(self):
        self.country = DerivedCountry.objects.create(slug='empire', name='Empire', source_country_code='spain')
        self.bridge = DerivedCountryConfig.objects.create(country=self.country, slug='old', name='Old recipe',
            source_country_code='spain', derived_country_code='saved_empire',
            content='kind = "derived_country_config"\nderived_country_code = "saved_empire"\n[legacy]\npython = "DIVISIONS = []"\n')
        self.root = NuevoAdminArea.objects.create(id='saved_empire', country_code='saved_empire', code='EMP', name='Empire', level=0)
        self.old = NuevoAdminArea.objects.create(id='old_castilla', country_code='saved_empire', code='CAS', name='Castilla', level=1, parent=self.root)
        self.source_root = AdminArea.objects.create(id='spain', country_code='spain', code='ESP', name='Spain', level=0)
        self.source = AdminArea.objects.create(id='source_town', country_code='spain', code='TOWN', name='Town', level=1,
            parent=self.source_root, pop_latest=123, area_km2=Decimal('12.5'))
        self.url = '/new-countries/spain/empire/configs/new/?embedded=1'
        self.tree = '/api/new-country-containers/empire/config-tree/'

    def create(self, *, code='NEW', parent=None, selected=None, client=None):
        return (client or self.client).post(self.url, {
            'slug': code, 'name': code, 'entity_type': 'Region',
            'parent_area_id': parent.pk if parent else '',
            'selection_json': json.dumps({'selected_ids': selected or [], 'use_subdivisions': False, 'selected_child_config_slugs': []}),
        }, HTTP_ACCEPT='application/json')

    def test_popup_and_create_actions_are_in_configuration_panel(self):
        response = self.client.get('/new-countries/spain/empire/')
        self.assertContains(response, 'data-new-country-entity-dialog')
        self.assertContains(response, 'data-new-country-entity-open')
        self.assertNotContains(response, 'class="button secondary" href="/new-countries/spain/empire/configs/new/"')
        rows = self.client.get(self.tree).json()['rows']
        self.assertIn('parent_area_id=old_castilla', rows[0]['new_url'])
        self.assertEqual(rows[0]['full_display_code'], 'EMP-CAS')
        popup = self.client.get(self.url + '&parent_area_id=old_castilla')
        self.assertEqual(popup['X-Frame-Options'], 'SAMEORIGIN')
        self.assertContains(popup, 'data-new-country-embedded')
        self.assertContains(popup, 'data-parent-code="EMP-CAS"')
        self.assertContains(popup, 'name="parent_area_id" value="old_castilla"')
        self.assertNotContains(popup, 'class="topbar"')

    def test_country_boxes_load_groups_by_country_and_save_them(self):
        SubdivisionGroup.objects.create(slug="spanish_towns", name="Towns", source_country_code="spain",
                                       content='internal_name = "TOWNS"\n')
        SubdivisionGroup.objects.create(slug="french_towns", name="Other", source_country_code="france")
        response = self.client.get('/new-countries/spain/empire/configs/new/source-areas/', {'country': 'spain', 'kind': 'groups'})
        self.assertEqual(response.json()["results"], [{"slug": "towns", "name": "Towns"}])
        NuevoAdminArea.objects.create(id="spain-test-division", country_code="spain", code="TEST", name="Spanish division", level=1)
        divisions = self.client.get('/new-countries/spain/empire/configs/new/created-subdivisions/', {'country': 'spain', 'compact': '1'})
        self.assertEqual(divisions.json()["results"], [{"name": "Spanish division", "key": "area:spain-test-division", "country_code": "spain"}])
        NuevoAdminArea.objects.all().delete()
        response = self.client.post(self.url, {
            "slug": "GROUPED", "name": "Grouped", "selection_json": json.dumps({
                "country_boxes": True, "selected_ids": [], "selected_groups": ["spanish_towns"],
                "selected_derived_subdivisions": [{"country_code": "france", "key": "LYON"}],
            }),
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        entity = tomllib.loads(self.country.configs.get(slug="grouped").content)["entities"][0]
        self.assertEqual(entity["include"][0]["derived_subdivisions"], ["LYON"])
        self.assertEqual(entity["include"][1]["groups"], ["towns"])

    def test_new_empty_entity_preserves_saved_nodes_and_appears_immediately_in_both_modes(self):
        old_ids = set(NuevoAdminArea.objects.values_list('id', flat=True))
        for java in (False, True):
            with self.subTest(java=java), override_settings(BEOGRAD_JAVA_ENABLED=java, BEOGRAD_JAVA_PORT=8001,
                MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware']), patch('ciudades_del_mundo.web.java_routing.build_opener') as upstream:
                client = Client()
                response = self.create(code='JAVA' if java else 'PYTHON', client=client)
                self.assertEqual(response.status_code, 200, response.content)
                rows = client.get(self.tree).json()['rows']
                self.assertIn(response.json()['tree_id'], [row['tree_id'] for row in rows])
                self.assertTrue(old_ids.issubset(set(NuevoAdminArea.objects.values_list('id', flat=True))))
                upstream.assert_not_called()

    def test_new_child_without_sources_sums_all_its_children(self):
        parent_response = self.create(code='PARENT', parent=self.old)
        self.assertEqual(parent_response.status_code, 200, parent_response.content)
        parent_config = self.country.configs.get(slug=parent_response.json()['slug'])
        parent_data = tomllib.loads(parent_config.content)
        parent = NuevoAdminArea.objects.get(pk=parent_data['materialized_area_id'])
        self.assertEqual(parent.parent_id, self.old.pk)
        self.assertEqual(full_area_code(parent), 'EMP-CAS-PARENT')
        self.assertEqual(parent_data['entities'][0]['mode'], 'intermediate')
        child_response = self.create(code='CHILD', parent=parent, selected=[self.source.pk])
        self.assertEqual(child_response.status_code, 200, child_response.content)
        parent.refresh_from_db()
        self.assertEqual(parent.pop_latest, 123)
        self.assertEqual(parent.area_km2, Decimal('12.5'))
        child_config = self.country.configs.get(slug=child_response.json()['slug'])
        self.assertEqual(tomllib.loads(child_config.content)['parent_config_slug'], parent_config.slug)
        rows = self.client.get(self.tree, {'parent': parent_response.json()['tree_id']}).json()['rows']
        self.assertEqual(rows[0]['full_display_code'], 'EMP-CAS-PARENT-CHILD')

    def test_explicit_sources_remain_authoritative_when_child_is_added(self):
        response = self.create(code='OWN', parent=self.old, selected=[self.source.pk])
        self.assertEqual(response.status_code, 200, response.content)
        config = self.country.configs.get(slug=response.json()['slug'])
        parent = NuevoAdminArea.objects.get(pk=tomllib.loads(config.content)['materialized_area_id'])
        response = self.create(code='EMPTY', parent=parent)
        self.assertEqual(response.status_code, 200, response.content)
        parent.refresh_from_db()
        self.assertEqual(parent.pop_latest, 123)
        self.assertEqual(parent.area_km2, Decimal('12.5'))

    def test_duplicate_and_foreign_parent_do_not_leave_partial_records(self):
        self.assertEqual(self.create(code='DUP', parent=self.old).status_code, 200)
        before = (self.country.configs.count(), NuevoAdminArea.objects.count())
        self.assertEqual(self.create(code='DUP', parent=self.old).status_code, 400)
        self.assertEqual((self.country.configs.count(), NuevoAdminArea.objects.count()), before)
        foreign = NuevoAdminArea.objects.create(id='foreign', country_code='other', code='OTHER', name='Other', level=0)
        response = self.create(code='BAD', parent=foreign)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.country.configs.filter(name='BAD').exists())

    def test_unbuilt_country_accepts_empty_configuration_and_child(self):
        NuevoAdminArea.objects.all().delete()
        response = self.create(code='EMPTY')
        self.assertEqual(response.status_code, 200, response.content)
        parent = self.country.configs.get(slug=response.json()['slug'])
        self.assertEqual(tomllib.loads(parent.content)['entities'][0]['mode'], 'intermediate')
        response = self.client.post(self.url, {'slug': 'CHILD', 'name': 'Child', 'parent_config_slug': parent.slug,
            'selection_json': json.dumps({'selected_ids': []})}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        rows = self.client.get(self.tree, {'parent': f'config:empire:{parent.slug}'}).json()['rows']
        self.assertEqual(rows[0]['full_display_code'], 'EMPIRE-EMPTY-CHILD')

    def test_child_of_previously_built_declarative_configuration(self):
        self.bridge.content = 'kind = "derived_country_config"\n[[entities]]\nname = "Castilla"\ncode = "CAS"\nlevel = 1\n'
        self.bridge.save()
        response = self.client.post(self.url, {'slug': 'NEW', 'name': 'New child',
            'parent_config_slug': self.bridge.slug, 'selection_json': json.dumps({'selected_ids': []})}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        record = self.country.configs.get(slug=response.json()['slug'])
        node = NuevoAdminArea.objects.get(pk=tomllib.loads(record.content)['materialized_area_id'])
        self.assertEqual(node.parent_id, self.old.pk)

    def test_whole_country_recipe_cannot_be_selected_as_a_child(self):
        options = self.client.get('/new-countries/spain/empire/configs/new/created-entities/').json()
        self.assertEqual(options['results'], [])
        response = self.client.post(self.url, {'slug': 'BAD', 'name': 'Bad',
            'selection_json': json.dumps({'selected_ids': [], 'selected_child_config_slugs': ['old']})}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.country.configs.filter(name='Bad').exists())

    def test_edit_saved_row_reuses_modal_and_preserves_identity(self):
        self.old.municipios_originales.add(self.source)
        child = NuevoAdminArea.objects.create(id="existing-child", parent=self.old, country_code=self.old.country_code,
                                              code="CAS-CH", name="Child", level=2)
        rows = self.client.get(self.tree).json()["rows"]
        url = rows[0]["modal_edit_url"] + "&embedded=1"
        response = self.client.get(url)
        self.assertContains(response, 'value="Castilla"')
        self.assertContains(response, self.source.pk)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        response = self.client.post(url, {"name": "Castilla editada", "entity_type": "Region",
                                         "selection_json": json.dumps({"selection_changed": False})})
        self.assertEqual(response.status_code, 200, response.content)
        self.old.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(self.old.name, "Castilla editada")
        self.assertEqual(self.old.code, "CAS")
        self.assertEqual(child.parent_id, self.old.pk)
        self.assertEqual(list(self.old.municipios_originales.values_list("pk", flat=True)), [self.source.pk])

    def test_delete_saved_row_promotes_child_without_changing_id(self):
        response = self.create(code="CHILD", parent=self.old)
        self.assertEqual(response.status_code, 200)
        config = self.country.configs.get(slug=response.json()["slug"])
        child_id = tomllib.loads(config.content)["materialized_area_id"]
        row = self.client.get(self.tree).json()["rows"][0]
        response = self.client.post(row["delete_url"], HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NuevoAdminArea.objects.filter(pk=self.old.pk).exists())
        child = NuevoAdminArea.objects.get(pk=child_id)
        self.assertEqual((child.parent_id, child.level, child.code), (self.root.pk, 1, "CHILD"))
        config.refresh_from_db()
        self.assertEqual(tomllib.loads(config.content)["parent_area_id"], self.root.pk)

    def test_record_includes_entire_branch_in_both_backend_modes(self):
        french = AdminArea.objects.create(id="roussillon", country_code="france", code="ROU",
            name="Roussillon", level=3, pop_latest=77, area_km2=Decimal("7.5"))
        province = NuevoAdminArea.objects.create(id="province", country_code=self.old.country_code,
            code="CAS-P", name="Province", level=2, parent=self.old)
        town = NuevoAdminArea.objects.create(id="town", country_code=self.old.country_code,
            code="CAS-P-T", name="Town", level=3, parent=province)
        province.municipios_originales.add(self.source)
        town.municipios_originales.add(self.source, french)
        sibling = NuevoAdminArea.objects.create(id="outside", country_code=self.old.country_code,
            code="OUT", name="Outside", level=1, parent=self.root)
        outside = AdminArea.objects.create(id="outside-source", country_code="spain", code="OUT",
            name="Outside", level=3, pop_latest=999)
        sibling.municipios_originales.add(outside)
        original_ids = set(NuevoAdminArea.objects.values_list("pk", flat=True))
        url = self.url + "&edit_area_id=" + self.old.pk
        for java in (True, False):
            with self.subTest(java=java), override_settings(BEOGRAD_JAVA_ENABLED=java, BEOGRAD_JAVA_PORT=8001,
                MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware']), patch('ciudades_del_mundo.web.java_routing.build_opener') as upstream:
                client = Client()
                self.old.municipios_originales.clear()
                popup = client.get(url)
                initial = popup.context["initial_selections"]
                self.assertEqual({item["key"] for item in initial}, {self.source.pk, french.pk})
                for changed in (False, True):
                    response = client.post(url, {"name": "Catalunya", "entity_type": "Region",
                        "selection_json": json.dumps({"country_boxes": True, "selected_ids": [self.source.pk],
                                                       "selection_changed": changed})})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.old.refresh_from_db()
                    self.assertEqual((self.old.pop_latest, self.old.area_km2), (200, Decimal("20")))
                    self.assertEqual(set(self.old.municipios_originales.values_list("pk", flat=True)),
                                     {self.source.pk, french.pk})
                # Selecting a saved subdivision uses the same complete branch.
                self.old.municipios_originales.clear()
                response = client.post(self.url + "&kind=record", {"slug": "COPYJ" if java else "COPYP",
                    "name": "Copy", "selection_json": json.dumps({"country_boxes": True, "selected_ids": [],
                        "selected_derived_subdivisions": [{"country_code": self.old.country_code,
                                                          "key": "area:" + self.old.pk}]})}, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200, response.content)
                config = self.country.configs.get(slug=response.json()["slug"])
                copy = NuevoAdminArea.objects.get(pk=tomllib.loads(config.content)["materialized_area_id"])
                self.assertEqual((copy.pop_latest, copy.area_km2), (200, Decimal("20")))
                self.assertTrue(original_ids.issubset(set(NuevoAdminArea.objects.values_list("pk", flat=True))))
                upstream.assert_not_called()

    def test_record_from_recipe_generates_all_source_children_in_both_modes(self):
        department = AdminArea.objects.create(id="french-department", country_code="france", code="DEP",
            name="Department", level=2)
        french = AdminArea.objects.create(id="french-town", country_code="france", code="FR",
            name="French town", level=3, parent=department, pop_latest=77, area_km2=Decimal("7.5"))
        excluded = AdminArea.objects.create(id="excluded-town", country_code="france", code="EX",
            name="Excluded town", level=3, parent=department, pop_latest=50, area_km2=Decimal("5"))
        recipe = DerivedSubdivision.objects.create(slug="catalunya", internal_name="CATALUNYA",
            source_country_code="spain", name="Catalunya", content='''kind = "derived_subdivision"
internal_name = "CATALUNYA"
[[include]]
country_code = "spain"
level = 1
ids = ["source_town"]
[[include]]
country_code = "france"
level = 2
ids = ["french-department"]
[[subtract]]
country_code = "france"
level = 3
ids = ["excluded-town"]
''')
        for java in (True, False):
            with self.subTest(java=java), override_settings(BEOGRAD_JAVA_ENABLED=java, BEOGRAD_JAVA_PORT=8001,
                MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware']), patch('ciudades_del_mundo.web.java_routing.build_opener') as upstream:
                response = Client().post(self.url + "&kind=record", {
                    "slug": "CATJ" if java else "CATP", "name": "Catalunya",
                    "selection_json": json.dumps({"country_boxes": True, "selected_ids": [],
                        "selected_derived_subdivisions": [{"country_code": "spain", "key": "recipe:" + recipe.pk}]}),
                }, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200, response.content)
                config = self.country.configs.get(slug=response.json()["slug"])
                area = NuevoAdminArea.objects.get(pk=tomllib.loads(config.content)["materialized_area_id"])
                self.assertEqual(set(area.children.values_list("municipios_originales", flat=True)),
                                 {self.source.pk, french.pk})
                self.assertEqual(area.children.count(), 2)
                self.assertEqual(area.pop_latest, 200)
                edit_url = self.url + "&edit_area_id=" + area.pk
                # An existing record without generated children can be populated by saving its selection.
                area.children.all().delete()
                response = Client().post(edit_url, {"name": "Catalunya", "entity_type": "Region",
                    "selection_json": json.dumps({"country_boxes": True, "selection_changed": True,
                        "selected_ids": [], "selected_derived_subdivisions": [
                            {"country_code": "spain", "key": "recipe:" + recipe.pk}]}),
                })
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(set(area.children.values_list("municipios_originales", flat=True)),
                                 {self.source.pk, french.pk})
                upstream.assert_not_called()

    def test_capital_picker_uses_selected_sources_and_children(self):
        response = self.create(code="CHILD", parent=self.old, selected=[self.source.pk])
        self.assertEqual(response.status_code, 200, response.content)
        url = "/new-countries/spain/empire/configs/new/capital-options/"
        for selection in ({"selected_ids": [self.source.pk]}, {"selected_ids": [], "area_id": self.old.pk}):
            response = self.client.post(url, {"country_boxes": True, **selection}, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["capital_options"], [{"value": self.source.pk, "label": self.source.name}])

    def test_manual_template_keeps_entered_metrics_and_sums_missing_values(self):
        response = self.client.post(self.url + "&kind=template", {
            "slug": "MANUAL", "name": "Manual", "parent_area_id": self.old.pk, "manual_population": "17",
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        owner = self.country.configs.get(slug=response.json()["slug"])
        parent = NuevoAdminArea.objects.get(pk=tomllib.loads(owner.content)["materialized_area_id"])
        child = self.create(code="SOURCE", parent=parent, selected=[self.source.pk])
        self.assertEqual(child.status_code, 200, child.content)
        parent.refresh_from_db()
        self.assertEqual((parent.pop_latest, parent.area_km2), (17, Decimal("12.5")))
        url = self.url + "&edit_area_id=" + parent.pk
        self.assertContains(self.client.get(url), 'name="manual_population" value="17"')
        self.assertNotContains(self.client.get(url), 'data-country-sources')
        edit = self.client.post(url, {"name": "Manual", "manual_area": "0"}, HTTP_ACCEPT="application/json")
        self.assertEqual(edit.status_code, 200, edit.content)
        parent.refresh_from_db()
        self.assertEqual((parent.pop_latest, parent.area_km2, parent.children.count()), (123, Decimal("0"), 1))

    def test_edit_config_retains_unmodified_selection(self):
        self.bridge.content = 'kind = "derived_country_config"\n[selection]\ninclude_ids = ["source_town"]\n[[entities]]\nname = "Old"\ncode = "OLD"\n'
        self.bridge.save()
        url = "/new-countries/spain/empire/configs/old/?embedded=1"
        self.assertContains(self.client.get(url), 'value="Old recipe"')
        response = self.client.post(url, {"name": "Updated", "entity_type": "Region",
                                         "selection_json": json.dumps({"selection_changed": False})})
        self.assertEqual(response.status_code, 200, response.content)
        self.bridge.refresh_from_db()
        self.assertEqual(self.bridge.name, "Updated")
        self.assertEqual(tomllib.loads(self.bridge.content)["selection"]["include_ids"], ["source_town"])

    def test_selecting_existing_created_child_moves_its_saved_branch_and_sums_it(self):
        response = self.create(code='CHILD', selected=[self.source.pk])
        child_config = self.country.configs.get(slug=response.json()['slug'])
        child_id = tomllib.loads(child_config.content)['materialized_area_id']
        response = self.client.post(self.url, {'slug': 'CONTAINER', 'name': 'Container',
            'selection_json': json.dumps({'selected_ids': [], 'selected_child_config_slugs': [child_config.slug]})}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        parent_config = self.country.configs.get(slug=response.json()['slug'])
        parent = NuevoAdminArea.objects.get(pk=tomllib.loads(parent_config.content)['materialized_area_id'])
        child = NuevoAdminArea.objects.get(pk=child_id)
        self.assertEqual(child.parent_id, parent.pk)
        self.assertEqual(child.level, parent.level + 1)
        self.assertEqual(parent.pop_latest, 123)
        self.assertEqual(full_area_code(child), 'EMP-CONTAINER-CHILD')
