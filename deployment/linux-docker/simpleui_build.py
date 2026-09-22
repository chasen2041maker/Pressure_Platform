"""The single fixed source-build exception. Never changes runtime dependencies."""
from email.parser import BytesParser
import hashlib
from importlib.metadata import version as installed_version
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

HERE = Path(__file__).resolve().parent
RECEIPT = json.loads((HERE / 'simpleui-build-sources.json').read_text(encoding='utf-8'))
SOURCE = RECEIPT['source']
WHEEL_NAME = 'django_simpleui-2025.6.24-py3-none-any.whl'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_source(data, source=SOURCE):
    require(len(data) == source['size'], 'Source archive size mismatch')
    require(hashlib.sha256(data).hexdigest() == source['sha256'], 'Source archive SHA256 mismatch')


def fetch_source(directory):
    url = SOURCE['url']
    require(urlparse(url).scheme == 'https' and urlparse(url).hostname == 'files.pythonhosted.org',
            'Source must be the frozen official PyPI artifact')
    require(SOURCE['name'] == 'django-simpleui' and SOURCE['version'] == '2025.6.24', 'Unexpected source exception')
    with urlopen(url, timeout=30) as response:
        require(response.geturl() == url, 'Unexpected source redirect')
        data = response.read(SOURCE['size'] + 1)
    verify_source(data)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / SOURCE['filename']).open('xb') as handle:
        handle.write(data)


def check_wheel(directory):
    files = list(directory.glob('*.whl'))
    require(len(files) == 1 and files[0].name == WHEEL_NAME, 'Only the expected simpleui wheel is permitted')
    path = files[0]
    prefix = 'django_simpleui-2025.6.24.dist-info/'
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, 'Corrupt built wheel')
        names = archive.namelist()
        require(len(set(names)) == len(names), 'Duplicate built wheel members')
        metadata = BytesParser().parsebytes(archive.read(prefix + 'METADATA'))
        wheel = BytesParser().parsebytes(archive.read(prefix + 'WHEEL'))
        require(metadata['Name'] == 'django-simpleui' and metadata['Version'] == '2025.6.24', 'Built package identity mismatch')
        require(metadata.get_all('Requires-Dist', []) == ['django'], 'Built runtime dependencies changed')
        require(wheel['Root-Is-Purelib'] == 'true' and wheel.get_all('Tag') == ['py3-none-any'], 'Expected pure Python3 wheel')
        require('simpleui/__init__.py' in names and any(name.startswith('simpleui/static/') for name in names)
                and any(name.startswith('simpleui/templates/') for name in names), 'Built wheel omits application assets')
    return dict(source_sha256=SOURCE['sha256'], wheel=path.name,
                wheel_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                build_lock_sha256=hashlib.sha256((HERE / 'requirements-simpleui-build.lock.txt').read_bytes()).hexdigest(),
                runtime_lock_sha256=RECEIPT['runtime_lock_sha256'])


def main():
    require(len(sys.argv) == 3 and sys.argv[1] in ('fetch', 'check'), 'Use fetch|check DIRECTORY')
    directory = Path(sys.argv[2])
    if sys.argv[1] == 'fetch':
        fetch_source(directory)
    else:
        result = check_wheel(directory)
        result['installed_build_tools'] = {item['name']: installed_version(item['name']) for item in RECEIPT['build_tools']}
        require(all(result['installed_build_tools'][item['name']] == item['version'] for item in RECEIPT['build_tools']),
                'Installed build tool versions differ from frozen lock')
        with (directory / 'simpleui-build-receipt.json').open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2)


if __name__ == '__main__':
    main()
