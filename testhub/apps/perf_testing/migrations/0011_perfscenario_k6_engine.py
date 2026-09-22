from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0010_perfcomparisonreport')]
    operations = [
        migrations.AlterField(
            model_name='perfscenario', name='engine',
            field=models.CharField(
                max_length=20, default='BUILTIN', verbose_name='压测引擎', db_comment='压测引擎',
                choices=[('K6', 'k6'), ('BUILTIN', '内置引擎(asyncio+httpx)'), ('LOCUST', 'Locust'), ('JMETER', 'JMeter')],
            ),
        ),
    ]
