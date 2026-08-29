from decimal import Decimal
import os
from pathlib import Path
import tomllib

import django
from django.apps import apps
from django.test import TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.settings")
if not apps.ready:
    django.setup()

from ciudades_del_mundo.models import AdminArea, DerivedSubdivision, NuevoAdminArea, ScrapingConfig
from ciudades_del_mundo.services.derived_subdivision_builder import build_derived_subdivisions_for_country
from ciudades_del_mundo.services.nuevo_admin_builder import (
    _descendants_at_level,
    create_nuevo_area_from_spec,
)


class NuevoAdminBuilderUsaTests(TestCase):
    def setUp(self):
        self.derived_root = NuevoAdminArea.objects.create(
            id="test_country",
            country_code="test_country",
            code="test_country",
            name="Test Country",
            level=0,
            entity_type="Country",
            municipal_level=3,
        )
        self.usa = AdminArea.objects.create(
            id="usa_usa",
            country_code="usa",
            code="usa",
            name="United States",
            level=0,
            area_km2=Decimal("0"),
            pop_latest=0,
            url="https://www.citypopulation.de/en/usa/",
        )
        self.texas = AdminArea.objects.create(
            id="usa_48",
            country_code="usa",
            code="48",
            name="Texas",
            level=1,
            parent=self.usa,
            area_km2=Decimal("0"),
            pop_latest=0,
            url="https://www.citypopulation.de/en/usa/texas/",
        )
        self.harris = AdminArea.objects.create(
            id="usa_48201",
            country_code="usa",
            code="48201",
            name="Harris",
            level=2,
            parent=self.texas,
            area_km2=Decimal("0"),
            pop_latest=0,
            url="https://www.citypopulation.de/en/usa/texas/admin/",
        )
        self.small_city = AdminArea.objects.create(
            id="usa_4801000",
            country_code="usa",
            code="4801000",
            name="Small City",
            level=3,
            parent=self.harris,
            entity_type="City",
            area_km2=Decimal("10"),
            pop_latest=100,
            url="https://www.citypopulation.de/en/usa/texas/harris/4801000__small_city/",
        )
        self.houston = AdminArea.objects.create(
            id="usa_4835000",
            country_code="usa",
            code="4835000",
            name="Houston",
            level=3,
            entity_type="City",
            area_km2=Decimal("20"),
            pop_latest=2000,
            url="https://www.citypopulation.de/en/usa/texas/harris_fort_bend_mont/4835000__houston/",
        )

    def test_usa_parentless_city_is_used_for_most_populated(self):
        area = create_nuevo_area_from_spec(
            parent_country_id=self.derived_root.id,
            new_name="Nueva Filipinas",
            include_spec={2: {"usa": ["Harris"]}},
            entity_type="Provincia",
            new_code="TEX",
        )

        self.assertEqual(area.most_populate_city_id, self.houston.id)
        self.assertEqual(
            set(area.municipios_originales.values_list("id", flat=True)),
            {self.small_city.id, self.houston.id},
        )

    def test_numeric_prefix_fallback_ignores_rows_with_real_parent(self):
        borden = AdminArea.objects.create(
            id="usa_48033",
            country_code="usa",
            code="48033",
            name="Borden",
            level=2,
            parent=self.texas,
        )
        red_river = AdminArea.objects.create(
            id="usa_48387",
            country_code="usa",
            code="48387",
            name="Red River",
            level=2,
            parent=self.texas,
        )
        AdminArea.objects.create(
            id="usa_4803360",
            country_code="usa",
            code="4803360",
            name="Annona",
            level=3,
            parent=red_river,
        )

        self.assertEqual(_descendants_at_level(borden, 3), [])


class NuevoAdminBuilderExternalRootTests(TestCase):
    def setUp(self):
        self.derived_root = NuevoAdminArea.objects.create(
            id="test_empire",
            country_code="test_empire",
            code="TES",
            name="Test Empire",
            level=0,
            entity_type="Country",
            municipal_level=3,
        )
        self.gibraltar = AdminArea.objects.create(
            id="gibraltar_gibraltar",
            country_code="gibraltar",
            code="gibraltar",
            name="Gibraltar",
            level=0,
            area_km2=Decimal("6.55"),
            pop_latest=38196,
        )
        ScrapingConfig.objects.update_or_create(
            slug="gibraltar",
            defaults={
                "country_code": "gibraltar",
                "name": "Gibraltar",
                "content": "LEGAL_SUBDIVISION = 0\n",
                "content_hash": "test",
            },
        )
        self.ea_1 = AdminArea.objects.create(
            id="gibraltar_001",
            country_code="gibraltar",
            code="001",
            name="EA 1",
            level=1,
            parent=self.gibraltar,
            area_km2=Decimal("2.00"),
            pop_latest=100,
        )
        self.ea_2 = AdminArea.objects.create(
            id="gibraltar_002",
            country_code="gibraltar",
            code="002",
            name="EA 2",
            level=1,
            parent=self.gibraltar,
            area_km2=Decimal("4.55"),
            pop_latest=200,
        )

    def test_external_country_root_uses_configured_legal_level_zero_as_unit(self):
        area = create_nuevo_area_from_spec(
            parent_country_id=self.derived_root.id,
            new_name="Sevilla",
            include_spec={0: {"gibraltar": [{"id": self.gibraltar.id}]}},
            entity_type="Reino",
            new_code="SEV",
        )

        self.assertEqual(
            set(area.municipios_originales.values_list("id", flat=True)),
            {self.gibraltar.id},
        )
        self.assertEqual(area.area_km2, Decimal("6.55"))
        self.assertEqual(area.pop_latest, 38196)

    def test_bourbon_spanish_empire_uses_sevilla_variant_with_gibraltar(self):
        seed_path = (
            Path(__file__).resolve().parents[1]
            / "new_country_configs"
            / "bourbon_spanish_empire.toml"
        )
        data = tomllib.loads(seed_path.read_text(encoding="utf-8"))
        legacy_python = data["legacy"]["python"]

        self.assertIn('"childs": [SEVILLA_B_C_GB, CORDOBA_B, JAEN_B, NUEVAS_POBLACIONES]', legacy_python)

    def test_partial_rebuild_replaces_existing_generated_children_and_gibraltar_root_unit(self):
        spain_root = AdminArea.objects.create(
            id="spain_root",
            country_code="spain",
            code="ESP",
            name="Espana",
            level=0,
        )
        province = AdminArea.objects.create(
            id="spain_41",
            country_code="spain",
            code="41",
            name="Sevilla",
            level=2,
            parent=spain_root,
        )
        municipality = AdminArea.objects.create(
            id="spain_41091",
            country_code="spain",
            code="41091",
            name="Sevilla",
            level=3,
            parent=province,
            area_km2=Decimal("141.00"),
            pop_latest=684000,
        )
        DerivedSubdivision.objects.create(
            slug="spain_sevilla_b_c_gb",
            internal_name="SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            source_country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            entity_type="Reino",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "SEVILLA_B_C_GB"',
                    'source_country_code = "spain"',
                    'name = "Sevilla (Borbones con Gibraltar)"',
                    'code = "ESP-SEVILLA_B_C_GB"',
                    'parent_code = "ESP"',
                    'entity_type = "Reino"',
                    "level = 1",
                    "use_selected_entities_as_children = false",
                    "",
                    "[[include]]",
                    'country_code = "spain"',
                    "level = 2",
                    'ids = ["spain_41"]',
                    "",
                    "[[include]]",
                    'country_code = "gibraltar"',
                    "level = 0",
                    'ids = ["gibraltar_gibraltar"]',
                ]
            ),
        )
        root = NuevoAdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Spain",
            level=0,
            entity_type="Country",
            municipal_level=3,
        )
        stale_parent = NuevoAdminArea.objects.create(
            id="spain-ESP-SEVILLA_B_C_GB",
            country_code="spain",
            code="ESP-SEVILLA_B_C_GB",
            name="Sevilla (Borbones con Gibraltar)",
            level=1,
            entity_type="Reino",
            parent=root,
            municipal_level=3,
        )
        stale_parent.municipios_originales.add(self.ea_1)
        NuevoAdminArea.objects.create(
            id="spain-ESP-SEVILLA_B_C_GB-41091",
            country_code="spain",
            code="ESP-SEVILLA_B_C_GB-41091",
            name="Sevilla",
            level=2,
            entity_type="Municipio",
            parent=stale_parent,
            municipal_level=3,
        )

        result = build_derived_subdivisions_for_country(
            "spain",
            force=True,
            slugs=["SEVILLA_B_C_GB"],
        )

        self.assertEqual(result.records, 1)
        rebuilt = NuevoAdminArea.objects.get(country_code="spain", code="ESP-SEVILLA_B_C_GB")
        self.assertEqual(
            set(rebuilt.municipios_originales.values_list("id", flat=True)),
            {municipality.id, self.gibraltar.id},
        )
        self.assertNotIn(self.ea_1.id, set(rebuilt.municipios_originales.values_list("id", flat=True)))
        child = NuevoAdminArea.objects.get(country_code="spain", code="ESP-SEVILLA_B_C_GB-41091")
        self.assertEqual(child.parent_id, rebuilt.id)


class NuevoAdminBuilderScopedSubtractTests(TestCase):
    def setUp(self):
        self.derived_root = NuevoAdminArea.objects.create(
            id="spain",
            country_code="spain",
            code="ESP",
            name="Spain",
            level=0,
            entity_type="Country",
            municipal_level=3,
        )
        self.france = AdminArea.objects.create(
            id="france_france",
            country_code="france",
            code="FR",
            name="France",
            level=0,
            area_km2=Decimal("0"),
            pop_latest=0,
        )
        self.pyrenees = AdminArea.objects.create(
            id="france_66",
            country_code="france",
            code="66",
            name="Pyrenees-Orientales",
            level=2,
            parent=self.france,
            area_km2=Decimal("3"),
            pop_latest=300,
        )
        self.prades = AdminArea.objects.create(
            id="france_663",
            country_code="france",
            code="663",
            name="Prades",
            level=3,
            parent=self.pyrenees,
        )
        self.belesta_inside = AdminArea.objects.create(
            id="france_66019",
            country_code="france",
            code="66019",
            name="Belesta",
            level=4,
            parent=self.prades,
            area_km2=Decimal("1"),
            pop_latest=100,
        )
        self.other_inside = AdminArea.objects.create(
            id="france_66020",
            country_code="france",
            code="66020",
            name="Other",
            level=4,
            parent=self.prades,
            area_km2=Decimal("2"),
            pop_latest=200,
        )
        self.ariege = AdminArea.objects.create(
            id="france_09",
            country_code="france",
            code="09",
            name="Ariege",
            level=2,
            parent=self.france,
        )
        self.foix = AdminArea.objects.create(
            id="france_092",
            country_code="france",
            code="092",
            name="Foix",
            level=3,
            parent=self.ariege,
        )
        self.belesta_outside = AdminArea.objects.create(
            id="france_09047",
            country_code="france",
            code="09047",
            name="Belesta",
            level=4,
            parent=self.foix,
            area_km2=Decimal("9"),
            pop_latest=900,
        )

    def test_subtract_prefers_same_name_inside_included_parent_scope(self):
        area = create_nuevo_area_from_spec(
            parent_country_id=self.derived_root.id,
            new_name="Catalunya",
            include_spec={
                2: {"france": ["Pyrenees-Orientales"]},
                "restar": {4: {"france": ["Belesta"]}},
            },
            entity_type="Principality",
            new_code="CAT",
        )

        self.assertEqual(
            set(area.municipios_originales.values_list("id", flat=True)),
            {self.other_inside.id},
        )


class NuevoAdminBuilderMixedMunicipalLevelsTests(TestCase):
    def setUp(self):
        self.derived_root = NuevoAdminArea.objects.create(
            id="mixed_france",
            country_code="mixed_france",
            code="MFR",
            name="Mixed France",
            level=0,
            entity_type="Country",
            municipal_level=4,
        )
        self.france = AdminArea.objects.create(
            id="france_france",
            country_code="france",
            code="FR",
            name="France",
            level=0,
        )
        self.department = AdminArea.objects.create(
            id="france_16",
            country_code="france",
            code="16",
            name="Charente",
            level=2,
            parent=self.france,
        )
        self.arrondissement = AdminArea.objects.create(
            id="france_161",
            country_code="france",
            code="161",
            name="Angouleme",
            level=3,
            parent=self.department,
        )
        self.nested_commune = AdminArea.objects.create(
            id="france_16015",
            country_code="france",
            code="16015",
            name="Nested Commune",
            level=4,
            parent=self.arrondissement,
            area_km2=Decimal("10"),
            pop_latest=100,
        )
        self.direct_commune = AdminArea.objects.create(
            id="france_16215",
            country_code="france",
            code="16215",
            name="Direct Commune",
            level=3,
            parent=self.department,
            area_km2=Decimal("5"),
            pop_latest=50,
        )

    def test_level4_selector_accepts_leaf_commune_imported_at_level3(self):
        area = create_nuevo_area_from_spec(
            parent_country_id=self.derived_root.id,
            new_name="Angoumois",
            include_spec={4: {"france": ["Nested Commune", "Direct Commune"]}},
            entity_type="Province",
            new_code="ANG",
        )

        self.assertEqual(
            set(area.municipios_originales.values_list("id", flat=True)),
            {self.nested_commune.id, self.direct_commune.id},
        )
        self.assertEqual(area.area_km2, Decimal("15.00"))
        self.assertEqual(area.pop_latest, 150)

    def test_macro_expansion_includes_leaf_commune_imported_above_atomic_level(self):
        descendants = _descendants_at_level(self.department, 4)

        self.assertEqual(
            {area.id for area in descendants},
            {self.nested_commune.id, self.direct_commune.id},
        )


class DerivedSubdivisionBuilderPartialErrorTests(TestCase):
    def setUp(self):
        self.france = AdminArea.objects.create(
            id="france_france",
            country_code="france",
            code="FR",
            name="France",
            level=0,
            area_km2=Decimal("20"),
            pop_latest=200,
        )
        self.department = AdminArea.objects.create(
            id="france_16",
            country_code="france",
            code="16",
            name="Charente",
            level=2,
            parent=self.france,
        )
        self.commune = AdminArea.objects.create(
            id="france_16001",
            country_code="france",
            code="16001",
            name="Resolvable",
            level=4,
            parent=self.department,
            area_km2=Decimal("7"),
            pop_latest=70,
        )
        DerivedSubdivision.objects.create(
            slug="france_good",
            internal_name="GOOD",
            name="Good",
            source_country_code="france",
            code="GOOD",
            entity_type="Province",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "GOOD"',
                    'source_country_code = "france"',
                    'name = "Good"',
                    'code = "GOOD"',
                    'entity_type = "Province"',
                    "",
                    "[[include]]",
                    'country_code = "france"',
                    "level = 4",
                    'names = ["Resolvable"]',
                    "",
                ]
            ),
        )
        DerivedSubdivision.objects.create(
            slug="france_bad",
            internal_name="BAD",
            name="Bad",
            source_country_code="france",
            code="BAD",
            entity_type="Province",
            content="\n".join(
                [
                    'kind = "derived_subdivision"',
                    'internal_name = "BAD"',
                    'source_country_code = "france"',
                    'name = "Bad"',
                    'code = "BAD"',
                    'entity_type = "Province"',
                    "",
                    "[[include]]",
                    'country_code = "france"',
                    "level = 4",
                    'names = ["Missing"]',
                    "",
                ]
            ),
        )

    def test_continue_on_error_builds_resolvable_records(self):
        result = build_derived_subdivisions_for_country("france", force=True, continue_on_error=True)

        self.assertEqual(result.records, 2)
        self.assertEqual(result.built, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("france_bad", result.errors[0])
        area = NuevoAdminArea.objects.get(country_code="france", name="Good")
        self.assertEqual(area.area_km2, Decimal("7.00"))
        self.assertEqual(area.pop_latest, 70)

    def test_slug_filter_builds_only_requested_derived_subdivision(self):
        result = build_derived_subdivisions_for_country("france", slugs=["GOOD"], force=True)

        self.assertEqual(result.records, 1)
        self.assertEqual(result.built, 1)
        self.assertEqual(result.errors, ())
        self.assertTrue(NuevoAdminArea.objects.filter(country_code="france", name="Good").exists())
        self.assertFalse(NuevoAdminArea.objects.filter(country_code="france", name="Bad").exists())
