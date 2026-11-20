"""District selection through SQL recipes, HTTP selectors and generated cities."""
import json
import tomllib
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from ciudades_del_mundo.models import AdminArea, DerivedCountry, DerivedCountryConfig, NuevoAdminArea, SubdivisionGroup
from ciudades_del_mundo.services.derived_config_seeds import (
    render_derived_country_selection_toml, _normalized_group_bundle_seed_content,
    _parse_subdivision_group_seed_records,
)
from ciudades_del_mundo.services.derived_country_builder import build_derived_country_config
from ciudades_del_mundo.services.derived_subdivision_builder import _BuildContext, _build_node
from ciudades_del_mundo.services.nuevo_admin_builder import create_nuevo_area_from_spec
from ciudades_del_mundo.web.queries.browser import _render_group_entry_toml
from ciudades_del_mundo.web.queries.new_countries import _new_country_config_rows
from ciudades_del_mundo.web.queries.groups import _group_entry_for_country
from ciudades_del_mundo.web.queries.subdivision_capitals import _derived_subdivision_group_block_refs, _derived_subdivision_areas_to_capital_source_ids
from ciudades_del_mundo.web.queries.subdivision_sources import _derived_subdivision_group_member_areas, _derived_subdivision_group_member_names


class CompositeCitySelectionTests(TestCase):
    def setUp(self):
        self.root = AdminArea.objects.create(id="ma", code="ma", country_code="morocco", name="Morocco", level=0)
        self.province = self.area("province", "Province", 2, self.root, 1000, "100")
        self.city = self.area("casablanca", "Casablanca", 3, self.province, 600, "60", 1)
        self.anfa = self.area("anfa", "Anfa", 4, self.city, 100, "10", 2)
        self.medina = self.area("medina", "Medina", 4, self.city, 200, "20", 2)
        self.other = self.area("other", "Other district", 4, self.city, 300, "30", 2)
        self.town = self.area("town", "Town", 3, self.province, 400, "40")
        self.derived = NuevoAdminArea.objects.create(id="new", code="new", name="New", country_code="new", level=0, municipal_level=3)

    def area(self, code, name, level, parent, population, surface, status=0):
        return AdminArea.objects.create(id=code, code=code, country_code="morocco", name=name, level=level,
                                       parent=parent, pop_latest=population, area_km2=surface, city_merge_status=status)

    def spec(self, *areas):
        result = {}
        for area in areas:
            result.setdefault(area.level, {}).setdefault(area.country_code, []).append({"id": area.id})
        return result

    def build(self, includes, subtract=()):
        spec = self.spec(*includes)
        if subtract:
            spec["restar"] = self.spec(*subtract)
        return create_nuevo_area_from_spec(parent_country_id=self.derived.id, new_name="Selection", include_spec=spec,
                                          allow_duplicate_source_data=True)

    def assert_metrics(self, area, population, surface):
        self.assertEqual(area.pop_latest, population)
        self.assertEqual(area.area_km2, Decimal(surface))

    def test_one_district(self):
        area = self.build([self.anfa])
        self.assert_metrics(area, 100, "10")
        self.assertQuerySetEqual(area.municipios_originales.all(), [self.anfa])

    def test_two_districts_and_duplicate_are_counted_once(self):
        area = self.build([self.anfa, self.medina, self.anfa])
        self.assert_metrics(area, 300, "30")

    def test_whole_city_minus_district(self):
        area = self.build([self.city], [self.medina])
        self.assert_metrics(area, 400, "40")
        self.assertSetEqual(set(area.municipios_originales.all()), {self.anfa, self.other})

    def test_province_minus_district(self):
        self.assert_metrics(self.build([self.province], [self.anfa]), 900, "90")

    def test_city_and_district_do_not_overlap(self):
        self.assert_metrics(self.build([self.city, self.anfa]), 600, "60")

    def test_selected_district_can_be_subtracted(self):
        self.assert_metrics(self.build([self.anfa, self.medina], [self.medina]), 100, "10")

    def test_all_selected_districts_can_be_removed(self):
        self.assert_metrics(self.build([self.anfa], [self.anfa]), 0, "0")

    def test_subtract_whole_city_removes_selected_districts(self):
        self.assert_metrics(self.build([self.anfa, self.medina], [self.city]), 0, "0")

    def test_unselected_district_subtraction_does_not_make_population_negative(self):
        self.assert_metrics(self.build([self.anfa], [self.medina]), 100, "10")

    def test_repeated_city_role_does_not_hide_districts(self):
        city = self.area("belgrade", "Belgrade", 1, self.root, 600, "60", 1)
        mirror = self.area("belgrade_city", "Belgrade", 3, city, 600, "60", 1)
        self.anfa.parent = city
        self.anfa.level = 2
        self.anfa.save(update_fields=["parent", "level"])
        self.medina.parent = city
        self.medina.level = 2
        self.medina.save(update_fields=["parent", "level"])
        self.other.parent = city
        self.other.level = 2
        self.other.save(update_fields=["parent", "level"])
        self.assert_metrics(self.build([city], [self.anfa]), 500, "50")
        self.assertTrue(AdminArea.objects.filter(id=mirror.id).exists())

    def test_http_selectors_expose_city_districts(self):
        for path in ("/groups/source-data/", "/new-countries/source-children/"):
            with self.subTest(path=path):
                response = self.client.get(path, {"country_code": "morocco", "parent_id": self.city.id})
                self.assertEqual(response.status_code, 200)
                self.assertSetEqual({item["id"] for item in response.json()["children"]}, {"anfa", "medina", "other"})
        response = self.client.get("/new-countries/source-children/", {"country_code": "morocco", "parent_id": self.province.id})
        self.assertTrue(next(item for item in response.json()["children"] if item["id"] == self.city.id)["has_children"])

    def test_new_country_generates_one_partial_city_and_rebuilds(self):
        country = DerivedCountry.objects.create(slug="newcountry", name="New Country", source_country_code="morocco")
        content = render_derived_country_selection_toml(country_slug=country.slug, config_slug="part", name="Part",
                                                       source_country_code="morocco", derived_country_code="newcountry", selected_ids=[self.anfa.id, self.medina.id])
        config = DerivedCountryConfig.objects.create(country=country, slug="part", name="Part", source_country_code="morocco",
                                                     derived_country_code="newcountry", content=content)
        for _ in range(2):
            result = build_derived_country_config(config)
            city = NuevoAdminArea.objects.get(country_code=result.country_code, name="Casablanca")
            self.assert_metrics(city, 300, "30")
            self.assertFalse(NuevoAdminArea.objects.filter(country_code=result.country_code, name__in=["Anfa", "Medina"]).exists())
            rows = _new_country_config_rows([config], include_assigned_subdivisions=True)
            assigned = [row for row in rows if row.get("is_assigned_subdivision")]
            self.assertEqual([row["entity_display_name"] for row in assigned], ["Casablanca"])
            self.assertEqual(assigned[0]["entity_population_value"], 300)

    def test_group_keeps_ids_and_subtractions_when_materialized(self):
        content = _render_group_entry_toml(record_slug="morocco_partial", entry_slug="partial", internal_name="PARTIAL",
            name="PARTIAL", source_country_code="morocco", blocks=[{"country_code": "morocco", "sections": [
                {"area_id": self.province.id, "level": 2, "selected": [{"id": self.city.id, "name": self.city.name}]},
                {"area_id": self.city.id, "level": 3, "selected": [{"id": self.anfa.id, "name": self.anfa.name, "operation": "subtract"}]},
            ]}])
        group = SubdivisionGroup.objects.create(slug="morocco_partial", name="PARTIAL", source_country_code="morocco", content=content)
        self.assertEqual(tomllib.loads(content)["country_groups"][0]["sections"][1]["selected_operations"], ["subtract"])
        exported = _normalized_group_bundle_seed_content("morocco", [group])
        imported = _parse_subdivision_group_seed_records(exported, path=Path("morocco.toml"))
        group.content = imported[0]["_content"]
        group.save(update_fields=["content"])
        self.assertEqual(tomllib.loads(group.content)["country_groups"][0]["sections"][1]["selected_operations"], ["subtract"])
        entry = _group_entry_for_country("morocco", "partial", hydrate=False)
        members = _derived_subdivision_group_member_areas(entry, "morocco", _derived_subdivision_group_member_names(entry, "morocco"))
        self.assertSetEqual({area.id for area in members}, {self.medina.id, self.other.id})
        refs = _derived_subdivision_group_block_refs(entry["blocks"][0])
        self.assertSetEqual(set(refs["ids"]), {self.medina.id, self.other.id})
        self.assertSetEqual(_derived_subdivision_areas_to_capital_source_ids(members, country_code="morocco"), {self.medina.id, self.other.id})
        context = _BuildContext("morocco", None, None, {}, {}, set())
        _build_node({"name": "Group area", "code": "group", "use_selected_entities_as_children": False,
                     "include": [{"country_code": "morocco", "level": 3, "groups": ["PARTIAL"]}]},
                    parent_id=self.derived.id, context=context, label="group")
        city = NuevoAdminArea.objects.get(parent__name="Group area", name="Casablanca")
        self.assert_metrics(city, 500, "50")

    def test_selected_entity_mode_groups_districts_as_one_city(self):
        context = _BuildContext("morocco", None, None, {}, {}, set())
        _build_node({"name": "Selected", "code": "selected", "use_selected_entities_as_children": True,
                     "include": [{"country_code": "morocco", "level": 4, "ids": ["anfa", "medina"]}],
                     "subtract": [{"country_code": "morocco", "level": 4, "ids": ["medina"]}]},
                    parent_id=self.derived.id, context=context, label="selected")
        city = NuevoAdminArea.objects.get(parent__name="Selected", name="Casablanca")
        self.assert_metrics(city, 100, "10")
