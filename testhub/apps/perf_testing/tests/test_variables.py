"""VariableContext 与 URL 变量解析的 DB-free 单测。

覆盖从接口测试导入场景后常见的 {{baseUrl}} / {{base_url}} 占位符问题，
以及 BuiltinEngine 对相对/绝对 URL 的拼接兼容。
"""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.services.variables import VariableContext  # noqa: E402
from apps.perf_testing.engines.builtin import BuiltinEngine  # noqa: E402


class TestVariableContext(unittest.TestCase):
    """VariableContext 占位符解析。"""

    def test_render_dollar_brace(self):
        ctx = VariableContext([{'name': 'token', 'type': 'CONSTANT', 'value': 'abc'}])
        self.assertEqual(ctx.render('Bearer ${token}'), 'Bearer abc')

    def test_render_double_brace(self):
        ctx = VariableContext([{'name': 'token', 'type': 'CONSTANT', 'value': 'abc'}])
        self.assertEqual(ctx.render('Bearer {{token}}'), 'Bearer abc')

    def test_render_double_brace_in_dict(self):
        ctx = VariableContext([{'name': 'token', 'type': 'CONSTANT', 'value': 'abc'}])
        self.assertEqual(
            ctx.render_dict({'Authorization': 'Bearer {{token}}'}),
            {'Authorization': 'Bearer abc'})

    def test_base_url_injected_as_both_names(self):
        ctx = VariableContext([], base_url='http://example.com')
        self.assertEqual(ctx.render('{{baseUrl}}/login'), 'http://example.com/login')
        self.assertEqual(ctx.render('{{base_url}}/login'), 'http://example.com/login')

    def test_user_defined_baseUrl_takes_precedence(self):
        ctx = VariableContext(
            [{'name': 'baseUrl', 'type': 'CONSTANT', 'value': 'http://user.com'}],
            base_url='http://env.com')
        self.assertEqual(ctx.render('{{baseUrl}}/login'), 'http://user.com/login')

    def test_unknown_double_brace_left_unchanged(self):
        ctx = VariableContext([], base_url='http://example.com')
        self.assertEqual(ctx.render('{{unknown}}/x'), '{{unknown}}/x')


class TestBuiltinBuildUrl(unittest.TestCase):
    """BuiltinEngine._build_url 的 URL 拼接与变量解析。"""

    def _engine(self, base_url='http://example.com'):
        return BuiltinEngine({
            'env_config': {'base_url': base_url},
            'load_config': {'concurrency': 1, 'duration': 1},
            'steps': [],
        })

    def test_relative_url_prepended(self):
        engine = self._engine()
        ctx = VariableContext([], base_url='http://example.com')
        self.assertEqual(engine._build_url('/login', ctx), 'http://example.com/login')

    def test_double_brace_baseUrl_resolved(self):
        engine = self._engine()
        ctx = VariableContext([], base_url='http://example.com')
        self.assertEqual(engine._build_url('{{baseUrl}}/login', ctx), 'http://example.com/login')

    def test_leading_slash_with_absolute_variable(self):
        """兼容数据层错误：/{{baseUrl}}/login 应被修正为 http://example.com/login。"""
        engine = self._engine()
        ctx = VariableContext([], base_url='http://example.com')
        self.assertEqual(engine._build_url('/{{baseUrl}}/login', ctx), 'http://example.com/login')


if __name__ == '__main__':
    unittest.main()
