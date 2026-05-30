from decimal import Decimal
import os

import django
from django.apps import apps
from django.test import TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.settings")
if not apps.ready:
    django.setup()

from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
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
