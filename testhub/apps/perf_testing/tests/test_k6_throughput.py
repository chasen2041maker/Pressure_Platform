"""Independent event-time arithmetic; no runner, process, HTTP or live database."""
import unittest
from collections import Counter
from unittest import mock
from apps.perf_testing.services.k6_throughput import CompletionBuckets, RATE_VERSION
from apps.perf_testing.engines.k6_engine import K6Engine


class CompletionThroughputTests(unittest.TestCase):
    def test_epoch_boundary_and_fixed_denominator(self):
        values = [999, 1000, 1001, 1999, 2000, 2100]
        buckets = CompletionBuckets()
        for value in values: buckets.record(value)
        result = buckets.snapshot()
        self.assertEqual(result['buckets'], [{'start_ms': 0, 'count': 1}, {'start_ms': 1000, 'count': 3}, {'start_ms': 2000, 'count': 2}])
        self.assertEqual(result['peak_rps'], 3)
        self.assertEqual(result['latest_rps'], 2)  # Last100ms is not divided by0.1.
        self.assertEqual(result['denominator_seconds'], 1)

    def test_out_of_order_and_late_events_revise_original_bucket(self):
        buckets = CompletionBuckets()
        for value in (3100, 1100, 1200): buckets.record(value)
        self.assertEqual(buckets.snapshot()['latest_rps'], 1)
        self.assertEqual(buckets.snapshot()['peak_rps'], 2)
        buckets.record(1300)
        self.assertEqual(buckets.snapshot()['peak_rps'], 3)
        self.assertEqual(buckets.snapshot()['latest_bucket_start_ms'], 3000)
        self.assertEqual(buckets.snapshot()['latest_rps'], 1)

    def test_invalid_timestamps_do_not_use_consumer_clock(self):
        for value in (None, '', '1000', 0, -1, True, 1.2, float('nan'), float('inf'), 10**18):
            with self.subTest(value=value):
                buckets = CompletionBuckets(); buckets.record(1100); buckets.record(value)
                result = buckets.snapshot()
                self.assertEqual(result['timestamped_business_events'], 1)
                self.assertEqual(result['invalid_timestamp_count'], 1)
                self.assertIsNone(result['peak_rps']); self.assertIsNone(result['latest_rps'])
                self.assertFalse(result['verified'])
                self.assertIn('时间戳', result['reason'])

    def test_zero_business_events_have_verified_zero_peak(self):
        result = CompletionBuckets().snapshot()
        self.assertEqual(result['peak_rps'], 0)
        self.assertTrue(result['verified'])
        self.assertEqual(result['buckets'], [])

    def engine(self):
        samples = []
        engine = K6Engine({'steps': [{'id': 1, 'name': 'business'}, {'id': 2, 'name': 'login', 'is_setup': True},
                                    {'id': 3, 'name': 'refresh', 'auth_phase': 'refresh'}]}, on_sample=samples.append)
        engine._start_ts = 10
        engine._end_ts = 15
        return engine, samples

    def consume(self, engine, timestamp, step=0):
        engine._consume_event({'kind': 'request', 'step': step, 'elapsed_ms': 1, 'timestamp_ms': timestamp, 'status': 200, 'ok': True})

    def test_22_events_drained_at_once_match_independent_bins_not_22000(self):
        engine, samples = self.engine()
        timestamps = [1700000000000 + n * 100 for n in range(22)]
        for stamp in timestamps: self.consume(engine, stamp)
        expected = Counter(stamp // 1000 for stamp in timestamps)
        engine._last_sample = 14.9999
        with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic', return_value=15):
            engine._emit_sample(force=True)
        result = engine.collect()['summary']
        self.assertEqual(expected, {1700000000: 10, 1700000001: 10, 1700000002: 2})
        self.assertEqual(result['peak_tps'], max(expected.values()))
        self.assertEqual(samples[0]['tps'], 2)
        self.assertEqual(result['business_rps'], 4.4)
        self.assertEqual(result['adapter_version'], '0.7.1')

    def test_drain_schedule_does_not_change_peak(self):
        values = [1000, 1000, 1999, 2000, 2100, 2100, 2200, 3999]
        for order in (values, list(reversed(values))):
            engine, samples = self.engine()
            for i, stamp in enumerate(order):
                self.consume(engine, stamp)
                with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic', return_value=11 + i * .00001):
                    engine._emit_sample(force=True)
            self.assertEqual(engine.collect()['summary']['peak_tps'], 4)
            self.assertLessEqual(max(s['tps'] for s in samples), 4)

    def test_auth_and_setup_never_enter_business_bins(self):
        engine, _ = self.engine()
        for step in (0, 1, 2): engine._consume_event({'kind': 'request_started', 'step': step})
        for _ in range(20):
            self.consume(engine, 1000, step=1); self.consume(engine, 1000, step=2)
        self.consume(engine, 1100)
        result = engine.collect()['summary']
        self.assertEqual(result['http_total'], 41)
        self.assertEqual(result['business_total'], 1)
        self.assertEqual(result['business_started'], 1)
        self.assertEqual(result['peak_tps'], 1)
        self.assertEqual(result['throughput']['business_events'], 1)

    def test_missing_time_does_not_erase_valid_average_or_latency(self):
        engine, samples = self.engine()
        self.consume(engine, 1000); self.consume(engine, None)
        with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic', return_value=15):
            engine._emit_sample(force=True); engine._emit_sample(force=True)
        result = engine.collect()['summary']
        self.assertIsNone(result['peak_tps'])
        self.assertIsNone(samples[-1]['tps'])
        self.assertEqual(result['business_rps'], .4)
        self.assertEqual(result['avg_rt'], 1)

    def test_sparse_seconds_and_idle_emission_keep_explicit_window(self):
        engine, samples = self.engine()
        for stamp in (1000, 1001, 4100): self.consume(engine, stamp)
        with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic', return_value=500):
            engine._emit_sample(force=True)
        self.assertEqual(samples[-1]['tps'], 1)
        self.assertEqual(samples[-1]['throughput']['latest_bucket_start_ms'], 4000)
        self.assertIn('最近已观测', samples[-1]['throughput']['notice'])
        self.assertEqual(engine.collect()['summary']['peak_tps'], 2)

    def test_builtin_collector_rate_behavior_is_unchanged(self):
        from apps.perf_testing.services.metrics import MetricsCollector
        collector = MetricsCollector()
        for _ in range(3): collector.record('business', 1, True)
        self.assertEqual(collector.take_window(.5)['tps'], 6)
        self.assertEqual(collector.build_summary(2)['peak_tps'], 6)
