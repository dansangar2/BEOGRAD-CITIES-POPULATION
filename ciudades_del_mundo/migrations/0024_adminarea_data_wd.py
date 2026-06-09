from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0023_program_message_codes_asset_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminarea",
            name="data_wd",
            field=models.CharField(blank=True, db_index=True, default="", max_length=40),
        ),
        migrations.AddIndex(
            model_name="adminarea",
            index=models.Index(fields=["country_code", "data_wd"], name="adminarea_country_datawd_idx"),
        ),
    ]
