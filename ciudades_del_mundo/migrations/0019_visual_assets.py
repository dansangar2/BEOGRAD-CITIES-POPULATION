import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ciudades_del_mundo", "0018_webtask_scrapingconfig"),
    ]

    operations = [
        migrations.CreateModel(
            name="VisualAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(max_length=40)),
                ("entity_key", models.CharField(max_length=200)),
                ("entity_name", models.CharField(blank=True, default="", max_length=300)),
                ("country_code", models.CharField(blank=True, default="", max_length=80)),
                ("kind", models.CharField(max_length=40)),
                ("wikidata_id", models.CharField(blank=True, default="", max_length=40)),
                ("commons_filename", models.CharField(blank=True, default="", max_length=500)),
                ("remote_url", models.TextField(blank=True, default="")),
                ("local_path", models.CharField(blank=True, default="", max_length=500)),
                ("local_exists", models.BooleanField(default=False)),
                ("source", models.CharField(blank=True, default="", max_length=40)),
                ("status", models.CharField(default="missing", max_length=40)),
                ("error", models.TextField(blank=True, default="")),
                ("license_name", models.CharField(blank=True, default="", max_length=255)),
                ("author", models.TextField(blank=True, default="")),
                ("attribution", models.TextField(blank=True, default="")),
                ("source_url", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "db_table": "ciudades_del_mundo_visual_asset",
            },
        ),
        migrations.CreateModel(
            name="VisualAssetTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language", models.CharField(max_length=20)),
                ("title", models.CharField(blank=True, default="", max_length=300)),
                ("description", models.TextField(blank=True, default="")),
                ("blazon", models.TextField(blank=True, default="")),
                ("source", models.CharField(blank=True, default="", max_length=80)),
                ("needs_review", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                (
                    "asset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="ciudades_del_mundo.visualasset",
                    ),
                ),
            ],
            options={
                "db_table": "ciudades_del_mundo_visual_asset_translation",
            },
        ),
        migrations.AddConstraint(
            model_name="visualasset",
            constraint=models.UniqueConstraint(fields=("entity_type", "entity_key", "kind"), name="ciudades_visual_asset_entity_kind_uniq"),
        ),
        migrations.AddConstraint(
            model_name="visualassettranslation",
            constraint=models.UniqueConstraint(fields=("asset", "language"), name="ciudades_visual_asset_translation_uniq"),
        ),
        migrations.AddIndex(
            model_name="visualasset",
            index=models.Index(fields=["entity_type", "entity_key"], name="ciudades_visual_asset_entity_idx"),
        ),
        migrations.AddIndex(
            model_name="visualasset",
            index=models.Index(fields=["country_code", "kind"], name="ciudades_visual_asset_country_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="visualasset",
            index=models.Index(fields=["status"], name="ciudades_visual_asset_status_idx"),
        ),
        migrations.AddIndex(
            model_name="visualassettranslation",
            index=models.Index(fields=["language"], name="ciudades_visual_asset_translation_lang_idx"),
        ),
    ]
