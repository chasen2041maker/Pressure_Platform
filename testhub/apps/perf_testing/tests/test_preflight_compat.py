"""executor.preflight 引擎-压力模型兼容性与权重告警的 DB-free 单测。

背景：Locust/JMeter 引擎只实现了固定并发模型，RAMPING/RPS/SPIKE 配置会被
静默忽略，导致报告的请求次数与并发数同用户输入严重不符（历史缺陷）。
预检必须显式拦截这类组合；步骤权重(weight)目前无引擎消费，需给出告警。

preflight 依赖 settings 与 PerfExecution 查询，这里用项目 settings 初始化
Django（仅加载应用注册表，不访问数据库）+ mock 掉 ORM 查询，
保持与 test_executor_dispatch 相同的 DB-free 风格。
"""
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from django.conf import settings  # noqa: E402

if not settings.configured:
    # 独立运行（unittest discover）时按项目 settings 初始化；
    # manage.py test 下 settings 已就绪，直接跳过。
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
    import django  # noqa: E402
    django.setup()

from apps.perf_testing.services import executor as executor_module  # noqa: E402


class FakeStep:
    """最小步骤桩：仅提供 preflight 读取的属性。"""

    def __init__(self, name='step-1', is_setup=False, weight=1,
                 url='http://target.example.com/api'):
        self.name = name
        self.is_setup = is_setup
        self.weight = weight
        self.url = url
        self.files = []


class FakeStepManager:

    def __init__(self, steps):
        self._steps = steps

    def filter(self, **kwargs):  # noqa: ARG002 - 兼容 filter(enabled=True) 调用
        return list(self._steps)


class FakeScenario:
    """最小场景桩：提供 preflight 所需属性，规避一切真实 ORM。"""

    def __init__(self, engine='BUILTIN', steps=None):
        self.id = 1
        self.project_id = 1
        self.created_by = None
        self.engine = engine
        self.steps = FakeStepManager(steps if steps is not None else [FakeStep()])
        self.env_config = {}
        self.variables = []

    def get_runtime_config(self):
        return {}

    def has_active_execution(self):
        return False


def _patch_env():
    """统一屏蔽引擎可用性探测与并发执行数查询。"""
    return (
        mock.patch('apps.perf_testing.engines.locust_available', return_value=True),
        mock.patch('apps.perf_testing.engines.jmeter_available', return_value=True),
        mock.patch('apps.perf_testing.models.PerfExecution.objects'),
    )


class TestEngineModelCompatibility(unittest.TestCase):

    def _preflight(self, engine, load_config, steps=None):
        scenario = FakeScenario(engine=engine, steps=steps)
        p1, p2, p3 = _patch_env()
        with p1, p2, p3 as mock_objects:
            mock_objects.filter.return_value.count.return_value = 0
            return executor_module.preflight(scenario, load_config=load_config)

    def test_locust_ramping_is_blocked(self):
        cfg = {'model': 'RAMPING', 'stages': [{'duration': 30, 'target': 20}]}
        result = self._preflight('LOCUST', cfg)
        self.assertFalse(result['passed'])
        self.assertTrue(any('仅支持「固定并发」' in e for e in result['errors']))

    def test_jmeter_rps_is_blocked(self):
        cfg = {'model': 'RPS', 'target_rps': 100, 'duration': 60, 'max_concurrency': 0}
        result = self._preflight('JMETER', cfg)
        self.assertFalse(result['passed'])
        self.assertTrue(any('仅支持「固定并发」' in e for e in result['errors']))

    def test_locust_spike_is_blocked(self):
        cfg = {'model': 'SPIKE', 'baseline_concurrency': 5, 'spike_concurrency': 50,
               'spike_duration': 10, 'spike_times': 2}
        result = self._preflight('LOCUST', cfg)
        self.assertFalse(result['passed'])
        self.assertTrue(any('仅支持「固定并发」' in e for e in result['errors']))

    def test_locust_concurrency_still_passes(self):
        cfg = {'model': 'CONCURRENCY', 'concurrency': 10, 'duration': 30, 'ramp_up': 0}
        result = self._preflight('LOCUST', cfg)
        self.assertTrue(result['passed'], msg=result['errors'])

    def test_builtin_ramping_not_affected(self):
        cfg = {'model': 'RAMPING', 'stages': [{'duration': 30, 'target': 20}]}
        result = self._preflight('BUILTIN', cfg)
        self.assertTrue(result['passed'], msg=result['errors'])


class TestWeightWarning(unittest.TestCase):

    def test_weighted_step_emits_warning(self):
        scenario = FakeScenario(engine='BUILTIN',
                                steps=[FakeStep(name='weighted-api', weight=3)])
        cfg = {'model': 'CONCURRENCY', 'concurrency': 5, 'duration': 30, 'ramp_up': 0}
        p1, p2, p3 = _patch_env()
        with p1, p2, p3 as mock_objects:
            mock_objects.filter.return_value.count.return_value = 0
            result = executor_module.preflight(scenario, load_config=cfg)
        self.assertTrue(result['passed'])
        self.assertTrue(any('权重不会生效' in w for w in result['warnings']))

    def test_default_weight_no_warning(self):
        scenario = FakeScenario(engine='BUILTIN', steps=[FakeStep(weight=1)])
        cfg = {'model': 'CONCURRENCY', 'concurrency': 5, 'duration': 30, 'ramp_up': 0}
        p1, p2, p3 = _patch_env()
        with p1, p2, p3 as mock_objects:
            mock_objects.filter.return_value.count.return_value = 0
            result = executor_module.preflight(scenario, load_config=cfg)
        self.assertTrue(result['passed'])
        self.assertFalse(any('权重' in w for w in result['warnings']))


if __name__ == '__main__':
    unittest.main()
