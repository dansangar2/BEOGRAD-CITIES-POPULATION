from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import tomllib
from pathlib import Path

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _aware_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


def _count_pages(data, slug):
    total = 0
    for item in data.get("pages") or []:
        raw_paths = item.get("path")
        if raw_paths is None:
            continue
        paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        total += len(paths)
    return total


def _metadata(slug, content):
    try:
        data = tomllib.loads(content)
        pages_count = _count_pages(data, slug)
        if pages_count <= 0:
            raise ValueError("La configuración debe definir al menos una página.")
        return {
            "country_code": str(data.get("country_code") or slug),
            "name": str(data.get("name") or ""),
            "pages_count": pages_count,
            "cities_count": len(data.get("cities") or []),
            "has_representation": bool(data.get("representation")),
            "is_valid": True,
            "validation_error": "",
        }
    except Exception as exc:  # noqa: BLE001 - stored for later repair in UI.
        try:
            data = tomllib.loads(content)
        except Exception:  # noqa: BLE001
            data = {}
        return {
            "country_code": str(data.get("country_code") or slug),
            "name": str(data.get("name") or ""),
            "pages_count": 0,
            "cities_count": 0,
            "has_representation": False,
            "is_valid": False,
            "validation_error": str(exc),
        }


def seed_scraping_configs(apps, schema_editor):
    ScrapingConfig = apps.get_model("ciudades_del_mundo", "ScrapingConfig")
    root = Path(settings.BASE_DIR) / "ciudades_del_mundo" / "subdivisions"
    if not root.is_dir():
        return
    now = timezone.now()
    rows = []
    for path in sorted(root.glob("*.toml")):
        if path.name.startswith("_"):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        slug = path.stem
        meta = _metadata(slug, content)
        rows.append(
            ScrapingConfig(
                slug=slug,
                country_code=meta["country_code"],
                name=meta["name"],
                content=content,
                content_hash=sha256(content.encode("utf-8")).hexdigest(),
                source_path=str(path.relative_to(settings.BASE_DIR)),
                pages_count=meta["pages_count"],
                cities_count=meta["cities_count"],
                has_representation=meta["has_representation"],
                is_valid=meta["is_valid"],
                validation_error=meta["validation_error"],
                imported_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    if rows:
        ScrapingConfig.objects.bulk_create(rows, ignore_conflicts=True, batch_size=100)


def seed_web_tasks(apps, schema_editor):
    WebTask = apps.get_model("ciudades_del_mundo", "WebTask")
    history_path = Path(settings.BASE_DIR) / ".web_tasks.json"
    try:
        records = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(records, list):
        return
    rows = []
    now = timezone.now()
    for record in records:
        if not isinstance(record, dict):
            continue
        task_id = str(record.get("id") or "").strip()
        key = str(record.get("key") or "").strip()
        label = str(record.get("label") or "").strip()
        args = record.get("args") or []
        if not task_id or not key or not isinstance(args, list):
            continue
        status = str(record.get("status") or "failed")
        output = [str(line) for line in record.get("output", [])][-240:]
        finished_at = _aware_datetime(record.get("finished_at"))
        if status not in TERMINAL_STATUSES:
            status = "failed"
            finished_at = finished_at or now
            output.append("\n[INFO] Tarea migrada como interrumpida por reinicio del servidor.\n")
        rows.append(
            WebTask(
                id=task_id,
                key=key,
                label=label or key,
                args=[str(arg) for arg in args],
                status=status,
                created_at=_aware_datetime(record.get("created_at")) or now,
                started_at=_aware_datetime(record.get("started_at")),
                finished_at=finished_at,
                returncode=record.get("returncode"),
                cancel_requested=bool(record.get("cancel_requested", False)),
                log_path=str(record.get("log_path") or "") or None,
                output=output,
                updated_at=now,
            )
        )
    if rows:
        WebTask.objects.bulk_create(rows, ignore_conflicts=True, batch_size=100)


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0017_alter_nuevoadminarea_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScrapingConfig",
            fields=[
                ("slug", models.SlugField(max_length=128, primary_key=True, serialize=False)),
                ("country_code", models.CharField(db_index=True, max_length=64)),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("content", models.TextField()),
                ("content_hash", models.CharField(db_index=True, max_length=64)),
                ("source_path", models.CharField(blank=True, default="", max_length=500)),
                ("pages_count", models.PositiveIntegerField(default=0)),
                ("cities_count", models.PositiveIntegerField(default=0)),
                ("has_representation", models.BooleanField(default=False)),
                ("is_valid", models.BooleanField(db_index=True, default=True)),
                ("validation_error", models.TextField(blank=True, default="")),
                ("imported_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["slug"]},
        ),
        migrations.CreateModel(
            name="WebTask",
            fields=[
                ("id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("key", models.CharField(db_index=True, max_length=255)),
                ("label", models.CharField(max_length=255)),
                ("args", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], db_index=True, default="queued", max_length=20)),
                ("created_at", models.DateTimeField(db_index=True)),
                ("started_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("returncode", models.IntegerField(blank=True, null=True)),
                ("cancel_requested", models.BooleanField(default=False)),
                ("log_path", models.CharField(blank=True, max_length=500, null=True)),
                ("output", models.JSONField(blank=True, default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="scrapingconfig",
            index=models.Index(fields=["country_code", "slug"], name="scrconf_country_slug_idx"),
        ),
        migrations.AddIndex(
            model_name="scrapingconfig",
            index=models.Index(fields=["is_valid", "slug"], name="scrconf_valid_slug_idx"),
        ),
        migrations.AddIndex(
            model_name="webtask",
            index=models.Index(fields=["key", "-created_at"], name="webtask_key_created_idx"),
        ),
        migrations.AddIndex(
            model_name="webtask",
            index=models.Index(fields=["status", "-created_at"], name="webtask_status_created_idx"),
        ),
        migrations.RunPython(seed_scraping_configs, migrations.RunPython.noop),
        migrations.RunPython(seed_web_tasks, migrations.RunPython.noop),
    ]
