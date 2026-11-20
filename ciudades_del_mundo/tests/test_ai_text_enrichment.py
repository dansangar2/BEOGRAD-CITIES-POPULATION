from django.db import connection
from django.test import TestCase
from django.utils import timezone, translation

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig
from ciudades_del_mundo.models import AdminArea, DynamicTranslation, EntityTypeInference
from ciudades_del_mundo.services.ai_text_enrichment import (
    AiTextEnrichmentService,
    apply_stored_entity_type_inferences,
)
from ciudades_del_mundo.services.dynamic_translations import clear_dynamic_translation_cache, dynamic_entity_type_label
from unittest.mock import Mock, patch
from ciudades_del_mundo.ports.ai import AiProviderResponseError


class FakeAiProvider:
    model = "fake-ai"

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        task = payload.get("task")
        if task == "complete_scraped_entity_type":
            return {
                "canonical_entity_type": "Province",
                "source_language": "en",
                "confidence": "high",
                "needs_review": False,
                "reason": "The sample level matches provinces.",
                "singular": {"es": "Provincia", "en": "Province"},
                "plural": {"es": "Provincias", "en": "Provinces"},
            }
        if task == "describe_visual_identity":
            return {
                "needs_review": False,
                "descriptions": {"es": "Bandera descrita por IA"},
                "blazons": {"es": "Blason descrito por IA"},
            }
        return {
            "needs_review": False,
            "translations": {"es": "Texto traducido"},
            "singular": {"es": "Provincia"},
            "plural": {"es": "Provincias"},
        }


class AiTextEnrichmentTests(TestCase):
    def setUp(self):
        clear_dynamic_translation_cache()

    def tearDown(self):
        clear_dynamic_translation_cache()

    def test_existing_entity_types_load_parents_in_one_query(self):
        root = AdminArea.objects.create(id="aa_aa", code="aa", country_code="aa", name="AA",
                                        level=0, entity_type="Country", raw_entity_type="Country")
        for number in range(5):
            AdminArea.objects.create(id=f"aa_{number}", code=str(number), country_code="aa", name=str(number),
                                     level=1, parent=root, entity_type="Province", raw_entity_type="Province")
        service = AiTextEnrichmentService(FakeAiProvider())
        with patch.object(service, "normalize_scraped_entities", side_effect=lambda config, entities: entities):
            with self.assertNumQueries(1):
                stats = service.enrich_existing_admin_area_entity_types(country_code="aa")
        self.assertEqual(stats.entity_types_applied, 0)

    def test_invalid_provider_response_writes_no_translation(self):
        service = AiTextEnrichmentService(Mock(model="fake", complete_json=Mock(return_value={
            "needs_review": "false", "translations": {"es": "bad"},
        })), languages=("es",))
        with self.assertRaises(AiProviderResponseError):
            service._translate_name(subject_type="country", subject_key="aa", country_code="aa", source_text="AA")
        self.assertEqual(DynamicTranslation.objects.count(), 0)

    def test_sql_translations_skip_provider_on_a_new_service_run(self):
        first = AiTextEnrichmentService(FakeAiProvider(), languages=("es",))
        first._translate_name(subject_type="country", subject_key="aa", country_code="aa", source_text="AA")
        provider = Mock(model="fake")
        second = AiTextEnrichmentService(provider, languages=("es",))
        self.assertEqual(second._translate_name(subject_type="country", subject_key="aa", country_code="aa", source_text="AA"), 0)
        provider.complete_json.assert_not_called()

    def test_filling_plural_keeps_an_existing_singular_translation(self):
        existing = DynamicTranslation.objects.create(
            subject_type="entity_type", subject_key="Province", country_code="aa", field="singular",
            language="es", text="Manual", needs_review=False, is_active=True,
        )
        service = AiTextEnrichmentService(FakeAiProvider(), languages=("es",))
        self.assertEqual(service._translate_entity_type("Province", country_code="aa"), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.text, "Manual")
        self.assertEqual(DynamicTranslation.objects.get(field="plural").text, "Provincias")

    def test_infers_incomplete_entity_type_and_stores_dynamic_labels(self):
        service = AiTextEnrichmentService(FakeAiProvider(), languages=("es", "en"))
        entities = [
            ScrapedAdminArea(code="aa", name="AA Country", level=0, country_code="aa", entity_type="Country"),
            ScrapedAdminArea(
                code="01",
                name="Sample Province",
                level=1,
                country_code="aa",
                entity_type="Prov",
                parent_code="aa",
            ),
        ]
        config = ScrapingJobConfig(slug="aa", country_code="aa", base_url="", name="AA Country")

        normalized = service.normalize_scraped_entities(config, entities)

        self.assertEqual(normalized[1].entity_type, "Province")
        self.assertEqual(normalized[1].raw_entity_type, "Prov")
        inference = EntityTypeInference.objects.get(country_code="aa", raw_entity_type="Prov")
        self.assertFalse(inference.needs_review)
        self.assertEqual(inference.canonical_entity_type, "Province")
        self.assertEqual(dynamic_entity_type_label("Province", "es", country_code="aa"), "Provincia")
        self.assertTrue(
            DynamicTranslation.objects.filter(
                subject_type="entity_type",
                subject_key="Province",
                country_code="aa",
                field="plural",
                language="es",
                text="Provincias",
            ).exists()
        )

    def test_applies_stored_reviewed_entity_type_inference_without_provider(self):
        EntityTypeInference.objects.create(
            country_code="aa",
            raw_entity_type="Prov",
            level=1,
            context_key="",
            canonical_entity_type="Province",
            needs_review=False,
            is_active=True,
        )
        entities = [
            ScrapedAdminArea(code="01", name="Sample", level=1, country_code="aa", entity_type="Prov")
        ]

        normalized = apply_stored_entity_type_inferences("aa", entities)

        self.assertEqual(normalized[0].entity_type, "Province")
        self.assertEqual(normalized[0].raw_entity_type, "Prov")

    def test_web_payload_prefers_dynamic_translations(self):
        root = AdminArea.objects.create(
            id="aa_root",
            country_code="aa",
            code="root",
            name="AA Country",
            level=0,
            entity_type="Country",
            pop_latest=100,
        )
        AdminArea.objects.create(
            id="aa_child",
            country_code="aa",
            code="child",
            name="Raw Child",
            level=1,
            entity_type="Province",
            parent=root,
            pop_latest=50,
        )
        DynamicTranslation.objects.create(
            subject_type="country",
            subject_key="aa",
            country_code="aa",
            field="name",
            language="es",
            source_text="AA Country",
            text="Pais dinamico",
            needs_review=False,
            is_active=True,
        )
        DynamicTranslation.objects.create(
            subject_type="admin_area",
            subject_key="aa_child",
            country_code="aa",
            field="name",
            language="es",
            source_text="Raw Child",
            text="Provincia dinamica",
            needs_review=False,
            is_active=True,
        )
        DynamicTranslation.objects.create(
            subject_type="entity_type",
            subject_key="Province",
            country_code="aa",
            field="singular",
            language="es",
            source_text="Province",
            text="Tipo dinamico",
            needs_review=False,
            is_active=True,
        )
        clear_dynamic_translation_cache()

        with translation.override("es"):
            response = self.client.get("/api/admin-areas/aa_child/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["area"]
        self.assertEqual(payload["name"], "Provincia dinamica")
        self.assertEqual(payload["parent"], "Pais dinamico")
        self.assertEqual(payload["entity_type"], "Tipo dinamico")

    def test_generates_visual_asset_descriptions(self):
        now = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ciudades_del_mundo_visual_asset
                    (entity_type, entity_key, entity_name, country_code, kind,
                     wikidata_id, commons_filename, remote_url, local_path,
                     local_exists, source, status, error, license_name, author,
                     attribution, source_url, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, '', %s, %s, '', 0, 'test', 'found', '', '', '', '', '', %s, %s)
                """,
                [
                    "admin_area",
                    "aa_child",
                    "Raw Child",
                    "aa",
                    "coat",
                    "Coat of Raw Child.svg",
                    "https://example.test/coat.svg",
                    now,
                    now,
                ],
            )
            asset_id = cursor.lastrowid

        service = AiTextEnrichmentService(FakeAiProvider(), languages=("es",))
        stats = service.describe_missing_visual_assets(country_code="aa", limit=10)

        self.assertEqual(stats.visual_descriptions, 1)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT description, blazon, source, needs_review
                  FROM ciudades_del_mundo_visual_asset_translation
                 WHERE asset_id=%s AND language='es'
                """,
                [asset_id],
            )
            row = cursor.fetchone()
        self.assertEqual(row[0], "Bandera descrita por IA")
        self.assertEqual(row[1], "Blason descrito por IA")
        self.assertEqual(row[2], "ai:visual-description")
        self.assertFalse(bool(row[3]))
