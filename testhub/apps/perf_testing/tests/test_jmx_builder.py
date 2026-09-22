"""jmx_builder 的 TDD 单测（DB-free，纯函数）。

验证：PerfScenario 步骤 -> 合法 .jmx（含 ThreadGroup / HTTP Sampler / Response Assertion）。
先 RED（模块尚未实现），再 GREEN。
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _sample_snapshot():
    return {
        'load_config': {
            'model': 'CONCURRENCY',
            'concurrency': 10,
            'ramp_up': 5,
            'duration': 60,
        },
        'variables': [
            {'name': 'token', 'type': 'CONSTANT', 'value': 'abc'},
        ],
        'steps': [
            {
                'name': '登录',
                'method': 'POST',
                'url': 'https://api.example.com/login',
                'headers': {'Content-Type': 'application/json'},
                'body': '{"u":"${token}"}',
                'body_type': 'JSON',
                'is_setup': True,
                'enabled': True,
                'assertions': [
                    {'type': 'STATUS_CODE', 'expected': '200'},
                    {'type': 'CONTAINS', 'expected': 'ok'},
                ],
                'extractors': [],
                'think_time': {'type': 'FIXED', 'min': 1},
            },
            {
                'name': '查询',
                'method': 'GET',
                'url': 'https://api.example.com/items?page=1',
                'headers': {},
                'body': '',
                'body_type': 'NONE',
                'is_setup': False,
                'enabled': True,
                'assertions': [
                    {'type': 'STATUS_CODE', 'expected': '200'},
                ],
                'extractors': [],
                'think_time': {'type': 'NONE'},
            },
        ],
    }


class TestJmxBuilder(unittest.TestCase):
    def test_returns_parseable_xml(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        xml_str = build_jmx(_sample_snapshot())
        self.assertIsInstance(xml_str, str)
        self.assertTrue(xml_str.lstrip().startswith('<?xml'))
        # 必须是良构 XML
        root = ET.fromstring(xml_str)
        self.assertEqual(root.tag, 'jmeterTestPlan')

    def test_threadgroup_reflects_concurrency(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        xml_str = build_jmx(_sample_snapshot())
        self.assertIn('ThreadGroup', xml_str)
        # 并发数应映射到 num_threads
        self.assertIn('<intProp name="ThreadGroup.num_threads">10</intProp>', xml_str)
        # ramp_up 与 duration
        self.assertIn('<intProp name="ThreadGroup.ramp_time">5</intProp>', xml_str)
        self.assertIn('<longProp name="ThreadGroup.duration">60</longProp>', xml_str)

    def test_generates_http_sampler_and_assertion(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        xml_str = build_jmx(_sample_snapshot())
        # HTTP 采样器
        self.assertIn('HTTPSampler', xml_str)
        self.assertIn('api.example.com', xml_str)
        self.assertIn('/login', xml_str)
        self.assertIn('/items', xml_str)
        # 断言（ResponseAssertion）
        self.assertIn('ResponseAssertion', xml_str)
        # 步骤名作为采样器名出现
        self.assertIn('登录', xml_str)
        self.assertIn('查询', xml_str)

    def test_disabled_steps_omitted(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        snap = _sample_snapshot()
        snap['steps'][1]['enabled'] = False
        xml_str = build_jmx(snap)
        self.assertIn('登录', xml_str)
        self.assertNotIn('/items', xml_str)


class TestMultipartSampler(unittest.TestCase):
    """FORM + files 步骤必须生成 multipart 采样器（文件字段 + DO_MULTIPART_POST）。"""

    def _snapshot_with_files(self):
        snap = _sample_snapshot()
        snap['steps'].append({
            'name': '上传',
            'method': 'POST',
            'url': 'https://api.example.com/upload',
            'headers': {},
            'body': 'remark=hello',
            'body_type': 'FORM',
            'files': [{'field': 'attachment', 'path': '/tmp/a.png',
                       'filename': 'a.png', 'content_type': 'image/png'}],
            'is_setup': False,
            'enabled': True,
            'assertions': [],
            'extractors': [],
            'think_time': {'type': 'NONE'},
        })
        return snap

    def test_multipart_sampler_generated(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        xml_str = build_jmx(self._snapshot_with_files())
        # 良构 XML 且包含 multipart 关键要素：缺一 JMeter 都会退化成普通表单
        ET.fromstring(xml_str)
        self.assertIn('<boolProp name="HTTPSampler.DO_MULTIPART_POST">true</boolProp>', xml_str)
        self.assertIn('<stringProp name="File.path">/tmp/a.png</stringProp>', xml_str)
        self.assertIn('<stringProp name="File.paramname">attachment</stringProp>', xml_str)
        self.assertIn('<stringProp name="File.mimetype">image/png</stringProp>', xml_str)
        # body 文本拆成命名表单字段（而非整个字符串当原始 body）
        self.assertIn('<stringProp name="Argument.name">remark</stringProp>', xml_str)
        self.assertIn('<stringProp name="Argument.value">hello</stringProp>', xml_str)

    def test_no_multipart_without_files(self):
        from apps.perf_testing.engines.jmx_builder import build_jmx
        xml_str = build_jmx(_sample_snapshot())
        self.assertNotIn('DO_MULTIPART_POST', xml_str)
        self.assertNotIn('HTTPFileArg', xml_str)

    def test_files_without_path_ignored(self):
        # 导入占位（file_id 为空 → executor 不下发 path）不触发 multipart
        from apps.perf_testing.engines.jmx_builder import build_jmx
        snap = self._snapshot_with_files()
        snap['steps'][2]['files'] = [{'field': 'attachment', 'filename': 'a.png'}]
        xml_str = build_jmx(snap)
        self.assertNotIn('DO_MULTIPART_POST', xml_str)


if __name__ == '__main__':
    unittest.main()
