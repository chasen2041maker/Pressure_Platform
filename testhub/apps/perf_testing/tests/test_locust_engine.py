"""LocustEngine 的 DB-free 单测。

覆盖点：
1. 与内置/JMeter 引擎共享的实例化签名——run_execution 统一以
   engine_class(snapshot, on_sample=…, on_log=…, raw_csv_path=…) 调用，
   本引擎必须接受 raw_csv_path，否则执行 Locust 时直接 TypeError 崩溃
   （这是前期评审发现的关键缺陷，必须有回归守着）。
2. locust 未安装时 prepare() 必须抛 EngineError（而非静默往下走）。
3. collect() 从 locust --csv 产出的 _stats.csv 归一化出 summary / request_stats，
   且返回结构含 samples / raw_rows（与 BaseEngine.collect 契约一致）。
4. fake-locust 桩验证「生成 locustfile → 跑通 → collect」整体生命周期。

全部为纯逻辑测试，使用绝对导入以兼容 unittest discover 与 manage.py test。
"""
import csv
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.engines import locust_engine  # noqa: E402
from apps.perf_testing.engines.base import EngineError  # noqa: E402


SNAPSHOT = {
    'engine': 'LOCUST',
    'env_config': {'base_url': 'http://httpbin.org'},
    'load_config': {'concurrency': 5, 'ramp_up': 2, 'duration': 10},
    'steps': [
        {'name': '登录', 'enabled': True, 'is_setup': False, 'method': 'POST',
         'url': '/login', 'headers': {}, 'body': '', 'body_type': 'NONE'},
        {'name': '查询', 'enabled': True, 'is_setup': False, 'method': 'GET',
         'url': '/items', 'headers': {}, 'body': '', 'body_type': 'NONE'},
    ],
}


class TestInitSignature(unittest.TestCase):
    """平台驱动层统一传入 raw_csv_path，引擎必须接受而不崩溃。"""

    def test_accepts_raw_csv_path(self):
        # 不触发 prepare，仅验证实例化签名不抛 TypeError
        engine = locust_engine.LocustEngine(
            SNAPSHOT, on_sample=lambda s: None, on_log=lambda *a: None,
            raw_csv_path='/tmp/whatever_raw.csv.gz')
        self.assertEqual(engine.raw_csv_path, '/tmp/whatever_raw.csv.gz')
        # work_dir 必须是独立临时目录，避免污染 cwd / 并发互相覆盖
        self.assertTrue(os.path.isdir(engine.work_dir))
        self.assertNotEqual(engine.work_dir, os.getcwd())

    def test_uses_explicit_work_dir(self):
        with tempfile.TemporaryDirectory() as d:
            engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=d)
            self.assertEqual(engine.work_dir, d)


class TestAvailability(unittest.TestCase):

    def test_prepare_raises_when_locust_missing(self):
        if locust_engine.is_available():
            self.skipTest('locust 已安装，跳过 unavailable 路径')
        engine = locust_engine.LocustEngine(SNAPSHOT)
        with self.assertRaises(EngineError):
            engine.prepare()

    def test_prepare_requires_base_url(self):
        if not locust_engine.is_available():
            self.skipTest('locust 未安装，prepare 在可用性检查即短路')
        # base_url 缺失必须被 prepare 拦下
        bad = dict(SNAPSHOT, env_config={})
        engine = locust_engine.LocustEngine(bad)
        with self.assertRaises(EngineError):
            engine.prepare()


class TestCollectFromCsv(unittest.TestCase):
    """collect() 必须能从 locust --csv 的 _stats.csv 还原汇总。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=self.tmp.name)
        # 写入一份 locust_stats.csv（Aggregated 行 + 两个请求行）
        rows = [
            ('Type', 'Name', 'Request Count', 'Failure Count', 'Median Response Time',
             'Average Response Time', 'Min Response Time', 'Max Response Time',
             'Average Content Size', 'Requests/s', '50%', '90%', '95%', '99%'),
            ('GET', '/login', '100', '5', '20', '25', '10', '200', '500', '10.0',
             '22', '40', '60', '90'),
            ('GET', '/items', '200', '0', '15', '18', '8', '150', '800', '20.0',
             '16', '30', '45', '70'),
            ('Aggregated', 'Aggregated', '300', '5', '17', '20', '8', '200', '700',
             '30.0', '18', '35', '52', '85'),
        ]
        with open(self.engine.csv_prefix + '_stats.csv', 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerows(rows)

    def test_summary_aggregated(self):
        result = self.engine.collect()
        summary = result['summary']
        self.assertEqual(summary['total_requests'], 300)
        self.assertEqual(summary['failed_requests'], 5)
        self.assertAlmostEqual(summary['error_rate'], round(5 / 300 * 100, 2), places=2)
        self.assertAlmostEqual(summary['tps'], 30.0, places=2)
        self.assertEqual(summary['avg_rt'], 20)
        self.assertEqual(summary['p99_rt'], 85)

    def test_request_stats_count(self):
        result = self.engine.collect()
        stats = result['request_stats']
        # Aggregated 行不计入逐请求明细，应为 2 条
        self.assertEqual(len(stats), 2)
        names = {s['step_name'] for s in stats}
        self.assertEqual(names, {'/login', '/items'})

    def test_contract_keys_present(self):
        result = self.engine.collect()
        for key in ('summary', 'request_stats', 'samples', 'duration',
                    'stop_reason', 'raw_rows'):
            self.assertIn(key, result)
        self.assertEqual(result['samples'], [])


class TestCollectGbkCsv(unittest.TestCase):
    """collect() 必须能处理中文 Windows 上 Locust 写出的 GBK/CP936 编码 CSV。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=self.tmp.name)
        # 使用 GBK 编码写出含中文 step name 的 CSV，复现生产现场错误
        header = ('Type', 'Name', 'Request Count', 'Failure Count',
                  'Median Response Time', 'Average Response Time', 'Min Response Time',
                  'Max Response Time', 'Average Content Size', 'Requests/s',
                  '50%', '90%', '95%', '99%')
        rows = [
            header,
            ('GET', '天气-分钟级降雨', '49354', '0', '130', '163.10',
             '81.72', '8008.99', '244.59', '165.74',
             '130', '230', '260', '450'),
            ('GET', '天气-实时天气', '49362', '49362', '20', '22.76',
             '0.36', '401.94', '0.0', '165.77',
             '20', '180', '210', '290'),
            ('Aggregated', 'Aggregated', '197428', '98741', '69', '69.21',
             '0', '8008', '122.30', '662.96',
             '50', '180', '210', '290'),
        ]
        stats_path = self.engine.csv_prefix + '_stats.csv'
        with open(stats_path, 'w', newline='', encoding='gbk') as fh:
            writer = csv.writer(fh)
            writer.writerows(rows)
        # 验证这份文件确实无法用 UTF-8 解码（与线上报错一致）
        with open(stats_path, 'rb') as fh:
            self.assertRaises(UnicodeDecodeError, fh.read().decode, 'utf-8')

    def test_summary_from_gbk_csv(self):
        result = self.engine.collect()
        summary = result['summary']
        self.assertEqual(summary['total_requests'], 197428)
        self.assertEqual(summary['failed_requests'], 98741)
        self.assertAlmostEqual(summary['error_rate'], round(98741 / 197428 * 100, 2), places=2)

    def test_chinese_step_names_preserved(self):
        result = self.engine.collect()
        stats = result['request_stats']
        self.assertEqual(len(stats), 2)
        names = {s['step_name'] for s in stats}
        self.assertEqual(names, {'天气-分钟级降雨', '天气-实时天气'})


class TestFakeLocustLifecycle(unittest.TestCase):
    """用假 locust 脚本桩跑通「prepare → run → collect」整链，不依赖真实 locust。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.fake_dir.cleanup)

        # 假 locust：写一份 _stats.csv 然后退出
        self.fake_locust = os.path.join(self.fake_dir.name, 'fake_locust.py')
        with open(self.fake_locust, 'w', encoding='utf-8') as fh:
            fh.write(textwrap.dedent('''
                import csv, sys, os
                # 找到 --csv 前缀（倒数第二个参数之前是 -f 文件，之后是其它）
                args = sys.argv
                prefix = None
                for i, a in enumerate(args):
                    if a == '--csv' and i + 1 < len(args):
                        prefix = args[i + 1]
                if not prefix:
                    sys.exit(2)
                rows = [
                    ('Type', 'Name', 'Request Count', 'Failure Count',
                     'Median Response Time', 'Average Response Time', 'Min Response Time',
                     'Max Response Time', 'Average Content Size', 'Requests/s',
                     '50%', '90%', '95%', '99%'),
                    ('Aggregated', 'Aggregated', '120', '0', '10', '12', '5', '80',
                     '300', '12.0', '11', '20', '30', '50'),
                ]
                with open(prefix + '_stats.csv', 'w', newline='') as fh:
                    csv.writer(fh).writerows(rows)
            '''))
        # 让 sys.executable -m locust 变成跑这个假脚本：打桩 subprocess.Popen
        self._orig_popen = subprocess.Popen

    def _fake_popen(self, cmd, *args, **kwargs):
        # 把 `python -m locust ...` 改写成 `python fake_locust.py ...`
        new_cmd = [cmd[0], self.fake_locust]
        # 保留 --csv 前缀参数
        new_cmd += cmd[cmd.index('-m', 1) + 1:] if '-m' in cmd else []
        return self._orig_popen(new_cmd, *args, **kwargs)

    def test_lifecycle_with_fake_locust(self):
        if not locust_engine.is_available():
            self.skipTest('真实 locust 未安装；fake 桩仅用于验证整链，不覆盖本环境')
        engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=self.tmp.name)
        # prepare 会生成 locustfile.py 并校验 locust 可用性
        engine.prepare()
        self.assertTrue(os.path.exists(engine.locustfile))
        with mock.patch.object(subprocess, 'Popen', self._fake_popen):
            engine.run()
        result = engine.collect()
        self.assertEqual(result['summary']['total_requests'], 120)
        self.assertEqual(result['summary']['tps'], 12.0)


class TestTryEmitSample(unittest.TestCase):
    """_try_emit_sample() 从 stats_history.csv 增量读取并回调 on_sample。

    验证：
    1. 无 history 文件时不调用 on_sample（静默跳过）
    2. 有 Aggregated 行时正确构造 sample 并回调
    3. 节流：两次调用间隔不足 _sample_interval 时跳过
    4. 增量读取：只处理新行，不重复回调旧数据
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=self.tmp.name)
        self.samples = []

    def _make_sample_cb(self):
        def cb(s):
            self.samples.append(s)
        return cb

    def _write_history(self, rows):
        path = self.engine.csv_prefix + '_stats_history.csv'
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerows(rows)

    def test_no_history_file_no_callback(self):
        """stats_history.csv 不存在时，on_sample 不应被调用。"""
        engine = locust_engine.LocustEngine(
            SNAPSHOT, on_sample=self._make_sample_cb(), work_dir=self.tmp.name)
        engine._start_ts = time.time() - 5.0
        engine._last_emit_ts = 0.0
        engine._try_emit_sample()
        self.assertEqual(len(self.samples), 0)

    def test_aggregated_row_produces_sample(self):
        """包含 Aggregated 行的 history 文件应触发一次 on_sample 回调。"""
        engine = locust_engine.LocustEngine(
            SNAPSHOT, on_sample=self._make_sample_cb(), work_dir=self.tmp.name)
        engine._start_ts = time.time() - 10.0
        engine._last_emit_ts = 0.0  # 允许立即发射

        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             'Min Response Time', 'Max Response Time',
             '50%', '90%', '95%', '99%'),
            ('10.5', '5', 'Aggregated', 'Aggregated', '500', '2',
             '50.0', '20.5', '8', '200', '18', '35', '52', '88'),
        ])

        engine._try_emit_sample()

        self.assertEqual(len(self.samples), 1)
        s = self.samples[0]
        self.assertAlmostEqual(s['tps'], 50.0, places=2)
        self.assertEqual(s['avg_rt'], 20.5)
        self.assertEqual(s['p95_rt'], 52)
        self.assertEqual(s['p99_rt'], 88)
        self.assertEqual(s['total_requests'], 500)
        self.assertEqual(s['active_users'], 5)  # from load_config concurrency
        self.assertGreater(s['ts_offset'], 0)
        self.assertEqual(s['error_rate'], round(2 / 500 * 100, 2))

    def test_throttle_interval_respected(self):
        """间隔不足 _sample_interval 时不应发射（节流）。"""
        engine = locust_engine.LocustEngine(
            SNAPSHOT, on_sample=self._make_sample_cb(), work_dir=self.tmp.name)
        now = time.time()
        engine._start_ts = now - 5.0
        engine._last_emit_ts = now - 1.0  # 1 秒前刚发过，< 3s interval

        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             'Min Response Time', 'Max Response Time',
             '50%', '90%', '95%', '99%'),
            ('10.0', '3', 'Aggregated', 'Aggregated', '100', '1',
             '10.0', '15.0', '5', '50', '12', '25', '40', '48'),
        ])

        engine._try_emit_sample()
        self.assertEqual(len(self.samples), 0)  # 被节流

    def test_incremental_read_no_duplicates(self):
        """增量读取：连续两次调用只处理新行，不重复回调。"""
        cb = self._make_sample_cb()
        engine = locust_engine.LocustEngine(
            SNAPSHOT, on_sample=cb, work_dir=self.tmp.name)
        now = time.time()
        engine._start_ts = now - 5.0
        engine._last_emit_ts = 0.0

        # 第一批：只有 header + 1 条 Aggregated
        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             'Min Response Time', 'Max Response Time',
             '50%', '90%', '95%', '99%'),
            ('5.0', '3', 'Aggregated', 'Aggregated', '100', '0',
             '20.0', '10.0', '5', '30', '9', '18', '25', '29'),
        ])
        engine._try_emit_sample()
        self.assertEqual(len(self.samples), 1)

        # 追加第二批数据（模拟 Locust 继续写入）
        engine._last_emit_ts = 0.0  # 重置节流以允许下次发射
        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             'Min Response Time', 'Max Response Time',
             '50%', '90%', '95%', '99%'),
            ('5.0', '3', 'Aggregated', 'Aggregated', '100', '0',
             '20.0', '10.0', '5', '30', '9', '18', '25', '29'),
            ('10.0', '5', 'GET', '/api/test', '200', '1',
             '40.0', '12.0', '6', '60', '11', '22', '33', '45'),
            ('10.0', '5', 'Aggregated', 'Aggregated', '300', '1',
             '60.0', '11.5', '6', '60', '10', '21', '32', '44'),
        ])
        engine._try_emit_sample()
        # 应只有 2 次 callback（第一批 1 次 + 第二批增量 1 次），不是 3 次
        self.assertEqual(len(self.samples), 2)
        # 第二次 callback 反映的是最新的 Aggregated 行
        latest = self.samples[1]
        self.assertEqual(latest['total_requests'], 300)


class TestCollectSamplesFromHistory(unittest.TestCase):
    """collect() 从 stats_history.csv 回填 samples 数组。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = locust_engine.LocustEngine(SNAPSHOT, work_dir=self.tmp.name)

    def _write_stats(self, rows):
        with open(self.engine.csv_prefix + '_stats.csv', 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerows(rows)

    def _write_history(self, rows):
        with open(self.engine.csv_prefix + '_stats_history.csv', 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerows(rows)

    def test_samples_populated_from_history(self):
        """collect() 应从 history 的 Aggregated 行构造 samples 列表。"""
        self._write_stats([
            ('Type', 'Name', 'Request Count', 'Failure Count',
             'Average Response Time', 'Requests/s', '50%', '90%', '95%', '99%'),
            ('Aggregated', 'Aggregated', '1000', '10', '15.0', '100.0',
             '12', '25', '38', '55'),
        ])
        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             '50%', '90%', '95%', '99%'),
            ('3.0', '2', 'Aggregated', 'Aggregated', '50', '0', '16.7',
             '12.0', '8', '30', '10', '20', '28', '35'),
            ('6.0', '4', 'Aggregated', 'Aggregated', '200', '2', '33.3',
             '14.5', '6', '50', '13', '26', '36', '48'),
            ('9.0', '5', 'Aggregated', 'Aggregated', '400', '3', '44.4',
             '13.0', '5', '80', '11', '22', '34', '52'),
        ])

        result = self.engine.collect()
        samples = result['samples']

        self.assertEqual(len(samples), 3)
        # 验证每个 sample 的关键字段
        self.assertEqual(samples[0]['total_requests'], 50)
        self.assertEqual(samples[0]['error_rate'], 0.0)
        self.assertEqual(samples[1]['total_requests'], 200)
        self.assertAlmostEqual(samples[1]['error_rate'], round(2 / 200 * 100, 2), places=2)
        self.assertEqual(samples[2]['total_requests'], 400)
        self.assertEqual(samples[2]['active_users'], 5)

    def test_samples_empty_when_no_history_file(self):
        """无 history 文件时 samples 为空列表。"""
        self._write_stats([
            ('Type', 'Name', 'Request Count', 'Failure Count',
             'Average Response Time', 'Requests/s', '50%', '90%', '95%', '99%'),
            ('Aggregated', 'Aggregated', '100', '0', '10.0', '10.0',
             '9', '18', '27', '40'),
        ])
        result = self.engine.collect()
        self.assertEqual(result['samples'], [])

    def test_non_aggregated_rows_skipped_in_samples(self):
        """history 中非 Aggregated 行不应进入 samples 列表。"""
        self._write_history([
            ('Timestamp', 'User Count', 'Type', 'Name', 'Request Count',
             'Failure Count', 'Requests/s', 'Average Response Time',
             '50%', '90%', '95%', '99%'),
            ('3.0', '2', 'GET', '/api/a', '30', '1', '10.0',
             '15.0', '5', '40', '12', '24', '32', '42'),
            ('3.0', '2', 'Aggregated', 'Aggregated', '30', '1', '10.0',
             '15.0', '5', '40', '12', '24', '32', '42'),
            ('6.0', '4', 'POST', '/api/b', '70', '0', '23.3',
             '11.0', '4', '55', '9', '19', '28', '38'),
            ('6.0', '4', 'Aggregated', 'Aggregated', '100', '1', '16.7',
             '12.5', '4', '55', '10', '20', '30', '40'),
        ])
        result = self.engine.collect()
        # 只有 2 行 Aggregated → 2 个 samples
        self.assertEqual(len(result['samples']), 2)
        self.assertEqual(result['samples'][0]['total_requests'], 30)
        self.assertEqual(result['samples'][1]['total_requests'], 100)


class TestLocustfileGeneration(unittest.TestCase):
    """生成的 locustfile 渲染后必须是合法 Python，且 multipart 分支不丢。

    模板用 str.format + {{}} 转义混排，任何一处括号错都会让整个引擎
    在真实压测时才爆 SyntaxError，这里用 compile() 守住。
    """

    def _render(self, snapshot):
        import json as _json
        payload = _json.dumps({
            'steps': snapshot.get('steps') or [],
            'env_config': snapshot.get('env_config') or {},
            'variables': [],
        }, ensure_ascii=False)
        return locust_engine.LOCUSTFILE_TEMPLATE.format(scenario_json=payload)

    def test_template_renders_with_files(self):
        snap = dict(SNAPSHOT)
        snap['steps'] = [{
            'name': '上传', 'enabled': True, 'is_setup': False, 'method': 'POST',
            'url': '/upload', 'headers': {}, 'body': 'remark=hello',
            'body_type': 'FORM',
            'files': [{'field': 'attachment', 'path': '/tmp/a.png',
                       'filename': 'a.png', 'content_type': 'image/png'}],
        }]
        code = self._render(snap)
        compile(code, 'locustfile.py', 'exec')  # 语法合法即通过
        self.assertIn('_read_file_cached', code)
        self.assertIn('kwargs["files"]', code)
        self.assertIn('/tmp/a.png', code)

    def test_template_renders_plain_steps(self):
        code = self._render(SNAPSHOT)
        compile(code, 'locustfile.py', 'exec')


if __name__ == '__main__':
    unittest.main()
