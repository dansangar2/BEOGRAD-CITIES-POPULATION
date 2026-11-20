"""Present visual identity resources and stored translations."""

from __future__ import annotations


from pathlib import Path
from urllib.parse import quote, urljoin
from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection
from django.utils.translation import gettext as _
from ciudades_del_mundo.services.visual_assets import (
    get_stored_visual_assets_for_entities, get_visual_assets_for_entity, visual_asset_tables_exist,
)
from ciudades_del_mundo.web.spain_translations import normalize_language_code
from ciudades_del_mundo.web.queries import groups as _queries_groups


def _identity_translation_rows(translations: dict) -> list[dict]:
    if not isinstance(translations, dict):
        return []
    rows = []
    for language, values in translations.items():
        if not isinstance(values, dict):
            continue
        title = str(values.get("title") or "")
        description = str(values.get("description") or "")
        blazon = str(values.get("blazon") or "")
        if not title and not description and not blazon:
            continue
        rows.append({
            "language": str(language),
            "title": title,
            "description": description,
            "blazon": blazon,
            "source": values.get("source") or "",
            "needs_review": bool(values.get("needs_review")),
        })
    preferred = ["es", "en", "fr", "de", "it", "pt", "ru", "sr", "sr_Latn", "ar"]
    order = {language: index for index, language in enumerate(preferred)}
    return sorted(rows, key=lambda row: (order.get(row["language"], 999), row["language"]))


def _select_identity_translation(rows: list[dict], language_code: str | None) -> dict:
    if not rows:
        return {}
    normalized = normalize_language_code(language_code or "")
    candidates = [normalized, normalized.split("-", 1)[0], "es", "en"]
    for candidate in candidates:
        if not candidate:
            continue
        for row in rows:
            if normalize_language_code(row.get("language") or "") == candidate:
                return row
    return rows[0]


def _identity_visual_description_text(translation: dict, *, kind: str = "") -> str:
    """Return the language-selected heraldic/flag explanation for the ficha.

    Generic Wikidata entity descriptions (for example "province of Spain") are
    useful metadata, but they are not the visual explanation requested on the
    ficha.  Only show text that is clearly curated for the visual identity: a
    blazon, configured/manual text, or test text.  This keeps the Description
    field language-specific without pretending a generic Wikidata description is
    a heraldic or vexillological explanation.
    """
    if not isinstance(translation, dict):
        return ""
    blazon = str(translation.get("blazon") or "").strip()
    if blazon:
        return blazon
    description = str(translation.get("description") or "").strip()
    if not description:
        return ""
    source = str(translation.get("source") or "").lower()
    curated_sources = ("config", "manual", "curated", "test")
    if source.startswith(curated_sources):
        return description
    if kind == "flag" and any(word in description.lower() for word in ("flag", "bandera", "drapeau", "flagge", "bandiera", "застав", "علم")):
        return description
    return ""


def _identity_description_label(kind: str):
    if kind == "flag":
        return _("Descripcion de la bandera")
    if kind in {"coat", "seal"}:
        return _("Descripcion heraldica")
    return _("Descripcion")


def _identity_local_media_path(value: str) -> str:
    """Return a safe MEDIA_ROOT-relative path if the identity target is local."""
    relative_path = str(value or "").replace("\\", "/").strip().lstrip("/")
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    media_prefix = media_url.strip("/") + "/"
    if relative_path.startswith(media_prefix):
        relative_path = relative_path[len(media_prefix):]
    if not relative_path.startswith("visual_assets/"):
        return ""
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root:
        return ""
    absolute = (media_root / relative_path).resolve()
    try:
        absolute.relative_to(media_root.resolve())
    except ValueError:
        return ""
    return relative_path if absolute.is_file() else ""


def _media_file_url(relative_path: str) -> str:
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    if not media_url.endswith("/"):
        media_url += "/"
    return urljoin(media_url, quote(str(relative_path or "").replace("\\", "/"), safe="/():,._-"))


def _identity_commons_filename_for_local_path(local_path: str) -> str:
    if not local_path:
        return ""
    try:
        if not visual_asset_tables_exist():
            return ""
    except Exception:
        return ""
    normalized = str(local_path).replace("\\", "/")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT commons_filename
                  FROM ciudades_del_mundo_visual_asset
                 WHERE REPLACE(local_path, '\\', '/') = %s
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                [normalized],
            )
            row = cursor.fetchone()
    except (OperationalError, ProgrammingError):
        return ""
    return str(row[0] or "") if row else ""


def _visual_assets_payload(entity_type: str, entity_key: str, *, include_fallbacks: bool = True) -> dict:
    try:
        return get_visual_assets_for_entity(entity_type, str(entity_key or ""), include_fallbacks=include_fallbacks)
    except (OperationalError, ProgrammingError):
        return {}
    except Exception:
        return {}


def _stored_visual_assets_payloads(entity_type: str, entity_keys: list[str]) -> dict[str, dict]:
    try:
        return get_stored_visual_assets_for_entities(entity_type, entity_keys)
    except Exception:
        return {}


def _visual_assets_ready() -> bool:
    try:
        return visual_asset_tables_exist()
    except Exception:
        return False


def _visual_assets_have_display_image(assets: dict | None) -> bool:
    if not isinstance(assets, dict):
        return False
    return any(_queries_groups._stored_visual_asset_image_url(assets.get(kind)) for kind in ("flag", "coat", "seal"))
