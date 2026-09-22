from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('api_testing', '0001_initial'),
        ('perf_testing', '0013_account_pools'),
    ]

    operations = [
        migrations.AddField(model_name='perfproject', name='api_project',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='perf_project', to='api_testing.apiproject')),
        migrations.AddField(model_name='perfscenariostep', name='source_metadata',
            field=models.JSONField(blank=True, default=dict, editable=False)),
        migrations.AddField(model_name='perfscenariostep', name='preparation',
            field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(name='PerfApiCatalogVersion', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('version', models.PositiveIntegerField()),
            ('content_hash', models.CharField(max_length=64)),
            ('source_version', models.CharField(blank=True, max_length=200)),
            ('document', models.JSONField(default=dict)),
            ('operations', models.JSONField(default=list)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                related_name='api_catalog_versions', to='perf_testing.perfproject')),
        ], options={'db_table': 'perf_api_catalog_versions', 'ordering': ['-version']}),
        migrations.AddConstraint(model_name='perfapicatalogversion',
            constraint=models.UniqueConstraint(fields=('project', 'version'), name='perf_catalog_version_unique')),
    ]
