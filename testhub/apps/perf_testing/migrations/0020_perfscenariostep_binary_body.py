from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('perf_testing', '0019_perfscenariostep_execution_policy')]
    operations = [migrations.AlterField(model_name='perfscenariostep', name='body_type',
        field=models.CharField(blank=False, choices=[('NONE', '无'), ('JSON', 'JSON'), ('FORM', '表单'),
            ('BINARY', '原始文件'), ('RAW', '原始文本'), ('XML', 'XML')], db_comment='请求体类型',
            default='NONE', max_length=20, verbose_name='请求体类型'))]
