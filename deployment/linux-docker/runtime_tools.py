"""Small local file helpers. Docker operations belong to the host CLI."""
from collections.abc import Callable
from contextlib import closing
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import tarfile


def sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def runtime_path(root: Path) -> Path:
    if not root.is_absolute() or len(root.parts) < 3 or root.resolve() != root:
        raise ValueError('Use an absolute, dedicated platform root without symlinks')
    runtime = root / 'runtime'
    if runtime.is_symlink():
        raise ValueError('Runtime must not be a symlink')
    return runtime


def prepare_runtime(root: Path, binary: Path, expected_sha256: str) -> dict:
    runtime = runtime_path(root)
    if sha256(binary) != expected_sha256:
        raise ValueError('k6 binary SHA256 does not match the delivery manifest')
    with binary.open('rb') as handle:
        header = handle.read(64)
    if len(header) < 64 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
        raise ValueError('This package requires a Linux amd64 ELF k6 binary')
    for name in ('bin', 'private', 'media', 'logs'):
        directory = runtime / name
        if directory.is_symlink():
            raise ValueError('Runtime directories must not be symlinks')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    target = runtime / 'bin/k6-linux'
    if target.is_symlink():
        raise ValueError('k6 binary must not be a symlink')
    if target.exists():
        if sha256(target) != expected_sha256:
            raise FileExistsError('Existing k6 binary differs; back up and perform an explicit version upgrade')
    else:
        with binary.open('rb') as source, target.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        if sha256(target) != expected_sha256:
            raise ValueError('Copied binary hash differs; initialization stopped')
    target.chmod(0o555)
    return {'runtime': str(runtime), 'binary_sha256': expected_sha256, 'architecture': 'linux/amd64'}


def ensure_secret(private: Path) -> Path:
    if private.is_symlink():
        raise ValueError('Private directory must not be a symlink')
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    target = private / 'django-secret.txt'
    if target.is_symlink():
        raise ValueError('Secret file must not be a symlink')
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not target.is_file() or len(target.read_text(encoding='utf-8').strip()) < 50:
            raise ValueError('Existing secret is invalid; never overwrite it automatically') from None
    else:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(secrets.token_urlsafe(64) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
    target.chmod(0o600)
    return target


def create_first_admin(users: object, username: str, password: str, validate: Callable) -> bool:
    if users.filter(is_superuser=True).exists():
        return False
    if users.filter(username=username).exists():
        raise FileExistsError('Username already exists; its password and permissions were not changed')
    if not username.strip():
        raise ValueError('Username must not be empty')
    validate(password)
    users.create_superuser(username=username, password=password, email='')
    return True


def check_database(runtime: Path) -> dict:
    database = runtime / 'private/testhub.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM perf_executions WHERE status IN ('PENDING','PREPARING','RUNNING','STOPPING')").fetchone()[0]
    if integrity != 'ok' or active:
        raise RuntimeError('Database integrity failed or active executions remain; stop/reconcile tasks before backup')
    return {'integrity': 'ok', 'active_executions': active}


def backup_runtime(root: Path, destination: Path, assert_stopped: Callable) -> dict:
    runtime = runtime_path(root)
    if not destination.is_absolute() or destination.resolve().is_relative_to(runtime) or destination.exists():
        raise FileExistsError('Use a new absolute archive path outside runtime')
    assert_stopped()
    status = check_database(runtime)
    paths = [runtime, *sorted(runtime.rglob('*'))]
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in paths):
        raise ValueError('Backup refuses symlinks or special files')
    with destination.open('xb') as output:
        os.chmod(destination, 0o600)
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for path in paths:
                if path.is_symlink():
                    raise ValueError('Runtime changed while creating cold backup')
                archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return {'archive': str(destination), 'sha256': sha256(destination), **status}


def restore_runtime(archive_path: Path, new_root: Path, expected_sha256: str) -> dict:
    runtime_path(new_root)
    if new_root.exists():
        raise FileExistsError('Restore only into a new platform root; existing data is never overwritten')
    if sha256(archive_path) != expected_sha256:
        raise ValueError('Backup SHA256 mismatch')
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'runtime'
                    or '\\' in member.name or ':' in member.name or member.name in names
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe or duplicate backup member')
            names.add(member.name)
        required = {'runtime/private/testhub.sqlite3', 'runtime/private/django-secret.txt', 'runtime/bin/k6-linux'}
        if not required <= names:
            raise ValueError('Backup lacks database, secret or engine binary')
        new_root.mkdir(parents=True, mode=0o700)
        archive.extractall(new_root, filter='data')
    ensure_secret(new_root / 'runtime/private')
    (new_root / 'runtime/bin/k6-linux').chmod(0o555)
    status = check_database(new_root / 'runtime')
    return {'root': str(new_root), 'archive_sha256': expected_sha256, **status}
