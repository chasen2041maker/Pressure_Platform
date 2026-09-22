from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0018_perfscenariostep_websocket')]
    operations = [migrations.AddField(model_name='perfscenariostep', name='execution_policy',
                                    field=models.JSONField(blank=True, default=dict))]
