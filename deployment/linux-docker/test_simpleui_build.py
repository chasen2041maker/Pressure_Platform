"""Offline checks for the sole, hash-locked sdist exception; never builds it."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent


class SimpleuiBuildTests(unittest.TestCase):
    def load_helper(self):
        spec = importlib.util.spec_from_file_location('simpleui_build', HERE / 'simpleui_build.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        return helper

    def test_docker_isolates_only_one_sdist_and_retains_binary_runtime_install(self):
        source = (HERE / 'Dockerfile.backend').read_text()
        self.assertIn('FROM ${PYTHON_IMAGE} AS simpleui_builder', source)
        self.assertIn('RUN --network=none', source)
        self.assertIn('wheel --no-index --no-deps --no-build-isolation', source)
        self.assertIn('--require-hashes --only-binary=:all:', source)
        self.assertIn('--no-index --no-deps --only-binary=:all: /opt/pressure-deploy/wheels/django_simpleui-2025.6.24-py3-none-any.whl', source)
        self.assertNotIn('--find-links', source)
        self.assertNotIn('--no-binary', source)
        self.assertNotIn('--no-use-pep517', source)
        self.assertIn('COPY --from=simpleui_builder /wheels/', source)

    def test_build_dependencies_have_exact_frozen_artifact_hashes(self):
        receipt = json.loads((HERE / 'simpleui-build-sources.json').read_text())
        actual = (HERE / 'requirements-simpleui-build.lock.txt').read_text().splitlines()
        expected = [f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}"
                    for item in receipt['build_tools']]
        self.assertEqual(actual, expected)
        self.assertEqual(set(item['name'] for item in receipt['build_tools']), {'pip', 'setuptools', 'wheel', 'packaging'})
        self.assertEqual(hashlib.sha256((HERE / 'requirements-linux.lock.txt').read_bytes()).hexdigest(),
                         receipt['runtime_lock_sha256'])
        self.assertEqual(receipt['source']['version'], '2025.6.24')

    def test_runtime_installs_checked_local_wheel_before_complete_lock(self):
        source = (HERE / 'Dockerfile.backend').read_text().split('FROM ${PYTHON_IMAGE}\n')[-1]
        local = 'pip install --no-cache-dir --no-index --no-deps --only-binary=:all: /opt/pressure-deploy/wheels/django_simpleui-2025.6.24-py3-none-any.whl'
        locked = 'pip install --no-cache-dir --no-deps --only-binary=:all: -r /opt/pressure-deploy/requirements-linux.lock.txt'
        self.assertIn(local, source)
        self.assertIn(locked, source)
        self.assertLess(source.index(local), source.index(locked))
        self.assertLess(source.index(locked), source.index('python -m pip check'))
        for forbidden in ('--find-links', '--upgrade', '--force-reinstall', '--ignore-installed'):
            self.assertNotIn(forbidden, source)

    def test_source_digest_and_size_must_match_before_use(self):
        helper = self.load_helper()
        data = b'synthetic archive bytes, never executed'
        source = dict(helper.SOURCE, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        helper.verify_source(data, source)
        for value in (data + b'changed', b''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                helper.verify_source(value, source)

    def wheel(self, folder, *, version='2025.6.24', dependency='django', tag='py3-none-any', assets=True):
        path = Path(folder) / 'django_simpleui-2025.6.24-py3-none-any.whl'
        prefix = 'django_simpleui-2025.6.24.dist-info/'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(prefix + 'METADATA', f'Name: django-simpleui\nVersion: {version}\nRequires-Dist: {dependency}\n')
            archive.writestr(prefix + 'WHEEL', f'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n')
            archive.writestr('simpleui/__init__.py', '# synthetic, never imported')
            if assets:
                archive.writestr('simpleui/static/test.css', '')
                archive.writestr('simpleui/templates/test.html', '')
        return path

    def test_built_wheel_identity_dependency_and_assets_are_checked(self):
        helper = self.load_helper()
        with tempfile.TemporaryDirectory() as folder:
            path = self.wheel(folder)
            result = helper.check_wheel(Path(folder))
            self.assertEqual(result['wheel_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(result['source_sha256'], helper.SOURCE['sha256'])

    def test_invalid_built_wheel_is_rejected(self):
        helper = self.load_helper()
        for options in ({'version': '2025.9.1'}, {'dependency': 'django>=5'},
                        {'tag': 'cp312-cp312-linux_x86_64'}, {'assets': False}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                self.wheel(folder, **options)
                with self.assertRaises(ValueError): helper.check_wheel(Path(folder))

    def test_multiple_wheels_are_rejected(self):
        helper = self.load_helper()
        with tempfile.TemporaryDirectory() as folder:
            self.wheel(folder)
            (Path(folder) / 'another.whl').write_bytes(b'')
            with self.assertRaises(ValueError): helper.check_wheel(Path(folder))


if __name__ == '__main__':
    unittest.main()
