"""Create synthetic Django schema/data and capture legacy responses, offline.

Only the explicitly supplied NEW database is used. No migrations, seeds,
scraping, task recovery, network providers or production database reads.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "ciudades_del_mundo.tests.isolated_settings"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--empty", action="store_true")
    args = parser.parse_args()
    db = args.database.resolve()
    if db.exists() or db == ROOT / "db.sqlite3":
        raise SystemExit("Characterization requires a new disposable database path")
    import django
    django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.db import connection
    from django.test import Client, override_settings
    from ciudades_del_mundo.models import AdminArea, NuevoAdminArea, ScrapingConfig, VisualAsset, VisualAssetTranslation, DynamicTranslation
    from ciudades_del_mundo.services.dynamic_translations import clear_dynamic_translation_cache

    connection.close()
    connection.settings_dict["NAME"] = str(db)
    with connection.schema_editor() as editor:
        for model in apps.get_app_config("ciudades_del_mundo").get_models():
            editor.create_model(model)
    stamp = datetime(2025, 6, 1, 12, 34, 56, 123456, tzinfo=timezone.utc)
    if not args.empty:
        root = AdminArea.objects.create(id="xx/raíz 市", country_code="xx", code="xx", name='Raíz, "Ciudad"', level=0,
                                        pop_latest=9_223_372_036_854_775_807, area_km2="120.50", density="21.2500",
                                        pop_latest_date=date(2025, 1, 2), annotations="primera\nsegunda", data_wd="Q1")
        for i, name in enumerate(("Álava", "Zeta", "Niš")):
            AdminArea.objects.create(id=f"xx/{i}/市", country_code="xx", code=str(i), name=name, level=1, parent=root,
                                     city_merge_status=3 if i == 1 else 0, pop_latest=None if i == 2 else i + 10,
                                     area_km2="1.20", density="0.1000", entity_type="Province")
        AdminArea.objects.update(created_at=stamp, updated_at=stamp)
        for code in ("alpha", "beta", "spain"):
            variant = NuevoAdminArea.objects.create(id=code, country_code=code, code=code, name="Nombre_" + code,
                                                    level=0, pop_latest=200, population_index=0)
            NuevoAdminArea.objects.create(id=code+"/市", country_code=code, code=code+"_1", name="Niš", level=1,
                                          parent=variant, pop_latest=25, population_index=0)
        NuevoAdminArea.objects.create(id="no-root", country_code="delta", code="no-root", name="Ignored", level=1,
                                      pop_latest=9_007_199_254_740_993, population_index=0)
        DynamicTranslation.objects.create(subject_type="country", subject_key="delta", country_code="delta", field="name", language="en",
                                          text="Reviewed name", needs_review=False, is_active=True)
        DynamicTranslation.objects.create(subject_type="country", subject_key="delta", country_code="delta", field="name", language="sr",
                                          text="Проверено име", needs_review=False, is_active=True)
        NuevoAdminArea.objects.create(id="empty-root", country_code="empty-root", code="empty-root", name="", level=0, pop_latest=50, population_index=0)
        DynamicTranslation.objects.create(subject_type="country", subject_key="empty-root", country_code="empty-root", field="name", language="en",
                                          text="Not selected over an existing root", needs_review=False, is_active=True)
        for slug, code, content in (("sample", "xx", 'country_code="xx"\n'), ("from-content", "", 'country_code="xx"\n'),
                                    ("invalid-content", "", "not toml ["), ("no-data", "zz", "")):
            ScrapingConfig.objects.create(slug=slug, country_code=code, content=content, content_hash="fixture")
        asset = VisualAsset.objects.create(entity_type="admin_area", entity_key=root.id, country_code="xx", kind="flag", status="remote",
                                           commons_filename="Flag of synthetic.svg", created_at=stamp, updated_at=stamp)
        VisualAssetTranslation.objects.create(asset=asset, language="es", title="Bandera", description="Descripción", source="test",
                                              created_at=stamp, updated_at=stamp)
        for code, population, area in (("france", 12345, "112.30"), ("synthetic", 12345, "10.20"), ("zero", 0, None)):
            AdminArea.objects.create(id=code, country_code=code, code=code, name="Root_"+code, level=0, pop_latest=population, area_km2=area)
        for part in ("one", "two"):
            AdminArea.objects.create(id="parts/"+part, country_code="parts", code=part, name=part, level=0,
                                     pop_latest=100, area_km2="2.20")
        AdminArea.objects.create(id="excluded", country_code="invisible", code="invisible", name="Hidden", level=0, pop_latest=999, city_merge_status=3)
        DynamicTranslation.objects.create(subject_type="country", subject_key="synthetic", country_code="synthetic", field="name", language="en",
                                          text="Reviewed country", needs_review=False, is_active=True)
        asset = VisualAsset.objects.create(entity_type="country", entity_key="xx", country_code="xx", kind="flag", status="remote",
                                           commons_filename="Flag of synthetic (á).svg", created_at=stamp, updated_at=stamp)
        VisualAssetTranslation.objects.create(asset=asset, language="es", title="Bandera", description="Descripción", source="test",
                                              created_at=stamp, updated_at=stamp)
    clear_dynamic_translation_cache()
    cases = []
    with override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"], MEDIA_ROOT=db.parent / "media"):
        client = Client(enforce_csrf_checks=True)
        client.cookies[settings.CSRF_COOKIE_NAME] = 'a' * 32
        def capture(path, method, language, accept='application/json'):
            headers = {'HTTP_X_CSRFTOKEN': 'a' * 32}
            if accept is not None:
                headers['HTTP_ACCEPT'] = accept
            response = client.generic(method, path, **headers)
            case = {"path": path, "method": method, "language": language, "accept": accept, "status": response.status_code,
                    "content_type": response.get("Content-Type"), "disposition": response.get("Content-Disposition")}
            if method != "HEAD" and response.get("Content-Type", "").startswith("application/json"):
                case["json"] = json.loads(response.content)
            else:
                case["body"] = base64.b64encode(response.content).decode("ascii")
            cases.append(case)
        for language in ("es", "en", "fr", "de", "ru", "it", "sr", "sr-latn", "ar"):
            client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
            paths = ["/api/derived/", "/dashboard/derived/", "/api/countries/", "/dashboard/population/"]
            paths += [f"/configs/{slug}/export-data-csv/" for slug in ("sample", "from-content", "invalid-content", "no-data", "missing", "SAMPLE")]
            for path in paths:
                for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                    capture(path, method, language)
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'
        for accept in ('text/html', '*/*', 'application/xml', 'not-a-media-type', None):
            for path in ('/api/countries/', '/dashboard/population/', '/api/derived/', '/dashboard/derived/',
                         '/configs/sample/export-data-csv/', '/configs/missing/export-data-csv/'):
                capture(path, 'GET', 'es', accept)
    connection.close()
    args.expected.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Captured {len(cases)} responses against synthetic schema: {db.name}")


if __name__ == "__main__":
    main()
