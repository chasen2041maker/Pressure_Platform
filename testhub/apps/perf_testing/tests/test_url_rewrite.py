"""DB-free 单测：baseUrl/base_url 变量引用重写（纯函数，直接 unittest 跑）。"""

import unittest

from apps.perf_testing.services.url_rewrite import (
    collapse_redundant_slashes,
    has_base_url_token,
    normalize_base_url_token,
    rewrite_base_url_token,
)


class TestHasBaseUrlToken(unittest.TestCase):
    def test_variants(self):
        for url in ('{{baseUrl}}/login', '{{base_url}}/login', '{{ BASEURL }}/x',
                    '{{base-url}}/x', '/{{baseUrl}}/login', '  {{baseUrl}}/a'):
            self.assertTrue(has_base_url_token(url), url)

    def test_non_token_returns_false(self):
        for url in ('https://api.x.com/login', '/login', '{{token}}/login', '', None):
            self.assertFalse(has_base_url_token(url), str(url))


class TestRewriteBaseUrlToken(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            rewrite_base_url_token('{{baseUrl}}/login', 'https://api.x.com'),
            'https://api.x.com/login',
        )

    def test_base_url_underscore(self):
        self.assertEqual(
            rewrite_base_url_token('{{base_url}}/login', 'https://api.x.com/'),
            'https://api.x.com/login',
        )

    def test_spacing_and_leading_slash(self):
        self.assertEqual(
            rewrite_base_url_token('  {{ baseUrl }}/a/b', 'http://h'),
            'http://h/a/b',
        )

    def test_only_token_no_path(self):
        self.assertEqual(
            rewrite_base_url_token('{{baseUrl}}', 'http://h'),
            'http://h',
        )

    def test_absolute_url_unchanged(self):
        self.assertEqual(
            rewrite_base_url_token('https://already.com/login', 'http://x'),
            'https://already.com/login',
        )

    def test_relative_without_token_unchanged(self):
        self.assertEqual(
            rewrite_base_url_token('/login', 'http://x'),
            '/login',
        )

    def test_other_variable_unchanged(self):
        self.assertEqual(
            rewrite_base_url_token('{{token}}/login', 'http://x'),
            '{{token}}/login',
        )

    def test_empty_base_url_noop(self):
        self.assertEqual(
            rewrite_base_url_token('{{baseUrl}}/login', ''),
            '{{baseUrl}}/login',
        )

    def test_case_insensitive(self):
        self.assertEqual(
            rewrite_base_url_token('{{BASEURL}}/x', 'http://h'),
            'http://h/x',
        )


class TestCollapseRedundantSlashes(unittest.TestCase):
    """导入时折叠路径中的冗余双斜杠（保留 scheme 的 ://）。"""

    def test_import_case_double_slash(self):
        self.assertEqual(
            collapse_redundant_slashes('{{base_url}}/jar//login/byUserNamePassword'),
            '{{base_url}}/jar/login/byUserNamePassword',
        )

    def test_keep_scheme(self):
        self.assertEqual(
            collapse_redundant_slashes('https://water.gxhypro.com/jar/weather/weatherNow'),
            'https://water.gxhypro.com/jar/weather/weatherNow',
        )

    def test_absolute_with_double_slash_path(self):
        self.assertEqual(
            collapse_redundant_slashes('https://h.com/a//b///c'),
            'https://h.com/a/b/c',
        )

    def test_multiple_double_slashes(self):
        self.assertEqual(
            collapse_redundant_slashes('{{baseUrl}}//api//ping'),
            '{{baseUrl}}/api/ping',
        )

    def test_query_string_preserved(self):
        self.assertEqual(
            collapse_redundant_slashes('https://h.com/api?x=1&y=2'),
            'https://h.com/api?x=1&y=2',
        )

    def test_empty_and_none(self):
        self.assertEqual(collapse_redundant_slashes(''), '')
        self.assertEqual(collapse_redundant_slashes(None), '')

    def test_single_slash_unchanged(self):
        self.assertEqual(collapse_redundant_slashes('{{baseUrl}}/login'), '{{baseUrl}}/login')


class TestNormalizeBaseUrlToken(unittest.TestCase):
    """字面 base_url/ 前缀归一化为 {{base_url}}/ 变量引用（keep 模式导入）。"""

    def test_literal_prefix_normalized(self):
        self.assertEqual(
            normalize_base_url_token('base_url/jar/login/byUserNamePassword'),
            '{{base_url}}/jar/login/byUserNamePassword',
        )

    def test_literal_variants(self):
        self.assertEqual(normalize_base_url_token('/base_url/a'), '{{base_url}}/a')
        self.assertEqual(normalize_base_url_token('baseUrl/x'), '{{base_url}}/x')
        self.assertEqual(normalize_base_url_token('BASE_URL/UP'), '{{base_url}}/UP')
        self.assertEqual(normalize_base_url_token('base_url'), '{{base_url}}')

    def test_already_variable_unchanged(self):
        self.assertEqual(
            normalize_base_url_token('{{base_url}}/keep'), '{{base_url}}/keep')

    def test_absolute_url_unchanged(self):
        self.assertEqual(
            normalize_base_url_token('https://x.com/api/base_url/y'),
            'https://x.com/api/base_url/y',
        )

    def test_word_suffix_not_treated_as_token(self):
        # my_base_url/ 是普通路径片段，不能误归一化
        self.assertEqual(normalize_base_url_token('my_base_url/z'), 'my_base_url/z')

    def test_rewrite_supports_literal_prefix(self):
        self.assertEqual(
            rewrite_base_url_token('base_url/jar/login', 'https://h.com/'),
            'https://h.com/jar/login',
        )

    def test_has_token_includes_literal(self):
        self.assertTrue(has_base_url_token('base_url/a'))
        self.assertFalse(has_base_url_token('https://x.com/base_url/a'))


if __name__ == '__main__':
    unittest.main()
