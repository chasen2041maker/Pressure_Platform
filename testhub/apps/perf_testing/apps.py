from django.apps import AppConfig


class PerfTestingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.perf_testing'
    verbose_name = '性能测试'

    def ready(self):
        """启动自愈：回收 Django 重启前遗留的僵尸执行。

        仅在主进程执行一次（避免 runserver 自动重载时重复执行），
        且失败不得影响服务启动。
        """
        import os
        import sys
        from django.db.models.signals import pre_delete
        from .models import PerfExecution
        from .services.reminder_recovery import protect_execution
        pre_delete.connect(protect_execution, sender=PerfExecution,
                           dispatch_uid='portfolio_reminder_recovery_retention', weak=False)

        # runserver 自动重载会启动两个进程，只在真正的工作进程执行
        if os.environ.get('RUN_MAIN') == 'false':
            return
        # Django creates the test database after ready(); never reap the live default first.
        if len(sys.argv) > 1 and sys.argv[1] == 'test':
            return
        # 迁移/收集静态等管理命令不做自愈
        argv = ' '.join(sys.argv)
        for skip in ('makemigrations', 'migrate', 'collectstatic', 'run_perf_execution', 'recover_portfolio_reminders', 'shell'):
            if skip in argv:
                return

        try:
            from apps.perf_testing.services.cleanup import reap_stale_executions
            reap_stale_executions(startup=True)
        except Exception:  # noqa: BLE001 - 启动自愈失败不能阻断服务
            pass
