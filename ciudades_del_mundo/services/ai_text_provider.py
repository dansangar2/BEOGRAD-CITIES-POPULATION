"""JSON-oriented AI provider used by dynamic geography enrichment."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AiTextProvider(Protocol):
    model: str

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        """Return a JSON object generated from a structured prompt."""


class AiProviderConfigurationError(RuntimeError):
    """Raised when AI enrichment is requested without provider settings."""


class AiProviderResponseError(RuntimeError):
    """Raised when the provider returns malformed data."""


@dataclass
class OpenAICompatibleJsonProvider:
    """Small OpenAI-compatible chat-completions client.

    Required environment:
    - CIUDADES_AI_API_KEY
    - CIUDADES_AI_MODEL

    Optional environment:
    - CIUDADES_AI_BASE_URL, defaults to https://api.openai.com/v1
    - CIUDADES_AI_TIMEOUT, defaults to 60
    """

    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    timeout: int = 60

    @classmethod
    def from_env(cls) -> "OpenAICompatibleJsonProvider":
        api_key = os.environ.get("CIUDADES_AI_API_KEY", "").strip()
        model = os.environ.get("CIUDADES_AI_MODEL", "").strip()
        if not api_key:
            raise AiProviderConfigurationError("CIUDADES_AI_API_KEY is required for AI enrichment.")
        if not model:
            raise AiProviderConfigurationError("CIUDADES_AI_MODEL is required for AI enrichment.")
        raw_timeout = os.environ.get("CIUDADES_AI_TIMEOUT", "60").strip()
        try:
            timeout = max(5, int(raw_timeout))
        except ValueError:
            timeout = 60
        return cls(
            api_key=api_key,
            model=model,
            base_url=os.environ.get("CIUDADES_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            timeout=timeout,
        )

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        user_content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if image_url:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_content},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": user_content})

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - explicit user-configured endpoint.
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise AiProviderResponseError(f"AI provider HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise AiProviderResponseError(f"AI provider request failed: {exc}") from exc

        try:
            response_payload = json.loads(raw)
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AiProviderResponseError("AI provider returned an unexpected response shape.") from exc
        return parse_json_object(content)


def parse_json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        raise AiProviderResponseError("AI provider returned empty JSON content.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiProviderResponseError("AI provider returned invalid JSON content.") from exc
    if not isinstance(parsed, dict):
        raise AiProviderResponseError("AI provider JSON content must be an object.")
    return parsed
