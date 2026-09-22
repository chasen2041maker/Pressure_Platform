"""token 跨接口共享复现测试（DB-free 引擎级）。

复现用户缺陷：性能测试 BUILTIN 引擎中，登录步骤从响应提取 token 后，
后续接口未能引用 {{token}}，导致 token 无法跨接口共享。

精确复现 _virtual_user 的时序：
  setup_steps(login, 提取 $.token)  ->  while: ctx.refresh()  ->  main_steps(secure, 引用 {{token}})

不依赖 Django/DB，直接驱动 BuiltinEngine._virtual_user + mock httpx client。
"""
import asyncio
import json
import os
import sys
import time
import unittest
from types import SimpleNamespace

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.engines.builtin import BuiltinEngine  # noqa: E402


class FakeResponse:
    """模拟 httpx.Response 的最小子集。"""

    def __init__(self, status=200, json_data=None, text='', headers=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.text = text if text else json.dumps(self._json)
        self.headers = headers or {}
        self.content = self.text.encode('utf-8')

    def json(self):
        return self._json


def _make_engine(variables, max_requests=1):
    """构造一个最小 BuiltinEngine：1 个 setup(登录提取 token) + 1 个 main(引用 token)。"""
    snapshot = {
        'env_config': {'base_url': 'http://127.0.0.1', 'verify_ssl': False},
        'load_config': {
            'model': 'CONCURRENCY', 'concurrency': 1,
            'duration': 1, 'ramp_up': 0, 'max_requests': max_requests,
        },
        'runtime_config': {'timeout': 5, 'sample_interval': 1},
        'variables': variables,
        'steps': [
            {
                'name': 'login', 'method': 'POST', 'url': '/login',
                'enabled': True, 'is_setup': True,
                'extractors': [
                    {'name': 'token', 'type': 'JSON_PATH', 'expr': '$.token'},
                ],
            },
            {
                'name': 'secure', 'method': 'GET', 'url': '/secure',
                'enabled': True, 'is_setup': False,
                'headers': {'Authorization': 'Bearer {{token}}'},
            },
        ],
    }
    return BuiltinEngine(snapshot, on_sample=lambda s: None,
                         on_log=lambda m, l: None)


def _run_token_sharing(variables):
    """跑一轮 setup(login) + main(secure)，返回 secure 实际收到的 Authorization 头。"""
    engine = _make_engine(variables, max_requests=1)
    captured = {'auth': None, 'login_called': False, 'secure_called': False}

    async def fake_request(method, url, **kw):
        if '/login' in url:
            captured['login_called'] = True
            return FakeResponse(200, {'token': 'real-xyz'})
        if '/secure' in url:
            captured['secure_called'] = True
            captured['auth'] = (kw.get('headers') or {}).get('Authorization')
            return FakeResponse(200, {'ok': True})
        return FakeResponse(404, {'error': 'not found'})

    engine._client = SimpleNamespace(request=fake_request, is_closed=False)
    engine._start_ts = time.time()

    asyncio.run(engine._virtual_user(0))
    return captured


class TestTokenSharing(unittest.TestCase):
    """token 跨接口共享：setup 提取 -> main 引用。"""

    def test_shared_when_no_placeholder_defined(self):
        """场景B：用户未在场景变量里定义 token 占位 -> 后续接口应正确引用提取值。"""
        captured = _run_token_sharing(variables=[])
        self.assertTrue(captured['login_called'], '登录步骤未执行')
        self.assertTrue(captured['secure_called'], '业务步骤未执行')
        self.assertEqual(
            captured['auth'], 'Bearer real-xyz',
            f'未定义占位时 token 应能共享，实际 Authorization={captured["auth"]!r}')

    def test_shared_when_placeholder_defined(self):
        """场景A：用户在场景变量里定义了 token 占位(CONSTANT空值) -> 提取值不应被 refresh 覆盖。

        这是用户缺陷的核心复现：主循环每轮 ctx.refresh() 重新求值场景变量定义，
        会把同名 token 重置回占位空值，导致后续接口拿到 'Bearer '(空 token)。
        """
        captured = _run_token_sharing(
            variables=[{'name': 'token', 'type': 'CONSTANT', 'value': ''}])
        self.assertTrue(captured['login_called'], '登录步骤未执行')
        self.assertTrue(captured['secure_called'], '业务步骤未执行')
        self.assertEqual(
            captured['auth'], 'Bearer real-xyz',
            f'定义占位时提取的 token 被 refresh 覆盖，实际 Authorization={captured["auth"]!r}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
