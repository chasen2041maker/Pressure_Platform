"""压测数据清理：python manage.py cleanup_perf_data [--dry-run]

建议挂到每日 crontab。也会顺带回收僵尸执行。
"""
from django.core.management.base import BaseCommand

from apps.perf_testing.services.cleanup import cleanup_perf_data, reap_stale_executions


class Command(BaseCommand):
    help = '清理过期的压测执行记录与产物文件，并回收僵尸执行'

    def add_arguments(self, parser):
        parser.add_argument('--retention-days', type=int, default=None,
                            help='执行记录保留天数（默认取 settings.PERF_RETENTION_DAYS）')
        parser.add_argument('--artifact-days', type=int, default=None,
                            help='产物文件保留天数（默认取 settings.PERF_ARTIFACT_RETENTION_DAYS）')
        parser.add_argument('--dry-run', action='store_true', help='只统计不删除')
        parser.add_argument('--skip-reap', action='store_true', help='跳过僵尸执行回收')

    def handle(self, *args, **options):
        if not options['skip_reap']:
            reaped = reap_stale_executions()
            self.stdout.write(f'回收僵尸执行：{reaped} 条')

        result = cleanup_perf_data(
            retention_days=options['retention_days'],
            artifact_days=options['artifact_days'],
            dry_run=options['dry_run'],
        )
        prefix = '[DRY-RUN] ' if result['dry_run'] else ''
        mb = result['bytes_freed'] / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}清理产物目录 {result["artifacts_removed"]} 个（释放 {mb:.2f} MB），'
            f'删除执行记录 {result["executions_removed"]} 条'))
