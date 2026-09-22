from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0008_prepared_request_pool')]

    operations = [
        migrations.AddField(
            model_name='perfscenariostep', name='protocol',
            field=models.CharField(choices=[('HTTP', 'HTTP'), ('WEBSOCKET', 'WebSocket')], default='HTTP', max_length=20)),
        migrations.AddField(
            model_name='perfscenariostep', name='websocket_config',
            field=models.JSONField(blank=True, default=dict)),
    ]
