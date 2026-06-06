"""AI-assisted enrichment for scraped dynamic geography text."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
import re

from django.db import OperationalError, ProgrammingError, connection

from ciudades_del_mundo.domain import ScrapedAdminArea, ScrapingJobConfig
from ciudades_del_mundo.models import AdminArea, EntityTypeInference
from ciudades_del_mundo.services.ai_text_provider import AiTextProvider
from ciudades_del_mundo.services.dynamic_translations import (
    FIELD_NAME,
    FIELD_PLURAL,
    FIELD_SINGULAR,
    SUBJECT_ADMIN_AREA,
    SUBJECT_COUNTRY,
    SUBJECT_ENTITY_TYPE,
    missing_translation_languages,
    normalize_dynamic_language_code,
    upsert_dynamic_translation,
)
from ciudades_del_mundo.services.visual_assets import (
    VISUAL_ASSET_TABLE,
    VISUAL_TRANSLATION_TABLE,
    upsert_visual_asset_translation,
    visual_asset_tables_exist,
)


DEFAULT_AI_LANGUAGES = ("es", "en", "fr", "de", "it", "ru", "sr", "sr-latn", "ar")
PROMPT_VERSION = "dynamic-geography-ai-v1"
AI_SOURCE_ENTITY_TYPE = "ai:entity-type"
AI_SOURCE_TRANSLATION = "ai:translation"
AI_SOURCE_VISUAL = "ai:visual-description"


@dataclass(frozen=True)
class AiEnrichmentStats:
    entity_type_inferences: int = 0
    entity_types_applied: int = 0
    dynamic_translations: int = 0
    visual_descriptions: int = 0

    def __add__(self, other: "AiEnrichmentStats") -> "AiEnrichmentStats":
        return AiEnrichmentStats(
            entity_type_inferences=self.entity_type_inferences + other.entity_type_inferences,
            entity_types_applied=self.entity_types_applied + other.entity_types_applied,
            dynamic_translations=self.dynamic_translations + other.dynamic_translations,
            visual_descriptions=self.visual_descriptions + other.visual_descriptions,
        )

    def as_log_line(self, prefix: str = "[ai]") -> str:
        return (
            f"{prefix} entity_type_inferences={self.entity_type_inferences}, "
            f"entity_types_applied={self.entity_types_applied}, "
            f"dynamic_translations={self.dynamic_translations}, "
            f"visual_descriptions={self.visual_descriptions}"
        )


class AiTextEnrichmentService:
    """Use an AI provider to fill dynamic geography text in SQL."""

    def __init__(
        self,
        provider: AiTextProvider,
        *,
        languages: tuple[str, ...] = DEFAULT_AI_LANGUAGES,
    ) -> None:
        self.provider = provider
        self.languages = tuple(
            language for language in (normalize_dynamic_language_code(item) for item in languages) if language
        )

    def normalize_scraped_entities(
        self,
        config: ScrapingJobConfig,
        entities: list[ScrapedAdminArea],
    ) -> list[ScrapedAdminArea]:
        normalized = apply_stored_entity_type_inferences(config.country_code, entities)
        groups = _incomplete_entity_type_groups(config, normalized)
        if not groups:
            return normalized

        replacements: dict[tuple[str, int], str] = {}
        for group in groups:
            result = self._infer_entity_type(group)
            if not result:
                continue
            canonical = result["canonical_entity_type"]
            inference = self._store_entity_type_inference(group, result)
            self._store_entity_type_translations(group, canonical, result)
            if inference.is_active and not inference.needs_review:
                replacements[(group["raw_entity_type"], int(group["level"]))] = canonical

        if not replacements:
            return normalized
        return [
            _replace_entity_type(entity, replacements.get((_raw_entity_type(entity), int(entity.level))))
            for entity in normalized
        ]

    def enrich_existing_admin_area_entity_types(
        self,
        *,
        country_code: str,
        limit: int | None = None,
    ) -> AiEnrichmentStats:
        query = AdminArea.objects.filter(country_code=country_code).exclude(entity_type__isnull=True).exclude(entity_type="")
        if limit:
            query = query.order_by("level", "name")[:limit]
        areas = list(query)
        entities = [
            ScrapedAdminArea(
                code=area.code,
                name=area.name,
                level=area.level,
                country_code=area.country_code,
                entity_type=area.entity_type,
                raw_entity_type=area.raw_entity_type or area.entity_type,
                parent_code=area.parent.code if area.parent_id and area.parent else None,
                area_km2=area.area_km2,
                density=area.density,
                pop_latest=area.pop_latest,
                pop_latest_date=area.pop_latest_date,
                last_census_year=area.last_census_year,
                url=area.url,
                city_merge_status=area.city_merge_status,
            )
            for area in areas
        ]
        config = ScrapingJobConfig(slug=country_code, country_code=country_code, base_url="", pages=[])
        normalized = self.normalize_scraped_entities(config, entities)
        applied = 0
        by_id = {entity.id: entity for entity in normalized}
        for area in areas:
            entity = by_id.get(area.id)
            if not entity:
                continue
            raw = entity.raw_entity_type or area.raw_entity_type or area.entity_type or ""
            if entity.entity_type != area.entity_type or raw != area.raw_entity_type:
                AdminArea.objects.filter(pk=area.pk).update(
                    entity_type=entity.entity_type,
                    raw_entity_type=raw,
                )
                applied += 1
        return AiEnrichmentStats(entity_types_applied=applied)

    def translate_dynamic_texts(
        self,
        *,
        country_code: str,
        include_country: bool = True,
        include_admin_area_names: bool = False,
        include_entity_types: bool = True,
        limit: int = 100,
    ) -> AiEnrichmentStats:
        written = 0
        if include_country:
            root = AdminArea.objects.filter(country_code=country_code, level=0).order_by("id").first()
            source_name = root.name if root else country_code
            written += self._translate_name(
                subject_type=SUBJECT_COUNTRY,
                subject_key=country_code,
                country_code=country_code,
                source_text=source_name,
            )

        if include_entity_types:
            entity_types = (
                AdminArea.objects.filter(country_code=country_code)
                .exclude(entity_type__isnull=True)
                .exclude(entity_type="")
                .values_list("entity_type", flat=True)
                .distinct()
                .order_by("entity_type")
            )
            for entity_type in entity_types[:limit]:
                written += self._translate_entity_type(str(entity_type), country_code=country_code)

        if include_admin_area_names:
            areas = (
                AdminArea.objects.filter(country_code=country_code)
                .exclude(name="")
                .only("id", "name", "country_code")
                .order_by("level", "name", "id")[:limit]
            )
            for area in areas:
                written += self._translate_name(
                    subject_type=SUBJECT_ADMIN_AREA,
                    subject_key=area.id,
                    country_code=area.country_code,
                    source_text=area.name,
                )

        return AiEnrichmentStats(dynamic_translations=written)

    def describe_missing_visual_assets(
        self,
        *,
        country_code: str = "",
        limit: int = 50,
    ) -> AiEnrichmentStats:
        if not visual_asset_tables_exist():
            return AiEnrichmentStats()
        rows = _visual_assets_for_description(country_code=country_code, limit=limit)
        written = 0
        for row in rows:
            missing = _missing_visual_languages(int(row["id"]), str(row["kind"] or ""), self.languages)
            if not missing:
                continue
            payload = {
                "task": "describe_visual_identity",
                "kind": row.get("kind") or "",
                "entity_type": row.get("entity_type") or "",
                "entity_key": row.get("entity_key") or "",
                "entity_name": row.get("entity_name") or "",
                "country_code": row.get("country_code") or "",
                "commons_filename": row.get("commons_filename") or "",
                "remote_url": row.get("remote_url") or "",
                "languages": missing,
                "requirements": [
                    "Return concise, visual descriptions only.",
                    "Do not invent historical symbolism if it is not visible or known from the filename/metadata.",
                    "For coats or seals, use blazon for heraldic wording when possible.",
                    "For flags, use description for vexillological wording.",
                ],
            }
            response = self.provider.complete_json(
                system_prompt=_visual_description_system_prompt(),
                payload=payload,
                image_url=str(row.get("remote_url") or ""),
            )
            descriptions = _text_map(response.get("descriptions"), missing)
            blazons = _text_map(response.get("blazons"), missing)
            needs_review = bool(response.get("needs_review", True))
            for language in missing:
                description = descriptions.get(language, "")
                blazon = blazons.get(language, "")
                if not description and not blazon:
                    continue
                upsert_visual_asset_translation(
                    int(row["id"]),
                    language,
                    title=str(row.get("commons_filename") or row.get("entity_name") or ""),
                    description=description,
                    blazon=blazon,
                    source=AI_SOURCE_VISUAL,
                    needs_review=needs_review,
                )
                written += 1
        return AiEnrichmentStats(visual_descriptions=written)

    def _infer_entity_type(self, group: dict) -> dict:
        payload = {
            "task": "complete_scraped_entity_type",
            "country_code": group["country_code"],
            "country_name": group["country_name"],
            "raw_entity_type": group["raw_entity_type"],
            "level": group["level"],
            "sample_area_names": group["sample_area_names"],
            "sample_parent_names": group["sample_parent_names"],
            "languages": self.languages,
            "requirements": [
                "canonical_entity_type must be English singular.",
                "Return singular and plural labels for every requested language when possible.",
                "Set needs_review true unless the administrative meaning is clear from the country, level and samples.",
            ],
        }
        response = self.provider.complete_json(
            system_prompt=_entity_type_system_prompt(),
            payload=payload,
        )
        canonical = str(response.get("canonical_entity_type") or response.get("entity_type") or "").strip()
        if not canonical:
            return {}
        confidence = str(response.get("confidence") or "").strip().lower()
        needs_review = bool(response.get("needs_review", confidence != "high"))
        return {
            "canonical_entity_type": canonical,
            "source_language": str(response.get("source_language") or ""),
            "confidence": confidence,
            "reason": str(response.get("reason") or ""),
            "needs_review": needs_review,
            "singular": _text_map(response.get("singular"), self.languages),
            "plural": _text_map(response.get("plural"), self.languages),
            "payload": payload,
        }

    def _store_entity_type_inference(self, group: dict, result: dict) -> EntityTypeInference:
        payload = result.get("payload") or {}
        input_hash = _input_hash(payload)
        obj, _created = EntityTypeInference.objects.update_or_create(
            country_code=group["country_code"],
            raw_entity_type=group["raw_entity_type"],
            level=int(group["level"]),
            context_key=group.get("context_key", ""),
            defaults={
                "canonical_entity_type": result["canonical_entity_type"],
                "source_language": normalize_dynamic_language_code(result.get("source_language")),
                "confidence": result.get("confidence", ""),
                "reason": result.get("reason", ""),
                "source": AI_SOURCE_ENTITY_TYPE,
                "model": getattr(self.provider, "model", ""),
                "prompt_version": PROMPT_VERSION,
                "input_hash": input_hash,
                "needs_review": bool(result.get("needs_review", True)),
                "is_active": True,
            },
        )
        return obj

    def _store_entity_type_translations(self, group: dict, canonical: str, result: dict) -> int:
        written = 0
        for field, values in ((FIELD_SINGULAR, result.get("singular") or {}), (FIELD_PLURAL, result.get("plural") or {})):
            for language, text in values.items():
                if not text:
                    continue
                upsert_dynamic_translation(
                    subject_type=SUBJECT_ENTITY_TYPE,
                    subject_key=canonical,
                    country_code=group["country_code"],
                    field=field,
                    language=language,
                    text=text,
                    source_text=group["raw_entity_type"],
                    source_language=result.get("source_language", ""),
                    source=AI_SOURCE_ENTITY_TYPE,
                    model=getattr(self.provider, "model", ""),
                    prompt_version=PROMPT_VERSION,
                    input_payload=result.get("payload") or {},
                    needs_review=bool(result.get("needs_review", True)),
                    is_active=True,
                )
                written += 1
        return written

    def _translate_name(
        self,
        *,
        subject_type: str,
        subject_key: str,
        country_code: str,
        source_text: str,
    ) -> int:
        missing = missing_translation_languages(
            subject_type=subject_type,
            subject_key=subject_key,
            field=FIELD_NAME,
            languages=self.languages,
            country_code=country_code,
        )
        if not missing:
            return 0
        payload = {
            "task": "translate_geography_name",
            "subject_type": subject_type,
            "subject_key": subject_key,
            "country_code": country_code,
            "source_text": source_text,
            "languages": missing,
            "requirements": [
                "Return the most common localized geography label.",
                "Keep the source name unchanged when a language normally uses it unchanged.",
                "Do not add administrative type words unless they are part of the proper name.",
            ],
        }
        response = self.provider.complete_json(system_prompt=_translation_system_prompt(), payload=payload)
        translations = _text_map(response.get("translations"), missing)
        needs_review = bool(response.get("needs_review", False))
        written = 0
        for language, text in translations.items():
            if not text:
                continue
            upsert_dynamic_translation(
                subject_type=subject_type,
                subject_key=subject_key,
                country_code=country_code,
                field=FIELD_NAME,
                language=language,
                text=text,
                source_text=source_text,
                source=AI_SOURCE_TRANSLATION,
                model=getattr(self.provider, "model", ""),
                prompt_version=PROMPT_VERSION,
                input_payload=payload,
                needs_review=needs_review,
                is_active=True,
            )
            written += 1
        return written

    def _translate_entity_type(self, entity_type: str, *, country_code: str) -> int:
        missing_singular = missing_translation_languages(
            subject_type=SUBJECT_ENTITY_TYPE,
            subject_key=entity_type,
            field=FIELD_SINGULAR,
            languages=self.languages,
            country_code=country_code,
        )
        missing_plural = missing_translation_languages(
            subject_type=SUBJECT_ENTITY_TYPE,
            subject_key=entity_type,
            field=FIELD_PLURAL,
            languages=self.languages,
            country_code=country_code,
        )
        languages = tuple(sorted(set(missing_singular) | set(missing_plural)))
        if not languages:
            return 0
        payload = {
            "task": "translate_administrative_entity_type",
            "country_code": country_code,
            "canonical_entity_type": entity_type,
            "languages": languages,
            "requirements": [
                "Return singular and plural labels.",
                "Use the usual administrative term for the country when language-specific usage is known.",
            ],
        }
        response = self.provider.complete_json(system_prompt=_translation_system_prompt(), payload=payload)
        needs_review = bool(response.get("needs_review", False))
        written = 0
        for field, values in (
            (FIELD_SINGULAR, _text_map(response.get("singular"), languages)),
            (FIELD_PLURAL, _text_map(response.get("plural"), languages)),
        ):
            for language, text in values.items():
                if not text:
                    continue
                upsert_dynamic_translation(
                    subject_type=SUBJECT_ENTITY_TYPE,
                    subject_key=entity_type,
                    country_code=country_code,
                    field=field,
                    language=language,
                    text=text,
                    source_text=entity_type,
                    source_language="en",
                    source=AI_SOURCE_TRANSLATION,
                    model=getattr(self.provider, "model", ""),
                    prompt_version=PROMPT_VERSION,
                    input_payload=payload,
                    needs_review=needs_review,
                    is_active=True,
                )
                written += 1
        return written


def apply_stored_entity_type_inferences(
    country_code: str,
    entities: list[ScrapedAdminArea],
) -> list[ScrapedAdminArea]:
    try:
        rules = list(
            EntityTypeInference.objects.filter(
                country_code=country_code,
                is_active=True,
                needs_review=False,
            ).only("raw_entity_type", "level", "context_key", "canonical_entity_type")
        )
    except (OperationalError, ProgrammingError):
        return entities
    if not rules:
        return entities
    by_key = {
        (str(rule.raw_entity_type or "").strip(), int(rule.level), str(rule.context_key or "")): rule.canonical_entity_type
        for rule in rules
        if rule.level is not None and rule.canonical_entity_type
    }
    normalized = []
    for entity in entities:
        raw = _raw_entity_type(entity)
        canonical = by_key.get((raw, int(entity.level), ""))
        normalized.append(_replace_entity_type(entity, canonical) if canonical else entity)
    return normalized


def _incomplete_entity_type_groups(config: ScrapingJobConfig, entities: list[ScrapedAdminArea]) -> list[dict]:
    by_code = {entity.code: entity for entity in entities}
    grouped: dict[tuple[str, int], list[ScrapedAdminArea]] = defaultdict(list)
    for entity in entities:
        raw = _raw_entity_type(entity)
        if raw and _looks_incomplete_entity_type(raw):
            grouped[(raw, int(entity.level))].append(entity)

    groups = []
    for (raw, level), items in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        parent_names = []
        for item in items:
            parent = by_code.get(str(item.parent_code or ""))
            if parent and parent.name not in parent_names:
                parent_names.append(parent.name)
        groups.append(
            {
                "country_code": config.country_code,
                "country_name": config.name or config.country_code,
                "raw_entity_type": raw,
                "level": level,
                "context_key": "",
                "sample_area_names": _unique_names([item.name for item in items], limit=10),
                "sample_parent_names": _unique_names(parent_names, limit=5),
            }
        )
    return groups


def _replace_entity_type(entity: ScrapedAdminArea, canonical: str | None) -> ScrapedAdminArea:
    if not canonical:
        return entity
    raw = _raw_entity_type(entity)
    if canonical == entity.entity_type and (entity.raw_entity_type or "") == raw:
        return entity
    return replace(entity, entity_type=canonical, raw_entity_type=raw)


def _raw_entity_type(entity: ScrapedAdminArea) -> str:
    return str(entity.raw_entity_type or entity.entity_type or "").strip()


def _looks_incomplete_entity_type(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = text.rstrip(".").strip().casefold()
    complete = {
        "borough",
        "canton",
        "city",
        "commune",
        "country",
        "county",
        "department",
        "district",
        "governorate",
        "locality",
        "municipality",
        "prefecture",
        "province",
        "region",
        "state",
        "town",
        "village",
    }
    if normalized in complete:
        return False
    if text.endswith("."):
        return True
    if len(normalized) <= 5:
        return True
    return bool(re.fullmatch(r"[A-Z][a-z]{0,5}", text))


def _unique_names(values, *, limit: int) -> list[str]:
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _visual_assets_for_description(*, country_code: str, limit: int) -> list[dict]:
    where = ["status = %s"]
    params: list = ["found"]
    if country_code:
        where.append("country_code = %s")
        params.append(country_code)
    sql = f"""
        SELECT id, entity_type, entity_key, entity_name, country_code, kind,
               commons_filename, remote_url, source_url
          FROM {VISUAL_ASSET_TABLE}
         WHERE {" AND ".join(where)}
         ORDER BY country_code, entity_type, entity_key, kind
         LIMIT %s
    """
    params.append(int(limit))
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _missing_visual_languages(asset_id: int, kind: str, languages: tuple[str, ...]) -> tuple[str, ...]:
    if not languages:
        return ()
    placeholders = ", ".join(["%s"] * len(languages))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT language, description, blazon
              FROM {VISUAL_TRANSLATION_TABLE}
             WHERE asset_id=%s AND language IN ({placeholders})
            """,
            [asset_id, *languages],
        )
        rows = {row[0]: {"description": row[1] or "", "blazon": row[2] or ""} for row in cursor.fetchall()}
    missing = []
    wants_blazon = kind in {"coat", "seal"}
    for language in languages:
        row = rows.get(language, {})
        if wants_blazon:
            if not row.get("blazon"):
                missing.append(language)
        elif not row.get("description"):
            missing.append(language)
    return tuple(missing)


def _text_map(raw, languages: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for language in languages:
        normalized = normalize_dynamic_language_code(language)
        value = raw.get(language)
        if value is None:
            value = raw.get(normalized)
        if value is None:
            value = raw.get(normalized.replace("-", "_"))
        text = str(value or "").strip()
        if text:
            result[normalized] = text
    return result


def _input_hash(payload: dict | None) -> str:
    serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _entity_type_system_prompt() -> str:
    return (
        "You complete abbreviated administrative entity type labels from CityPopulation data. "
        "Return only a JSON object with canonical_entity_type, source_language, confidence, "
        "needs_review, reason, singular and plural. Do not invent uncertain mappings."
    )


def _translation_system_prompt() -> str:
    return (
        "You translate dynamic geography labels for a Django geography database. "
        "Return only a JSON object. Preserve proper names when the target language normally keeps them."
    )


def _visual_description_system_prompt() -> str:
    return (
        "You describe flags, coats of arms and seals for a geography database. "
        "Return only a JSON object with descriptions, blazons and needs_review. "
        "Describe visible elements concisely and do not invent symbolism."
    )
