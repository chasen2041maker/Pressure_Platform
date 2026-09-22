"""Linux host preparation and cold backup/restore; never operates a live task."""
import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

from runtime_tools import backup_runtime, prepare_runtime, restore_runtime


def require_stopped(project: str) -> None:
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise ValueError('Invalid Compose project name')
    for label in (f'com.docker.compose.project={project}', 'io.testhub.k6.owner'):
        result = subprocess.run(['docker', 'ps', '--filter', f'label={label}', '--format', '{{.Names}}'],
                                check=True, capture_output=True, text=True, timeout=15)
        if result.stdout.strip():
            raise RuntimeError('Compose services or owned k6 containers are running; stop them through their owner before cold backup')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--root', type=Path, required=True)
    prepare.add_argument('--k6', type=Path, required=True)
    prepare.add_argument('--sha256', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--root', type=Path, required=True)
    backup.add_argument('--archive', type=Path, required=True)
    backup.add_argument('--project', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--new-root', type=Path, required=True)
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--sha256', required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        parser.error('This package requires Linux amd64; use Python 3.12 or later for safe archive extraction')
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or later is required; run this helper in the provided backend image if unavailable')
    if args.command == 'prepare':
        result = prepare_runtime(args.root, args.k6, args.sha256)
    elif args.command == 'backup':
        result = backup_runtime(args.root, args.archive, assert_stopped=lambda: require_stopped(args.project))
    else:
        result = restore_runtime(args.archive, args.new_root, args.sha256)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'部署命令失败：{error}', file=sys.stderr)
        raise SystemExit(1)
