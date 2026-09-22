"""jmx_inspect 静态解析的 DB-free 单测。

jmx_inspect 是「上传脚本」模式下唯一的护栏来源——preflight 的并发/时长/域名
限制全部依赖它的解析结果，解析错一个字段就等于护栏失效，所以这里覆盖得比较密。

使用绝对导入（apps.perf_testing...）以兼容 unittest discover 与 manage.py test 两种发现方式。
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from apps.perf_testing.services import jmx_inspect  # noqa: E402


HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'


def wrap(inner, jmeter='5.6.3'):
    return (f'{HEADER}<jmeterTestPlan version="1.2" properties="5.0" jmeter="{jmeter}">'
            f'<hashTree>'
            f'<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Demo Plan" enabled="true">'
            f'<boolProp name="TestPlan.functional_mode">false</boolProp>'
            f'</TestPlan><hashTree>{inner}</hashTree></hashTree></jmeterTestPlan>')


def thread_group(name='TG-1', threads='10', ramp='5', duration='60',
                 tag='ThreadGroup', enabled='true', loops='-1'):
    return (f'<{tag} guiclass="ThreadGroupGui" testclass="{tag}" testname="{name}" '
            f'enabled="{enabled}">'
            f'<stringProp name="ThreadGroup.num_threads">{threads}</stringProp>'
            f'<stringProp name="ThreadGroup.ramp_time">{ramp}</stringProp>'
            f'<stringProp name="ThreadGroup.duration">{duration}</stringProp>'
            f'<boolProp name="ThreadGroup.scheduler">true</boolProp>'
            f'<elementProp name="ThreadGroup.main_controller" elementType="LoopController">'
            f'<stringProp name="LoopController.loops">{loops}</stringProp>'
            f'</elementProp>'
            f'</{tag}><hashTree/>')


def sampler(name='Req', domain='api.example.com'):
    return (f'<HTTPSamplerProxy testname="{name}" enabled="true">'
            f'<stringProp name="HTTPSampler.domain">{domain}</stringProp>'
            f'<stringProp name="HTTPSampler.path">/x</stringProp>'
            f'</HTTPSamplerProxy><hashTree/>')


class TestInspectValidPlan(unittest.TestCase):
    """正常脚本：字段要全部对得上，否则 preflight 就是在拿错数据做判断。"""

    def setUp(self):
        self.meta = jmx_inspect.inspect_jmx_text(
            wrap(thread_group() + sampler('Login') + sampler('Profile', 'API.Example.com')))

    def test_valid(self):
        self.assertTrue(self.meta['valid'], self.meta['error'])
        self.assertEqual(self.meta['error'], '')

    def test_plan_meta(self):
        self.assertEqual(self.meta['test_plan_name'], 'Demo Plan')
        self.assertEqual(self.meta['jmeter_version'], '5.6.3')

    def test_thread_group_fields(self):
        self.assertEqual(len(self.meta['thread_groups']), 1)
        group = self.meta['thread_groups'][0]
        self.assertEqual(group['name'], 'TG-1')
        self.assertEqual(group['type'], 'ThreadGroup')
        self.assertTrue(group['enabled'])
        self.assertEqual(group['num_threads'], 10)
        self.assertEqual(group['ramp_time'], 5)
        self.assertEqual(group['duration'], 60)
        self.assertEqual(group['loops'], -1)
        self.assertTrue(group['scheduler'])
        self.assertFalse(group['dynamic'])

    def test_totals(self):
        self.assertEqual(self.meta['total_threads'], 10)
        self.assertEqual(self.meta['max_duration'], 60)
        self.assertEqual(self.meta['sampler_count'], 2)

    def test_hosts_deduped_and_lowercased(self):
        # 大小写不同的同一域名必须收敛成一个，否则禁用域名黑名单会被大小写绕过
        self.assertEqual(self.meta['hosts'], ['api.example.com'])

    def test_no_dynamic(self):
        self.assertFalse(self.meta['has_dynamic_props'])


class TestInspectInvalid(unittest.TestCase):

    def test_not_xml(self):
        meta = jmx_inspect.inspect_jmx_text('this is not xml at all')
        self.assertFalse(meta['valid'])
        self.assertIn('XML', meta['error'])

    def test_wrong_root(self):
        meta = jmx_inspect.inspect_jmx_text(f'{HEADER}<project><hashTree/></project>')
        self.assertFalse(meta['valid'])
        self.assertIn('jmeterTestPlan', meta['error'])

    def test_no_thread_group(self):
        meta = jmx_inspect.inspect_jmx_text(wrap(sampler()))
        self.assertFalse(meta['valid'])
        self.assertIn('线程组', meta['error'])

    def test_empty_bytes(self):
        meta = jmx_inspect.inspect_jmx_bytes(b'')
        self.assertFalse(meta['valid'])

    def test_missing_file(self):
        meta = jmx_inspect.inspect_jmx_file(os.path.join(tempfile.gettempdir(), '__no_such__.jmx'))
        self.assertFalse(meta['valid'])
        self.assertIn('不存在', meta['error'])


class TestInspectDynamic(unittest.TestCase):
    """含 __P 属性函数的参数化脚本：不能一刀切拒绝，但静态值必须置 None。"""

    def setUp(self):
        self.meta = jmx_inspect.inspect_jmx_text(
            wrap(thread_group(threads='${__P(threads,50)}', duration='${__P(hold,300)}')
                 + sampler()))

    def test_still_valid(self):
        self.assertTrue(self.meta['valid'], self.meta['error'])

    def test_totals_are_none(self):
        # None 表示「无法静态判定」，上层据此降级为 warning 而不是直接放行成 0
        self.assertIsNone(self.meta['total_threads'])
        self.assertIsNone(self.meta['max_duration'])

    def test_flag(self):
        self.assertTrue(self.meta['has_dynamic_props'])
        self.assertTrue(self.meta['thread_groups'][0]['dynamic'])

    def test_dynamic_domain_not_collected(self):
        meta = jmx_inspect.inspect_jmx_text(
            wrap(thread_group() + sampler(domain='${host}')))
        self.assertEqual(meta['hosts'], [])


class TestThreadGroupFiltering(unittest.TestCase):

    def test_setup_and_teardown_excluded_from_load(self):
        meta = jmx_inspect.inspect_jmx_text(wrap(
            thread_group('Setup', threads='5', tag='SetupThreadGroup')
            + thread_group('Main', threads='20')
            + thread_group('Teardown', threads='3', tag='PostThreadGroup')
            + sampler()))
        self.assertTrue(meta['valid'], meta['error'])
        # 三个线程组都要展示给用户看，但只有主线程组计入并发
        self.assertEqual(len(meta['thread_groups']), 3)
        self.assertEqual(meta['total_threads'], 20)

    def test_disabled_group_excluded(self):
        meta = jmx_inspect.inspect_jmx_text(wrap(
            thread_group('Off', threads='500', enabled='false')
            + thread_group('On', threads='8')
            + sampler()))
        self.assertEqual(meta['total_threads'], 8)
        self.assertFalse(meta['thread_groups'][0]['enabled'])

    def test_multiple_groups_summed_and_max_duration(self):
        meta = jmx_inspect.inspect_jmx_text(wrap(
            thread_group('A', threads='10', duration='60')
            + thread_group('B', threads='30', duration='120')
            + sampler()))
        self.assertEqual(meta['total_threads'], 40)
        self.assertEqual(meta['max_duration'], 120)

    def test_plugin_thread_group_fqcn_tag(self):
        # bzm 插件线程组用全限定类名当标签，_local 必须取最后一段
        inner = ('<com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup '
                 'testname="bzm" enabled="true">'
                 '<stringProp name="TargetLevel">25</stringProp>'
                 '<stringProp name="RampUp">10</stringProp>'
                 '<stringProp name="Hold">90</stringProp>'
                 '</com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup>'
                 '<hashTree/>') + sampler()
        meta = jmx_inspect.inspect_jmx_text(wrap(inner))
        self.assertTrue(meta['valid'], meta['error'])
        self.assertEqual(meta['thread_groups'][0]['type'], 'ConcurrencyThreadGroup')
        self.assertEqual(meta['total_threads'], 25)
        self.assertEqual(meta['max_duration'], 90)


class TestExtraElements(unittest.TestCase):

    def test_csv_dataset(self):
        inner = (thread_group()
                 + '<CSVDataSet testname="users" enabled="true">'
                   '<stringProp name="filename">users.csv</stringProp>'
                   '<stringProp name="variableNames">user,pwd</stringProp>'
                   '</CSVDataSet><hashTree/>'
                 + sampler())
        meta = jmx_inspect.inspect_jmx_text(wrap(inner))
        self.assertEqual(meta['csv_datasets'],
                         [{'filename': 'users.csv', 'variable_names': 'user,pwd'}])

    def test_http_defaults_domain(self):
        # 域名常常只写在 HTTP Request Defaults 里，漏掉就等于域名护栏形同虚设
        inner = (thread_group()
                 + '<ConfigTestElement testname="Defaults" enabled="true">'
                   '<stringProp name="HTTPSampler.domain">defaults.example.com</stringProp>'
                   '</ConfigTestElement><hashTree/>'
                 + '<HTTPSamplerProxy testname="R" enabled="true">'
                   '<stringProp name="HTTPSampler.path">/x</stringProp>'
                   '</HTTPSamplerProxy><hashTree/>')
        meta = jmx_inspect.inspect_jmx_text(wrap(inner))
        self.assertEqual(meta['hosts'], ['defaults.example.com'])
        self.assertEqual(meta['sampler_count'], 1)

    def test_controller_counted(self):
        inner = (thread_group()
                 + '<LoopController testname="Loop" enabled="true"/><hashTree/>'
                 + sampler())
        meta = jmx_inspect.inspect_jmx_text(wrap(inner))
        self.assertEqual(meta['controller_count'], 1)


class TestBytesAndFile(unittest.TestCase):

    def test_utf8_bom(self):
        raw = b'\xef\xbb\xbf' + wrap(thread_group() + sampler()).encode('utf-8')
        meta = jmx_inspect.inspect_jmx_bytes(raw)
        self.assertTrue(meta['valid'], meta['error'])

    def test_gbk(self):
        text = wrap(thread_group(name='登录压测') + sampler())
        meta = jmx_inspect.inspect_jmx_bytes(text.encode('gbk'))
        self.assertTrue(meta['valid'], meta['error'])
        self.assertEqual(meta['thread_groups'][0]['name'], '登录压测')

    def test_inspect_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'plan.jmx')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(wrap(thread_group() + sampler()))
            meta = jmx_inspect.inspect_jmx_file(path)
        self.assertTrue(meta['valid'], meta['error'])
        self.assertEqual(meta['total_threads'], 10)


class TestSummarize(unittest.TestCase):

    def test_keys_and_truncation(self):
        meta = jmx_inspect.inspect_jmx_text(wrap(
            ''.join(thread_group(f'TG-{i}', threads='1') for i in range(30)) + sampler()))
        summary = jmx_inspect.summarize(meta)
        for key in ('test_plan_name', 'jmeter_version', 'thread_group_count', 'thread_groups',
                    'total_threads', 'max_duration', 'sampler_count', 'controller_count',
                    'hosts', 'csv_datasets', 'has_dynamic_props'):
            self.assertIn(key, summary)
        self.assertEqual(summary['thread_group_count'], 30)
        # 落库摘要必须截断，避免把整棵树塞进 JSONField
        self.assertEqual(len(summary['thread_groups']), 20)

    def test_summarize_none_safe(self):
        summary = jmx_inspect.summarize(None)
        self.assertEqual(summary['thread_group_count'], 0)
        self.assertEqual(summary['sampler_count'], 0)


if __name__ == '__main__':
    unittest.main()
