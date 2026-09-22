"""JmeterEngine 的 TDD 单测（DB-free）。

覆盖：jtl 解析归一化、subprocess 生命周期（fake jmeter）、二进制缺失报错。
先 RED（模块尚未实现），再 GREEN。
"""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SAMPLE_JTL = """timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,sentBytes,allThreads,URL,Latency,Connect
1700000000000,120,login,200,OK,Thread Group 1-1,text,true,,500,200,10,https://api.example.com/login,100,50
1700000000100,200,login,200,OK,Thread Group 1-2,text,true,,500,200,10,https://api.example.com/login,180,60
1700000000200,500,query,500,Internal Error,Thread Group 1-1,text,false,boom,0,150,10,https://api.example.com/items,400,80
1700000000300,150,query,200,OK,Thread Group 1-3,text,true,,800,150,10,https://api.example.com/items,130,70
"""

FAKE_JMETER = """# -*- coding: utf-8 -*-
import sys, os
# parse -l <path> and write the preset jtl
args = sys.argv
out = None
for i, a in enumerate(args):
    if a == "-l" and i + 1 < len(args):
        out = args[i + 1]
if out:
    data = '''timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,sentBytes,allThreads,URL,Latency,Connect
1700000000000,120,login,200,OK,Thread Group 1-1,text,true,,500,200,10,https://api.example.com/login,100,50
1700000000100,200,login,200,OK,Thread Group 1-2,text,true,,500,200,10,https://api.example.com/login,180,60
1700000000200,500,query,500,Internal Error,Thread Group 1-1,text,false,boom,0,150,10,https://api.example.com/items,400,80
1700000000300,150,query,200,OK,Thread Group 1-3,text,true,,800,150,10,https://api.example.com/items,130,70
'''
    with open(out, "w", newline="") as f:
        f.write(data)
"""


def _sample_snapshot():
    return {
        'engine': 'JMETER',
        'load_config': {'model': 'CONCURRENCY', 'concurrency': 10, 'ramp_up': 5, 'duration': 60},
        'variables': [],
        'steps': [
            {'name': 'login', 'method': 'POST', 'url': 'https://api.example.com/login',
             'enabled': True, 'assertions': [{'type': 'STATUS_CODE', 'expected': '200'}]},
            {'name': 'query', 'method': 'GET', 'url': 'https://api.example.com/items',
             'enabled': True, 'assertions': [{'type': 'STATUS_CODE', 'expected': '200'}]},
        ],
    }


class TestJmeterEngine(unittest.TestCase):
    def test_parse_jtl_normalizes_metrics(self):
        from apps.perf_testing.engines.jmeter_engine import parse_jtl
        with tempfile.NamedTemporaryFile('w', suffix='.jtl', delete=False, newline='') as f:
            f.write(SAMPLE_JTL)
            path = f.name
        try:
            result = parse_jtl(path)
            summary = result['summary']
            self.assertEqual(summary['total_requests'], 4)
            self.assertEqual(summary['failed_requests'], 1)
            self.assertAlmostEqual(summary['error_rate'], 25.0, places=1)
            self.assertGreater(summary['tps'], 0)
            for k in ('avg_rt', 'p50_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'min_rt', 'max_rt'):
                self.assertIn(k, summary)
                self.assertIsInstance(summary[k], (int, float))
            # request_stats 每个 label 一条
            labels = {s['step_name'] for s in result['request_stats']}
            self.assertEqual(labels, {'login', 'query'})
            self.assertIn('error_rate', result['request_stats'][0])
        finally:
            os.unlink(path)

    def test_engine_lifecycle_with_fake_jmeter(self):
        from apps.perf_testing.engines.jmeter_engine import JmeterEngine
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(FAKE_JMETER)
            fake = f.name
        try:
            samples = []
            engine = JmeterEngine(_sample_snapshot(), on_sample=lambda s: samples.append(s),
                                  jmeter_bin=[sys.executable, fake])
            engine.prepare()
            self.assertTrue(os.path.exists(engine.jmx_path), 'jmx 应已生成')
            engine.run()
            result = engine.collect()
            summary = result['summary']
            self.assertGreater(summary['total_requests'], 0)
            self.assertGreaterEqual(len(samples), 1, 'run 期间应至少推送一次采样')
        finally:
            os.unlink(fake)
            shutil.rmtree(getattr(engine, 'work_dir', ''), ignore_errors=True)

    def test_missing_binary_raises(self):
        from apps.perf_testing.engines.jmeter_engine import JmeterEngine
        from apps.perf_testing.engines.base import EngineError
        engine = JmeterEngine(_sample_snapshot(), jmeter_bin=['/no/such/jmeter'])
        with self.assertRaises(EngineError):
            engine.prepare()

    def test_script_mode_missing_jmx_raises(self):
        from apps.perf_testing.engines.jmeter_engine import JmeterEngine
        from apps.perf_testing.engines.base import EngineError
        snap = _sample_snapshot()
        snap['script_ref'] = {'mode': 'script', 'jmx_path': '/no/such/file.jmx'}
        engine = JmeterEngine(snap, jmeter_bin=[sys.executable, '--version'])
        with self.assertRaises(EngineError):
            engine.prepare()

    def test_temp_files_reside_in_project_not_system_tmp(self):
        """硬性约束：所有临时产物必须落在项目内（F 盘），绝不落到系统 TMP（C 盘）。"""
        from apps.perf_testing.engines.jmeter_engine import JmeterEngine
        from apps.perf_testing.engines.workspace import project_root, workspace_root

        engine = JmeterEngine(_sample_snapshot(), jmeter_bin=['/no/such/jmeter'])
        root = project_root()
        self.assertTrue(engine.work_dir.startswith(root),
                        'work_dir 必须位于项目根目录内（F 盘），实际：%s' % engine.work_dir)
        self.assertTrue(engine.work_dir.startswith(workspace_root()),
                        'work_dir 必须位于 perf_workspace 下')
        # 绝不能是系统 TMP（典型 C 盘路径）
        self.assertNotIn('\\AppData\\Local\\Temp', engine.work_dir)
        self.assertNotIn('\\Temp\\', engine.work_dir)
        self.assertFalse(engine.work_dir.startswith('C:\\'), 'work_dir 不应位于 C 盘')

    def test_non_c_env_redirects_tmp_and_java_tmpdir(self):
        """non_c_env 必须把 TMP/TEMP/TMPDIR 与 java.io.tmpdir 指向非 C 目录。"""
        from apps.perf_testing.engines.workspace import non_c_env

        target = 'F:\\TJC-Work\\PythonProject\\testhub_platform\\perf_workspace\\demo'
        try:
            env = non_c_env(target)
            self.assertEqual(env['TMP'], target)
            self.assertEqual(env['TEMP'], target)
            self.assertEqual(env['TMPDIR'], target)
            self.assertIn('-Djava.io.tmpdir=' + target, env.get('_JAVA_OPTIONS', ''))
            # 不污染 C 盘临时路径
            self.assertNotIn('C:\\', env['TMP'])
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def test_prepare_writes_artifacts_under_work_dir(self):
        """prepare() 生成的 .jmx/.jtl/report 路径必须都在 work_dir 内（非系统 TMP）。

        注意：result.jtl 由 JMeter 进程后续写入，prepare 阶段仅是路径规划，
        故对其只校验「路径落在 work_dir」而不校验文件存在。
        """
        from apps.perf_testing.engines.jmeter_engine import JmeterEngine

        # 用真实可解析的二进制绕过 prepare 的可用性检查（仅生成 .jmx，不真正压测）
        engine = JmeterEngine(_sample_snapshot(), jmeter_bin=[sys.executable, '--version'])
        try:
            engine.prepare()
            # plan.jmx 与 report/ 在 prepare 阶段即落盘
            self.assertTrue(os.path.exists(engine.jmx_path), 'plan.jmx 应已生成')
            self.assertTrue(os.path.isdir(engine.report_dir), 'report 目录应已创建')
            for attr in ('jmx_path', 'jtl_path', 'report_dir'):
                path = getattr(engine, attr)
                self.assertTrue(path.startswith(engine.work_dir),
                                '%s 必须位于 work_dir 内，实际：%s' % (attr, path))
                self.assertNotIn('\\AppData\\Local\\Temp', path)
        finally:
            shutil.rmtree(engine.work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
