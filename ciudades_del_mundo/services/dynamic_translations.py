"""Runtime translations for dynamic geography labels stored in SQL."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json

from django.db import OperationalError, ProgrammingError

from ciudades_del_mundo.models import DynamicTranslation


SUBJECT_COUNTRY = "country"
SUBJECT_ADMIN_AREA = "admin_area"
SUBJECT_NUEVO_ADMIN_AREA = "nuevo_admin_area"
SUBJECT_ENTITY_TYPE = "entity_type"

FIELD_NAME = "name"
FIELD_SINGULAR = "singular"
FIELD_PLURAL = "plural"


def normalize_dynamic_language_code(language_code: str | None) -> str:
    value = str(language_code or "").strip().lower().replace("_", "-")
    if value.startswith("sr-latn"):
        return "sr-latn"
    return value.split("-", 1)[0] if value else ""


def dynamic_country_name(country_code: str, language_code: str | None) -> str:
    return dynamic_translation(
        SUBJECT_COUNTRY,
        str(country_code or "").strip().lower(),
        FIELD_NAME,
        language_code,
        country_code=str(country_code or "").strip().lower(),
    )


def dynamic_area_name(area, language_code: str | None) -> str:
    if not area:
        return ""
    subject_type = SUBJECT_NUEVO_ADMIN_AREA if area.__class__.__name__ == "NuevoAdminArea" else SUBJECT_ADMIN_AREA
    return dynamic_translation(
        subject_type,
        str(getattr(area, "id", "") or ""),
        FIELD_NAME,
        language_code,
        country_code=str(getattr(area, "country_code", "") or ""),
    )


def dynamic_entity_type_label(
    entity_type: str | None,
    language_code: str | None,
    *,
    country_code: str | None = None,
    plural: bool = False,
) -> str:
    value = str(entity_type or "").strip()
    if not value:
        return ""
    field = FIELD_PLURAL if plural else FIELD_SINGULAR
    country = str(country_code or "").strip().lower()
    return dynamic_translation(
        SUBJECT_ENTITY_TYPE,
        value,
        field,
        language_code,
        country_code=country,
    ) or dynamic_translation(
        SUBJECT_ENTITY_TYPE,
        value,
        field,
        language_code,
        country_code="",
    )


def dynamic_translation(
    subject_type: str,
    subject_key: str,
    field: str,
    language_code: str | None,
    *,
    country_code: str = "",
) -> str:
    subject_type = str(subject_type or "").strip()
    subject_key = str(subject_key or "").strip()
    field = str(field or "").strip()
    country_code = str(country_code or "").strip().lower()
    if not subject_type or not subject_key or not field:
        return ""
    for language in _language_candidates(language_code):
        text = _cached_dynamic_translation(subject_type, subject_key, field, language, country_code)
        if text:
            return text
    return ""


def upsert_dynamic_translation(
    *,
    subject_type: str,
    subject_key: str,
    field: str,
    language: str,
    text: str,
    country_code: str = "",
    source_text: str = "",
    source_language: str = "",
    source: str = "",
    model: str = "",
    prompt_version: str = "",
    input_payload: dict | None = None,
    needs_review: bool = True,
    is_active: bool = True,
) -> DynamicTranslation:
    input_hash = _input_hash(input_payload) if input_payload else ""
    obj, _created = DynamicTranslation.objects.update_or_create(
        subject_type=str(subject_type or "").strip(),
        subject_key=str(subject_key or "").strip(),
        country_code=str(country_code or "").strip().lower(),
        field=str(field or "").strip(),
        language=normalize_dynamic_language_code(language),
        defaults={
            "source_text": str(source_text or ""),
            "source_language": normalize_dynamic_language_code(source_language),
            "text": str(text or "").strip(),
            "source": str(source or ""),
            "model": str(model or ""),
            "prompt_version": str(prompt_version or ""),
            "input_hash": input_hash,
            "needs_review": bool(needs_review),
            "is_active": bool(is_active),
        },
    )
    clear_dynamic_translation_cache()
    return obj


def missing_translation_languages(
    *,
    subject_type: str,
    subject_key: str,
    field: str,
    languages: tuple[str, ...],
    country_code: str = "",
) -> tuple[str, ...]:
    missing = []
    for language in languages:
        if not dynamic_translation(
            subject_type,
            subject_key,
            field,
            language,
            country_code=country_code,
        ):
            missing.append(normalize_dynamic_language_code(language))
    return tuple(language for language in missing if language)


def clear_dynamic_translation_cache() -> None:
    _cached_dynamic_translation.cache_clear()


@lru_cache(maxsize=8192)
def _cached_dynamic_translation(
    subject_type: str,
    subject_key: str,
    field: str,
    language: str,
    country_code: str,
) -> str:
    try:
        row = (
            DynamicTranslation.objects.filter(
                subject_type=subject_type,
                subject_key=subject_key,
                field=field,
                language=language,
                country_code=country_code,
                is_active=True,
                needs_review=False,
            )
            .only("text")
            .first()
        )
    except (OperationalError, ProgrammingError):
        return ""
    return str(row.text or "") if row else ""


def _language_candidates(language_code: str | None) -> tuple[str, ...]:
    language = normalize_dynamic_language_code(language_code)
    if not language:
        return ()
    candidates = [language]
    base = language.split("-", 1)[0]
    if base and base not in candidates:
        candidates.append(base)
    return tuple(candidates)


def _input_hash(payload: dict | None) -> str:
    serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
