from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('perf_testing', '0004_perfexecution_script_ref_alter_perfscenario_engine'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfdatafile',
            name='file_type',
            field=models.CharField(choices=[('CSV', 'CSV 参数化数据'), ('JMX', 'JMeter 脚本')],
                                   db_comment='文件类型：CSV参数化数据/JMX脚本', default='CSV',
                                   max_length=10, verbose_name='文件类型'),
        ),
        migrations.AddField(
            model_name='perfdatafile',
            name='meta',
            field=models.JSONField(blank=True, db_comment='文件解析摘要，JMX 存线程组/采样器/目标域名等',
                                   default=dict, verbose_name='解析元信息'),
        ),
        migrations.AlterField(
            model_name='perfdatafile',
            name='columns',
            field=models.JSONField(blank=True, db_comment='首行解析出的列名(仅CSV)', default=list,
                                   verbose_name='列名'),
        ),
        migrations.AlterField(
            model_name='perfdatafile',
            name='row_count',
            field=models.IntegerField(db_comment='数据行数(不含表头，仅CSV)', default=0,
                                      verbose_name='数据行数'),
        ),
    ]
