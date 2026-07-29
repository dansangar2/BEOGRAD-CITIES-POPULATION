from django.db import migrations, models


def swap_city_merge_status_values(apps, schema_editor):
    AdminArea = apps.get_model("ciudades_del_mundo", "AdminArea")
    AdminArea.objects.filter(city_merge_status=1).update(city_merge_status=99)
    AdminArea.objects.filter(city_merge_status=2).update(city_merge_status=1)
    AdminArea.objects.filter(city_merge_status=99).update(city_merge_status=2)


class Migration(migrations.Migration):
    dependencies = [
        ("ciudades_del_mundo", "0028_derivedsubdivision"),
    ]

    operations = [
        migrations.RunPython(
            swap_city_merge_status_values,
            reverse_code=swap_city_merge_status_values,
        ),
        migrations.AlterField(
            model_name="adminarea",
            name="city_merge_status",
            field=models.IntegerField(
                choices=[
                    (0, "No unificada"),
                    (1, "Ciudad unificada"),
                    (2, "Fuente de ciudad unificada"),
                ],
                db_index=True,
                default=0,
            ),
        ),
    ]
