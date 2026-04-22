# Replaces an obsolete, unapplied migration branch from the pre-AdminArea model.

from django.db import migrations


class Migration(migrations.Migration):

    replaces = [
        ("ciudades_del_mundo", "0002_country_remove_municipality_country_and_more"),
        ("ciudades_del_mundo", "0003_municipality_codecountry"),
        ("ciudades_del_mundo", "0004_municipality_province"),
        ("ciudades_del_mundo", "0005_alter_municipality_size"),
        ("ciudades_del_mundo", "0006_municipality_type"),
        ("ciudades_del_mundo", "0007_remove_municipality_subdivisions_and_more"),
        ("ciudades_del_mundo", "0008_subdivision_population_subdivision_size_and_more"),
        ("ciudades_del_mundo", "0009_remove_subdivision_municipalities"),
        ("ciudades_del_mundo", "0010_subdivision_municipalities"),
        ("ciudades_del_mundo", "0011_alter_country_id"),
        ("ciudades_del_mundo", "0012_subdivision_capital_subdivision_mostpopulate_and_more"),
    ]

    dependencies = [
        ("ciudades_del_mundo", "0008_adminarea_representatives"),
    ]

    operations = []
