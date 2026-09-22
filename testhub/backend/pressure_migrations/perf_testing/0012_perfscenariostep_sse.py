from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0011_perfscenariostep_binary_body')]
    operations = [
        migrations.AddField(model_name='perfscenariostep', name='sse_config', field=models.JSONField(blank=True, default=dict)),
        migrations.AlterField(model_name='perfscenariostep', name='protocol', field=models.CharField(
            choices=[('HTTP', 'HTTP'), ('WEBSOCKET', 'WebSocket'), ('SSE', 'SSE')], default='HTTP', max_length=20)),
    ]
