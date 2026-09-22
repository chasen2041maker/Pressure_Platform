"""Container entry points. Only explicit init/admin commands initialize data."""
import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_tools import create_first_admin, ensure_secret, runtime_path, sha256

APP = Path('/app')


def configured_runtime() -> Path:
    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise RuntimeError('This deployment package requires Linux amd64')
    value = os.environ.get('PRESSURE_PLATFORM_ROOT', '')
    runtime = runtime_path(Path(value))
    if os.environ.get('PERF_PRIVATE_ROOT') != str(runtime / 'private'):
        raise ValueError('PERF_PRIVATE_ROOT must use the same absolute host runtime path')
    if os.environ.get('K6_LINUX_BIN') != str(runtime / 'bin/k6-linux'):
        raise ValueError('K6_LINUX_BIN must use the same absolute host runtime path, including version probes')
    if not runtime.is_dir() or not (runtime / 'bin/k6-linux').is_file():
        raise FileNotFoundError('Prepare host runtime and the verified k6 binary first')
    return runtime


def configure_django() -> None:
    os.environ['DJANGO_SETTINGS_MODULE'] = 'backend.pressure_intranet_settings'
    sys.path.insert(0, str(APP))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'admin', 'serve', 'doctor', 'health'])
    args = parser.parse_args()
    if args.command == 'health':
        try:
            with urlopen(Request('http://127.0.0.1:8000/api/users/me/'), timeout=3):
                return 1  # Protected API must not accept an anonymous health request.
        except HTTPError as error:
            return 0 if error.code in (401, 403) else 1
        except OSError:
            return 1
    runtime = configured_runtime()
    configure_django()
    if args.command == 'init':
        if (runtime / 'private/testhub.sqlite3').exists() and not (runtime / 'private/django-secret.txt').exists():
            raise RuntimeError('Database exists but its secret is missing; restore the matching secret, never regenerate')
        ensure_secret(runtime / 'private')
        subprocess.run([sys.executable, str(APP / 'manage.py'), 'migrate', '--noinput'], check=True, cwd=APP)
        print('数据库迁移完成；既有密钥和账号密码保持。首次管理员请单独运行 admin。')
        return 0
    secret = runtime / 'private/django-secret.txt'
    if not secret.is_file() or secret.is_symlink() or len(secret.read_text(encoding='utf-8').strip()) < 50:
        raise ValueError('Valid existing secret required; run init only for a new installation')
    if args.command == 'serve':
        subprocess.run([sys.executable, str(APP / 'manage.py'), 'migrate', '--check'], check=True, cwd=APP)
        os.chdir(APP)
        from recovery_supervisor import serve
        return serve(['gunicorn', '--config', '/opt/pressure-deploy/gunicorn.conf.py', 'backend.wsgi:application'],
                     [sys.executable, str(APP/'manage.py'), 'recover_portfolio_reminders'], cwd=APP)
    # These management operations must not run application startup cleanup.
    sys.argv = ['manage.py', 'shell']
    import django
    django.setup()
    if args.command == 'admin':
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.db import transaction
        users = get_user_model().objects
        if users.filter(is_superuser=True).exists():
            print('已有管理员；未修改任何账号或密码。')
            return 0
        username = input('首次管理员用户名：').strip()
        password = getpass.getpass('首次管理员密码（不回显）：')
        if password != getpass.getpass('再次输入密码：'):
            raise ValueError('Passwords differ')
        with transaction.atomic():
            created = create_first_admin(users, username, password, validate_password)
        print('首次管理员已创建。' if created else '已有管理员；未修改任何账号或密码。')
    else:
        from runtime_probe import probe
        from apps.perf_testing.engines.k6_docker import resolve_config
        config = resolve_config()
        module = probe(runtime / 'bin/k6-linux', config['image_id'], os.environ.get('K6_EXPECTED_SHA256', ''))
        print(json.dumps({'runtime': str(runtime), 'binary_sha256': sha256(runtime / 'bin/k6-linux'),
                          'binary_version': config['binary_version'], 'image_id': config['image_id'],
                          'network': config['network'], 'network_id': config['network_id'],
                          'sse_module': module}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'部署命令失败：{error}', file=sys.stderr)
        raise SystemExit(1)
