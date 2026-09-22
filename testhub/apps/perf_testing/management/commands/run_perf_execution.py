"""压测子进程入口：python manage.py run_perf_execution <execution_id>

由 services.executor.spawn_execution 通过 subprocess 拉起，
也可以在排障时手动执行以便直接看到完整日志。
"""
from django.core.management.base import BaseCommand, CommandError

from apps.perf_testing.models import PerfExecution
from apps.perf_testing.services.executor import run_execution


class Command(BaseCommand):
    help = '执行一次压测（压测子进程入口，通常由平台自动调用）'

    def add_arguments(self, parser):
        parser.add_argument('execution_id', type=int, help='PerfExecution 主键')

    def handle(self, *args, **options):
        execution_id = options['execution_id']

        try:
            execution = PerfExecution.objects.get(id=execution_id)
        except PerfExecution.DoesNotExist:
            raise CommandError(f'压测执行记录 {execution_id} 不存在')

        if execution.status in PerfExecution.FINAL_STATUSES:
            self.stdout.write(self.style.WARNING(
                f'执行 {execution.execution_no} 已处于终态 {execution.status}，跳过'))
            return

        self.stdout.write(f'开始执行压测 {execution.execution_no}')
        try:
            execution = run_execution(execution_id, stdout=self.stdout)
        except Exception as exc:  # noqa: BLE001 - 兜底保证记录不悬挂
            from django.utils import timezone
            PerfExecution.objects.filter(id=execution_id).update(
                status='FAILED',
                error_message=f'执行入口异常：{type(exc).__name__}: {exc}'[:5000],
                end_time=timezone.now(),
            )
            raise CommandError(f'压测执行失败：{exc}')

        style = self.style.SUCCESS if execution.status == 'COMPLETED' else self.style.WARNING
        self.stdout.write(style(
            f'压测 {execution.execution_no} 结束：{execution.status}，SLA {execution.sla_result}'))
