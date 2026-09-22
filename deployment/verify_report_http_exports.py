"""Verify actual local report response bodies. This is NOT browser-download proof."""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sqlite3

import requests

ROOT = Path(__file__).resolve().parents[1]


class Cards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items, self.text, self.kind, self.label = {}, [], None, None
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag == 'td' and self.row is not None:
            self.cell = []
        classes = dict(attrs).get('class', '').split()
        if 'card-label' in classes or 'card-value' in classes:
            self.kind = 'label' if 'card-label' in classes else 'value'
            self.text = []

    def handle_data(self, data):
        if self.kind:
            self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == 'div' and self.kind:
            value = ''.join(self.text).strip()
            if self.kind == 'label':
                self.label = value
            else:
                self.items[self.label] = value
            self.kind = None


def verify():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='', help='New evidence suffix; existing evidence is never overwritten')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9-]{0,40}', args.tag)
    suffix = '-' + args.tag if args.tag else ''
    directory = ROOT / ('runtime/private/c12-http-report-exports' + suffix)
    directory.mkdir(exist_ok=False)
    session = requests.Session()
    session.trust_env = False
    credentials = json.loads((ROOT / 'runtime/private/testhub-admin.json').read_text(encoding='utf-8-sig'))
    login = session.post('http://127.0.0.1:58100/api/auth/login/',
                         json={k: credentials[k] for k in ('username', 'password')}, timeout=10)
    assert login.status_code == 200, 'Local login failed'
    session.headers['Authorization'] = 'Bearer ' + login.json()['access']
    secrets = [credentials['password'], login.json()['access']]
    with (ROOT / 'runtime/private/auth-ui-users.csv').open(encoding='utf-8-sig', newline='') as handle:
        secrets.extend(row['password'] for row in csv.DictReader(handle))
    results, findings = [], []
    for execution in (28, 32):
        with sqlite3.connect((ROOT / 'runtime/private/testhub.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            run = dict(db.execute('SELECT id,project_id,scenario_id,execution_no,status,sla_result,summary,sla_detail,duration FROM perf_executions WHERE id=?', (execution,)).fetchone())
        assert run['project_id'] == 7 and run['scenario_id'] >= 23
        assert run['status'] in ('COMPLETED', 'STOPPED')
        responses, hashes = {}, {}
        for format_name, content_type in (('json', 'application/json'), ('csv', 'text/csv'), ('html', 'text/html')):
            response = session.get(f'http://127.0.0.1:58100/api/perf-testing/executions/{execution}/report/',
                                   params={'export': format_name}, timeout=20, allow_redirects=False)
            assert response.status_code == 200
            assert response.headers.get('Content-Type', '').startswith(content_type)
            if format_name != 'html':
                assert f'{run["execution_no"]}.{format_name}' in response.headers.get('Content-Disposition', '')
            assert all(secret not in response.content.decode('utf-8-sig') for secret in secrets if secret)
            name = f'{run["execution_no"]}.{format_name}'
            (directory / name).write_bytes(response.content)
            hashes[name] = hashlib.sha256(response.content).hexdigest()
            responses[format_name] = response.content.decode('utf-8-sig')
        document = json.loads(responses['json'])
        summary = json.loads(run['summary'])
        for key in ('execution_no', 'status', 'sla_result'):
            assert document[key] == run[key]
        assert document['execution_id'] == execution
        assert document['sla_detail'] == json.loads(run['sla_detail'])
        for key in ('http_total', 'http_started', 'http_incomplete', 'business_total', 'business_started', 'business_incomplete',
                    'total_requests', 'success_requests', 'failed_requests', 'business_rps', 'peak_tps', 'error_rate', 'avg_rt', 'p95_rt'):
            assert document['summary'].get(key) == summary.get(key), (execution, key)
        rows = list(csv.DictReader(io.StringIO(responses['csv'])))
        for section in ('summary', 'sla_detail', 'evidence'):
            matched = [r for r in rows if r['section'] == section]
            assert len(matched) == 1 and json.loads(matched[0]['data_json']) == document[section]
        interfaces = {str(row['step_id']): row for row in document['interfaces']}
        exported = [row for row in rows if row['section'] == 'interface']
        assert len(interfaces) == len(document['interfaces']) == len(exported)
        for row in exported:
            source = interfaces[row['step_id']]
            for key in ('started', 'total', 'incomplete', 'success', 'failed', 'error_rate', 'tps', 'avg_rt',
                        'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt'):
                if source.get(key) is None:
                    assert row[key] == ''
                else:
                    assert float(row[key]) == source[key]
        with gzip.open(ROOT / f'runtime/media/perf-testing/executions/{execution}/raw.csv.gz', 'rt', encoding='utf-8') as handle:
            raw = list(csv.DictReader(handle))
        business = [row for row in raw if row['is_setup'] == '0']
        failed = sum(row['success'] == '0' for row in business)
        assert len(raw) == summary['http_total']
        assert len(business) == summary['business_total']
        assert failed == summary['failed_requests']
        assert len(business) - failed == summary['success_requests']
        for interface in interfaces.values():
            matched = [row for row in raw if row['method'] == interface['method'] and row['url'] == interface['url']]
            if interface.get('total') is None:
                assert not matched
                continue
            assert len(matched) == interface['total']
            assert sum(row['success'] == '1' for row in matched) == interface['success']
            assert sum(row['success'] == '0' for row in matched) == interface['failed']
        cards = Cards()
        cards.feed(responses['html'])
        for interface in interfaces.values():
            expected_row = [str(interface[key]) if interface.get(key) is not None else '未采集'
                            for key in ('step_name', 'phase', 'method', 'url', 'total', 'success', 'failed', 'error_rate',
                                        'tps', 'avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt')]
            assert cards.rows.count(expected_row) == 1, 'HTML interface row must match exactly once'
        for label, expected in (('已完成 HTTP 请求', len(raw)), ('已完成业务请求', len(business)),
                                ('已发起 HTTP 请求', summary['http_started']), ('已发起业务请求', summary['business_started']),
                                ('完整执行轮次', summary['completed_iterations']),
                                ('未完成 HTTP 请求', summary['http_incomplete']), ('未完成业务请求', summary['business_incomplete'])):
            assert int(cards.items[label].replace(',', '')) == expected
        assert cards.items['业务成功 / 失败'] == f'{len(business)-failed:,} / {failed:,}'
        assert float(cards.items['业务失败率'].rstrip('%')) == summary['error_rate']
        assert float(cards.items['平均业务 RPS']) == summary['business_rps']
        assert float(cards.items['峰值业务 RPS']) == summary['peak_tps']
        assert float(cards.items['累计 P95（估算）'].removesuffix(' ms')) == summary['p95_rt']
        assert float(cards.items['累计 P99（估算）'].removesuffix(' ms')) == summary['p99_rt']
        assert float(cards.items['平均业务响应'].removesuffix(' ms')) == summary['avg_rt']
        assert float(cards.items['最大业务响应'].removesuffix(' ms')) == summary['max_rt']
        assert float(cards.items['脚本活动用户峰值']) == summary['max_concurrency']
        assert float(cards.items['执行时长'].removesuffix(' s')) == run['duration']
        if 'k6 进程 CPU 峰值' in responses['html'] or 'CPU 为 k6 进程占用' in responses['html']:
            findings.append({'execution': execution, 'code': 'CPU_SOURCE_LABEL',
                             'detail': 'HTML labels Docker container CPU as k6 process CPU; web report explicitly describes the complete container including the watchdog.'})
        cpu_semantics = summary.get('metric_semantics', {}).get('cpu_percent')
        if cpu_semantics and html.escape(cpu_semantics) not in responses['html']:
            findings.append({'execution': execution, 'code': 'FROZEN_CPU_SEMANTICS_MISSING',
                             'detail': 'HTML must include the escaped CPU-source description retained for this execution.'})
        results.append({'execution': execution, 'status': run['status'], 'sla_result': run['sla_result'],
                        'http': len(raw), 'business': len(business), 'failed': failed,
                        'numeric_checks': 'PASS', 'sha256': hashes})
    result = {'status': 'NUMERIC_PASS_SEMANTIC_FINDINGS' if findings else 'PASS_HTTP_RESPONSE_CONTENT_ONLY',
              'at': datetime.now(timezone.utc).isoformat(), 'runs': results, 'findings': findings,
              'transport': 'Authenticated direct HTTP GET, not browser download',
              'browser_download_verified': False,
              'limits': ['This does not accept the UI export delivery gate or Example capacity.',
                         'HTML verification checks selected rendered metric cards; it is not a full visual or chart acceptance.']}
    with (ROOT / ('evidence/p1-delivery/c12-http-report-export-result' + suffix + '.json')).open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ('status', 'findings', 'browser_download_verified')}, ensure_ascii=False))


if __name__ == '__main__':
    verify()
