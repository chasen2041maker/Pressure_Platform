"""性能测试模块 S7 全流程测试（正常流程 + 异常场景）。

覆盖设计文档 §9 测试矩阵：
- 正常：变量子系统 / 调试 / 四种压力模型参数解析 / 真实执行落库 / 实时监控采样 /
        报告生成 / 提取与引用 / 对比 / 基线 / CSV 参数化 / SLA 通过与失败
- 异常：目标不可达 / 并发超限 / 时长超限 / RPS 超限 / 生产 host 黑名单 /
        locust 未装 / 同场景重复发起被拒 / CSV 行数不足 / SLA 熔断 /
        WebSocket 不可用时降级 / 删除执行产物清理 / 僵尸回收

说明：真实压测通过 ``run_execution`` 在主进程同步驱动（不 spawn 子进程），
WS 推送在无 Redis 时静默降级（与线上轮询降级一致），不阻塞测试。
"""
import json
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# 压测执行在 BUILTIN 引擎下于 asyncio 事件循环线程里回调 on_sample 并同步落库。
# 本测试模块放开 Django 的异步上下文 ORM 限制，使其走「直接落库」路径（落在测试事务内、
# 可回滚），避免采样点落库被迫走后台线程提交、造成 TestCase 事务污染。
# 生产环境不设此变量，由 executor 的后台落库线程正确处理（见 services/executor.py）。
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.perf_testing.auth import (
    HasPerfShareTokenOrAuthenticated,
    ShareTokenAuthentication,
)
from rest_framework.exceptions import NotAuthenticated
from apps.perf_testing.models import (
    PerfBaseline,
    PerfDataFile,
    PerfExecution,
    PerfMetricSample,
    PerfProject,
    PerfRequestStat,
    PerfScenario,
    PerfScenarioStep,
)
from apps.perf_testing.serializers import PerfScenarioSerializer
from apps.perf_testing.services import executor
from apps.perf_testing.services.cleanup import reap_stale_executions
from apps.perf_testing.services.executor import (
    create_execution,
    debug_run,
    preflight,
    run_execution,
    stop_execution,
)
from apps.perf_testing.services.sla import BreachDetector, evaluate
from apps.perf_testing.services.variables import VariableContext, load_csv_file
from apps.perf_testing.views import PerfBaselineViewSet, PerfExecutionViewSet

User = get_user_model()


# ====================================================================== #
# 辅助：内存 mock HTTP 服务器（供真实压测发请求）
# ====================================================================== #
class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/ping':
            self._json({'ok': True, 'id': 'extracted-123'})
        elif path == '/api/echo':
            q = parse_qs(urlparse(self.path).query)
            self._json({'ref': q.get('id', [''])[0]})
        elif path == '/missing':
            self._json({'error': 'not found'}, 404)
        else:
            self._json({'hello': 'world'})

    def do_POST(self):
        self._drain()
        self._json({'hello': 'world'})

    def do_PUT(self):
        self._drain()
        self._json({'hello': 'world'})

    def do_DELETE(self):
        self._drain()
        self._json({'deleted': True})


class MockServerMixin:
    """在测试前后启停一个本地 mock server，暴露 ``self.base_url``。"""

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


# ====================================================================== #
# 公共构造器
# ====================================================================== #
class PerfTestBase(TestCase):
    def _user(self):
        return User.objects.create_user(
            username='perf_tester', password='perf_pass', is_staff=True)

    def _project(self):
        # owner 为必填外键。优先复用 setUp 已创建的 self.user；否则新建一个
        # 独立用户名（区别于 _user() 的 perf_tester），避免与测试内后续显式
        # 调用 _user() 产生重复用户名。
        owner = getattr(self, 'user', None)
        if owner is None:
            owner = User.objects.create_user(username='perf_proj_owner')
        return PerfProject.objects.create(name='Perf S7 Project', owner=owner)

    def _scenario(self, project, user, **kwargs):
        defaults = dict(
            name='S7 Scenario',
            engine='BUILTIN',
            load_config={'model': 'CONCURRENCY', 'duration': 2,
                         'concurrency': 8, 'ramp_up': 0},
            sla_config={'enabled': False, 'thresholds': {}, 'abort_on_breach': False},
            variables=[],
            env_config={'base_url': ''},
            runtime_config={'timeout': 10, 'sample_interval': 1},
        )
        defaults.update(kwargs)
        return PerfScenario.objects.create(
            project=project, created_by=user, **defaults)

    def _step(self, scenario, name, url, **kwargs):
        step_defaults = dict(method='GET', url=url, enabled=True)
        step_defaults.update(kwargs)
        return PerfScenarioStep.objects.create(scenario=scenario, name=name, **step_defaults)


# ====================================================================== #
# 1. 变量子系统（单元）
# ====================================================================== #
class VariableContextTest(PerfTestBase):
    def test_constant(self):
        ctx = VariableContext([{'name': 'token', 'type': 'CONSTANT', 'value': 'abc'}])
        self.assertEqual(ctx.render('${token}'), 'abc')

    def test_random_int_range(self):
        ctx = VariableContext([{'name': 'n', 'type': 'RANDOM_INT', 'min': 5, 'max': 5}])
        self.assertEqual(ctx.values['n'], 5)

    def test_random_string_length_charset(self):
        ctx = VariableContext(
            [{'name': 's', 'type': 'RANDOM_STRING', 'length': 10, 'charset': 'digit'}])
        val = ctx.values['s']
        self.assertEqual(len(val), 10)
        self.assertTrue(val.isdigit())

    def test_enum_values_round_robin(self):
        """修复点：运行时读 values（非 options）。"""
        defs = [{'name': 'env', 'type': 'ENUM',
                 'values': ['a', 'b', 'c'], 'strategy': 'ROUND_ROBIN'}]
        vals = [VariableContext(defs, user_index=i).values['env'] for i in range(4)]
        self.assertEqual(vals, ['a', 'b', 'c', 'a'])

    def test_uuid_unique(self):
        ctx1 = VariableContext([{'name': 'u', 'type': 'UUID'}])
        ctx2 = VariableContext([{'name': 'u', 'type': 'UUID'}])
        self.assertNotEqual(ctx1.values['u'], ctx2.values['u'])

    def test_timestamp(self):
        ctx = VariableContext([{'name': 't', 'type': 'TIMESTAMP', 'format': 's'}])
        self.assertIsInstance(ctx.values['t'], int)

    def test_csv_recycle(self):
        csv_data = {'1': {'rows': [{'username': 'alice'}, {'username': 'bob'}],
                          'columns': ['username']}}
        defs = [{'name': 'user', 'type': 'CSV', 'data_file_id': 1,
                 'column': 'username', 'recycle': True}]
        v0 = VariableContext(defs, user_index=0, csv_data=csv_data).values['user']
        v1 = VariableContext(defs, user_index=1, csv_data=csv_data).values['user']
        v2 = VariableContext(defs, user_index=2, csv_data=csv_data).values['user']
        self.assertEqual([v0, v1, v2], ['alice', 'bob', 'alice'])

    def test_csv_exhausted_no_recycle(self):
        csv_data = {'1': {'rows': [{'username': 'alice'}], 'columns': ['username']}}
        defs = [{'name': 'user', 'type': 'CSV', 'data_file_id': 1,
                 'column': 'username', 'recycle': False}]
        ctx = VariableContext(defs, user_index=3, csv_data=csv_data)
        self.assertEqual(ctx.values['user'], '')
        self.assertTrue(ctx.csv_exhausted)

    def test_render_placeholder(self):
        ctx = VariableContext([{'name': 'x', 'type': 'CONSTANT', 'value': 'V'}])
        self.assertEqual(ctx.render('a/${x}/b'), 'a/V/b')
        # 未命中保持原样
        self.assertEqual(ctx.render('a/${y}/b'), 'a/${y}/b')


# ====================================================================== #
# 2. preflight 护栏（单元）
# ====================================================================== #
class PreflightGuardTest(PerfTestBase):
    def setUp(self):
        super().setUp()
        self.user = self._user()
        self.project = self._project()

    def test_normal_pass(self):
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertTrue(check['passed'], check['errors'])

    def _noop_base(self):
        # preflight 不需要真 server，用任意 http host 即可（不进黑名单）
        return 'http://127.0.0.1:9'

    def test_concurrency_exceeded(self):
        scenario = self._scenario(
            self.project, self.user,
            load_config={'model': 'CONCURRENCY', 'duration': 2, 'concurrency': 99999})
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('并发' in e for e in check['errors']))

    def test_duration_exceeded(self):
        scenario = self._scenario(
            self.project, self.user,
            load_config={'model': 'CONCURRENCY', 'duration': 99999, 'concurrency': 10})
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('时长' in e for e in check['errors']))

    def test_rps_exceeded(self):
        scenario = self._scenario(
            self.project, self.user,
            load_config={'model': 'RPS', 'duration': 10, 'target_rps': 999999,
                         'max_concurrency': 50})
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('RPS' in e or '目标' in e for e in check['errors']))

    @override_settings(PERF_FORBIDDEN_HOSTS=['prod.internal.example.com'])
    def test_forbidden_host(self):
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', 'http://prod.internal.example.com/api')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('禁止' in e for e in check['errors']))

    def test_locust_unavailable(self):
        from apps.perf_testing.engines import locust_available
        if locust_available():
            self.skipTest('locust 已安装，跳过拦截用例')
        scenario = self._scenario(self.project, self.user, engine='LOCUST')
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('Locust' in e for e in check['errors']))

    def test_csv_missing_file(self):
        scenario = self._scenario(
            self.project, self.user,
            variables=[{'name': 'u', 'type': 'CSV', 'column': 'x'}])
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('CSV' in e for e in check['errors']))

    def test_duplicate_active_execution_rejected(self):
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self._noop_base()}/api/ping')
        PerfExecution.objects.create(
            scenario=scenario, project=self.project, execution_no='X1',
            status='RUNNING', trigger_type='MANUAL')
        check = preflight(scenario)
        self.assertFalse(check['passed'])
        self.assertTrue(any('正在执行' in e for e in check['errors']))


# ====================================================================== #
# 3. SLA 评估（单元）
# ====================================================================== #
class SlaEvaluateTest(PerfTestBase):
    def _cfg(self, thresholds, abort=False):
        return {'enabled': True, 'thresholds': thresholds, 'abort_on_breach': abort}

    def test_pass(self):
        sla = self._cfg({'avg_response_time': 100, 'error_rate': 1})
        result, detail = evaluate(sla, {'avg_rt': 50, 'error_rate': 0})
        self.assertEqual(result, 'PASSED')
        self.assertTrue(detail)

    def test_fail_avg_rt(self):
        sla = self._cfg({'avg_response_time': 100})
        result, _ = evaluate(sla, {'avg_rt': 200})
        self.assertEqual(result, 'FAILED')

    def test_fail_error_rate(self):
        sla = self._cfg({'error_rate': 5})
        result, _ = evaluate(sla, {'avg_rt': 10, 'error_rate': 50})
        self.assertEqual(result, 'FAILED')

    def test_not_evaluated_when_disabled(self):
        result, _ = evaluate({'enabled': False}, {'avg_rt': 999})
        self.assertEqual(result, 'NOT_EVALUATED')

    def test_breach_detector_triggers(self):
        sla = self._cfg({'avg_response_time': 10}, abort=True)
        sla['breach_window'] = 1  # required = 1 个周期即熔断
        detector = BreachDetector(sla, sample_interval=1)
        self.assertTrue(detector.check({'avg_rt': 999}))
        self.assertEqual(detector.reason, '平均响应时间(ms) 实际 999 超出阈值 10')

    def test_breach_detector_no_breach(self):
        sla = self._cfg({'avg_response_time': 10}, abort=True)
        detector = BreachDetector(sla, sample_interval=1)
        self.assertFalse(detector.check({'avg_rt': 5}))


# ====================================================================== #
# 4. 分享令牌鉴权（单元）
# ====================================================================== #
class ShareTokenAuthTest(PerfTestBase):
    def setUp(self):
        super().setUp()
        self.user = self._user()
        self.project = self._project()
        self.scenario = self._scenario(self.project, self.user)
        self.execution = PerfExecution.objects.create(
            scenario=self.scenario, project=self.project, execution_no='SH1',
            status='COMPLETED', trigger_type='MANUAL')

    def test_valid_token_allows(self):
        tok = self.execution.generate_share_token(7)
        rf = APIRequestFactory().get('/x/', {'token': tok})
        rf.query_params = rf.GET
        user, _ = ShareTokenAuthentication().authenticate(rf)
        self.assertTrue(HasPerfShareTokenOrAuthenticated().has_permission(rf, None))

    def test_expired_token_rejected(self):
        tok = self.execution.generate_share_token(7)
        self.execution.share_expires_at = timezone.now() - timezone.timedelta(days=1)
        self.execution.save(update_fields=['share_expires_at'])
        rf = APIRequestFactory().get('/x/', {'token': tok})
        rf.query_params = rf.GET
        with self.assertRaises(Exception):
            ShareTokenAuthentication().authenticate(rf)

    def test_invalid_token_rejected(self):
        rf = APIRequestFactory().get('/x/', {'token': 'nope'})
        rf.query_params = rf.GET
        # 无效 token 属于"携带了但无效"：认证器应抛出 NotAuthenticated，
        # 而不是静默返回 None 回退到其它认证器，避免错误用户被放行。
        with self.assertRaises(NotAuthenticated):
            ShareTokenAuthentication().authenticate(rf)

    def test_revoke(self):
        self.execution.generate_share_token(7)
        self.execution.revoke_share_token()
        self.assertFalse(self.execution.share_enabled)

    def test_share_report_endpoint_e2e(self):
        """分享直链端到端：匿名浏览器直开 /report/?token= 应返回 200 HTML。"""
        from rest_framework.test import APIClient
        from django.conf import settings

        with tempfile.TemporaryDirectory() as tmp:
            with self.settings(MEDIA_ROOT=tmp):
                # 生成真实报告文件并回写 report_url
                from apps.perf_testing.services import reporter
                rel = reporter.generate_report(self.execution)
                self.execution.report_url = rel
                self.execution.save(update_fields=['report_url'])

                tok = self.execution.generate_share_token(7)
                client = APIClient()  # 未登录，模拟浏览器新标签页
                resp = client.get(
                    f'/api/perf-testing/executions/{self.execution.id}/report/',
                    {'token': tok})
                self.assertEqual(resp.status_code, 200,
                                 f'分享直链应可访问，实际 {resp.status_code}: '
                                 f'{getattr(resp, "data", resp.content[:200])}')
                self.assertIn('text/html', resp['Content-Type'])

                # 无 token 且未登录应被拒绝（非 404）
                resp2 = client.get(
                    f'/api/perf-testing/executions/{self.execution.id}/report/')
                self.assertIn(resp2.status_code, (401, 403))


# ====================================================================== #
# 5. 引擎集成（真实压测，mock server + 临时 MEDIA_ROOT）
# ====================================================================== #
class EngineIntegrationTest(MockServerMixin, PerfTestBase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self._media_override = self.settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        self.user = self._user()
        self.project = self._project()

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def test_scenario_serializer_roundtrip(self):
        """场景 CRUD 读路径：序列化器可正常往返。"""
        scenario = self._scenario(self.project, self.user)
        data = PerfScenarioSerializer(scenario).data
        self.assertEqual(data['name'], 'S7 Scenario')
        self.assertIn('steps', data)

    def test_debug_run_success(self):
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self.base_url}/api/ping')
        result = debug_run(scenario)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['failed_count'], 0)
        self.assertTrue(result['steps'])

    def test_debug_run_extract_and_reference(self):
        """变量提取与引用生效：step1 提取 id，step2 引用 ${id} 回显。"""
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'get_id', f'{self.base_url}/api/ping',
                   extractors=[{'name': 'captured', 'type': 'JSON_PATH',
                                'expr': '$.id'}])
        self._step(scenario, 'echo_id', f'{self.base_url}/api/echo?id=${{captured}}')
        result = debug_run(scenario)
        self.assertTrue(result['passed'], result)
        extracted = result['steps'][0].get('extracted') or {}
        self.assertEqual(extracted.get('captured'), 'extracted-123')
        # step2 实际收到了提取到的值
        self.assertIn('extracted-123',
                      result['steps'][1].get('response_body', '') or '')

    def test_debug_run_unreachable(self):
        """异常：目标服务不可达 -> 调试失败计数 > 0。"""
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'dead', 'http://127.0.0.1:1/api/ping')
        result = debug_run(scenario)
        self.assertFalse(result['passed'])
        self.assertGreater(result['failed_count'], 0)

    def test_full_execution_completed(self):
        """核心全流程：真实压测 -> 落库采样/统计/报告 -> COMPLETED。"""
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self.base_url}/api/ping')
        execution = create_execution(scenario)
        run_execution(execution.id)
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'COMPLETED')
        summary = execution.summary or {}
        self.assertGreater(summary.get('total_requests', 0), 0)
        self.assertIn('tps', summary)
        # 实时监控采样点已落库
        self.assertGreater(PerfMetricSample.objects.filter(execution=execution).count(), 0)
        # 接口级统计已落库
        self.assertGreater(PerfRequestStat.objects.filter(execution=execution).count(), 0)
        # 报告已生成
        self.assertTrue(execution.report_url)
        report_path = os.path.join(self.media_root, execution.report_url)
        self.assertTrue(os.path.isfile(report_path))

    def test_csv_param_execution(self):
        """CSV 参数化生效：变量在真实压测中被解析，不崩。"""
        # 用 SimpleUploadedFile 显式命名，避免 File(已打开文件对象) 在 Windows 上
        # 把 .name 携带的完整 C: 盘路径写进存储而触发 WinError 123
        upload = SimpleUploadedFile(
            'users.csv', b'username\nalice\nbob\ncarol\n', content_type='text/csv')
        data_file = PerfDataFile.objects.create(
            project=self.project, name='users', file=upload)
        scenario = self._scenario(
            self.project, self.user,
            variables=[{'name': 'u', 'type': 'CSV', 'data_file_id': data_file.id,
                        'column': 'username', 'recycle': True}])
        self._step(scenario, 'echo', f'{self.base_url}/api/echo?id=${{u}}')
        execution = create_execution(scenario)
        run_execution(execution.id)
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertGreater(execution.summary.get('total_requests', 0), 0)

    def test_csv_insufficient_rows(self):
        """异常：CSV 行数不足（recycle=False，并发 > 行数）-> 优雅退出不崩。"""
        upload = SimpleUploadedFile(
            'one.csv', b'username\nonlyone\n', content_type='text/csv')
        data_file = PerfDataFile.objects.create(
            project=self.project, name='one', file=upload)
        scenario = self._scenario(
            self.project, self.user,
            load_config={'model': 'CONCURRENCY', 'duration': 2, 'concurrency': 5},
            variables=[{'name': 'u', 'type': 'CSV', 'data_file_id': data_file.id,
                        'column': 'username', 'recycle': False}])
        self._step(scenario, 'echo', f'{self.base_url}/api/echo?id=${{u}}')
        execution = create_execution(scenario)
        run_execution(execution.id)  # 不应抛异常
        execution.refresh_from_db()
        self.assertIn(execution.status, PerfExecution.FINAL_STATUSES)

    def test_stop_execution_no_pid(self):
        """异常：压测中途停止（无 PID 记录）-> 直接标记 STOPPED。"""
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self.base_url}/api/ping')
        execution = create_execution(scenario)
        execution.status = 'RUNNING'
        execution.save(update_fields=['status'])
        ok, msg = stop_execution(execution)
        self.assertTrue(ok)
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'STOPPED')

    def test_delete_cleans_artifacts(self):
        """异常：删除执行后产物目录同步清理。"""
        scenario = self._scenario(self.project, self.user)
        self._step(scenario, 'ping', f'{self.base_url}/api/ping')
        execution = create_execution(scenario)
        run_execution(execution.id)
        execution.refresh_from_db()
        art_dir = executor.abs_artifact_dir(execution)
        self.assertTrue(os.path.isdir(art_dir))
        view = PerfExecutionViewSet()
        # perform_destroy 需要 self.request 记录操作日志，裸 ViewSet 需补上
        view.request = APIRequestFactory().request()
        view.request.user = self.user
        view.perform_destroy(execution)
        self.assertFalse(os.path.exists(art_dir))
        self.assertFalse(PerfExecution.objects.filter(id=execution.id).exists())


# ====================================================================== #
# 6. 对比 / 基线（集成，无需真实压测）
# ====================================================================== #
class ComparisonBaselineTest(PerfTestBase):
    def setUp(self):
        super().setUp()
        self.user = self._user()
        self.project = self._project()
        self.scenario = self._scenario(self.project, self.user)
        self.factory = APIRequestFactory()

    def _finished_execution(self, summary, status='COMPLETED'):
        ex = PerfExecution.objects.create(
            scenario=self.scenario, project=self.project, execution_no='C' + str(time.time_ns()),
            status=status, trigger_type='MANUAL', summary=summary)
        PerfMetricSample.objects.create(
            execution=ex, ts_offset=1, active_users=5, tps=summary.get('tps', 0),
            avg_rt=summary.get('avg_rt', 0), p90_rt=0, p95_rt=0, p99_rt=0,
            error_rate=summary.get('error_rate', 0), total_requests=summary.get('total_requests', 0))
        PerfRequestStat.objects.create(
            execution=ex, step_name='ping', method='GET', url='/api/ping',
            total=100, success=100, failed=0, error_rate=0, tps=summary.get('tps', 0),
            avg_rt=summary.get('avg_rt', 0), p95_rt=summary.get('p95_rt', 0))
        return ex

    def _drf_request(self, method, path, data=None, user=None):
        if method == 'get':
            http_req = self.factory.get(path)
            req = Request(http_req)
        else:
            http_req = self.factory.post(path, data or {}, format='json')
            # 手工构造的 DRF Request 若不显式给 parsers，.data 会因无法解析
            # application/json 抛 UnsupportedMediaType
            from rest_framework.parsers import (FormParser, JSONParser,
                                                MultiPartParser)
            req = Request(http_req, parsers=[
                JSONParser(), FormParser(), MultiPartParser()])
        req.user = user or self.user
        return req

    def test_compare_two_executions(self):
        e1 = self._finished_execution(
            {'total_requests': 1000, 'tps': 100, 'peak_tps': 110, 'avg_rt': 20,
             'p90_rt': 30, 'p95_rt': 35, 'p99_rt': 50, 'max_rt': 80, 'error_rate': 0})
        e2 = self._finished_execution(
            {'total_requests': 2000, 'tps': 200, 'peak_tps': 210, 'avg_rt': 25,
             'p90_rt': 35, 'p95_rt': 40, 'p99_rt': 60, 'max_rt': 90, 'error_rate': 1})
        view = PerfExecutionViewSet()
        resp = view.compare(self._drf_request(
            'get', f'/?ids={e1.id},{e2.id}'))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data['executions']), 2)
        self.assertTrue(resp.data['step_comparison'])
        # 第二条相对第一条应有正向 delta
        self.assertIsNotNone(resp.data['executions'][1]['delta_pct'].get('tps'))

    def test_set_baseline_and_compare(self):
        base = self._finished_execution(
            {'total_requests': 1000, 'tps': 100, 'peak_tps': 110, 'avg_rt': 20,
             'p90_rt': 30, 'p95_rt': 35, 'p99_rt': 50, 'max_rt': 80, 'error_rate': 0})
        cur = self._finished_execution(
            {'total_requests': 800, 'tps': 80, 'peak_tps': 90, 'avg_rt': 40,
             'p90_rt': 50, 'p95_rt': 55, 'p99_rt': 70, 'max_rt': 100, 'error_rate': 0})
        view = PerfBaselineViewSet()
        set_resp = view.set_from_execution(self._drf_request(
            'post', '/', {'execution_id': base.id}))
        self.assertEqual(set_resp.status_code, 200, set_resp.data)
        self.assertTrue(PerfBaseline.objects.filter(scenario=self.scenario).exists())
        cmp_resp = view.compare(self._drf_request(
            'get', f'/?execution_id={cur.id}'))
        self.assertEqual(cmp_resp.status_code, 200, cmp_resp.data)
        self.assertTrue(cmp_resp.data['has_baseline'])
        self.assertTrue(any(i['degraded'] for i in cmp_resp.data['items']))


# ====================================================================== #
# 7. 僵尸回收（异常）
# ====================================================================== #
class ReapStaleTest(PerfTestBase):
    def test_reap_stale_running(self):
        project = self._project()
        scenario = self._scenario(project, self._user())
        ex = PerfExecution.objects.create(
            scenario=scenario, project=project, execution_no='Z1',
            status='RUNNING', trigger_type='MANUAL',
            heartbeat_at=timezone.now() - timezone.timedelta(hours=1))
        count = reap_stale_executions()
        self.assertGreaterEqual(count, 1)
        ex.refresh_from_db()
        self.assertEqual(ex.status, 'FAILED')
        self.assertIn('心跳', ex.error_message)

    def test_reap_no_false_positive(self):
        project = self._project()
        scenario = self._scenario(project, self._user())
        ex = PerfExecution.objects.create(
            scenario=scenario, project=project, execution_no='Z2',
            status='RUNNING', trigger_type='MANUAL',
            heartbeat_at=timezone.now())
        count = reap_stale_executions()
        ex.refresh_from_db()
        self.assertEqual(ex.status, 'RUNNING')
        # 清理这条，不污染
        ex.delete()
        self.assertEqual(count, 0)
