"""拆分通知渠道为「一级通道(主) / 二级通道(备)」，支持主通道失败自动切换备通道。

迁移策略（保证旧数据不丢）：
1. 新增 primary_channels / secondary_channels 两个 M2M；
2. 将历史 notify_channels 配置整体迁移到 primary_channels（旧目标继续走原渠道）；
3. 删除 notify_channels 字段（及其中间表）。
"""
from django.db import migrations, models


def copy_notify_to_primary(apps, schema_editor):
    """将历史 notify_channels 配置迁移到 primary_channels，保证旧目标不丢通知。"""
    MonitorTarget = apps.get_model('monitor', 'MonitorTarget')
    for target in MonitorTarget.objects.all().prefetch_related('notify_channels'):
        ids = list(target.notify_channels.values_list('id', flat=True))
        if ids:
            target.primary_channels.set(ids)


def revert_primary_to_notify(apps, schema_editor):
    # 破坏性字段已移除，回滚仅作占位，不做反向数据搬运。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0005_alter_monitortarget_alert_repeat_interval_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='monitortarget',
            name='primary_channels',
            field=models.ManyToManyField(
                blank=True,
                related_name='primary_targets',
                to='monitor.notificationchannel',
                verbose_name='一级通道(主)',
            ),
        ),
        migrations.AddField(
            model_name='monitortarget',
            name='secondary_channels',
            field=models.ManyToManyField(
                blank=True,
                related_name='secondary_targets',
                to='monitor.notificationchannel',
                verbose_name='二级通道(备)',
            ),
        ),
        migrations.RunPython(copy_notify_to_primary, revert_primary_to_notify),
        migrations.RemoveField(
            model_name='monitortarget',
            name='notify_channels',
        ),
    ]
