"""内置引擎 multipart 文件上传能力的 DB-free 单测。

覆盖点：
1. _parse_form_body 兼容 FORM body 的两种历史格式（JSON 对象 / k=v&k=v）。
2. _build_multipart_files 把快照文件字段转成 httpx files 参数，且字节带缓存：
   压测下不得每次迭代重读磁盘；文件丢失时返回错误信息而不是抛异常打断压测。
3. prepare() 必须对上传文件做存在性前置校验——文件丢失要在压测开始前就报错。
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.engines import builtin as builtin_engine  # noqa: E402
from apps.perf_testing.engines.base import EngineError  # noqa: E402


class TestParseFormBody(unittest.TestCase):

    def test_json_object(self):
        self.assertEqual(builtin_engine._parse_form_body('{"a": "1", "b": "x"}'),
                         {'a': '1', 'b': 'x'})

    def test_kv_pairs(self):
        self.assertEqual(builtin_engine._parse_form_body('a=1&b=x=2'),
                         {'a': '1', 'b': 'x=2'})

    def test_empty(self):
        self.assertEqual(builtin_engine._parse_form_body(''), {})
        self.assertEqual(builtin_engine._parse_form_body(None), {})


class TestBuildMultipartFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file_path = os.path.join(self.tmp.name, 'upload.bin')
        with open(self.file_path, 'wb') as fh:
            fh.write(b'\x00\x01payload')

    def test_builds_httpx_files_tuple(self):
        files, err = builtin_engine._build_multipart_files(
            [{'field': 'attachment', 'path': self.file_path,
              'filename': 'upload.bin', 'content_type': 'image/png'}], {})
        self.assertEqual(err, '')
        self.assertEqual(len(files), 1)
        field, (filename, content, ctype) = files[0]
        self.assertEqual(field, 'attachment')
        self.assertEqual(filename, 'upload.bin')
        self.assertEqual(content, b'\x00\x01payload')
        self.assertEqual(ctype, 'image/png')

    def test_defaults_and_cache(self):
        cache = {}
        files, _ = builtin_engine._build_multipart_files(
            [{'path': self.file_path}], cache)
        # field/filename/content_type 缺省值
        self.assertEqual(files[0][0], 'file')
        self.assertEqual(files[0][1][0], 'upload.bin')
        self.assertEqual(files[0][1][2], 'application/octet-stream')
        # 首次读取后落入缓存；删掉源文件仍能再次构造（命中缓存不再读盘）
        self.assertIn(self.file_path, cache)
        os.remove(self.file_path)
        files2, err2 = builtin_engine._build_multipart_files(
            [{'path': self.file_path}], cache)
        self.assertEqual(err2, '')
        self.assertEqual(files2[0][1][1], b'\x00\x01payload')

    def test_missing_file_returns_error(self):
        files, err = builtin_engine._build_multipart_files(
            [{'field': 'file', 'path': os.path.join(self.tmp.name, 'gone.bin')}], {})
        self.assertEqual(files, [])
        self.assertIn('gone.bin', err)

    def test_empty_path_skipped(self):
        # file_id 为空的导入占位（path 缺失）不参与发送
        files, err = builtin_engine._build_multipart_files(
            [{'field': 'file'}, {'field': 'x', 'path': ''}], {})
        self.assertEqual(files, [])
        self.assertEqual(err, '')


def _snapshot_with_files(file_items):
    return {
        'load_config': {'model': 'CONCURRENCY', 'concurrency': 1,
                        'duration': 10, 'ramp_up': 1},
        'runtime_config': {},
        'env_config': {'base_url': 'http://localhost:8000'},
        'steps': [{'name': '上传', 'enabled': True, 'is_setup': False,
                   'method': 'POST', 'url': '/upload',
                   'body_type': 'FORM', 'body': 'remark=hello',
                   'files': file_items}],
        'variables': [],
    }


class TestPrepareFilePreflight(unittest.TestCase):

    def test_missing_file_raises_in_prepare(self):
        engine = builtin_engine.BuiltinEngine(_snapshot_with_files(
            [{'field': 'file', 'path': '/no/such/dir/nope.bin'}]))
        with self.assertRaises(EngineError) as cm:
            engine.prepare()
        self.assertIn('nope.bin', str(cm.exception))

    def test_existing_file_passes_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ok.bin')
            with open(path, 'wb') as fh:
                fh.write(b'ok')
            engine = builtin_engine.BuiltinEngine(_snapshot_with_files(
                [{'field': 'file', 'path': path}]))
            engine.prepare()  # 不抛即通过


if __name__ == '__main__':
    unittest.main()
