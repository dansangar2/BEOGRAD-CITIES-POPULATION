"""HTTP endpoints for assets."""

from __future__ import annotations
from ciudades_del_mundo.web.http import missing_data_as_404

from ciudades_del_mundo.services.visual_assets import commons_file_url
from ciudades_del_mundo.web.queries import assets as _queries_assets
from ciudades_del_mundo.web.spain_translations import normalize_language_code
from django.utils.translation import get_language
from urllib.parse import quote

import json
from pathlib import Path
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import AdminArea, NuevoAdminArea
from ciudades_del_mundo.services.visual_assets import get_visual_asset_by_local_or_commons, get_visual_asset_for_entity_kind
from ciudades_del_mundo.web.queries import browser as _queries_browser


@missing_data_as_404
def api_visual_assets(request, entity_type, entity_key):
    """Return persisted local/remote flag and coat assets for one entity."""
    return JsonResponse({
        "ok": True,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "assets": _queries_assets._visual_assets_payload(entity_type, entity_key),
    })


@missing_data_as_404
def area_map_detail(request, source, area_id):
    """Render map and visual identity helpers for one source or derived area."""
    if source == "admin":
        area = (
            AdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(id=area_id)
            .first()
        )
    elif source == "derived":
        area = (
            NuevoAdminArea.objects.select_related("parent", "most_populate_city")
            .prefetch_related("capitals")
            .filter(id=area_id)
            .first()
        )
    else:
        raise Http404(_("Origen de mapa no soportado."))

    if area is None:
        raise Http404(_("No se encontro el area solicitada."))

    map_query = _queries_browser._area_search_query(area)
    language_code = getattr(request, "LANGUAGE_CODE", None)
    context = {
        "area": area,
        "source": source,
        "area_display_name": _queries_browser._area_display_name(area),
        "entity_type_display": _queries_browser._entity_type_label(
            getattr(area, "entity_type", ""),
            country_code=getattr(area, "country_code", ""),
        ),
        "parent_display_name": _queries_browser._area_display_name(area.parent) if getattr(area, "parent", None) else "",
        "most_populate_city_display_name": (
            _queries_browser._area_display_name(area.most_populate_city) if getattr(area, "most_populate_city", None) else ""
        ),
        "map_query": map_query,
        "wikidata_query": map_query,
        "country_display_name": _queries_browser._area_country_display_name(area),
        "capital_names": ", ".join(_queries_browser._area_capital_display_names(area, language_code)),
        "related_places_json": json.dumps(_queries_browser._area_related_places(area, language_code), ensure_ascii=False),
        "commons_flag_query": f"flag {area.name} {getattr(area, 'country_code', '')}",
        "commons_coat_query": f"coat of arms {area.name} {getattr(area, 'country_code', '')}",
    }
    return render(request, "ciudades_del_mundo/area_map_detail.html", context)


@missing_data_as_404
def visual_identity_entity_detail(request, kind, entity_type, entity_key):
    """Render the Ficha page for one stored asset using a readable entity URL."""
    kind = (kind or "image").lower()
    entity_type = str(entity_type or "").strip()
    entity_key = str(entity_key or "").strip().strip("/")
    asset = get_visual_asset_for_entity_kind(entity_type, entity_key, kind)
    if not asset:
        raise Http404(_("No se encontro la ficha solicitada."))
    return _render_visual_identity_asset(request, asset, kind=kind)


@missing_data_as_404
def visual_identity_detail(request, kind, filename):
    """Render legacy Ficha URLs that still pass a Commons filename or local path."""
    kind = (kind or "image").lower()
    safe_filename = str(filename or "").strip().lstrip("/")
    if not safe_filename:
        raise Http404(_("No se encontro la imagen solicitada."))

    asset = get_visual_asset_by_local_or_commons(safe_filename, kind=kind)
    if asset:
        return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)

    local_path = _queries_assets._identity_local_media_path(safe_filename)
    if local_path:
        asset = {
            "kind": kind,
            "entity_type": "",
            "entity_key": "",
            "entity_name": "",
            "commons_filename": _queries_assets._identity_commons_filename_for_local_path(local_path),
            "remote_url": "",
            "local_path": local_path,
            "local_url": _queries_assets._media_file_url(local_path),
            "image_url": _queries_assets._media_file_url(local_path),
            "translations": {},
            "source": "local",
            "source_url": "",
            "wikidata_id": "",
            "license_name": "",
            "author": "",
            "attribution": "",
        }
        return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)

    commons_filename = safe_filename.replace("_", " ") if "." in Path(safe_filename).name else safe_filename
    asset = {
        "kind": kind,
        "entity_type": "",
        "entity_key": "",
        "entity_name": "",
        "commons_filename": commons_filename,
        "remote_url": commons_file_url(commons_filename, width=1400),
        "local_path": "",
        "local_url": "",
        "image_url": commons_file_url(commons_filename, width=1400),
        "translations": {},
        "source": "wikimedia",
        "source_url": "",
        "wikidata_id": "",
        "license_name": "",
        "author": "",
        "attribution": "",
    }
    return _render_visual_identity_asset(request, asset, kind=kind, legacy_target=safe_filename)


def _render_visual_identity_asset(request, asset: dict, *, kind: str, legacy_target: str = ""):
    labels = {
        "flag": _("Bandera"),
        "coat": _("Escudo"),
        "seal": _("Sello"),
        "locator": _("Mapa localizador"),
    }
    commons_filename = str(asset.get("commons_filename") or "")
    remote_url = str(asset.get("remote_url") or "")
    local_path = str(asset.get("local_path") or "")
    image_url = remote_url or str(asset.get("image_url") or "") or str(asset.get("local_url") or "")
    if not image_url and commons_filename:
        image_url = commons_file_url(commons_filename, width=1400)
    encoded_commons = quote((commons_filename or "").replace(" ", "_"), safe="/():,._-")
    commons_url = f"https://commons.wikimedia.org/wiki/File:{encoded_commons}" if encoded_commons else str(asset.get("source_url") or "")

    translations = _queries_assets._identity_translation_rows(asset.get("translations") or {})
    active_language = normalize_language_code(get_language())
    active_translation = _queries_assets._select_identity_translation(translations, active_language)
    description_text = _queries_assets._identity_visual_description_text(active_translation, kind=kind)
    entity_label = str(asset.get("entity_name") or asset.get("entity_key") or "")
    display_filename = commons_filename or Path(local_path).name or Path(legacy_target).name
    context = {
        "kind": kind,
        "kind_label": labels.get(kind, _("Imagen")),
        "description_label": _queries_assets._identity_description_label(kind),
        "asset": asset,
        "entity_label": entity_label,
        "filename": display_filename,
        "local_path": local_path,
        "image_url": image_url,
        "commons_url": commons_url,
        "wikidata_url": f"https://www.wikidata.org/wiki/{asset.get('wikidata_id')}" if asset.get("wikidata_id") else "",
        "source_label": "Wikimedia Commons" if commons_filename or remote_url else _("Archivo local"),
        "active_language": active_language,
        "active_translation": active_translation,
        "description_text": description_text,
        "has_visual_description": bool(description_text),
    }
    return render(request, "ciudades_del_mundo/visual_identity_detail.html", context)
