import os
import unittest

import django
from django.apps import apps

from ciudades_del_mundo.domain import RepresentationConfig, RepresentationSystem

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ciudades_del_mundo.settings")
if not apps.ready:
    django.setup()

from ciudades_del_mundo.services.nuevo_admin_representatives import (
    RepresentationArea,
    allocate_dhondt_representatives,
    representation_config_from_mapping,
)


class RepresentationTests(unittest.TestCase):
    def test_allocate_dhondt_representatives_is_deterministic(self):
        areas = [
            RepresentationArea(id="a", code="A", name="Alpha", pop_latest=100),
            RepresentationArea(id="b", code="B", name="Beta", pop_latest=60),
            RepresentationArea(id="c", code="C", name="Gamma", pop_latest=40),
        ]
        config = RepresentationConfig(level=1, system=RepresentationSystem.DHONDT, total=10)

        seats = allocate_dhondt_representatives(areas, config)

        self.assertEqual(seats, {"a": 5, "b": 3, "c": 2})

    def test_representation_config_from_mapping_accepts_legacy_names(self):
        config = representation_config_from_mapping({"nivel": 2, "escanhos": 7, "min": 1})

        self.assertEqual(config.level, 2)
        self.assertEqual(config.total, 7)
        self.assertEqual(config.minimum, 1)
        self.assertEqual(config.system, RepresentationSystem.DHONDT)
