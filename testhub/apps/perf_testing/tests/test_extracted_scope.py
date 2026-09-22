"""debug_run 的 extracted 字段作用域测试（DB-free 引擎级）。

复现用户缺陷：压测调试「提取结果」里出现 base_url/baseUrl，疑似被硬编码。
根因是 extracted 返回了整个 ctx.values（含环境基址注入、场景变量、前序步骤
提取值），而非仅本步骤提取器实际写入的变量。

本测试直接驱动 engines.builtin.debug_run + mock httpx.AsyncClient，断言：
  1. extracted 只含本步骤提取器写入的变量；
  2. 环境注入的 base_url/baseUrl 不混入 extracted；
  3. 前序步骤提取的变量不混入后续步骤的 extracted；
  4. context_snapshot 字段保留全量上下文供调试查看。
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.engines.builtin import debug_run  # noqa: E402


class FakeResponse:
    """模拟 httpx.Response 的最小子集。"""

    def __init__(self, status=200, json_data=None, text='', headers=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.text = text if text else json.dumps(self._json)
        self.headers = headers or {'Content-Type': 'application/json'}
        self.content = self.text.encode('utf-8')

    def json(self):
        return self._json


class _FakeClient:
    """模拟 httpx.AsyncClient 的 async context manager 子集。"""

    def __init__(self, responder):
        self._responder = responder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        return self._responder(method, url, **kwargs)


def _patch_httpx(responder):
    """返回一个 patch 上下文，把 builtin 内的 httpx.AsyncClient 替换为假客户端。"""
    return mock.patch(
        'apps.perf_testing.engines.builtin.httpx.AsyncClient',
        lambda **kw: _FakeClient(responder),
    )


def _login_response():
    """模拟 jar 服务登录接口的真实响应（含 base_url/baseUrl/token/code）。"""
    return FakeResponse(200, {
        'base_url': 'http://192.168.11.64:8087/jar',
        'baseUrl': 'http://192.168.11.64:8087/jar',
        'token': 'eyJhbGciOiJIUzUxMiJ9',
        'code': 200,
    })


class TestExtractedScope(unittest.TestCase):
    """extracted 字段应仅包含本步骤提取器写入的变量。"""

    def test_extracted_excludes_env_injected_base_url(self):
        """环境注入的 base_url/baseUrl 不得出现在 extracted 里。"""
        snapshot = {
            'env_config': {'base_url': 'http://192.168.11.64:8087/jar'},
            'runtime_config': {'timeout': 5},
            'variables': [],
            'steps': [{
                'name': 'login', 'method': 'POST',
                'url': '{{base_url}}/login',
                'body_type': 'JSON', 'body': '{}',
                'enabled': True,
                'extractors': [
                    {'name': 'token', 'type': 'JSON_PATH', 'expr': '$.token'},
                ],
            }],
        }
        with _patch_httpx(lambda m, u, **kw: _login_response()):
            result = asyncio.run(debug_run(snapshot))

        step0 = result[0]
        extracted = step0.get('extracted') or {}

        self.assertEqual(
            extracted, {'token': 'eyJhbGciOiJIUzUxMiJ9'},
            f'extracted 应只含本步骤提取的 token，实际={extracted!r}')
        self.assertNotIn('base_url', extracted, '环境注入的 base_url 混入 extracted')
        self.assertNotIn('baseUrl', extracted, '环境注入的 baseUrl 混入 extracted')

        # context_snapshot 保留全量上下文（含环境注入），供调试查看
        ctx_snapshot = step0.get('context_snapshot') or {}
        self.assertEqual(ctx_snapshot.get('base_url'), 'http://192.168.11.64:8087/jar')
        self.assertEqual(ctx_snapshot.get('baseUrl'), 'http://192.168.11.64:8087/jar')
        self.assertEqual(ctx_snapshot.get('token'), 'eyJhbGciOiJIUzUxMiJ9')

    def test_extracted_empty_when_no_extractor(self):
        """步骤未配提取器时 extracted 为空，context_snapshot 仍保留上下文。"""
        snapshot = {
            'env_config': {'base_url': 'http://192.168.11.64:8087/jar'},
            'runtime_config': {'timeout': 5},
            'variables': [],
            'steps': [{
                'name': 'ping', 'method': 'GET',
                'url': '{{base_url}}/ping',
                'enabled': True,
            }],
        }
        with _patch_httpx(lambda m, u, **kw: FakeResponse(200, {'ok': True})):
            result = asyncio.run(debug_run(snapshot))

        step0 = result[0]
        self.assertEqual(step0.get('extracted') or {}, {})
        # 环境基址仍在上下文快照里
        self.assertEqual(
            (step0.get('context_snapshot') or {}).get('base_url'),
            'http://192.168.11.64:8087/jar')

    def test_extracted_does_not_leak_prior_step_variables(self):
        """前序步骤提取的 token 不得混入后续步骤的 extracted。"""
        snapshot = {
            'env_config': {'base_url': 'http://192.168.11.64:8087/jar'},
            'runtime_config': {'timeout': 5},
            'variables': [],
            'steps': [
                {
                    'name': 'login', 'method': 'POST',
                    'url': '{{base_url}}/login',
                    'body_type': 'JSON', 'body': '{}',
                    'enabled': True,
                    'extractors': [
                        {'name': 'token', 'type': 'JSON_PATH', 'expr': '$.token'},
                    ],
                },
                {
                    'name': 'secure', 'method': 'GET',
                    'url': '{{base_url}}/secure',
                    'enabled': True,
                    'headers': {'Authorization': 'Bearer {{token}}'},
                },
            ],
        }

        def responder(method, url, **kw):
            if url.endswith('/login'):
                return _login_response()
            return FakeResponse(200, {'ok': True})

        with _patch_httpx(responder):
            result = asyncio.run(debug_run(snapshot))

        step1 = result[1]
        extracted1 = step1.get('extracted') or {}
        # step1 没有提取器 -> extracted 必须为空，不能混入 step0 提取的 token
        self.assertEqual(
            extracted1, {},
            f'后续步骤 extracted 不得混入前序提取值，实际={extracted1!r}')
        # 但上下文快照里应能看到 step0 提取的 token（供调试）
        ctx1 = step1.get('context_snapshot') or {}
        self.assertEqual(ctx1.get('token'), 'eyJhbGciOiJIUzUxMiJ9')


if __name__ == '__main__':
    unittest.main(verbosity=2)
