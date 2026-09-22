"""Check an explicitly pinned local k6 binary without contacting a target service."""
import hashlib
from pathlib import Path
import re
import subprocess
import uuid


def _binary(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected or ''):
        raise ValueError('Set K6_EXPECTED_SHA256 to the reviewed binary SHA256')
    if path.is_symlink() or not path.is_file():
        raise ValueError('Runtime binary must be an existing regular file')
    with path.open('rb') as source:
        header = source.read(64)
        source.seek(0)
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if len(header) < 64 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
        raise ValueError('Runtime binary must be Linux amd64 ELF')
    if digest != expected:
        raise ValueError('Runtime binary differs from K6_EXPECTED_SHA256')
    return digest


def probe(path, image, expected_sha256):
    path = Path(path)
    digest = _binary(path, expected_sha256)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image or ''):
        raise ValueError('Pinned existing runner image required')
    if any(character in str(path) for character in (',', '\r', '\n')):
        raise ValueError('Invalid runtime mount path')
    script = (
        "import sse from 'k6/x/testhub-sse';\n"
        "if (sse.version !== 'testhub-sse/1' || typeof sse.open !== 'function') throw new Error('SSE module contract');\n"
        "export default function () { console.log('TESTHUB_SSE_MODULE_OK:testhub-sse/1'); }\n"
    )
    command = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=256m', '--cpus=1', '--user=0:0',
        '--label=io.testhub.k6.owner=runtime-doctor', '--name=pressure-runtime-probe-' + uuid.uuid4().hex,
        '--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m', '--env=HOME=/tmp',
        '--mount=type=bind,source=' + str(path.resolve()) + ',target=/k6,readonly',
        '--entrypoint=/k6', '-i', image, 'run', '--quiet', '--no-usage-report', '--vus=1', '--iterations=1', '-']
    result = subprocess.run(command, input=script, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or (result.stdout + result.stderr).count('TESTHUB_SSE_MODULE_OK:testhub-sse/1') != 1:
        raise ValueError('Zero-network SSE module proof failed; inspect any owned probe before retry')
    _binary(path, digest)
    return {'sha256': digest, 'module': 'k6/x/testhub-sse', 'api_version': 'testhub-sse/1',
            'network': 'none', 'stream_requests': 0}
