# Generated manually for AdminArea representatives support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0007_remove_nuevoadminarea_last_census_year_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminarea",
            name="representatives",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
