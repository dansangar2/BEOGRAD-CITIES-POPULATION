from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0015_adminarea_city_merge_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="nuevoadminarea",
            name="capital_names_by_language",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
