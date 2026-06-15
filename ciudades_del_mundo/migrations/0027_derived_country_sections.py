from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0026_scrapingconfig_pages_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="DerivedCountry",
            fields=[
                ("slug", models.SlugField(max_length=128, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("source_country_code", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name", "slug"],
            },
        ),
        migrations.CreateModel(
            name="SubdivisionGroup",
            fields=[
                ("slug", models.SlugField(max_length=128, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("content", models.TextField(blank=True, default="")),
                ("source_country_code", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name", "slug"],
            },
        ),
        migrations.CreateModel(
            name="DerivedCountryConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=128)),
                ("name", models.CharField(max_length=255)),
                ("content", models.TextField(blank=True, default="")),
                ("source_country_code", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("derived_country_code", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "country",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configs",
                        to="ciudades_del_mundo.derivedcountry",
                    ),
                ),
            ],
            options={
                "ordering": ["country_id", "name", "slug"],
            },
        ),
        migrations.AddIndex(
            model_name="derivedcountry",
            index=models.Index(fields=["source_country_code", "slug"], name="dercountry_source_slug_idx"),
        ),
        migrations.AddIndex(
            model_name="subdivisiongroup",
            index=models.Index(fields=["source_country_code", "slug"], name="subgroup_source_slug_idx"),
        ),
        migrations.AddIndex(
            model_name="derivedcountryconfig",
            index=models.Index(fields=["country", "is_active"], name="derconf_country_active_idx"),
        ),
        migrations.AddIndex(
            model_name="derivedcountryconfig",
            index=models.Index(fields=["derived_country_code"], name="derconf_derived_code_idx"),
        ),
        migrations.AddConstraint(
            model_name="derivedcountryconfig",
            constraint=models.UniqueConstraint(fields=("country", "slug"), name="derconf_country_slug_uniq"),
        ),
    ]
