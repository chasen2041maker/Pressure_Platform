"""executor.debug_run 引擎分发的 DB-free 单测（mock 掉快照与引擎调试函数）。

executor 大量逻辑依赖 DB（场景/步骤/CSV 预加载），无法走 unittest.TestCase 之外的
远程 DB；这里只测「按 scenario.engine 分发到对应引擎调试入口」这一薄分支，
引擎本身的指标解析由 test_jmeter_engine / test_jmx_builder 覆盖。

使用绝对导入（apps.perf_testing...）以兼容 unittest discover 与 manage.py test 两种发现方式。
"""
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing import engines  # noqa: E402
from apps.perf_testing.services import executor as executor_module  # noqa: E402
from apps.perf_testing.engines import builtin as builtin_engine  # noqa: E402


class FakeScenario:
    """最小场景桩：仅提供 debug_run 分发所需的属性。"""

    def __init__(self, engine='BUILTIN'):
        self.id = 1
        self.name = 'fake-scenario'
        self.engine = engine


class TestDebugRunDispatch(unittest.TestCase):

    def _patch_snapshot(self):
        return mock.patch.object(
            executor_module, 'build_snapshot',
            return_value={'load_config': {}, 'variables': [], 'steps': [{'name': 's1'}]})

    def test_jmeter_dispatch_calls_jmeter_debug_run(self):
        scenario = FakeScenario(engine='JMETER')
        with self._patch_snapshot(), \
                mock.patch.object(engines, 'jmeter_debug_run',
                                  return_value={'passed': True, 'jmx_valid': True,
                                                'steps': [{'name': 's1', 'ok': True}]}) as m_jm:
            result = executor_module.debug_run(scenario)
        self.assertTrue(m_jm.called)
        self.assertEqual(result['engine'], 'JMETER')
        self.assertTrue(result['jmx_valid'])
        self.assertEqual(result['total_steps'], 1)
        self.assertIn('steps', result)

    def test_builtin_dispatch_calls_builtin_debug_run(self):
        scenario = FakeScenario(engine='BUILTIN')
        fake_steps = [{'name': 's1', 'success': True}]
        with self._patch_snapshot(), \
                mock.patch.object(builtin_engine, 'debug_run',
                                  new=mock.AsyncMock(return_value=fake_steps)) as m_builtin:
            result = executor_module.debug_run(scenario)
        self.assertTrue(m_builtin.called)
        self.assertEqual(result['engine'], 'BUILTIN')
        self.assertTrue(result['passed'])
        self.assertEqual(result['failed_count'], 0)

    def test_locust_falls_back_to_builtin_debug_run(self):
        scenario = FakeScenario(engine='LOCUST')
        fake_steps = [{'name': 's1', 'success': True}]
        with self._patch_snapshot(), \
                mock.patch.object(builtin_engine, 'debug_run',
                                  new=mock.AsyncMock(return_value=fake_steps)) as m_builtin:
            result = executor_module.debug_run(scenario)
        self.assertTrue(m_builtin.called)
        self.assertEqual(result['engine'], 'LOCUST')


if __name__ == '__main__':
    unittest.main()
