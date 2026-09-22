"""Run catalog API tests against a disposable runtime, never deployment data."""
import os
import logging
from pathlib import Path
import sys
import tempfile


def main():
    source = Path.cwd()
    if not (source / 'manage.py').is_file():
        raise SystemExit('Run from the backend source directory')
    sys.path.insert(0, str(source))
    with tempfile.TemporaryDirectory(prefix='pressure-catalog-tests-') as folder:
        private = Path(folder) / 'runtime' / 'private'
        private.mkdir(parents=True)
        (private / 'django-secret.txt').write_text('disposable-catalog-test-secret', encoding='utf-8')
        os.environ.update(PRESSURE_PLATFORM_ROOT=folder, DJANGO_SETTINGS_MODULE='backend.pressure_settings',
                          RUN_MAIN='false', PYTHONDONTWRITEBYTECODE='1',
                          K6_ADAPTER_TEST_ROOT=str(Path(folder) / 'adapter-tests'))
        import django
        from django.conf import settings
        settings.LOGGING_CONFIG = None
        logging.disable(logging.CRITICAL)
        django.setup()
        from django.test.runner import DiscoverRunner
        labels = sys.argv[1:] or ['apps.perf_testing.tests.test_api_catalog',
                                  'apps.perf_testing.tests.test_catalog_union_schema',
                                  'apps.perf_testing.tests.test_k6_uploads',
                                  'apps.perf_testing.tests.test_catalog_source',
                                  'apps.perf_testing.tests.test_prepared_requests',
                                  'apps.perf_testing.tests.test_prepared_dependencies',
                                  'apps.perf_testing.tests.test_prepared_recovery',
                                  'apps.perf_testing.tests.test_scenario_defaults',
                                  'apps.perf_testing.tests.test_pool_verification',
                                  'apps.perf_testing.tests.test_prepared_websocket',
                                  'apps.perf_testing.tests.test_pool_setup_verification',
                                  'apps.perf_testing.tests.test_pool_dependency_launch',
                                  'apps.perf_testing.tests.test_k6_request_id',
                                  'apps.perf_testing.tests.test_k6_setup_dependencies',
                                  'apps.perf_testing.tests.test_k6_capabilities',
                                  'apps.perf_testing.tests.test_websocket_steps',
                                  'apps.perf_testing.tests.test_sse_steps',
                                  'apps.perf_testing.tests.test_sse_diagnostics',
                                  'apps.perf_testing.tests.test_k6_sse_integration',
                                  'apps.perf_testing.tests.test_reminder_recovery',
                                  'apps.perf_testing.tests.test_reminder_recovery_db',
                                  'apps.perf_testing.tests.test_reminder_recovery_native',
                                  'apps.perf_testing.tests.test_execution_policy',
                                  'apps.perf_testing.tests.test_execution_policy_api',
                                  'apps.perf_testing.tests.test_k6_websocket_metrics',
                                  'apps.perf_testing.tests.test_k6_samples',
                                  'apps.perf_testing.tests.test_k6_websocket_integration',
                                  'apps.perf_testing.tests.test_k6_worker',
                                  'apps.perf_testing.tests.test_k6_thresholds',
                                  'apps.perf_testing.tests.test_k6_stop_race',
                                  'apps.perf_testing.tests.test_k6_start_wait',
                                  'apps.perf_testing.tests.test_k6_report',
                                  'apps.perf_testing.tests.test_k6_engine.AdapterContractTests']
        failures = DiscoverRunner(verbosity=1, interactive=False).run_tests(labels)
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
