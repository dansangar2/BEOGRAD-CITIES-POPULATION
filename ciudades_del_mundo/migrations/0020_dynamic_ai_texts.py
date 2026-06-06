from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ciudades_del_mundo", "0019_visual_assets"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminarea",
            name="raw_entity_type",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.CreateModel(
            name="DynamicTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject_type", models.CharField(max_length=40)),
                ("subject_key", models.CharField(max_length=255)),
                ("country_code", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("field", models.CharField(max_length=40)),
                ("source_text", models.TextField(blank=True, default="")),
                ("source_language", models.CharField(blank=True, default="", max_length=20)),
                ("language", models.CharField(db_index=True, max_length=20)),
                ("text", models.TextField()),
                ("source", models.CharField(blank=True, default="", max_length=80)),
                ("model", models.CharField(blank=True, default="", max_length=120)),
                ("prompt_version", models.CharField(blank=True, default="", max_length=80)),
                ("input_hash", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("needs_review", models.BooleanField(db_index=True, default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["subject_type", "country_code", "subject_key", "field", "language"],
            },
        ),
        migrations.CreateModel(
            name="EntityTypeInference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("country_code", models.CharField(db_index=True, max_length=64)),
                ("raw_entity_type", models.CharField(max_length=80)),
                ("level", models.IntegerField(blank=True, db_index=True, null=True)),
                ("context_key", models.CharField(blank=True, default="", max_length=120)),
                ("canonical_entity_type", models.CharField(max_length=80)),
                ("source_language", models.CharField(blank=True, default="", max_length=20)),
                ("confidence", models.CharField(blank=True, default="", max_length=20)),
                ("reason", models.TextField(blank=True, default="")),
                ("source", models.CharField(blank=True, default="", max_length=80)),
                ("model", models.CharField(blank=True, default="", max_length=120)),
                ("prompt_version", models.CharField(blank=True, default="", max_length=80)),
                ("input_hash", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("needs_review", models.BooleanField(db_index=True, default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["country_code", "level", "raw_entity_type", "context_key"],
            },
        ),
        migrations.AddConstraint(
            model_name="dynamictranslation",
            constraint=models.UniqueConstraint(
                fields=("subject_type", "subject_key", "country_code", "field", "language"),
                name="dyn_translation_subject_field_lang_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="entitytypeinference",
            constraint=models.UniqueConstraint(
                fields=("country_code", "level", "raw_entity_type", "context_key"),
                name="entity_type_inference_context_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="dynamictranslation",
            index=models.Index(fields=["subject_type", "subject_key"], name="dyn_translation_subject_idx"),
        ),
        migrations.AddIndex(
            model_name="dynamictranslation",
            index=models.Index(fields=["country_code", "language"], name="dyn_tr_country_lang_idx"),
        ),
        migrations.AddIndex(
            model_name="entitytypeinference",
            index=models.Index(fields=["country_code", "raw_entity_type"], name="entity_type_inf_raw_idx"),
        ),
        migrations.AddIndex(
            model_name="entitytypeinference",
            index=models.Index(fields=["country_code", "canonical_entity_type"], name="entity_type_inf_canon_idx"),
        ),
    ]
