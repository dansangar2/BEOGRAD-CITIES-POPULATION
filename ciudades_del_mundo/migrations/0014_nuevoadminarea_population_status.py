import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0013_replace_legacy_subdivision_branch"),
    ]

    operations = [
        migrations.AddField(
            model_name="nuevoadminarea",
            name="population_index",
            field=models.DecimalField(
                decimal_places=4,
                default=1,
                help_text=(
                    "Multiplicador de poblacion aplicado a esta subdivision y a sus "
                    "descendientes para el computo de representacion."
                ),
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="nuevoadminarea",
            name="province_status",
            field=models.CharField(
                choices=[
                    ("normal", "Normal"),
                    ("dependency", "Dependencia"),
                    ("territory", "Territorio"),
                ],
                default="normal",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="nuevoadminarea",
            name="depends_on",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Provincia con la que comparte representacion si el estado es "
                    "dependencia."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dependent_areas",
                to="ciudades_del_mundo.nuevoadminarea",
            ),
        ),
        migrations.AddConstraint(
            model_name="nuevoadminarea",
            constraint=models.CheckConstraint(
                condition=models.Q(("population_index__gte", 0)),
                name="nuevo_area_population_index_nonnegative",
            ),
        ),
        migrations.AddIndex(
            model_name="nuevoadminarea",
            index=models.Index(fields=["province_status"], name="ciudades_de_provinc_d01143_idx"),
        ),
    ]
