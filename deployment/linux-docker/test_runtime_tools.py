"""Offline package tests. Never contacts Docker or a live database."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest


class RuntimeToolsTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('runtime_tools.py')
        self.assertTrue(source.exists(), 'Deployment runtime helpers must exist')
        spec = importlib.util.spec_from_file_location('runtime_tools', source)
        self.tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tools)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'platform'
        self.binary = self.base / 'k6-linux'
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        elf[18:20] = (62).to_bytes(2, 'little')
        self.binary.write_bytes(elf)
        self.digest = hashlib.sha256(elf).hexdigest()

    def prepare(self):
        self.tools.prepare_runtime(self.root, self.binary, self.digest)
        return self.root / 'runtime'

    def make_database(self, runtime, status='COMPLETED'):
        db = sqlite3.connect(runtime / 'private/testhub.sqlite3')
        try:
            db.execute('CREATE TABLE perf_executions(status TEXT)')
            db.execute('INSERT INTO perf_executions VALUES (?)', (status,))
            db.execute('CREATE TABLE saved_config(value TEXT)')
            db.execute('INSERT INTO saved_config VALUES (?)', ('private-test-sentinel',))
            db.commit()
        finally:
            db.close()

    def test_binary_hash_and_architecture_checked_before_install(self):
        with self.assertRaises(ValueError):
            self.tools.prepare_runtime(self.root, self.binary, '0' * 64)
        self.assertFalse((self.root / 'runtime/bin/k6-linux').exists())
        self.binary.write_bytes(b'not an ELF binary')
        with self.assertRaises(ValueError):
            self.tools.prepare_runtime(self.root, self.binary, hashlib.sha256(self.binary.read_bytes()).hexdigest())

    def test_prepare_is_idempotent_and_never_replaces_an_existing_binary(self):
        runtime = self.prepare()
        original = (runtime / 'bin/k6-linux').read_bytes()
        self.prepare()
        self.assertEqual((runtime / 'bin/k6-linux').read_bytes(), original)
        self.binary.write_bytes(original + b'new-version')
        with self.assertRaises(FileExistsError):
            self.tools.prepare_runtime(self.root, self.binary, hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertEqual((runtime / 'bin/k6-linux').read_bytes(), original)

    def test_secret_created_once_and_invalid_existing_secret_not_overwritten(self):
        private = self.prepare() / 'private'
        secret = self.tools.ensure_secret(private)
        contents = secret.read_bytes()
        self.assertGreaterEqual(len(contents), 64)
        self.assertEqual(self.tools.ensure_secret(private).read_bytes(), contents)
        secret.write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.tools.ensure_secret(private)
        self.assertEqual(secret.read_text(encoding='utf-8'), 'broken')

    def test_backup_restore_roundtrip_keeps_db_secret_binary_and_artifacts(self):
        runtime = self.prepare()
        secret = self.tools.ensure_secret(runtime / 'private').read_bytes()
        self.make_database(runtime)
        (runtime / 'media/report.json').write_text('{"saved":true}', encoding='utf-8')
        backup = self.base / 'backup.tar.gz'
        result = self.tools.backup_runtime(self.root, backup, assert_stopped=lambda: None)
        restored = self.base / 'restored'
        self.tools.restore_runtime(backup, restored, result['sha256'])
        self.assertEqual((restored / 'runtime/private/django-secret.txt').read_bytes(), secret)
        self.assertEqual((restored / 'runtime/bin/k6-linux').read_bytes(), self.binary.read_bytes())
        db = sqlite3.connect((restored / 'runtime/private/testhub.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            self.assertEqual(db.execute('SELECT value FROM saved_config').fetchone()[0], 'private-test-sentinel')
        finally:
            db.close()
        self.assertNotIn('private-test-sentinel', json.dumps(result))
        with self.assertRaises(FileExistsError):
            self.tools.backup_runtime(self.root, backup, assert_stopped=lambda: None)
        with self.assertRaises(FileExistsError):
            self.tools.restore_runtime(backup, restored, result['sha256'])

    def test_backup_requires_stopped_services_and_no_active_execution(self):
        runtime = self.prepare()
        self.tools.ensure_secret(runtime / 'private')
        self.make_database(runtime, 'RUNNING')
        destination = self.base / 'backup.tar.gz'
        with self.assertRaises(RuntimeError):
            self.tools.backup_runtime(self.root, destination, assert_stopped=lambda: None)
        self.assertFalse(destination.exists())
        def still_running():
            raise RuntimeError('Backend is running')
        with self.assertRaises(RuntimeError):
            self.tools.backup_runtime(self.root, destination, assert_stopped=still_running)
        self.assertFalse(destination.exists())

    def test_restore_rejects_hash_path_traversal_and_links_before_creating_target(self):
        for kind in ('traversal', 'symlink'):
            archive = self.base / f'{kind}.tar.gz'
            with tarfile.open(archive, 'w:gz') as handle:
                member = tarfile.TarInfo('../escape' if kind == 'traversal' else 'runtime/link')
                if kind == 'symlink':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                    handle.addfile(member)
                else:
                    member.size = 1
                    handle.addfile(member, io.BytesIO(b'x'))
            target = self.base / f'restore-{kind}'
            with self.assertRaises(ValueError):
                self.tools.restore_runtime(archive, target, hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertFalse(target.exists())
        with self.assertRaises(ValueError):
            self.tools.restore_runtime(archive, self.base / 'wrong-hash', '0' * 64)

    def test_admin_creation_never_changes_existing_account_or_admin(self):
        class Manager:
            def __init__(self):
                self.accounts = {}
            def filter(self, **kwargs):
                found = any(all(row.get(key) == value for key, value in kwargs.items()) for row in self.accounts.values())
                return type('Query', (), {'exists': lambda self: found})()
            def create_superuser(self, **kwargs):
                self.accounts[kwargs['username']] = {**kwargs, 'is_superuser': True}
        users = Manager()
        self.assertTrue(self.tools.create_first_admin(users, 'admin', 'test-password', lambda password: None))
        self.assertFalse(self.tools.create_first_admin(users, 'admin', 'changed', lambda password: None))
        self.assertEqual(users.accounts['admin']['password'], 'test-password')
        other = Manager()
        other.accounts['existing'] = {'username': 'existing', 'password': 'original', 'is_superuser': False}
        with self.assertRaises(FileExistsError):
            self.tools.create_first_admin(other, 'existing', 'changed', lambda password: None)
        self.assertEqual(other.accounts['existing']['password'], 'original')


if __name__ == '__main__':
    unittest.main()
