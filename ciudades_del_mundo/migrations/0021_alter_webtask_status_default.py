# Generated manually for web task immediate-start semantics.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0020_dynamic_ai_texts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="webtask",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="running",
                max_length=20,
            ),
        ),
    ]
