from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0015_metric_sample_payload')]
    operations = [migrations.AddField(model_name='perfproject', name='catalog_source',
                                     field=models.JSONField(default=dict, blank=True, editable=False))]
