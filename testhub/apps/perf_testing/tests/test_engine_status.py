"""engine_status 注册 JMETER 的回归测试（DB-free）。"""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestEngineStatus(unittest.TestCase):
    def test_jmeter_registered(self):
        from apps.perf_testing.engines import engine_status, get_engine_class, ENGINES
        self.assertIn('JMETER', ENGINES)
        self.assertIs(ENGINES['JMETER'], __import__(
            'apps.perf_testing.engines.jmeter_engine', fromlist=['JmeterEngine']).JmeterEngine)

    def test_status_structure_and_jmeter_entry(self):
        from apps.perf_testing.engines import engine_status
        statuses = {s['name']: s for s in engine_status()}
        self.assertIn('BUILTIN', statuses)
        self.assertIn('LOCUST', statuses)
        self.assertIn('JMETER', statuses)
        jmeter = statuses['JMETER']
        self.assertEqual(jmeter['label'], 'JMeter')
        # available 必须是布尔（本机若无 jmeter 应为 False）
        self.assertIsInstance(jmeter['available'], bool)
        for key in ('name', 'label', 'available', 'version', 'description'):
            self.assertIn(key, jmeter)

    def test_get_engine_class_jmeter(self):
        from apps.perf_testing.engines import get_engine_class
        from apps.perf_testing.engines.base import EngineError
        self.assertEqual(get_engine_class('JMETER').__name__, 'JmeterEngine')
        with self.assertRaises(EngineError):
            get_engine_class('NOPE')


class TestWebsocketAvailable(unittest.TestCase):
    """EngineStatusView._websocket_available 必须真实探测 Redis 可达性。

    不能只因 CHANNEL_LAYERS 已配置就判 True——否则 Redis 不可达时前端连上
    “假” WebSocket、实时数据一片空白。
    """

    def _run(self, connect_ok=True, layer_ok=True):
        from unittest import mock
        views = __import__('apps.perf_testing.views', fromlist=['EngineStatusView'])

        class _Sock:
            def __init__(self, *_a, **_k):
                pass

            def settimeout(self, *_a):
                pass

            def close(self):
                pass

            def connect(self, addr):
                if not connect_ok:
                    raise OSError('refused')

        layer = mock.MagicMock() if layer_ok else None
        with mock.patch('socket.socket', new=lambda *a, **k: _Sock()), \
                mock.patch('channels.layers.get_channel_layer', return_value=layer):
            return views.EngineStatusView._websocket_available()

    def test_returns_false_when_redis_unreachable(self):
        self.assertFalse(self._run(connect_ok=False))

    def test_returns_true_when_redis_reachable(self):
        self.assertTrue(self._run(connect_ok=True))

    def test_returns_false_when_no_channel_layer(self):
        self.assertFalse(self._run(connect_ok=True, layer_ok=False))


if __name__ == '__main__':
    unittest.main()
