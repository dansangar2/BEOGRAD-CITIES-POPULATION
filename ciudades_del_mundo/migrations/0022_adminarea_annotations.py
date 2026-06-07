# Generated manually for scraping parent-resolution annotations.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0021_alter_webtask_status_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminarea",
            name="annotations",
            field=models.TextField(blank=True, default="", verbose_name="Anotaciones"),
        ),
    ]
