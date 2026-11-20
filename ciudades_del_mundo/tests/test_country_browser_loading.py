"""Country navigation fetches the opened branch without eager descendants."""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.utils.translation import override

from ciudades_del_mundo.models import AdminArea, DynamicTranslation, VisualAsset, VisualAssetTranslation
from ciudades_del_mundo.services.dynamic_translations import dynamic_country_name, dynamic_country_names
from ciudades_del_mundo.services.visual_assets import get_stored_visual_assets_for_entities, get_visual_assets_for_entity
from ciudades_del_mundo.web.queries import browser


class CountryBrowserLoadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = AdminArea.objects.create(
            id="lazy_root", country_code="lazy", code="lazy", name="Country",
            level=0, pop_latest=1000, area_km2=100,
        )
        cls.region = AdminArea.objects.create(
            id="lazy_region", country_code="lazy", code="region", name="Region",
            level=1, parent=cls.root, pop_latest=1000, area_km2=100,
        )
        cls.locality = AdminArea.objects.create(
            id="lazy_locality", country_code="lazy", code="locality", name="Locality",
            level=2, parent=cls.region, pop_latest=1000, area_km2=100,
        )
        AdminArea.objects.create(
            id="lazy_place", country_code="lazy", code="place", name="Place",
            level=3, parent=cls.locality, pop_latest=1000, area_km2=100,
        )
        AdminArea.objects.create(
            id="unrelated_root", country_code="unrelated", code="unrelated", name="Other country",
            level=0, pop_latest=2000,
        )

    def test_countries_shell_does_not_wait_for_unused_external_libraries(self):
        response = self.client.get("/countries/")
        self.assertContains(response, "ciudades_del_mundo/app.js")
        for host in ("code.jquery.com", "cdn.jsdelivr.net", "unpkg.com"):
            self.assertNotContains(response, host)

    def test_country_request_omits_descendants_and_only_summarizes_selected_country(self):
        with patch.object(browser, "_second_order_share_rows", side_effect=AssertionError("eager descendants")):
            with patch.object(browser, "_canonical_country_area", wraps=browser._canonical_country_area) as roots:
                response = self.client.get("/api/countries/lazy/?include_children=0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["country"]["population"], 1000)
        self.assertEqual([row["id"] for row in data["first_order"]["cards"]], [self.region.id])
        self.assertEqual(data["first_order"]["cards"][0]["children"], [])
        self.assertEqual(data["first_order"]["cards"][0]["child_count"], 1)
        self.assertTrue(roots.call_args_list)
        self.assertEqual({call.args[0] for call in roots.call_args_list}, {"lazy"})

    def test_clicking_each_area_returns_only_its_direct_children(self):
        region_data = self.client.get(f"/api/admin-areas/{self.region.id}/").json()
        self.assertEqual([row["id"] for row in region_data["children"]], [self.locality.id])
        self.assertEqual(region_data["children"][0]["child_count"], 1)
        locality_data = self.client.get(f"/api/admin-areas/{self.locality.id}/").json()
        self.assertEqual([row["id"] for row in locality_data["children"]], ["lazy_place"])

    def test_level_labels_prioritize_common_types_and_branch_labels_describe_actual_children(self):
        AdminArea.objects.filter(pk='lazy_place').delete()
        rows = []
        for entity_type, count in [('Air Force Base', 1), ('Airport', 1), ('Area', 1),
                                   ('Quarter', 4), ('Village', 5)]:
            for index in range(count):
                code = f'{entity_type}_{index}'
                rows.append(AdminArea(
                    id=f'lazy_{code}', country_code='lazy', code=code, name=code,
                    level=3, parent=self.locality if entity_type == 'Quarter' else self.region,
                    entity_type=entity_type,
                ))
        AdminArea.objects.bulk_create(rows)
        with override('en'):
            country = self.client.get('/api/countries/lazy/?include_children=0').json()
            option = next(row for row in country['levels'] if row['value'] == 3)
            self.assertEqual(option['label'].split(' / ')[:2], ['Village', 'Quarter'])
            branch = self.client.get(f'/api/admin-areas/{self.locality.id}/').json()
            self.assertTrue(branch['child_groups'][0]['label'].endswith(' · Quarter'))
            self.assertEqual(len(branch['children']), 4)
            region = self.client.get(f'/api/admin-areas/{self.region.id}/').json()
            level_three = next(group for group in region['child_groups'] if group['level'] == 3)
            self.assertNotIn('Quarter', level_three['label'])
            self.assertIn('Village', level_three['label'])

    def test_level_one_returns_all_rows_even_with_stale_pagination_parameters(self):
        AdminArea.objects.bulk_create([
            AdminArea(
                id=f"lazy_region_{i}", country_code="lazy", code=f"region_{i}",
                name=f"Region {i}", level=1, parent=self.root,
            )
            for i in range(101)
        ])
        with patch.object(browser, "_second_order_share_rows", side_effect=AssertionError("eager descendants")):
            response = self.client.get("/api/countries/lazy/?include_children=0&page_size=1&page=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["table"]["rows"]), 102)
        self.assertEqual(len(data["first_order"]["cards"]), 102)
        for section in ("table", "first_order"):
            pagination = data[section]["pagination"]
            self.assertEqual(pagination["page"], 1)
            self.assertEqual(pagination["total"], 102)
            self.assertEqual(pagination["num_pages"], 1)
            self.assertFalse(pagination["has_next"])
            self.assertFalse(pagination["has_previous"])

    def test_deeper_level_keeps_requested_pagination(self):
        AdminArea.objects.bulk_create([
            AdminArea(
                id=f"lazy_locality_{i}", country_code="lazy", code=f"locality_{i}",
                name=f"Locality {i}", level=2, parent=self.region,
            )
            for i in range(10)
        ])
        response = self.client.get("/api/countries/lazy/?level=2&include_children=0&page_size=10&page=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([row["id"] for row in data["table"]["rows"]], ["lazy_locality_9"])
        self.assertEqual(data["table"]["pagination"]["num_pages"], 2)

    def test_first_visible_level_returns_all_rows_when_internal_level_one_is_absent(self):
        AdminArea.objects.filter(pk=self.region.pk).update(level=2)
        AdminArea.objects.filter(pk=self.locality.pk).update(level=3)
        AdminArea.objects.filter(pk="lazy_place").update(level=4)
        AdminArea.objects.bulk_create([
            AdminArea(
                id=f"lazy_region_{i}", country_code="lazy", code=f"region_{i}",
                name=f"Region {i}", level=2, parent=self.root,
            )
            for i in range(101)
        ])
        response = self.client.get("/api/countries/lazy/?include_children=0&page_size=20&page=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["selected_level"], 2)
        self.assertEqual(len(data["first_order"]["child_groups"][0]["children"]), 102)
        self.assertEqual(data["first_order"]["pagination"]["num_pages"], 1)
        self.assertEqual(data["first_order"]["pagination"]["page"], 1)

    def test_default_country_contract_preserves_dashboard_share_details(self):
        data = self.client.get("/api/countries/lazy/").json()
        self.assertEqual(data["first_order"]["cards"][0]["children"][0]["name"], "Locality")

    def test_child_counts_are_computed_once_per_visible_country_row(self):
        with patch.object(browser, "_admin_area_browser_child_count", wraps=browser._admin_area_browser_child_count) as counts:
            response = self.client.get("/api/countries/lazy/?include_children=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[0].id for call in counts.call_args_list], [self.region.id])

    def test_count_metadata_preserves_same_name_collapse_without_loading_population(self):
        AdminArea.objects.create(
            id="lazy_flat", country_code="lazy", code="flat", name="Locality",
            level=3, parent=self.region,
        )
        with patch.object(browser, "_collapse_same_name_flat_children", wraps=browser._collapse_same_name_flat_children) as collapse:
            count = browser._admin_area_browser_child_count(self.region)
        self.assertEqual(count, 1)
        rows = collapse.call_args.args[0]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue({"pop_latest", "area_km2", "entity_type"}.issubset(row.get_deferred_fields()))

    def test_summary_query_count_stays_constant_when_more_countries_are_added(self):
        VisualAsset.objects.create(
            entity_type="country", entity_key="lazy", kind="flag", remote_url="https://example.test/flag.svg",
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        for additional in (0, 30):
            if additional:
                AdminArea.objects.bulk_create([
                    AdminArea(id=f"summary_{i}", country_code=f"summary{i}", code=f"summary{i}",
                              name=f"Country {i}", level=0, pop_latest=100)
                    for i in range(additional)
                ])
            with self.subTest(additional=additional), self.assertNumQueries(5):
                data = browser._country_summary_payload(
                    detail_route="ciudades_del_mundo:api_country_detail", include_visual_assets=True,
                )
            self.assertEqual(len(data["countries"]), 2 + additional)
            self.assertTrue(all("children" not in country for country in data["countries"]))

    def test_summary_ignores_rootless_countries_and_hidden_roots(self):
        AdminArea.objects.bulk_create([
            AdminArea(id="rootless_area", country_code="rootless", code="area",
                      name="Area without national root", level=1, pop_latest=9999),
            AdminArea(id="hidden_root", country_code="hidden", code="hidden",
                      name="Hidden root", level=0, pop_latest=9999, city_merge_status=3),
        ])
        with self.assertNumQueries(2):
            records = browser._admin_country_summary_records()
        self.assertEqual({row["code"] for row in records}, {"lazy", "unrelated"})

    def test_batched_roots_preserve_canonical_priority_and_synthetic_totals(self):
        for country, code, name, population, annotations, qid in (
            ("by_code", "other", "A", 700, "CityPopulation section: infosection", ""),
            ("by_code", "by_code", "Z", 100, "", ""),
            ("by_section", "other", "A", 700, "", ""),
            ("by_section", "section", "Z", 200, "CityPopulation section: infosection", ""),
            ("france", "other", "A", 700, "", ""),
            ("france", "FR", "Z", 300, "", "Q142"),
            ("ambiguous", "one", "A", 400, "", ""),
            ("ambiguous", "two", "Z", 500, "", ""),
        ):
            AdminArea.objects.create(
                id=f"{country}_{code}", country_code=country, code=code, name=name, level=0,
                pop_latest=population, area_km2=10, annotations=annotations, data_wd=qid,
            )
        rows = browser._admin_country_summary_records()
        for row in rows:
            root = browser._canonical_country_area(row["code"])
            self.assertEqual(row["root_id"], root.id if root else "")
            self.assertEqual(row["population"], browser._country_total(row["code"], "pop_latest", root))
        self.assertEqual(next(row for row in rows if row["code"] == "ambiguous")["population"], 900)

    def test_batched_assets_match_single_entity_contract_including_translations(self):
        asset = VisualAsset.objects.create(
            entity_type="country", entity_key="lazy", kind="flag", remote_url="https://example.test/flag.svg",
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        VisualAssetTranslation.objects.create(
            asset=asset, language="es", title="Bandera", description="Stored description",
            created_at=timezone.now(), updated_at=timezone.now(),
        )
        keys = ["lazy", "unrelated", "missing"]
        expected = {key: get_visual_assets_for_entity("country", key, include_fallbacks=False) for key in keys}
        with self.assertNumQueries(3):
            actual = get_stored_visual_assets_for_entities("country", keys)
        self.assertEqual(actual, expected)

    def test_batched_names_preserve_review_language_and_country_scope(self):
        for language, text, reviewed, scope in (
            ("sr", "Base name", True, "lazy"), ("sr-latn", "Preferred name", True, "lazy"),
            ("fr", "Unreviewed name", False, "lazy"), ("es", "Wrong scope", True, "unrelated"),
        ):
            DynamicTranslation.objects.create(
                subject_type="country", subject_key="lazy", country_code=scope, field="name",
                language=language, text=text, needs_review=not reviewed,
            )
        for language in ("sr-latn", "fr", "es"):
            self.assertEqual(dynamic_country_names(["lazy"], language)["lazy"], dynamic_country_name("lazy", language))
        with override("sr-latn"):
            self.assertEqual(browser._admin_country_summary_records("lazy")[0]["label"], "Preferred name")
