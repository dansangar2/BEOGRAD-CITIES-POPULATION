from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0014_nuevoadminarea_population_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminarea",
            name="city_merge_status",
            field=models.IntegerField(
                choices=[
                    (0, "No unificada"),
                    (1, "Fuente de ciudad unificada"),
                    (2, "Ciudad unificada"),
                ],
                db_index=True,
                default=0,
            ),
        ),
        migrations.AddIndex(
            model_name="adminarea",
            index=models.Index(
                fields=["parent", "city_merge_status"],
                name="adminarea_parent_merge_idx",
            ),
        ),
    ]
