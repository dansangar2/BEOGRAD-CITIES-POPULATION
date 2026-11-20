"""Provider failures are exercised with doubles, never paid requests."""
from io import BytesIO
import json
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase

from ciudades_del_mundo.infrastructure.ai.json_provider import (
    OpenAICompatibleJsonProvider,
    parse_json_object,
)
from ciudades_del_mundo.ports.ai import AiProviderResponseError
from ciudades_del_mundo.services.ai_text_enrichment import AiTextEnrichmentService


class JsonProviderTests(SimpleTestCase):
    def provider(self):
        return OpenAICompatibleJsonProvider(api_key="test-key", model="test-model", timeout=7)

    def test_timeout_and_request_shape(self):
        body = {"choices": [{"message": {"content": '{"translations": {"es": "Texto"}}'}}]}
        with patch("ciudades_del_mundo.infrastructure.ai.json_provider.urlopen",
                   return_value=BytesIO(json.dumps(body).encode())) as opener:
            result = self.provider().complete_json(system_prompt="System", payload={"task": "test"})
        self.assertEqual(result["translations"]["es"], "Texto")
        self.assertEqual(opener.call_args.kwargs["timeout"], 7)
        request = opener.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data)["response_format"], {"type": "json_object"})

    def test_transport_errors_have_a_stable_boundary_exception(self):
        for error in (TimeoutError("timeout"), OSError("disconnected"), URLError("offline"),
                      HTTPError("https://example.test", 429, "limited", {}, BytesIO(b"limited"))):
            with self.subTest(error=type(error).__name__):
                with patch("ciudades_del_mundo.infrastructure.ai.json_provider.urlopen", side_effect=error):
                    with self.assertRaises(AiProviderResponseError):
                        self.provider().complete_json(system_prompt="System", payload={})

    def test_invalid_envelopes_and_encoding_are_rejected(self):
        for raw in (b"[]", b"null", b"{}", b'{"choices": []}', b"invalid", b"\xff"):
            with self.subTest(raw=raw):
                with patch("ciudades_del_mundo.infrastructure.ai.json_provider.urlopen", return_value=BytesIO(raw)):
                    with self.assertRaises(AiProviderResponseError):
                        self.provider().complete_json(system_prompt="System", payload={})

    def test_object_and_fenced_json_compatibility(self):
        self.assertEqual(parse_json_object('```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(parse_json_object({"ok": True}), {"ok": True})
        for value in (None, [], "[]", "null", "not json", 42):
            with self.subTest(value=value), self.assertRaises(AiProviderResponseError):
                parse_json_object(value)


class EnrichmentResponseTests(SimpleTestCase):
    def test_cache_reuses_only_valid_identical_requests(self):
        provider = Mock(model="test", complete_json=Mock(return_value={"translations": {"es": "Texto"}}))
        service = AiTextEnrichmentService(provider, languages=("es",))
        for _ in range(2):
            service._complete_json(system_prompt="prompt", payload={"task": "name"})
        self.assertEqual(provider.complete_json.call_count, 1)
        service._complete_json(system_prompt="changed", payload={"task": "name"})
        service._complete_json(system_prompt="prompt", payload={"task": "name"}, image_url="https://example.test/a")
        self.assertEqual(provider.complete_json.call_count, 3)

    def test_malformed_responses_are_not_cached(self):
        for response in ([], {"needs_review": "false"}, {"canonical_entity_type": ["Province"]},
                         {"translations": {"es": {"text": "bad"}}}, {"singular": ["bad"]}):
            with self.subTest(response=response):
                provider = Mock(model="test", complete_json=Mock(return_value=response))
                service = AiTextEnrichmentService(provider)
                for _ in range(2):
                    with self.assertRaises(AiProviderResponseError):
                        service._complete_json(system_prompt="prompt", payload={})
                self.assertEqual(provider.complete_json.call_count, 2)

    def test_failed_request_can_be_retried(self):
        provider = Mock(model="test", complete_json=Mock(side_effect=[
            AiProviderResponseError("offline"), {"translations": {"es": "Texto"}},
        ]))
        service = AiTextEnrichmentService(provider)
        with self.assertRaises(AiProviderResponseError):
            service._complete_json(system_prompt="prompt", payload={})
        self.assertEqual(service._complete_json(system_prompt="prompt", payload={})["translations"]["es"], "Texto")
        self.assertEqual(provider.complete_json.call_count, 2)

    def test_cache_is_bounded(self):
        provider = Mock(model="test", complete_json=Mock(return_value={}))
        service = AiTextEnrichmentService(provider)
        for number in range(140):
            service._complete_json(system_prompt="prompt", payload={"number": number})
        self.assertEqual(len(service._response_cache), 128)
