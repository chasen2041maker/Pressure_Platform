from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0021_perfscenariostep_sse')]
    operations = [migrations.AddField(model_name='perfexecution', name='resource_recovery',
        field=models.JSONField(default=dict, blank=True, editable=False, verbose_name='资源恢复状态',
                               db_comment='提醒恢复安全计数；与压力结果独立'))]
