"""JSON completion boundary; providers live in infrastructure."""
from typing import Protocol


class AiTextProvider(Protocol):
    model: str

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        """Return structured text without mutating application data."""


class AiProviderConfigurationError(RuntimeError):
    """The requested provider is not configured."""


class AiProviderResponseError(RuntimeError):
    """The provider failed or returned invalid data."""
