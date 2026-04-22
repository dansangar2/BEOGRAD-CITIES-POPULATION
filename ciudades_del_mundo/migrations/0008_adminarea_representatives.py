# Generated manually for representatives support.

from django.db import migrations, models


def _has_column(schema_editor, table_name: str, column_name: str) -> bool:
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor,
            table_name,
        )
    return any(column.name == column_name for column in description)


def add_missing_representatives_columns(apps, schema_editor):
    for model_name in ("AdminArea", "NuevoAdminArea"):
        model = apps.get_model("ciudades_del_mundo", model_name)
        if _has_column(schema_editor, model._meta.db_table, "representatives"):
            continue

        field = models.PositiveIntegerField(blank=True, null=True)
        field.set_attributes_from_name("representatives")
        schema_editor.add_field(model, field)


def remove_representatives_columns(apps, schema_editor):
    for model_name in ("AdminArea", "NuevoAdminArea"):
        model = apps.get_model("ciudades_del_mundo", model_name)
        if not _has_column(schema_editor, model._meta.db_table, "representatives"):
            continue

        field = models.PositiveIntegerField(blank=True, null=True)
        field.set_attributes_from_name("representatives")
        schema_editor.remove_field(model, field)


def copy_escanhos_to_nuevoadminarea(apps, schema_editor):
    NuevoAdminArea = apps.get_model("ciudades_del_mundo", "NuevoAdminArea")
    Escanho = apps.get_model("ciudades_del_mundo", "Escanho")

    for seat in Escanho.objects.all().only("subdivision_id", "seats"):
        NuevoAdminArea.objects.filter(id=seat.subdivision_id).update(
            representatives=seat.seats
        )


def copy_nuevoadminarea_to_escanhos(apps, schema_editor):
    NuevoAdminArea = apps.get_model("ciudades_del_mundo", "NuevoAdminArea")
    Escanho = apps.get_model("ciudades_del_mundo", "Escanho")

    for area in NuevoAdminArea.objects.exclude(representatives__isnull=True):
        Escanho.objects.update_or_create(
            country_code=area.country_code,
            subdivision_code=area.code,
            defaults={
                "country_id": area.country_code,
                "subdivision_id": area.id,
                "seats": area.representatives,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0007_remove_nuevoadminarea_last_census_year_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_missing_representatives_columns,
                    remove_representatives_columns,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="adminarea",
                    name="representatives",
                    field=models.PositiveIntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="nuevoadminarea",
                    name="representatives",
                    field=models.PositiveIntegerField(blank=True, null=True),
                ),
            ],
        ),
        migrations.RunPython(
            copy_escanhos_to_nuevoadminarea,
            copy_nuevoadminarea_to_escanhos,
        ),
        migrations.DeleteModel(
            name="Escanho",
        ),
    ]
