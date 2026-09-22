"""Build a pinned candidate without fetching k6 source or replacing a runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def command(args, *, cwd=ROOT, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True, encoding='utf-8').strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--k6-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    env = dict(os.environ, GOTOOLCHAIN='local', GOWORK='off')
    env.pop('GOFLAGS', None)
    lock = json.loads((ROOT / 'runtime-lock.json').read_text(encoding='utf-8'))
    source, output = args.k6_source.resolve(), args.output.resolve()
    if command(['git', 'rev-parse', 'HEAD'], cwd=source) != lock['k6_revision']:
        raise SystemExit('Pinned k6 source revision does not match')
    if command(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=source):
        raise SystemExit('Pinned k6 source must have a clean working tree')
    if command(['go', 'version'], env=env).split()[2] != lock['go_version']:
        raise SystemExit('Pinned Go toolchain does not match')
    if output.exists() or output.with_suffix(output.suffix + '.json').exists():
        raise SystemExit('Candidate output already exists; no overwrite is permitted')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bounded-sse-build-') as folder:
        module = Path(folder) / 'candidate.mod'
        shutil.copyfile(ROOT / 'go.mod', module)
        shutil.copyfile(ROOT / 'go.sum', module.with_suffix('.sum'))
        command(['go', 'mod', 'edit', f'-modfile={module}', f'-replace=go.k6.io/k6/v2={source.as_posix()}'], env=env)
        if args.test:
            subprocess.run(['go', 'test', f'-modfile={module}', '-mod=readonly', '-timeout=60s', './...'], cwd=ROOT, env=env, check=True)
        with tempfile.TemporaryDirectory(prefix='.bounded-sse-', dir=output.parent) as stage:
            binary = Path(stage) / output.name
            subprocess.run(['go', 'build', f'-modfile={module}', '-mod=readonly', '-trimpath', '-buildvcs=false',
                            '-ldflags=-s -w', '-o', str(binary), './cmd/k6-bounded-sse'], cwd=ROOT, env=env, check=True)
            # Atomic, exclusive publication of the candidate on the same filesystem.
            os.link(binary, output)
    inputs = {str(path.relative_to(ROOT)).replace('\\', '/'): digest(path)
              for path in sorted(ROOT.rglob('*')) if path.is_file() and path.suffix in ('.go', '.mod', '.sum', '.json', '.py', '.md')}
    receipt = dict(lock, binary_sha256=digest(output), inputs=inputs,
                   target=command(['go', 'env', 'GOOS', 'GOARCH'], env=env).splitlines(), installed_runtime_changed=False)
    with output.with_suffix(output.suffix + '.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False)
    print(json.dumps({'binary_sha256': receipt['binary_sha256'], 'api_version': lock['api_version'],
                      'target': receipt['target'], 'installed_runtime_changed': False}))


if __name__ == '__main__':
    main()
