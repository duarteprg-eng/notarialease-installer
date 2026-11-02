#!/usr/bin/env python3
"""Install the newest published private release; supports curl | sudo python3 -."""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import warnings
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

API = 'https://api.github.com/repos/duarteprg-eng/notarialease'
IMAGE_PREFIX = 'ghcr.io/duarteprg-eng/notarialease@sha256:'
CONFIG = '/root/.config/notarialease-install'
NAMES = ('github-release.token', 'ghcr-read.token')
TOKEN_PATTERN = r'[A-Za-z0-9._~+/-]+=*'

class Failure(Exception):
    pass

def require(ok, code):
    if not ok: raise Failure(code)

def directory_guard(directory, allow_missing=False):
    require(directory.is_absolute(), 'CREDENTIAL_DIRECTORY_INVALID')
    for current in (directory, *directory.parents):
        try: info = current.lstat()
        except FileNotFoundError:
            require(allow_missing, 'CREDENTIAL_DIRECTORY_MISSING'); continue
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0, 'CREDENTIAL_DIRECTORY_NOT_PRIVATE')
        require(not info.st_mode & 0o022 or (current != directory and info.st_mode & stat.S_ISVTX),
                'CREDENTIAL_DIRECTORY_WRITABLE')

def token_format(value):
    require(isinstance(value, str) and 0 < len(value) <= 8192
            and re.fullmatch(TOKEN_PATTERN, value) is not None, 'CREDENTIAL_FORMAT_INVALID')
    return value

def read_private(path):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
            and stat.S_IMODE(before.st_mode) in (0o400, 0o600)
            and 0 < before.st_size <= 8192, 'CREDENTIAL_FILE_NOT_PRIVATE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'CREDENTIAL_FILE_CHANGED')
        body = os.read(fd, 8193)
    finally: os.close(fd)
    return token_format(body.decode('ascii').rstrip('\r\n'))

def protected_prompt(prompt):
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        try: require(os.isatty(fd), 'PROTECTED_TTY_REQUIRED')
        finally: os.close(fd)
    except OSError: raise Failure('PROTECTED_TTY_REQUIRED') from None
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return token_format(getpass.getpass(prompt))

def credentials(directory):
    directory_guard(directory, allow_missing=True)
    paths = tuple(directory / name for name in NAMES)
    exists = tuple(os.path.lexists(path) for path in paths)
    require(exists[0] == exists[1], 'CREDENTIAL_PAIR_INCOMPLETE')
    if all(exists):
        directory_guard(directory)
        return paths, tuple(read_private(path) for path in paths), False
    values = (protected_prompt('Token GitHub Contents read: '),
              protected_prompt('Token GHCR read:packages: '))
    return paths, values, True

def save_new_pair(directory, paths, values):
    # Called only after both credentials passed the remote GET-only checks.
    import fcntl
    directory_guard(directory, allow_missing=True)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_guard(directory)
    created = []
    lock = os.open(directory / '.credentials.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
                'CREDENTIAL_LOCK_NOT_PRIVATE')
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not any(os.path.lexists(path) for path in paths), 'CREDENTIAL_CREATION_CONFLICT')
        for path, value in zip(paths, values):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd); created.append((path, info.st_dev, info.st_ino))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(value.encode('ascii')); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        for path, device, inode in created:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (device, inode): path.unlink()
            except OSError: pass
        raise
    finally: os.close(lock)

class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, hosts): self.hosts = hosts
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urlsplit(newurl)
        require(target.scheme == 'https' and target.hostname in self.hosts
                and not target.username and not target.password, 'ACCESS_REDIRECT_REJECTED')
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected and urlsplit(request.full_url).netloc.lower() != target.netloc.lower():
            redirected.remove_header('Authorization')
        return redirected

def http_get(url, headers=None, hosts=()):
    target = urlsplit(url)
    require(target.scheme == 'https' and target.hostname in hosts and target.port in (None, 443)
            and not target.username and not target.password, 'ACCESS_URL_REJECTED')
    request = Request(url, headers={'User-Agent': 'NotarialEase-Curl-Installer', 'Cache-Control': 'no-cache', **(headers or {})})
    try:
        with build_opener(ProxyHandler({}), SafeRedirect(hosts)).open(request, timeout=60) as response:
            require(response.status == 200, 'ACCESS_HTTP_STATUS_INVALID')
            body = response.read(8 * 1024 * 1024 + 1)
            require(len(body) <= 8 * 1024 * 1024, 'ACCESS_RESPONSE_TOO_LARGE')
            return response.headers, body
    except Failure: raise
    except Exception: raise Failure('ACCESS_REQUEST_FAILED') from None

def release_order(release):
    match = re.fullmatch(r'docker-v(\d+)\.(\d+)\.(\d+)(?:-test\.(\d+))?', release.get('tag_name', ''))
    if release.get('draft') or not match:
        return None
    return (*map(int, match.group(1, 2, 3)), int(match[4]) if match[4] else float('inf'))

def newest_release(github):
    auth = {'Authorization': 'Bearer ' + github, 'Accept': 'application/vnd.github+json'}
    releases = []
    for page in range(1, 101):
        _, body = http_get(API + '/releases?per_page=100&page=' + str(page), auth, ('api.github.com',))
        batch = json.loads(body); require(isinstance(batch, list), 'RELEASE_LIST_INVALID')
        releases.extend(r for r in batch if release_order(r) is not None)
        if len(batch) < 100: break
    else: raise Failure('RELEASE_HISTORY_LIMIT')
    require(bool(releases), 'PUBLISHED_RELEASE_REQUIRED')
    # A broken newest release blocks; never silently install an older package.
    return max(releases, key=release_order)

def certified_bundle(github):
    release = newest_release(github)
    require(type(release.get('id')) is int and release['id'] > 0, 'CERTIFIED_RELEASE_MISMATCH')
    def asset(name):
        matches = [a for a in release.get('assets', []) if a.get('name') == name]
        require(len(matches) == 1, 'CERTIFIED_ASSET_NOT_UNIQUE')
        url = matches[0].get('url', '')
        require(re.fullmatch(re.escape(API) + r'/releases/assets/[0-9]+', url) is not None,
                'CERTIFIED_ASSET_URL_INVALID')
        return url
    def fetch(url):
        return http_get(url, {'Authorization': 'Bearer ' + github, 'Accept': 'application/octet-stream'},
                        ('api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'))[1]
    metadata_url = asset('notarialease-release.json')
    body = fetch(metadata_url); metadata = json.loads(body)
    expected_version = '.'.join(map(str, release_order(release)[:3]))
    require(metadata.get('schema') == 1 and metadata.get('release_id') == release['tag_name']
            and metadata.get('app_version') == expected_version
            and metadata.get('github_release_id') == release['id']
            and re.fullmatch(re.escape(IMAGE_PREFIX) + r'[a-f0-9]{64}', metadata.get('app_image', '')) is not None,
            'CERTIFIED_IMAGE_METADATA_MISMATCH')
    checks = {}
    for line in fetch(asset('SHA256SUMS.txt')).decode('ascii').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        require(match is not None and match[2] not in checks, 'CERTIFIED_CHECKSUM_INVALID')
        checks[match[2]] = match[1]
    require(checks.get('notarialease-release.json') == hashlib.sha256(body).hexdigest(),
            'CERTIFIED_METADATA_CHECKSUM_MISMATCH')
    engine_sha = metadata.get('python_sha256', '')
    require(re.fullmatch(r'[a-f0-9]{64}', engine_sha) is not None and checks.get('install.py') == engine_sha,
            'CERTIFIED_BOOTSTRAP_HASH_MISMATCH')
    engine = fetch(asset('install.py'))
    require(hashlib.sha256(engine).hexdigest() == engine_sha, 'CERTIFIED_BOOTSTRAP_HASH_MISMATCH')
    return {'metadata_url': metadata_url, 'engine': engine, 'image': metadata['app_image'], 'tag': release['tag_name']}

def check_access(values, registry_user):
    github, registry = values
    bundle = certified_bundle(github)
    image_digest = bundle['image'].split('@')[1]
    headers, body = http_get('https://api.github.com/user', {'Authorization': 'Bearer ' + registry}, ('api.github.com',))
    scopes = {s.strip() for s in headers.get('X-OAuth-Scopes', '').split(',') if s.strip()}
    require(scopes == {'read:packages'}, 'REGISTRY_SCOPE_NOT_READ_ONLY')
    require(json.loads(body).get('login', '').lower() == registry_user.lower(), 'REGISTRY_ACCOUNT_MISMATCH')
    basic = base64.b64encode((registry_user + ':' + registry).encode('ascii')).decode('ascii')
    url = 'https://ghcr.io/token?' + urlencode({'service': 'ghcr.io', 'scope': 'repository:duarteprg-eng/notarialease:pull'})
    _, body = http_get(url, {'Authorization': 'Basic ' + basic}, ('ghcr.io',))
    payload = json.loads(body); bearer = payload.get('token') or payload.get('access_token')
    token_format(bearer)
    accept = ', '.join(('application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
                        'application/vnd.docker.distribution.manifest.v2+json', 'application/vnd.docker.distribution.manifest.list.v2+json'))
    url = 'https://ghcr.io/v2/duarteprg-eng/notarialease/manifests/' + image_digest
    headers, body = http_get(url, {'Authorization': 'Bearer ' + bearer, 'Accept': accept}, ('ghcr.io',))
    require(headers.get('Docker-Content-Digest') == image_digest
            and 'sha256:' + hashlib.sha256(body).hexdigest() == image_digest, 'REGISTRY_MANIFEST_DIGEST_MISMATCH')
    return bundle

def run_certified(paths, options, bundle):
    body = bundle['engine']
    with tempfile.TemporaryDirectory(prefix='notarialease-curl-') as temporary:
        directory = Path(temporary); directory.chmod(0o700)
        filename = directory / 'install.py'; filename.write_bytes(body); filename.chmod(0o600)
        args = [sys.executable, '-I', str(filename), '--metadata-url', bundle['metadata_url'], '--github-token-file', str(paths[0]),
                '--registry-token-file', str(paths[1]), '--registry-user', options.registry_user,
                '--root', options.root, '--project', options.project, '--bind', options.bind, '--port', options.port]
        env = {k: v for k, v in os.environ.items() if k not in ('GH_TOKEN', 'NEXO_GHCR_TOKEN', 'PYTHONPATH', 'PYTHONHOME')}
        require(subprocess.run(args, cwd=directory, env=env, check=False).returncode == 0, 'CERTIFIED_INSTALLER_FAILED')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-dir', default=CONFIG)
    parser.add_argument('--registry-user', default='duarteprg-eng')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', default='3080')
    parser.add_argument('--root', default='/opt/notarialease')
    parser.add_argument('--project', default='notarialease')
    parser.add_argument('--check-access', action='store_true')
    options = parser.parse_args(argv)
    require(platform.system() == 'Linux' and os.geteuid() == 0, 'LINUX_ROOT_REQUIRED')
    os.umask(0o077)
    require(re.fullmatch(r'[1-9][0-9]{0,4}', options.port) is not None and int(options.port) <= 65535, 'HTTP_PORT_INVALID')
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', options.project) is not None, 'PROJECT_INVALID')
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', options.registry_user) is not None, 'REGISTRY_USER_INVALID')
    require(Path(options.root).is_absolute() and options.root != '/', 'ROOT_INVALID')
    try: ipaddress.IPv4Address(options.bind)
    except ValueError: raise Failure('BIND_INVALID') from None
    directory = Path(options.credentials_dir)
    paths, values, new = credentials(directory)
    bundle = check_access(values, options.registry_user)
    if new: save_new_pair(directory, paths, values)
    values = None
    if options.check_access:
        print('NEXO_CURL_ACCESS status=APPROVED code=READ_ONLY_ACCESS_OK')
    else:
        run_certified(paths, options, bundle)
        print('NEXO_CURL_INSTALL status=READY code=CERTIFIED_INSTALLER_OK')

def entrypoint(argv=None):
    try:
        main(argv); return 0
    except Exception as error:
        code = str(error) if isinstance(error, Failure) else 'BOOTSTRAP_FAILED'
        print('NEXO_CURL_INSTALL status=FAILED code=' + code, file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(entrypoint())
