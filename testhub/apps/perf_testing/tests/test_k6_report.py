"""Offline report contract: request counts must retain their measured meaning."""
import copy
import html
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from apps.perf_testing.services import reporter


class K6ReportTests(unittest.TestCase):
    def test_request_path_strips_origin_and_secrets_without_expanding_templates(self):
        for source, expected in (
            ('/api/me?access_token=SECRET#SECRET', '/api/me'),
            ('https://user:SECRET@example.test/api/me?secret=SECRET', '/api/me'),
            ('/https://user:SECRET@example.test/api/me?secret=SECRET', '/api/me'),
            ('/http://user:SECRET@example.test/api/me?secret=SECRET', '/api/me'),
            ('{{base_url}}/api/items/{{item_id}}?secret=SECRET', '/api/items/{{item_id}}'),
            ('${baseUrl}/api/items/${item_id}', '/api/items/${item_id}'),
            ('step:541', None), ('javascript:/SECRET', None),
            ('https://[invalid/SECRET', None), ('/api/\nSECRET', None),
            (None, None), ({'secret': 'SECRET'}, None),
        ):
            with self.subTest(source=source):
                self.assertEqual(reporter.safe_request_path(source), expected)

    def execution(self):
        return SimpleNamespace(
            scenario=SimpleNamespace(name='两接口验证', engine='LOCUST',
                                     project=SimpleNamespace(name='本地验证')),
            load_snapshot={'_engine': 'K6', 'model': 'CONCURRENCY'},
            steps_snapshot=[{'name': '登录', 'is_setup': True}],
            summary={
                'adapter_version': '0.1.0', 'http_started': 214, 'http_total': 210,
                'http_incomplete': 4, 'business_started': 204, 'business_total': 200,
                'business_incomplete': 4, 'total_requests': 200,
                'success_requests': 190, 'failed_requests': 10, 'error_rate': 5,
                'business_rps': 20, 'tps': 20, 'peak_tps': 30,
                'completed_iterations': 100, 'p95_rt': 12.5, 'p99_rt': 18,
                'sent_bytes': 0, 'recv_bytes': 0,
            },
            sla_result='NOT_EVALUATED', sla_detail=[], status='STOPPED',
            STATUS_CHOICES=[('STOPPED', '已停止')], duration=10,
            error_message='', start_time=None, end_time=None,
            execution_no='K6-REPORT-TEST', executed_by=None,
        )

    def render(self, execution, samples=None):
        now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        with mock.patch.object(reporter.timezone, 'localtime', return_value=now):
            return reporter._render(execution, samples or [], [])

    def cards(self, document):
        return dict(re.findall(
            r'<div class="card-label">([^<]*)</div>\s*'
            r'<div class="card-value[^"]*">([^<]*)</div>', document))

    def test_frozen_engine_survives_scene_engine_change(self):
        document = self.render(self.execution())
        self.assertIn('压测引擎：<b>K6</b>', document)
        self.assertNotIn('压测引擎：<b>LOCUST</b>', document)

    def test_timed_load_report_keeps_configured_and_engine_time_distinct(self):
        execution = self.execution()
        execution.summary.update(setup_incomplete_vus=1, timed_load={
            'configured_seconds': 300, 'drain_limit_seconds': 35, 'engine_seconds': 307.5,
            'notice': '配置时长包含前置和认证；<script>unsafe</script>',
        })
        document = self.render(execution)
        for value in ('配置发压时间', '300 s', '收尾时间上限', '35 s', '引擎总时间', '307.5 s',
                      '前置未完成用户', '&lt;script&gt;unsafe&lt;/script&gt;'):
            self.assertIn(value, document)
        self.assertNotIn('<script>unsafe</script>', document)

    def test_request_cards_separate_started_completed_and_incomplete(self):
        cards = self.cards(self.render(self.execution()))
        for label, expected in {
            '已发起 HTTP 请求': '214', '已完成 HTTP 请求': '210',
            '未完成 HTTP 请求': '4', '已发起业务请求': '204',
            '已完成业务请求': '200', '未完成业务请求': '4',
            '业务成功 / 失败': '190 / 10', '业务失败率': '5%',
            '完整执行轮次': '100', '平均业务 RPS': '20',
        }.items():
            with self.subTest(label=label):
                self.assertEqual(cards.get(label), expected)
        self.assertNotIn('总请求数', cards)
        self.assertNotIn('平均 TPS', cards)

    def test_report_explains_rounds_aborted_requests_and_histogram_estimates(self):
        document = self.render(self.execution())
        self.assertIn('完整执行轮次不代表成功事务数', document)
        self.assertIn('到时结束、手动停止或异常中止', document)
        self.assertIn('累计直方图估算', document)
        self.assertIn('请求与响应字节数未采集', document)
        self.assertIn('累计 P95（估算）', document)
        self.assertIn('业务 RPS 与脚本活动用户趋势', document)
        self.assertNotIn("name:'TPS'", document)
        self.assertNotIn('0 B', document)

    def test_k6_user_chart_and_peak_keep_script_event_meaning_with_or_without_native(self):
        for native in (None, {'available': True, 'source': 'k6_rest_status_v1',
                              'valid_count': 3, 'target_count': 3, 'expected_vus': 1000}):
            for frozen_marker in (True, False):
                with self.subTest(native=native, frozen_marker=frozen_marker):
                    execution = self.execution()
                    execution.summary['max_concurrency'] = 7
                    execution.load_snapshot['concurrency'] = 1000
                    if not frozen_marker:
                        execution.load_snapshot.pop('_engine')
                    if native is not None:
                        execution.summary['native_vu'] = native
                    samples = [dict(ts_offset=i, active_users=999, k6_payload={
                        'version': 'k6_sample_v1', 'sample_seq': i + 1,
                        'active_users': value, 'elapsed_seconds': i,
                    }) for i, value in enumerate((7, None, 0))]
                    document = self.render(execution, samples)
                    self.assertEqual(self.cards(document)['脚本活动用户峰值'], '7')
                    self.assertEqual(self.chart_data(document)['users'], [7, None, 0])
                    self.assertIn('业务 RPS 与脚本活动用户趋势', document)
                    self.assertIn("name:'脚本活动用户'", document)
                    self.assertNotIn("name:'并发用户'", document)
                    self.assertNotIn("name:'并发'", document)
                    self.assertNotIn('峰值模拟用户', document)
                    self.assertIn('脚本活动用户及其峰值来自脚本事件', document)
                    self.assertIn('不等于原生活动 VU 或同时在途的 HTTP 请求数', document)
                    self.assertIn('不能证明配置人数（包括 1000 VU）在整个时段持续运行或系统承载能力', document)
                    self.assertIn('原生 VU 缺失时仍为未知，不能用本图补齐', document)

    def test_k6_average_rps_denominator_explains_startup_and_cleanup(self):
        execution = self.execution()
        document = self.render(execution)
        self.assertIn('业务 RPS＝已完成业务请求数÷引擎启动至清理结束的时间；'
                      '包含容器启动、登录与刷新、思考时间、采集及停止清理耗时。', document)
        self.assertEqual(self.cards(document)['平均业务 RPS'], '20')
        execution.load_snapshot['_engine'] = 'BUILTIN'
        self.assertNotIn('引擎启动至清理结束', self.render(execution))

    def test_k6_script_peak_missing_null_and_zero_remain_distinct(self):
        for value, expected in ((None, '未采集'), (0, '0')):
            execution = self.execution()
            execution.summary['max_concurrency'] = value
            self.assertEqual(self.cards(self.render(execution))['脚本活动用户峰值'], expected)
        execution.summary.pop('max_concurrency')
        self.assertEqual(self.cards(self.render(execution))['脚本活动用户峰值'], '未采集')

    def test_non_k6_user_chart_peak_and_labels_stay_unchanged(self):
        for engine in ('BUILTIN', 'LOCUST', 'JMETER'):
            with self.subTest(engine=engine):
                execution = self.execution()
                execution.load_snapshot['_engine'] = engine
                execution.summary['max_concurrency'] = 7
                document = self.render(execution, [{'ts_offset': 1, 'active_users': 3}])
                self.assertEqual(self.cards(document)['峰值并发'], '7')
                self.assertEqual(self.chart_data(document)['users'], [3])
                self.assertIn('TPS 与并发趋势', document)
                self.assertIn("name:'并发用户'", document)
                self.assertIn("name:'并发'", document)
                self.assertNotIn('脚本活动用户', document)

    def test_missing_started_counters_are_not_invented_as_zero(self):
        execution = self.execution()
        for key in ('http_started', 'http_incomplete', 'business_started', 'business_incomplete'):
            execution.summary.pop(key)
        cards = self.cards(self.render(execution))
        self.assertEqual(cards.get('已完成 HTTP 请求'), '210')
        self.assertEqual(cards.get('已发起 HTTP 请求'), '未采集')
        self.assertEqual(cards.get('未完成 HTTP 请求'), '未采集')
        self.assertEqual(cards.get('未完成业务请求'), '未采集')

    def test_k6_summary_identifies_older_report_without_engine_marker(self):
        execution = self.execution()
        execution.load_snapshot.pop('_engine')
        document = self.render(execution)
        self.assertIn('压测引擎：<b>K6</b>', document)
        self.assertIn('已完成 HTTP 请求', document)

    def test_other_engines_keep_existing_report_labels(self):
        execution = self.execution()
        execution.load_snapshot.pop('_engine')
        execution.summary = copy.deepcopy({
            'total_requests': 200, 'success_requests': 190, 'failed_requests': 10,
            'error_rate': 5, 'tps': 20, 'peak_tps': 30,
        })
        document = self.render(execution)
        cards = self.cards(document)
        self.assertEqual(cards['总请求数'], '200')
        self.assertEqual(cards['平均 TPS'], '20')
        self.assertIn('压测引擎：<b>LOCUST</b>', document)
        self.assertIn('TPS 与并发趋势', document)
        self.assertNotIn('请求与响应字节数未采集', document)

    def test_unknown_cpu_is_not_zero_or_a_bottleneck_warning(self):
        execution = self.execution()
        execution.summary.update(peak_load_gen_cpu=None, data_trustworthy=None,
                                 load_generator_capacity_verified=False)
        document = self.render(execution)
        self.assertEqual(self.cards(document).get('CPU 峰值'), '暂无数据')
        self.assertNotIn('已接近单机瓶颈', document)
        self.assertNotIn('None%', document)

    def test_cpu_sample_does_not_claim_generator_capacity_verified(self):
        execution = self.execution()
        execution.summary.update(peak_load_gen_cpu=12.5, data_trustworthy=True,
                                 load_generator_capacity_verified=False)
        document = self.render(execution)
        cpu_card = re.search(r'card-label">CPU 峰值</div>\s*'
                             r'<div class="([^"]*)">([^<]*)', document)
        self.assertEqual(cpu_card.group(2), '12.5%')
        self.assertNotIn('good', cpu_card.group(1))
        self.assertIn('发压器最大承载量尚未标定', document)

    def cpu_samples(self, observations):
        return [dict(ts_offset=i, cpu_percent=999, k6_payload={
            'version': 'k6_sample_v1', 'sample_seq': i + 1, 'elapsed_seconds': i,
            'cpu_percent': value, 'cpu_sampled': sampled,
        }) for i, (value, sampled) in enumerate(observations)]

    def chart_data(self, document):
        return json.loads(re.search(r'var D = (.*);', document).group(1))

    def test_cpu_source_uses_escaped_frozen_semantics_only(self):
        sources = [
            '仅 k6 进程 CPU，多核时可能超过 100%',
            'Docker stats 的整个发压容器 CPU，包含 k6 和轻量看门狗',
            '</p><script>alert("source")</script><img src=x onerror=alert(1)> &',
        ]
        for source in sources:
            with self.subTest(source=source):
                execution = self.execution()
                execution.summary['metric_semantics'] = {'cpu_percent': source}
                document = self.render(execution)
                self.assertIn('CPU 采样来源：' + html.escape(source), document)
                self.assertNotIn(source, document.split('<script>')[-1])
                self.assertEqual(document.count('<script>'), 1)
                self.assertIn("name:'CPU(%)'", document)
                self.assertIn('<h2>业务失败率与 CPU</h2>', document)

    def test_unknown_cpu_source_does_not_hide_measured_peak_or_guess_attribution(self):
        for semantics in [None, {}, [], 'container', {'cpu_percent': ''},
                          {'cpu_percent': '  '}, {'cpu_percent': 100}, {'cpu_percent': []}]:
            with self.subTest(semantics=semantics):
                execution = self.execution()
                execution.summary.update(metric_semantics=semantics, peak_load_gen_cpu=250)
                document = self.render(execution)
                self.assertIn('CPU 采样来源未记录', document)
                self.assertEqual(self.cards(document)['CPU 峰值'], '250%')
                self.assertNotIn('k6 进程', document)
                self.assertNotIn('Docker', document)

    def test_cpu_chart_requires_retained_sample_validity_and_preserves_zero(self):
        samples = self.cpu_samples([(250, True), (0, True), (0, False), (12, False),
                                   (float('inf'), True), (float('nan'), True),
                                   (True, True), (9, 'true')])
        samples.extend([{'ts_offset': 8, 'cpu_percent': 0},
                        {'ts_offset': 9, 'cpu_percent': 45, 'cpu_sampled': True}])
        document = self.render(self.execution(), samples)
        self.assertEqual(self.chart_data(document)['cpu'], [250, 0] + [None] * 8)
        self.assertNotIn("name:'CPU', max:100", document)

    def test_negative_trust_flag_has_no_cpu_capacity_inference(self):
        for peak in [None, 0, 12, 250]:
            with self.subTest(peak=peak):
                execution = self.execution()
                execution.summary.update(data_trustworthy=False, peak_load_gen_cpu=peak)
                document = self.render(execution)
                self.assertIn('数据可信标记为否', document)
                self.assertIn('原因未记录', document)
                self.assertIn('不能单独证明发压器容量或确定系统瓶颈', document)
                self.assertNotIn('已接近单机瓶颈', document)
                self.assertNotIn('可能受限于压力机', document)
        execution.summary.update(sample_data_incomplete=True, sample_missing_count=2,
                                 metric_semantics={'data_trustworthy': '<b>冻结说明</b>'})
        document = self.render(execution)
        self.assertIn('缺失 2 条时序采样', document)
        self.assertIn('数据可信标记为否', document)
        self.assertIn('&lt;b&gt;冻结说明&lt;/b&gt;', document)
        self.assertNotIn('<b>冻结说明</b>', document)

    def test_legacy_cpu_projection_axis_and_warning_stay_unchanged(self):
        execution = self.execution()
        execution.load_snapshot['_engine'] = 'BUILTIN'
        execution.summary.update(data_trustworthy=False, peak_load_gen_cpu=98)
        document = self.render(execution, [{'ts_offset': 1, 'cpu_percent': 0},
                                           {'ts_offset': 2, 'cpu_percent': 98}])
        self.assertEqual(self.chart_data(document)['cpu'], [0, 98])
        self.assertIn("name:'CPU', max:100", document)
        self.assertIn("name:'压力机CPU(%)'", document)
        self.assertIn('已接近单机瓶颈', document)
        self.assertEqual(self.cards(document)['压力机CPU峰值'], '98%')

    def test_actual_html_cpu_svg_preserves_isolated_points_gaps_and_axis_range(self):
        node = shutil.which('node')
        echarts = Path(__file__).resolve().parents[3] / 'frontend/node_modules/echarts/dist/echarts.js'
        if not node or not echarts.exists():
            self.skipTest('Node and installed ECharts required for actual SVG regression')
        cases = [([None, 250, None], [1]), ([None, 0, None], [1]),
                 ([0, None, None], [0]), ([None, None, 250], [2]),
                 ([0], [0]), ([0, None, 250], [0, 2]), ([None, None, None], [])]
        documents = [self.render(self.execution(), self.cpu_samples(
            [(value, value is not None) for value in values])) for values, _ in cases]
        script = r'''
const fs = require('fs'), assert = require('assert/strict'), echarts = require(process.argv[1]);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const [html, values, expected] of cases) {
  const options = {};
  const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  new Function('echarts','document','window', js)(
    {init: el => ({setOption: option => options[el.id] = option})},
    {getElementById: id => ({id}),querySelectorAll: () => []}, {addEventListener() {}});
  const option = options.c3, cpu = option.series[1];
  assert.deepEqual(cpu.data, values);
  assert.equal(cpu.connectNulls, false); assert.equal(cpu.step, false);
  const chart = echarts.init(null, null, {renderer:'svg', ssr:true, width:600, height:300});
  chart.setOption({...option, animation:false, legend:{show:false}, series:[cpu]});
  const extent = chart.getModel().getComponent('yAxis', 1).axis.scale.getExtent();
  for (const value of values.filter(v => v !== null)) assert.ok(value >= extent[0] && value <= extent[1]);
  const svg = chart.renderToSVGString();
  const paths = [...svg.matchAll(/<path\b[^>]*>/g)].map(m => m[0]);
  const markers = paths.filter(p => p.includes('ecmeta_data_index=') && !/matrix\(0[, ]/.test(p));
  assert.deepEqual(markers.map(p => Number(p.match(/ecmeta_data_index="(\d+)"/)[1])), expected);
  const line = paths.filter(p => p.includes('fill="none"') && p.includes('stroke="#6366f1"'))
    .map(p => p.match(/ d="([^"]*)"/)?.[1] || '').join(' ');
  assert.doesNotMatch(line, /[LC]/); chart.dispose();
}
process.stdout.write('7 actual HTML CPU SVG cases passed');
'''
        result = subprocess.run([node, '-e', script, str(echarts)], text=True, encoding='utf-8',
                                input=json.dumps([[doc, values, expected] for doc, (values, expected)
                                                  in zip(documents, cases)]),
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('7 actual HTML CPU SVG cases passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
