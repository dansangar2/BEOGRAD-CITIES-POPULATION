import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ciudades_del_mundo', '0029_swap_city_merge_status_values'),
    ]

    operations = [
        migrations.CreateModel(
            name='TerritorialEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country', models.SlugField(max_length=64)),
                ('version', models.CharField(max_length=64)),
                ('content', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('country', 'version'), name='territorial_evidence_version')],
            },
        ),
        migrations.CreateModel(
            name='TerritorialReference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country', models.SlugField(max_length=64)),
                ('version', models.CharField(max_length=100)),
                ('content', models.JSONField()),
                ('content_hash', models.CharField(max_length=64)),
                ('reviewed_by', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('country', 'version'), name='territorial_reference_version')],
            },
        ),
        migrations.CreateModel(
            name='TerritorialRun',
            fields=[
                ('id', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('country', models.SlugField(max_length=64)),
                ('status', models.CharField(default='pending', max_length=24)),
                ('inputs', models.JSONField(default=dict)),
                ('inputs_hash', models.CharField(blank=True, max_length=64)),
                ('plan', models.JSONField(default=dict)),
                ('plan_hash', models.CharField(blank=True, max_length=64)),
                ('approved_plan', models.JSONField(default=dict)),
                ('diagnostics', models.JSONField(default=list)),
                ('comparison', models.JSONField(default=list)),
                ('metrics', models.JSONField(default=dict)),
                ('result', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('applied_at', models.DateTimeField(blank=True, null=True)),
                ('evidence', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='ciudades_del_mundo.territorialevidence')),
                ('reference', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='ciudades_del_mundo.territorialreference')),
                ('task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='ciudades_del_mundo.webtask')),
            ],
        ),
    ]
