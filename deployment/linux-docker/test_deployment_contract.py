"""Static package contract only; never builds, contacts Docker, or opens a database."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

HERE = Path(__file__).resolve().parent
STAGE = HERE.parents[1]


class DeploymentContractTests(unittest.TestCase):
    def compose(self):
        return yaml.safe_load((HERE / 'compose.yaml').read_text(encoding='utf-8'))

    def test_only_backend_web_and_only_web_publishes(self):
        config = self.compose()
        self.assertEqual(set(config['services']), {'backend', 'web'})
        self.assertNotIn('ports', config['services']['backend'])
        self.assertEqual(len(config['services']['web']['ports']), 1)
        self.assertIn('PRESSURE_BIND_IP:?', config['services']['web']['ports'][0])
        for service in config['services'].values():
            self.assertEqual(service['pull_policy'], 'never')
            self.assertEqual(service['platform'], 'linux/amd64')
            self.assertTrue(service['read_only'])
            self.assertNotIn('privileged', service)
        self.assertTrue(config['networks']['pressure']['external'])

    def test_runtime_same_absolute_path_socket_only_backend_and_no_anonymous_volume(self):
        services = self.compose()['services']
        volumes = services['backend']['volumes']
        runtime = next(item for item in volumes if item['target'].endswith('/runtime'))
        self.assertEqual(runtime['source'], runtime['target'])
        self.assertFalse(runtime['bind']['create_host_path'])
        self.assertEqual(runtime['type'], 'bind')
        self.assertTrue(any(item['target'] == '/var/run/docker.sock' for item in volumes))
        self.assertFalse(services['web'].get('volumes'))
        self.assertEqual(services['backend']['user'], '0:0')
        env = services['backend']['environment']
        self.assertEqual(env['K6_RUNNER'], 'DOCKER')
        self.assertTrue(env['PERF_PRIVATE_ROOT'].endswith('/runtime/private'))
        self.assertTrue(env['K6_LINUX_BIN'].endswith('/runtime/bin/k6-linux'))

    def test_entrypoint_single_instance_and_health_keep_auth(self):
        backend = self.compose()['services']['backend']
        self.assertEqual(backend['command'], ['serve'])
        self.assertIn('COMPOSE_PROJECT_NAME:?', backend['container_name'])
        self.assertIn('health', backend['healthcheck']['test'])
        source = ast.parse((HERE / 'gunicorn.conf.py').read_text())
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in source.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        self.assertEqual(values['workers'], 1)
        self.assertEqual(values['worker_class'], 'gthread')
        self.assertIn('flock', (HERE / 'gunicorn.conf.py').read_text())

    def test_nginx_spa_api_and_private_files_are_distinct(self):
        source = (HERE / 'nginx.conf').read_text()
        self.assertIn('location /api/', source)
        self.assertIn('proxy_pass http://backend:8000;', source)
        self.assertIn('proxy_set_header Authorization $http_authorization;', source)
        self.assertIn('proxy_set_header Host $http_host;', source)
        self.assertIn('try_files $uri $uri/ /index.html;', source)
        self.assertIn('location ^~ /media/ { return 404; }', source)
        self.assertIn('location ^~ /runtime/ { return 404; }', source)
        self.assertNotIn('alias ', source)
        self.assertNotIn('access_log /', source)

    def test_linux_lock_exact_local_versions_minus_windows_plus_verified_gunicorn(self):
        local = STAGE / 'deployment/requirements-local.lock.txt'
        expected = {line for line in local.read_text().splitlines() if line and not line.lower().startswith('pywin32==')}
        actual = {line for line in (HERE / 'requirements-linux.lock.txt').read_text().splitlines() if line and not line.startswith('#')}
        metadata = json.loads((HERE / 'dependency-sources.json').read_text())
        self.assertEqual(hashlib.sha256(local.read_bytes()).hexdigest(),
                         metadata['local_lock_sha256'])
        self.assertEqual(actual, expected | {'gunicorn==' + metadata['gunicorn']['version']})
        self.assertTrue(metadata['gunicorn']['source'].startswith('https://pypi.org/'))
        self.assertFalse(any(line.lower().startswith('pywin32') for line in actual))
        self.assertTrue(all('==' in line and '*' not in line for line in actual))

    def test_backend_build_keeps_full_source_and_uses_fixed_explicit_bases(self):
        source = (HERE / 'Dockerfile.backend').read_text()
        self.assertIn('COPY testhub/ /app/', source)
        self.assertIn('ARG PYTHON_IMAGE', source)
        self.assertIn('ARG DOCKER_CLI_IMAGE', source)
        self.assertNotIn(':latest', source)
        self.assertIn('pip check', source)
        self.assertIn('--no-deps', source)
        self.assertIn('test -f /app/manage.py', source)
        self.assertIn('/app/media/judge_rubrics', source)
        self.assertIn('/opt/pressure-deploy/container.py', source)

    def test_frontend_build_uses_lock_and_runtime_has_no_application_mount(self):
        source = (HERE / 'Dockerfile.web').read_text()
        self.assertIn('npm ci', source)
        self.assertIn('npm run build', source)
        self.assertIn('package-lock.json', source)
        self.assertIn('COPY --from=frontend /frontend/dist/', source)
        self.assertNotIn('npm install', source)

    def test_build_context_excludes_secrets_runtime_and_dependency_trees(self):
        source = (STAGE / '.dockerignore').read_text()
        for pattern in ('**/.env', '**/.env.*', '**/.git', '**/node_modules', '**/private', '**/media', '**/*.sqlite3', 'runtime/'):
            self.assertIn(pattern, source)

    def test_environment_template_has_no_password_or_secret_value(self):
        source = (HERE / '.env.example').read_text()
        entries = dict(line.split('=', 1) for line in source.splitlines() if line and not line.startswith('#'))
        self.assertEqual(entries['PRESSURE_BACKEND_IMAGE'], '')
        self.assertEqual(entries['PRESSURE_WEB_IMAGE'], '')
        self.assertEqual(entries['K6_DOCKER_IMAGE'], '')
        self.assertFalse(any('PASSWORD' in key or 'SECRET' in key for key in entries))
        self.assertIn('未构建', (HERE / 'README.md').read_text(encoding='utf-8'))


class DeploymentRelocationTests(unittest.TestCase):
    def test_contract_runs_from_relocated_release(self):
        outputs = STAGE / 'test-outputs'
        outputs.mkdir(exist_ok=True)
        release = Path(tempfile.mkdtemp(prefix='relocated-release-', dir=outputs))
        destination = release / 'deployment/linux-docker'
        destination.mkdir(parents=True)
        for source in HERE.iterdir():
            if source.is_file():
                shutil.copy2(source, destination / source.name)
        shutil.copy2(STAGE / '.dockerignore', release / '.dockerignore')
        shutil.copy2(STAGE / 'deployment/requirements-local.lock.txt',
                     release / 'deployment/requirements-local.lock.txt')
        result = subprocess.run(
            [sys.executable, '-B', str(destination / Path(__file__).name),
             'DeploymentContractTests'], cwd=release, capture_output=True,
            text=True, encoding='utf-8', timeout=30,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 9 tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
