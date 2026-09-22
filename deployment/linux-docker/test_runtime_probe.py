"""Portable runtime doctor checks only; no Docker daemon is contacted."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from runtime_probe import probe


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / 'k6-linux'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = b'\x3e\x00'
        self.binary.write_bytes(header)
        self.digest = hashlib.sha256(header).hexdigest()
        self.image = 'sha256:' + 'a' * 64

    @patch('runtime_probe.subprocess.run')
    def test_missing_or_mismatched_pin_never_starts_container(self, run):
        for digest in ('', 'not-a-hash', '0' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                probe(self.binary, self.image, digest)
        run.assert_not_called()

    @patch('runtime_probe.subprocess.run')
    def test_invalid_binary_or_image_never_starts_container(self, run):
        with self.assertRaises(ValueError):
            probe(self.binary, 'example:latest', self.digest)
        self.binary.write_bytes(b'not-an-elf')
        with self.assertRaises(ValueError):
            probe(self.binary, self.image, hashlib.sha256(b'not-an-elf').hexdigest())
        run.assert_not_called()

    @patch('runtime_probe.subprocess.run')
    def test_success_uses_fixed_image_no_network_and_bounded_container(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'TESTHUB_SSE_MODULE_OK:testhub-sse/1\n', '')
        result = probe(self.binary, self.image, self.digest)
        command = run.call_args.args[0]
        for flag in ('--network=none', '--pull=never', '--read-only', '--cap-drop=ALL', '--memory=256m', '--cpus=1'):
            self.assertIn(flag, command)
        self.assertIn(self.image, command)
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertEqual(result['sha256'], self.digest)
        self.assertEqual(result['stream_requests'], 0)

    @patch('runtime_probe.subprocess.run')
    def test_failure_or_duplicate_proof_remains_failure(self, run):
        for code, output in ((1, ''), (0, ''), (0, 'TESTHUB_SSE_MODULE_OK:testhub-sse/1\n' * 2)):
            run.return_value = subprocess.CompletedProcess([], code, output, '')
            with self.subTest(code=code, output=output), self.assertRaises(ValueError):
                probe(self.binary, self.image, self.digest)

    @patch('runtime_probe.subprocess.run')
    def test_binary_change_during_probe_is_rejected(self, run):
        def mutate(*args, **kwargs):
            self.binary.write_bytes(self.binary.read_bytes() + b'changed')
            return subprocess.CompletedProcess([], 0, 'TESTHUB_SSE_MODULE_OK:testhub-sse/1\n', '')
        run.side_effect = mutate
        with self.assertRaises(ValueError):
            probe(self.binary, self.image, self.digest)

    @patch('runtime_probe.subprocess.run', side_effect=subprocess.TimeoutExpired('docker', 30))
    def test_timeout_is_not_reported_as_success(self, _run):
        with self.assertRaises(subprocess.TimeoutExpired):
            probe(self.binary, self.image, self.digest)


if __name__ == '__main__':
    unittest.main()
