"""采样点落库回归测试（独立、可隔离）。

复现并锁定线上缺陷：BUILTIN 引擎以 asyncio.run 驱动，on_sample 回调跑在事件循环
所在线程；Django 禁止在异步上下文同步访问数据库（SynchronousOnlyOperation:
"You cannot call this from an async context"）。历史上 _flush 把该异常吞掉并告警，
导致采样点被静默丢弃、时序曲线全空。

修复后，executor 把落库搬到一个独立后台线程；仅当「处于异步上下文 且 未放开
DJANGO_ALLOW_ASYNC_UNSAFE」时才走后台线程，其余（同步上下文 / 测试放开）直接落库。

本文件两个测试：
1. DbFlushWorkerTest —— 直接验证后台落库线程能把一批采样点持久化。
2. BuiltinAsyncFlushTest —— 复现线上条件（异步上下文 + 未放开 ALLOW_ASYNC_UNSAFE），
   端到端跑一次真实 BUILTIN 压测，断言采样点经后台线程落库（不再丢失）。

注意：后台线程的提交不在 TestCase 事务内，本文件统一用 TransactionTestCase（按表
截断清理），否则 TestCase 回滚会把执行记录复活、留下孤儿采样行。
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from apps.perf_testing.models import (
    PerfExecution,
    PerfMetricSample,
    PerfProject,
    PerfScenario,
    PerfScenarioStep,
)
from apps.perf_testing.services import executor
from apps.perf_testing.services.executor import create_execution, run_execution

User = get_user_model()


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _json(self, obj, code=200):
        import json
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json({'ok': True})


class DbFlushWorkerTest(TransactionTestCase):
    """直接验证 _DbFlushWorker 后台线程落库。"""

    def test_worker_persists_batch(self):
        owner = User.objects.create_user(username='dbflush_owner')
        project = PerfProject.objects.create(name='db-flush', owner=owner)
        scenario = PerfScenario.objects.create(
            project=project, created_by=owner, name='s', engine='BUILTIN')
        execution = PerfExecution.objects.create(
            scenario=scenario, project=project, status='RUNNING')

        batch = [
            PerfMetricSample(execution_id=execution.id, ts_offset=i, tps=float(i), avg_rt=1.0)
            for i in range(5)
        ]
        executor._db_flush_worker.submit(execution.id, batch)
        executor._db_flush_worker.flush(timeout=5.0)

        self.assertEqual(
            PerfMetricSample.objects.filter(execution=execution).count(), 5)
        execution.delete()


class BuiltinAsyncFlushTest(TransactionTestCase):
    """复现线上：异步上下文 + 未放开 ALLOW_ASYNC_UNSAFE，采样点应经后台线程落库。"""

    def setUp(self):
        super().setUp()
        self._httpd = HTTPServer(('127.0.0.1', 0), _MockHandler)
        self.port = self._httpd.server_address[1]
        self.base_url = f'http://127.0.0.1:{self.port}'
        self._srv_thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._srv_thread.start()

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        super().tearDown()

    def _make_execution(self):
        user = User.objects.create_user(username='bf_owner')
        project = PerfProject.objects.create(name='bf', owner=user)
        scenario = PerfScenario.objects.create(
            project=project, created_by=user, name='bf', engine='BUILTIN',
            load_config={'model': 'CONCURRENCY', 'duration': 2,
                         'concurrency': 4, 'ramp_up': 0},
            sla_config={'enabled': False, 'thresholds': {}, 'abort_on_breach': False},
            variables=[], env_config={'base_url': ''},
            runtime_config={'timeout': 10, 'sample_interval': 1})
        PerfScenarioStep.objects.create(
            scenario=scenario, name='ping', method='GET',
            url=f'{self.base_url}/api/ping', enabled=True)
        return create_execution(scenario)

    def test_samples_persist_async_without_allow_flag(self):
        # 复现线上条件：异步上下文 + 未放开 ALLOW_ASYNC_UNSAFE -> 走后台线程落库
        old = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'false'
        try:
            execution = self._make_execution()
            run_execution(execution.id)
        finally:
            if old is None:
                os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
            else:
                os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = old

        execution.refresh_from_db()
        self.assertIn(execution.status, PerfExecution.FINAL_STATUSES)
        # 关键断言：异步上下文下采样点仍落库，未因 SynchronousOnlyOperation 丢失
        self.assertGreater(
            PerfMetricSample.objects.filter(execution=execution).count(), 0)
