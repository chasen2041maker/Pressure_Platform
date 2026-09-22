from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0005_api_catalog')]
    operations = [migrations.AddField(
        model_name='perfmetricsample', name='k6_payload',
        field=models.JSONField(default=dict, blank=True, verbose_name='K6采样来源'),
    )]
