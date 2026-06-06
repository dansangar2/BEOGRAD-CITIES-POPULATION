from decimal import Decimal
import os

import django
from django.apps import apps
from django.test import TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.settings")
if not apps.ready:
    django.setup()

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
from ciudades_del_mundo.services.nuevo_admin_builder import create_nuevo_area_from_spec
from ciudades_del_mundo.services.source_population_indices import SourcePopulationIndexRegistry, _population_index_mapping


class SourcePopulationIndexTests(TestCase):
    def setUp(self):
        self.source_root = AdminArea.objects.create(
            id="spain_spain",
            country_code="spain",
            code="spain",
            name="Spain",
            level=0,
            area_km2=Decimal("0"),
            pop_latest=3000,
        )
        self.madrid = AdminArea.objects.create(
            id="spain_mad",
            country_code="spain",
            code="mad",
            name="Madrid",
            level=1,
            parent=self.source_root,
            area_km2=Decimal("10"),
            pop_latest=1000,
        )
        self.castilla_la_mancha = AdminArea.objects.create(
            id="spain_clm",
            country_code="spain",
            code="clm",
            name="Castilla-La Mancha",
            level=1,
            parent=self.source_root,
            area_km2=Decimal("20"),
            pop_latest=2000,
        )
        self.root = NuevoAdminArea.objects.create(
            id="test_country",
            country_code="test_country",
            code="test_country",
            name="Test Country",
            level=0,
            entity_type="Country",
            municipal_level=1,
        )


    def test_shared_seed_file_ignores_scraping_configs_for_population_indices(self):
        raw = {
            "scraping_configs": [{"slug": "spain", "content": "name = \"Spain\""}],
            "spain": {"Madrid": {1995: 0.5}},
        }

        registry = SourcePopulationIndexRegistry.from_mapping(_population_index_mapping(raw))

        self.assertEqual(registry.multiplier_for_area(self.madrid, 1999), Decimal("0.5"))

    def test_registry_uses_period_until_next_configured_year(self):
        registry = SourcePopulationIndexRegistry.from_mapping(
            {"spain": {"Madrid": {1995: 0.5, 2000: 0.8}}}
        )

        self.assertIsNone(registry.multiplier_for_area(self.madrid, 1994))
        self.assertEqual(registry.multiplier_for_area(self.madrid, 1999), Decimal("0.5"))
        self.assertEqual(registry.multiplier_for_area(self.madrid, 2000), Decimal("0.8"))

    def test_build_applies_source_population_indices_to_created_area(self):
        registry = SourcePopulationIndexRegistry.from_mapping(
            {
                "spain": {
                    "Madrid": {1995: 0.5, 2000: 0.8},
                    "Castilla-La Mancha": {1995: 0.3},
                }
            }
        )

        area = create_nuevo_area_from_spec(
            parent_country_id=self.root.id,
            new_name="Castilla",
            include_spec={1: {"spain": ["Madrid", "Castilla-La Mancha"]}},
            entity_type="Provincia",
            new_code="CAS",
            source_population_year=1999,
            source_population_index_registry=registry,
        )

        self.assertEqual(area.pop_latest, 1100)
        self.assertEqual(area.area_km2, Decimal("30.00"))

    def test_build_inherits_source_population_index_from_parent_area(self):
        madrid_city = AdminArea.objects.create(
            id="spain_mad_city",
            country_code="spain",
            code="mad_city",
            name="Madrid City",
            level=2,
            parent=self.madrid,
            area_km2=Decimal("5"),
            pop_latest=500,
        )
        registry = SourcePopulationIndexRegistry.from_mapping(
            {"spain": {"Madrid": {1995: 0.4}}}
        )

        area = create_nuevo_area_from_spec(
            parent_country_id=self.root.id,
            new_name="Villa de Madrid",
            include_spec={2: {"spain": [madrid_city.name]}},
            entity_type="Provincia",
            new_code="VMA",
            source_population_year=1999,
            source_population_index_registry=registry,
        )

        self.assertEqual(area.pop_latest, 200)
        self.assertEqual(area.area_km2, Decimal("5.00"))
