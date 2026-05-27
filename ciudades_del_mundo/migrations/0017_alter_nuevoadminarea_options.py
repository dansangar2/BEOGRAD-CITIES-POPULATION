from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ciudades_del_mundo", "0016_nuevoadminarea_capital_names_by_language"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="nuevoadminarea",
            options={"ordering": ["country_code", "level", "name", "id"]},
        ),
    ]
