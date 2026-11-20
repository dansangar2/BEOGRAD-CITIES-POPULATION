"""Multiple seed files coexist with SQL-only countries in both HTTP modes."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig
from ciudades_del_mundo.services.derived_config_seeds import (
    PACKAGE_ROOT, export_new_countries_to_toml, import_new_country_config_path, new_country_seed_source,
)


class NewCountryTomlTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed_patch = patch('ciudades_del_mundo.services.derived_config_seeds.NEW_COUNTRY_CONFIG_SEED_ROOT', self.root)
        self.seed_patch.start()
        self.addCleanup(self.seed_patch.stop)
        self.country = DerivedCountry.objects.create(slug='custom', name='Mi pais', description='Creado en SQL', source_country_code='spain')
        self.config = DerivedCountryConfig.objects.create(
            country=self.country, slug='first', name='Primera', source_country_code='spain', derived_country_code='custom_first',
            content='kind = "derived_country_config"\nname = "Antiguo"\n[selection]\ninclude_ids = []\n[[entities]]\nname = "Primera"\nlevel = 1\n',
        )

    def seed(self, filename='incoming.toml', *, country='custom', slug='second', source='spain'):
        path = self.root / filename
        path.write_text(f'kind = "derived_country_config"\ncountry = "{country}"\nslug = "{slug}"\nname = "Importada"\ncountry_name = "Otro nombre"\nsource_country_code = "{source}"\n[selection]\ninclude_ids = []\n', encoding='utf-8')
        return path

    def test_import_adds_recipe_without_replacing_sql_recipe_or_container(self):
        before = self.config.content
        path = self.seed()
        imported = import_new_country_config_path(path)
        self.assertEqual(imported.slug, 'second')
        self.config.refresh_from_db()
        self.country.refresh_from_db()
        self.assertEqual(self.config.content, before)
        self.assertEqual(self.country.name, 'Mi pais')
        self.assertEqual(self.country.description, 'Creado en SQL')
        self.assertEqual(self.country.configs.count(), 2)
        import_new_country_config_path(path)
        self.assertEqual(self.country.configs.count(), 2)

    def test_export_writes_sql_only_recipe_and_updates_original_file_by_identity(self):
        incoming = self.seed()
        imported = import_new_country_config_path(incoming)
        imported.name = 'Actualizada'
        imported.save()
        paths = export_new_countries_to_toml()
        self.assertEqual(set(paths), {incoming, self.root / 'custom__first.toml'})
        data = tomllib.loads(incoming.read_text(encoding='utf-8'))
        self.assertEqual(data['name'], 'Actualizada')
        self.assertEqual(data['country_name'], 'Mi pais')
        first = tomllib.loads((self.root / 'custom__first.toml').read_text(encoding='utf-8'))
        self.assertEqual(first['entities'][0]['level'], 1)
        self.assertEqual(first['selection']['include_ids'], [])
        self.assertEqual(first['source_country_code'], 'spain')
        self.assertEqual(len(export_new_countries_to_toml()), 2)
        self.assertEqual(len(list(self.root.glob('*.toml'))), 2)
        DerivedCountry.objects.all().delete()
        for path in paths:
            import_new_country_config_path(path)
        restored = DerivedCountry.objects.get(pk='custom')
        self.assertEqual(restored.name, 'Mi pais')
        self.assertEqual(restored.description, 'Creado en SQL')
        self.assertEqual(set(restored.configs.values_list('slug', flat=True)), {'first', 'second'})

    def test_same_recipe_slug_in_different_countries_has_separate_files(self):
        other = DerivedCountry.objects.create(slug='other', name='Otro', source_country_code='france')
        DerivedCountryConfig.objects.create(country=other, slug='first', name='Otra', source_country_code='france', content=self.config.content)
        unrelated = self.root / 'custom__first.toml'
        unrelated.write_text('invalid unrelated file', encoding='utf-8')
        paths = export_new_countries_to_toml()
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'invalid unrelated file')
        self.assertEqual({path.name for path in paths}, {'custom__first__2.toml', 'other__first.toml'})

    def test_empty_container_round_trip_and_cli_import(self):
        empty = DerivedCountry.objects.create(slug='empty', name='Vacio', source_country_code='spain')
        paths = export_new_countries_to_toml(countries=[empty])
        self.assertEqual(len(paths), 1)
        empty.delete()
        call_command('sync_derived_configs', 'new-countries', 'empty', stdout=StringIO())
        restored = DerivedCountry.objects.get(pk='empty')
        self.assertEqual(restored.name, 'Vacio')
        self.assertFalse(restored.configs.exists())

    def test_scoped_import_and_exports_in_python_and_combined_java_modes(self):
        incoming = self.seed()
        self.seed('france.toml', country='french', source='france')
        for java in (False, True):
            with self.subTest(java=java), override_settings(
                BEOGRAD_JAVA_ENABLED=java, BEOGRAD_JAVA_PORT=8001,
                MIDDLEWARE=[*settings.MIDDLEWARE, 'ciudades_del_mundo.web.java_routing.JavaApiRoutingMiddleware'],
            ), patch('ciudades_del_mundo.web.java_routing.build_opener') as upstream:
                client = Client()
                def run_import(**kwargs):
                    call_command(*kwargs['args'], stdout=StringIO())
                    return type('Task', (), {'id': 'imported', 'status': 'queued', 'is_active': True})()
                with patch('ciudades_del_mundo.web.tasks.task_manager.start', side_effect=run_import) as start:
                    response = client.post('/new-countries/import-toml/', {'source_country_code': 'spain'}, HTTP_ACCEPT='application/json')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(all(call.kwargs['args'][2] != 'france' for call in start.call_args_list))
                self.assertEqual(self.country.configs.count(), 2)
                response = client.post('/new-countries/export-toml/', {'country_slug': 'custom', 'config_slug': 'second'}, HTTP_ACCEPT='application/json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['exported'], 1)
                self.assertFalse((self.root / 'custom__first.toml').exists())
                response = client.post('/new-countries/export-toml/', {'source_country_code': 'spain'}, HTTP_ACCEPT='application/json')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['exported'], 2)
                upstream.assert_not_called()
                (self.root / 'custom__first.toml').unlink()
        self.assertTrue(incoming.exists())

    def test_export_requires_post_and_csrf_and_missing_selection_does_not_export_all(self):
        self.assertEqual(self.client.get('/new-countries/export-toml/').status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/new-countries/export-toml/').status_code, 403)
        response = self.client.post('/new-countries/export-toml/', {'country_slug': 'custom', 'config_slug': 'missing'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(list(self.root.glob('*.toml')))

    def test_country_panel_and_container_offer_scoped_actions(self):
        AdminArea.objects.create(id='spain', country_code='spain', code='ESP', name='Espana', level=0, pop_latest=100)
        response = self.client.get('/new-countries/')
        self.assertContains(response, 'name="source_country_code" value="spain"')
        self.assertContains(response, 'name="country_slug" value="custom"')
        self.assertContains(response, 'data-config-export-form')
        self.assertContains(response, '/new-countries/import-toml/')

    def test_export_preserves_nested_recipe_tables_and_global_keys(self):
        self.config.content += '\n[[entities.include]]\ncountry_code = "spain"\nlevel = 2\nnames = ["A", "B"]\n[[entities.subtract]]\ncountry_code = "france"\nlevel = 3\nnames = ["C"]\n'
        self.config.save()
        before = tomllib.loads(self.config.content)
        paths = export_new_countries_to_toml()
        text = paths[0].read_text(encoding='utf-8')
        after = tomllib.loads(text)
        self.assertEqual(after['entities'], before['entities'])
        self.assertEqual(after['selection'], before['selection'])
        self.assertIn('[[entities.include]]', text)
        self.assertLess(text.index('source_country_code ='), text.index('[selection]'))

    def test_spanish_empire_seeds_belong_to_spain_and_preserve_saved_definitions(self):
        original = self.config.content
        names = ('first_spanish_empire', 'second_spanish_empire', 'austria_spanish_empire',
                 'bourbon_spanish_empire', 'perfect_spanish_empire')
        for name in names:
            path = PACKAGE_ROOT / 'new_country_configs' / f'{name}.toml'
            with self.subTest(seed=name):
                self.assertEqual(new_country_seed_source(path), 'spain')
                data = tomllib.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['selection']['source_country_code'], 'spain')
                record = import_new_country_config_path(path)
                self.assertEqual(record.source_country_code, 'spain')
                self.assertEqual(record.country.source_country_code, 'spain')
                self.assertEqual(tomllib.loads(record.content)['legacy'], data['legacy'])
        self.config.refresh_from_db()
        self.assertEqual(self.config.content, original)
