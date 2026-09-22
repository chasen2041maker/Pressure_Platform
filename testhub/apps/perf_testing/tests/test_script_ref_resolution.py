"""views.resolve_script_ref 的安全性单测（不落 DB，PerfDataFile 查询全部 mock）。

这一层是脚本模式唯一的信任边界：前端只能提交 data_file_id，jmx_path 必须由服务端
反查。一旦前端能直接指定路径，JMeter 会把任意文件当测试计划加载，配合
JSR223/BeanShell 元件可升级成任意命令执行——所以「客户端传来的 jmx_path 被忽略」
这条必须有回归测试守着。

只 mock ORM 查询，不建测试库（远程 MySQL 无建库权限）。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

import django  # noqa: E402

django.setup()

from django.test import override_settings  # noqa: E402

from apps.perf_testing import views  # noqa: E402
from apps.perf_testing.models import PerfDataFile  # noqa: E402


class FakeScenario:
    """最小场景桩：resolve_script_ref 只用到这三个属性。"""

    def __init__(self, engine='JMETER', project_id=1, runtime_config=None):
        self.id = 7
        self.engine = engine
        self.project_id = project_id
        self.runtime_config = runtime_config if runtime_config is not None else {}


class FakeFileField:
    def __init__(self, path):
        self.path = path


class FakeDataFile:
    def __init__(self, pk=11, project_id=1, file_type='JMX', name='plan.jmx', path=''):
        self.id = pk
        self.pk = pk
        self.project_id = project_id
        self.file_type = file_type
        self.name = name
        self.file = FakeFileField(path)


class ResolveScriptRefBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.media_root = os.path.realpath(self._tmp.name)
        self.jmx_path = os.path.join(self.media_root, 'perf', 'plan.jmx')
        os.makedirs(os.path.dirname(self.jmx_path), exist_ok=True)
        with open(self.jmx_path, 'w', encoding='utf-8') as fh:
            fh.write('<jmeterTestPlan/>')
        self.addCleanup(self._tmp.cleanup)

    def resolve(self, scenario, raw, data_file=None, side_effect=None):
        target = 'apps.perf_testing.views.PerfDataFile.objects.get'
        kwargs = {'side_effect': side_effect} if side_effect else {'return_value': data_file}
        with override_settings(MEDIA_ROOT=self.media_root), mock.patch(target, **kwargs):
            return views.resolve_script_ref(scenario, raw)


class TestPassThroughCases(ResolveScriptRefBase):
    """不进入脚本模式的输入必须安静地返回空引用，不能报错也不能误判。"""

    def test_none(self):
        ref, err = self.resolve(FakeScenario(), None)
        self.assertEqual(ref, {})
        self.assertIsNone(err)

    def test_empty_dict(self):
        ref, err = self.resolve(FakeScenario(), {})
        self.assertEqual(ref, {})
        self.assertIsNone(err)

    def test_mode_scenario(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'scenario'})
        self.assertEqual(ref, {})
        self.assertIsNone(err)

    def test_mode_blank(self):
        ref, err = self.resolve(FakeScenario(), {'mode': ''})
        self.assertEqual(ref, {})
        self.assertIsNone(err)


class TestRejectCases(ResolveScriptRefBase):

    def test_not_a_dict(self):
        ref, err = self.resolve(FakeScenario(), 'script')
        self.assertIsNone(ref)
        self.assertIn('对象', err)

    def test_unknown_mode(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'distributed'})
        self.assertIsNone(ref)
        self.assertIn('distributed', err)

    def test_non_jmeter_engine(self):
        ref, err = self.resolve(FakeScenario(engine='BUILTIN'),
                                {'mode': 'script', 'data_file_id': 11})
        self.assertIsNone(ref)
        self.assertIn('JMeter', err)

    def test_missing_data_file_id(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'script'})
        self.assertIsNone(ref)
        self.assertIn('.jmx', err)

    def test_data_file_not_found(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'script', 'data_file_id': 99},
                                side_effect=PerfDataFile.DoesNotExist)
        self.assertIsNone(ref)
        self.assertIn('99', err)

    def test_cross_project_file_rejected(self):
        # 越权取用别的项目的脚本必须挡住
        ref, err = self.resolve(FakeScenario(project_id=1),
                                {'mode': 'script', 'data_file_id': 11},
                                data_file=FakeDataFile(project_id=2, path=self.jmx_path))
        self.assertIsNone(ref)
        self.assertIn('项目', err)

    def test_csv_file_rejected(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'script', 'data_file_id': 11},
                                data_file=FakeDataFile(file_type='CSV', name='users.csv',
                                                       path=self.jmx_path))
        self.assertIsNone(ref)
        self.assertIn('users.csv', err)

    def test_path_outside_media_root_rejected(self):
        # 即便 DB 里被写进了越界路径（脏数据/软链），也不能放行
        with tempfile.TemporaryDirectory() as outside:
            evil = os.path.join(os.path.realpath(outside), 'evil.jmx')
            with open(evil, 'w', encoding='utf-8') as fh:
                fh.write('<jmeterTestPlan/>')
            ref, err = self.resolve(FakeScenario(), {'mode': 'script', 'data_file_id': 11},
                                    data_file=FakeDataFile(path=evil))
        self.assertIsNone(ref)
        self.assertIn('路径非法', err)

    def test_missing_file_on_disk(self):
        ghost = os.path.join(self.media_root, 'perf', 'gone.jmx')
        ref, err = self.resolve(FakeScenario(), {'mode': 'script', 'data_file_id': 11},
                                data_file=FakeDataFile(path=ghost))
        self.assertIsNone(ref)
        self.assertIn('丢失', err)


class TestAcceptCase(ResolveScriptRefBase):

    def test_valid_reference(self):
        ref, err = self.resolve(FakeScenario(), {'mode': 'script', 'data_file_id': 11},
                                data_file=FakeDataFile(path=self.jmx_path))
        self.assertIsNone(err)
        self.assertEqual(ref['mode'], 'script')
        self.assertEqual(ref['data_file_id'], 11)
        self.assertEqual(ref['data_file_name'], 'plan.jmx')
        self.assertEqual(os.path.realpath(ref['jmx_path']), os.path.realpath(self.jmx_path))

    def test_client_supplied_jmx_path_is_ignored(self):
        """核心回归：前端塞进来的 jmx_path 一律丢弃，只认 data_file_id 反查的结果。"""
        evil = os.path.join(os.path.sep, 'etc', 'passwd')
        ref, err = self.resolve(
            FakeScenario(),
            {'mode': 'script', 'data_file_id': 11, 'jmx_path': evil},
            data_file=FakeDataFile(path=self.jmx_path))
        self.assertIsNone(err)
        self.assertNotEqual(os.path.normpath(ref['jmx_path']), os.path.normpath(evil))
        self.assertEqual(os.path.realpath(ref['jmx_path']), os.path.realpath(self.jmx_path))

    def test_mode_case_insensitive(self):
        ref, err = self.resolve(FakeScenario(), {'mode': ' Script ', 'data_file_id': 11},
                                data_file=FakeDataFile(path=self.jmx_path))
        self.assertIsNone(err)
        self.assertEqual(ref['mode'], 'script')


class TestScenarioFallback(ResolveScriptRefBase):
    """请求没带 script_ref 时回落到场景持久化配置，定时压测才能复用同一份选择。"""

    def test_fallback_to_runtime_config(self):
        scenario = FakeScenario(runtime_config={'script_ref': {'mode': 'script',
                                                               'data_file_id': 11}})
        ref, err = self.resolve(scenario, None, data_file=FakeDataFile(path=self.jmx_path))
        self.assertIsNone(err)
        self.assertEqual(ref['data_file_id'], 11)

    def test_fallback_scenario_mode(self):
        scenario = FakeScenario(runtime_config={'script_ref': {'mode': 'scenario'}})
        ref, err = self.resolve(scenario, None)
        self.assertEqual(ref, {})
        self.assertIsNone(err)

    def test_request_overrides_scenario(self):
        # 显式传 scenario 模式时，不能被场景里存的 script 配置反向覆盖
        scenario = FakeScenario(runtime_config={'script_ref': {'mode': 'script',
                                                               'data_file_id': 11}})
        ref, err = self.resolve(scenario, {'mode': 'scenario'})
        self.assertEqual(ref, {})
        self.assertIsNone(err)

    def test_missing_runtime_config_attr(self):
        scenario = FakeScenario()
        scenario.runtime_config = None
        ref, err = self.resolve(scenario, None)
        self.assertEqual(ref, {})
        self.assertIsNone(err)


class TestRuntimeConfigSanitize(unittest.TestCase):
    """序列化层落库前的 script_ref 削平：只留 mode 与 data_file_id。"""

    def setUp(self):
        from apps.perf_testing.serializers import PerfScenarioSerializer
        self.validate = PerfScenarioSerializer().validate_runtime_config

    def test_strips_client_path(self):
        out = self.validate({'timeout': 30,
                             'script_ref': {'mode': 'script', 'data_file_id': 11,
                                            'jmx_path': '/etc/passwd',
                                            'data_file_name': 'x'}})
        self.assertEqual(out['script_ref'], {'mode': 'script', 'data_file_id': 11})

    def test_scenario_mode_normalized(self):
        out = self.validate({'script_ref': {'mode': 'scenario', 'data_file_id': 11}})
        self.assertEqual(out['script_ref'], {'mode': 'scenario'})

    def test_script_without_file_rejected(self):
        from rest_framework import serializers as drf
        with self.assertRaises(drf.ValidationError):
            self.validate({'script_ref': {'mode': 'script'}})

    def test_bad_mode_rejected(self):
        from rest_framework import serializers as drf
        with self.assertRaises(drf.ValidationError):
            self.validate({'script_ref': {'mode': 'exec'}})

    def test_absent_script_ref_untouched(self):
        out = self.validate({'timeout': 30, 'sample_interval': 1})
        self.assertNotIn('script_ref', out)
        self.assertEqual(out['timeout'], 30)


if __name__ == '__main__':
    unittest.main()
