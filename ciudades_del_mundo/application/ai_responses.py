"""Validate enrichment output before it reaches persistence adapters."""
from ciudades_del_mundo.ports.ai import AiProviderResponseError


def validate_enrichment_response(response: object) -> dict:
    """Allow partial answers, but never coerce containers into stored text."""
    if not isinstance(response, dict):
        raise AiProviderResponseError("AI enrichment response must be an object.")
    if "needs_review" in response and not isinstance(response["needs_review"], bool):
        raise AiProviderResponseError("AI enrichment needs_review must be a boolean.")
    for field in ("canonical_entity_type", "entity_type", "source_language", "confidence", "reason"):
        if field in response and not isinstance(response[field], str):
            raise AiProviderResponseError(f"AI enrichment {field} must be text.")
    for field in ("translations", "singular", "plural", "descriptions", "blazons"):
        values = response.get(field)
        if values is None:
            continue
        if not isinstance(values, dict) or any(
            not isinstance(language, str) or not isinstance(text, str)
            for language, text in values.items()
        ):
            raise AiProviderResponseError(f"AI enrichment {field} must map languages to text.")
    return response
