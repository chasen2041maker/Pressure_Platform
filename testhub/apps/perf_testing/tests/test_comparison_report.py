# -*- coding: utf-8 -*-
"""
多轮对照报告测试。
运行: python manage.py test apps.perf_testing.tests.test_comparison_report
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.perf_testing.models import (PerfComparisonReport, PerfExecution,
                                      PerfProject, PerfScenario)
from apps.perf_testing.services.compare_report import build_snapshot, trim_snapshot_for_ai

User = get_user_model()


def _make_execution(project, scenario, no, summary, executed_by):
    return PerfExecution.objects.create(
        scenario=scenario, project=project, execution_no=no,
        status='COMPLETED', summary=summary, executed_by=executed_by)


class ComparisonReportTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='perf_cmp', password='pass12345')
        self.project = PerfProject.objects.create(name='压测项目A', owner=self.user)
        self.scenario = PerfScenario.objects.create(
            project=self.project, name='场景1', created_by=self.user)
        self.exec1 = _make_execution(
            self.project, self.scenario, 'E001',
            {'tps': 100.0, 'peak_tps': 120.0, 'avg_rt': 50.0, 'p90_rt': 80.0,
             'p95_rt': 100.0, 'p99_rt': 150.0, 'max_rt': 300.0,
             'error_rate': 1.0, 'total_requests': 6000}, self.user)
        self.exec2 = _make_execution(
            self.project, self.scenario, 'E002',
            {'tps': 90.0, 'peak_tps': 110.0, 'avg_rt': 60.0, 'p90_rt': 90.0,
             'p95_rt': 120.0, 'p99_rt': 180.0, 'max_rt': 400.0,
             'error_rate': 2.0, 'total_requests': 5400}, self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class TestBuildSnapshot(ComparisonReportTestBase):

    def test_order_and_default_reference(self):
        snap = build_snapshot([self.exec2, self.exec1])
        self.assertEqual(snap['baseline_execution_no'], 'E002')
        self.assertEqual([e['execution_no'] for e in snap['executions']],
                         ['E002', 'E001'])
        self.assertEqual(snap['reference_execution_id'], self.exec2.id)

    def test_delta_pct_against_reference(self):
        snap = build_snapshot([self.exec1, self.exec2])
        second = snap['executions'][1]
        # TPS: 90 vs 100 → -10%
        self.assertAlmostEqual(second['delta_pct']['tps'], -10.0)
        # P95: 120 vs 100 → +20%
        self.assertAlmostEqual(second['delta_pct']['p95_rt'], 20.0)
        # 基准自身全 0
        self.assertEqual(snap['executions'][0]['delta_pct']['tps'], 0.0)

    def test_explicit_reference(self):
        snap = build_snapshot([self.exec2, self.exec1],
                              reference_execution_id=self.exec1.id)
        self.assertEqual(snap['baseline_execution_no'], 'E001')
        ref = next(e for e in snap['executions'] if e['is_reference'])
        self.assertEqual(ref['id'], self.exec1.id)

    def test_created_at_is_json_serializable(self):
        snap = build_snapshot([self.exec1, self.exec2])
        import json
        json.dumps(snap, ensure_ascii=False)  # 不抛错即可


class TestTrimSnapshotForAi(TestCase):

    def test_contains_rows_and_truncate(self):
        snap = {
            'baseline_execution_no': 'E001',
            'executions': [
                {'execution_no': 'E001',
                 'summary': {'tps': 100, 'peak_tps': 120, 'avg_rt': 50,
                             'p95_rt': 100, 'p99_rt': 150, 'error_rate': 1},
                 'delta_pct': {'tps': 0, 'p95_rt': 0, 'error_rate': 0}},
            ],
            'step_comparison': [
                {'step_name': 'login',
                 'values': [{'tps': 50, 'p95_rt': 90, 'error_rate': 0.5}]},
            ],
        }
        text = trim_snapshot_for_ai(snap)
        self.assertIn('E001', text)
        self.assertIn('login', text)
        self.assertLessEqual(len(text), 2000)
        short = trim_snapshot_for_ai(snap, max_chars=10)
        self.assertEqual(len(short), 10)


class TestComparisonReportApi(ComparisonReportTestBase):

    def test_create_requires_two(self):
        resp = self.client.post('/api/perf-testing/comparison-reports/',
                                {'execution_ids': [self.exec1.id]}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_create_ok(self):
        resp = self.client.post('/api/perf-testing/comparison-reports/',
                                {'execution_ids': [self.exec1.id, self.exec2.id],
                                 'title': '轮次对照'}, format='json')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['title'], '轮次对照')
        self.assertEqual(data['execution_ids'], [self.exec1.id, self.exec2.id])
        self.assertTrue(PerfComparisonReport.objects.filter(id=data['id']).exists())

    def test_reference_must_be_in_list(self):
        resp = self.client.post('/api/perf-testing/comparison-reports/',
                                {'execution_ids': [self.exec1.id, self.exec2.id],
                                 'reference_execution_id': 99999}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_missing_execution_404(self):
        resp = self.client.post('/api/perf-testing/comparison-reports/',
                                {'execution_ids': [self.exec1.id, 99999]},
                                format='json')
        self.assertEqual(resp.status_code, 404)

    def test_list_filter_by_project(self):
        other_project = PerfProject.objects.create(name='项目B', owner=self.user)
        self.client.post('/api/perf-testing/comparison-reports/',
                         {'execution_ids': [self.exec1.id, self.exec2.id]},
                         format='json')
        resp = self.client.get('/api/perf-testing/comparison-reports/',
                               {'project': self.project.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 1)
        resp = self.client.get('/api/perf-testing/comparison-reports/',
                               {'project': other_project.id})
        self.assertEqual(resp.json()['total'], 0)

    def test_retrieve_contains_snapshot(self):
        report = PerfComparisonReport.objects.create(
            project=self.project, title='t',
            execution_ids=[self.exec1.id, self.exec2.id],
            snapshot=build_snapshot([self.exec1, self.exec2]),
            created_by=self.user)
        resp = self.client.get(f'/api/perf-testing/comparison-reports/{report.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('executions', resp.json()['snapshot'])

    def test_destroy(self):
        report = PerfComparisonReport.objects.create(
            project=self.project, title='t', execution_ids=[1, 2],
            created_by=self.user)
        resp = self.client.delete(f'/api/perf-testing/comparison-reports/{report.id}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(PerfComparisonReport.objects.filter(id=report.id).exists())
