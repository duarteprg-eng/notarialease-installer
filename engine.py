#!/usr/bin/env python3
"""Ubuntu bootstrap for the private TEST release; never logs credentials."""
import argparse
import getpass
import hashlib
from http.client import HTTPConnection
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

RELEASE_API = 'https://api.github.com/repos/duarteprg-eng/notarialease/releases/tags/docker-v1.0.29-test.1'
DOCKER_KEY_URL = 'https://download.docker.com/linux/ubuntu/gpg'
DOCKER_KEY_FINGERPRINT = '9DC858229FC7DD38854AE2D88D81803C0EBFCD88'
PHASE = 'preflight'

class Failure(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Failure(code)

def safe_url(value):
    u = urlsplit(value)
    require(u.scheme == 'https' and u.hostname and not u.username and not u.password
            and not u.query and not u.fragment and u.port in (None, 443)
            and all(32 < ord(c) < 127 for c in value), 'HTTPS_URL_INVALID')
    return u

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        require(target.scheme == 'https' and not target.username and not target.password,
                'HTTPS_REDIRECT_INVALID')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc.lower() != target.netloc.lower():
            redirected.remove_header('Authorization')
        return redirected

def download(url, token='', json_api=False):
    u = safe_url(url)
    headers = {'User-Agent': 'NotarialEase-Test-Installer',
               'Accept': 'application/vnd.github+json' if json_api else 'application/octet-stream'}
    if token and u.hostname == 'api.github.com':
        headers['Authorization'] = 'Bearer ' + token
    with build_opener(SafeRedirect()).open(Request(url, headers=headers), timeout=90) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    require(len(body) <= 8 * 1024 * 1024, 'ARTIFACT_TOO_LARGE')
    return body

def read_token(filename, variable, prompt):
    if filename:
        p = Path(filename)
        mode = p.lstat()
        require(stat.S_ISREG(mode.st_mode) and not p.is_symlink() and mode.st_uid == 0
                and mode.st_mode & 0o077 == 0 and mode.st_size <= 8192, 'TOKEN_FILE_NOT_PRIVATE')
        token = p.read_text().strip()
    else:
        token = os.environ.get(variable, '')
        if not token:
            available = sys.stdin.isatty()
            if not available:
                try:
                    fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
                    try: available = os.isatty(fd)
                    finally: os.close(fd)
                except OSError:
                    pass
            require(available, 'TOKEN_REQUIRED_NO_PROTECTED_TTY')
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass(prompt)
    require(bool(token) and len(token) <= 8192 and not any(c in token for c in '\r\n\0'), 'TOKEN_INVALID')
    return token

def execute(args, env, *, data=None, check=True):
    result = subprocess.run(args, input=data, capture_output=True, text=True, env=env, timeout=600)
    if check:
        require(result.returncode == 0, 'COMMAND_FAILED_' + Path(args[0]).name.upper().replace('-', '_'))
    return result

def ubuntu_codename():
    require(platform.system() == 'Linux' and platform.machine() in ('x86_64', 'amd64')
            and os.geteuid() == 0, 'UBUNTU_AMD64_ROOT_REQUIRED')
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"\'')
    require(values.get('ID') == 'ubuntu', 'UBUNTU_REQUIRED')
    codename = values.get('UBUNTU_CODENAME') or values.get('VERSION_CODENAME', '')
    require(bool(re.fullmatch('[a-z]+', codename)), 'UBUNTU_CODENAME_INVALID')
    return codename

def installed(package, env):
    result = execute(['dpkg-query', '-W', '-f=${db:Status-Status}', package], env, check=False)
    return result.returncode == 0 and result.stdout.strip() == 'installed'

def apt_install(packages, env):
    execute(['apt-get', 'install', '-y', '--no-remove', '--no-upgrade', '--no-install-recommends', *packages], env)

def ensure_runtime_tools(env):
    tools = {'curl': 'curl', 'openssl': 'openssl', 'flock': 'util-linux', 'ss': 'iproute2',
             'realpath': 'coreutils', 'sha256sum': 'coreutils', 'install': 'coreutils', 'bash': 'bash'}
    missing = sorted({package for tool, package in tools.items() if shutil.which(tool) is None})
    if missing:
        execute(['apt-get', 'update'], env)
        apt_install(missing, env)

def configure_official_repo(codename, env):
    source = Path('/etc/apt/sources.list.d/notarialease-docker.sources')
    key = Path('/etc/apt/keyrings/notarialease-docker.asc')
    text = ('Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: ' + codename
            + '\nComponents: stable\nArchitectures: amd64\nSigned-By: ' + str(key) + '\n')
    files = [Path('/etc/apt/sources.list'), *Path('/etc/apt/sources.list.d').glob('*.list'),
             *Path('/etc/apt/sources.list.d').glob('*.sources')]
    for p in files:
        if p.exists() and 'download.docker.com/linux/ubuntu' in p.read_text():
            require(p == source and p.read_text() == text, 'EXISTING_DOCKER_REPOSITORY_REQUIRES_REVIEW')
    execute(['apt-get', 'update'], env)
    apt_install(['ca-certificates', 'gnupg'], env)
    with tempfile.TemporaryDirectory(prefix='nexo-docker-key-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        candidate = directory / 'docker.asc'
        candidate.write_bytes(download(DOCKER_KEY_URL))
        gnupg = directory / 'gnupg'
        gnupg.mkdir(mode=0o700)
        result = execute(['gpg', '--homedir', str(gnupg), '--batch', '--with-colons',
                          '--import-options', 'show-only', '--import', str(candidate)], env)
        fingerprints = [line.split(':')[9] for line in result.stdout.splitlines() if line.startswith('fpr:')]
        require(bool(fingerprints) and fingerprints[0] == DOCKER_KEY_FINGERPRINT, 'DOCKER_GPG_FINGERPRINT_INVALID')
        require(not source.is_symlink() and not key.is_symlink(), 'APT_PATH_SYMLINK')
        if source.exists():
            require(source.read_text() == text, 'DOCKER_APT_SOURCE_CONFLICT')
        key.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        key.write_bytes(candidate.read_bytes()); key.chmod(0o644)
        source.write_text(text); source.chmod(0o644)
    execute(['apt-get', 'update'], env)

def ensure_docker(codename, env):
    exists = shutil.which('docker') is not None
    compose = exists and execute(['docker', 'compose', 'version'], env, check=False).returncode == 0
    if not exists:
        conflicts = ['docker.io', 'docker-compose', 'docker-compose-v2', 'podman-docker', 'containerd', 'runc', 'docker-ce', 'docker-ce-cli']
        require(not any(installed(package, env) for package in conflicts), 'CONFLICTING_PACKAGES_REQUIRE_REVIEW')
        configure_official_repo(codename, env)
        apt_install(['docker-ce', 'docker-ce-cli', 'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin'], env)
    elif not compose:
        configure_official_repo(codename, env)
        apt_install(['docker-compose-plugin'], env)
    active = execute(['systemctl', 'is-active', '--quiet', 'docker'], env, check=False).returncode == 0
    enabled = execute(['systemctl', 'is-enabled', '--quiet', 'docker'], env, check=False).returncode == 0
    if not active or not enabled:
        execute(['systemctl', 'enable', '--now', 'docker'], env)
    execute(['docker', 'info', '--format', '{{.OSType}}'], env)
    execute(['docker', 'compose', 'version'], env)

def metadata(url, token):
    if not url:
        release = json.loads(download(RELEASE_API, token, True))
        matches = [a['url'] for a in release['assets'] if a['name'] == 'notarialease-release.json']
        require(len(matches) == 1, 'METADATA_ASSET_NOT_UNIQUE')
        url = matches[0]
    document = json.loads(download(url, token))
    require(document.get('schema') == 1 and bool(re.fullmatch(r'ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}', document.get('app_image', ''))), 'METADATA_INVALID')
    if not document.get('installer_url'):
        release = json.loads(download(RELEASE_API, token, True))
        matches = [a['url'] for a in release['assets'] if a['name'] == 'install.sh']
        require(len(matches) == 1, 'INSTALLER_ASSET_NOT_UNIQUE')
        document['installer_url'] = matches[0]
    return url, document

def verified_artifacts(document, token, directory):
    artifacts=[('install.sh','installer'),('compose.yaml','compose'),('notarialease','operator')]
    controls=[('host_control.py','controller'),('notarialease-control.service','control_unit'),('install-control.py','control_installer')]
    if any(prefix+'_url' in document for _,prefix in controls):artifacts+=controls
    for name, prefix in artifacts:
        expected = document.get(prefix + '_sha256', '')
        require(bool(re.fullmatch('[a-f0-9]{64}', expected)), 'ARTIFACT_HASH_MISSING')
        body = download(document[prefix + '_url'], token)
        require(hashlib.sha256(body).hexdigest() == expected, 'ARTIFACT_HASH_INVALID')
        target = directory / name
        target.write_bytes(body); target.chmod(0o600)

def control_request(method,path,body=None):
    connection=HTTPConnection('localhost',timeout=15)
    connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.settimeout(15)
    try:
        connection.sock.connect('/run/notarialease-control/control.sock')
        connection.request(method,path,json.dumps(body).encode() if body is not None else None,{'Content-Type':'application/json'})
        response=connection.getresponse();data=response.read(1024*1024+1)
        require(200<=response.status<300 and len(data)<=1024*1024,'CONTROL_REQUEST_FAILED')
        value=json.loads(data);require(isinstance(value,dict) and value.get('schema')==1,'CONTROL_RESPONSE_INVALID');return value
    finally:connection.close()

def install_controller(root,document,directory,env,existing_ready):
    require(all((directory/name).is_file() for name in ('host_control.py','notarialease-control.service','install-control.py')),'CONTROL_ARTIFACTS_REQUIRED')
    args=[sys.executable,'-I',str(directory/'install-control.py'),'--root',str(root)]
    for option,name,prefix in [('host-code','host_control.py','controller'),('unit','notarialease-control.service','control_unit'),
                               ('compose-file','compose.yaml','compose'),('operator','notarialease','operator')]:
        hash_option={'host-code':'host','compose-file':'compose'}.get(option,option)
        args+=['--'+option,str(directory/name),'--'+hash_option+'-sha256',document[prefix+'_sha256']]
    require(type(document.get('github_release_id'))is int and document['github_release_id']>0,'CONTROL_RELEASE_ID_REQUIRED')
    if document.get('prerelease') is True:args+=['--allow-prerelease']
    helper=execute(args,env,check=False)
    if helper.returncode!=0:
        output=helper.stdout if isinstance(helper.stdout,str) else ''
        kinds='APPLICATION_ROOT|APPLICATION_RECEIPT|APPLICATION_ENV|APPLICATION_COMPOSE|APPLICATION_OPERATOR|CONTROLLER_SOURCE|CONTROL_UNIT|CONTROL_CONFIG|CONTROL_CONFIG_DIRECTORY|CONTROL_LIBRARY_DIRECTORY|CONTROL_STATE_DIRECTORY|CONTROL_SOCKET_DIRECTORY|OTHER_OBJECT'
        markers=[m for line in output.splitlines() if (m:=re.fullmatch(r'NEXO_CONTROL_INSTALL status=FAILED code=([A-Z][A-Z0-9_]{1,90})(?: exception=([A-Za-z][A-Za-z0-9_]{0,79}) line=([0-9]{1,6}))?(?: object=('+kinds+r') uid=([0-9]{1,10}) mode=([0-7]{4}))?',line))]
        if len(markers)==1:
            marker=markers[0]
            if marker[2]:print('NEXO_PYTHON control_exception='+marker[2]+' control_line='+marker[3],flush=True)
            if marker[4]:print('NEXO_PYTHON control_object='+marker[4]+' control_uid='+marker[5]+' control_mode='+marker[6],flush=True)
            raise Failure(marker[1])
        raise Failure('CONTROL_HELPER_FAILED')
    for _ in range(30):
        try:
            control_request('GET','/v1/info');break
        except Exception:time.sleep(1)
    else:raise Failure('CONTROL_NOT_READY')
    values=dict(line.split('=',1) for line in (root/'.env').read_text().splitlines())
    if existing_ready and values['NEXO_IMAGE']!=document['app_image']:
        # Persist one root-origin UUID/release tuple. A failed live job is never retried silently.
        path=root/('.bootstrap-update-'+str(document['github_release_id'])+'.json')
        if path.exists():
            info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_mode&0o077==0,'BOOTSTRAP_REQUEST_NOT_PRIVATE')
            request=json.loads(path.read_bytes())
            require(request.get('release_id')==document['github_release_id'] and request.get('operation')=='update'
                    and request.get('actor_id')==0 and uuid.UUID(request['request_id']).version==4,'BOOTSTRAP_REQUEST_CONFLICT')
        else:
            request={'request_id':str(uuid.uuid4()),'operation':'update','release_id':document['github_release_id'],'actor_id':0}
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(request,stream);stream.flush();os.fsync(stream.fileno())
        control_request('POST','/v1/bootstrap/update',request)
        previous=None
        for _ in range(1800):
            job=control_request('GET','/v1/jobs/'+request['request_id'])['job']
            if job['phase']!=previous:
                require(re.fullmatch('[A-Z_]{1,80}',job['phase']) is not None,'CONTROL_PHASE_INVALID')
                print('NEXO_PYTHON update_phase='+job['phase'],flush=True);previous=job['phase']
            if job['state']=='SUCCEEDED':break
            require(job['state'] not in ('FAILED','RECOVERY_REQUIRED','VALIDATION_FAILED'),'CONTROL_UPDATE_REQUIRES_REVIEW')
            time.sleep(1)
        else:raise Failure('CONTROL_UPDATE_STILL_RUNNING')
    else:
        execute(['docker','compose','--project-directory',str(root),'--env-file',str(root/'.env'),'-p',values['NEXO_PROJECT_NAME'],
                 '-f',str(root/'compose.yaml'),'up','-d','--no-build','--wait','--wait-timeout','300','app'],env)
    require(control_request('GET','/v1/info')['current']['release_id']==document['release_id'],'CONTROL_CURRENT_RELEASE_MISMATCH')

def private_registry_config(root):
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    directory = Path('/root/.config/notarialease-registry') / suffix
    for p in [directory, *directory.parents]:
        require(not p.is_symlink(), 'REGISTRY_PATH_SYMLINK')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    config = directory / 'config.json'
    if config.exists():
        require(not config.is_symlink() and config.stat().st_uid == 0 and config.stat().st_mode & 0o077 == 0, 'REGISTRY_CONFIG_NOT_PRIVATE')
        current = json.loads(config.read_text())
        require(not current.get('credsStore') and not current.get('credHelpers'), 'EXTERNAL_CREDENTIAL_HELPER_NOT_ALLOWED')
    return directory

def validated_existing(root):
    require(root.is_dir() and not any(p.is_symlink() for p in (root,*root.parents)),'EXISTING_ROOT_REQUIRES_REVIEW')
    info=root.stat();require(info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700,'EXISTING_ROOT_NOT_PRIVATE')
    path=root/'receipt.json';info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1,'EXISTING_RECEIPT_NOT_PRIVATE')
    mode=stat.S_IMODE(info.st_mode);require(mode in (0o600,0o400,0o644,0o666),'EXISTING_RECEIPT_MODE_REQUIRES_REVIEW')
    body=path.read_bytes();receipt=json.loads(body)
    require(receipt.get('schema')==1 and receipt.get('root')==str(root) and receipt.get('phase') in ('PREPARED','DB_READY','READY'),'EXISTING_RECEIPT_INVALID')
    require(re.fullmatch(r'https://api\.github\.com/repos/duarteprg-eng/notarialease/releases/assets/[0-9]+',receipt.get('metadata_url','')) is not None,'EXISTING_RECEIPT_PROVENANCE_INVALID')
    for name,key in (('.env','env_sha256'),('compose.yaml','compose_sha256'),('notarialease','operator_sha256')):
        file=root/name;current=file.lstat()
        require(stat.S_ISREG(current.st_mode) and current.st_uid==0 and not current.st_mode&0o022
                and hashlib.sha256(file.read_bytes()).hexdigest()==receipt.get(key),'EXISTING_RECEIPT_CHANGED')
    values={}
    for line in (root/'.env').read_text().splitlines():
        key,value=line.split('=',1);require(key not in values,'EXISTING_INSTALLATION_PROVENANCE_INVALID');values[key]=value
    keys={'NEXO_PROJECT_NAME','NEXO_BIND','NEXO_PORT','NEXO_IMAGE','NEXO_RELEASE_ID','NEXO_APP_VERSION','NEXO_INSTALL_ID'}
    require(set(values) in (keys,keys|{'NEXO_PG_VOLUME'}) and re.fullmatch(r'ghcr\.io/duarteprg-eng/notarialease@sha256:[a-f0-9]{64}',values.get('NEXO_IMAGE','')) is not None,'EXISTING_INSTALLATION_PROVENANCE_INVALID')
    require(uuid.UUID(values['NEXO_INSTALL_ID']).version==4 and re.fullmatch('[a-z0-9][a-z0-9_-]{0,47}',values['NEXO_PROJECT_NAME']) is not None,'EXISTING_INSTALLATION_PROVENANCE_INVALID')
    return receipt,body,info

def existing_receipt(root):
    receipt,body,info=validated_existing(root)
    if stat.S_IMODE(info.st_mode) not in (0o644,0o666):return receipt
    import fcntl
    lock=os.open(root/'.operation.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        lock_info=os.fstat(lock)
        require(stat.S_ISREG(lock_info.st_mode) and lock_info.st_uid==0 and not lock_info.st_mode&0o022,'OPERATION_LOCK_NOT_PROTECTED')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Failure('OPERATION_RUNNING') from None
        receipt,body,info=validated_existing(root)
        if stat.S_IMODE(info.st_mode) not in (0o644,0o666):return receipt
        backup=root/('receipt.permissions-before-'+uuid.uuid4().hex+'.json')
        backup_fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.fchmod(backup_fd,0o600)
        with os.fdopen(backup_fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
        fd=os.open(root/'receipt.json',os.O_RDONLY|os.O_NOFOLLOW)
        try:
            current=os.fstat(fd)
            require(current.st_dev==info.st_dev and current.st_ino==info.st_ino and current.st_uid==0 and current.st_nlink==1,'EXISTING_RECEIPT_CHANGED')
            require((root/'receipt.json').read_bytes()==body,'EXISTING_RECEIPT_CHANGED')
            os.fchmod(fd,0o600);os.fsync(fd)
        finally:os.close(fd)
        require((root/'receipt.json').read_bytes()==body,'EXISTING_RECEIPT_CHANGED')
        print('NEXO_PYTHON receipt_permissions=normalized_root_0600 backup_created=true',flush=True)
        return receipt
    finally:os.close(lock)

def main(argv=None):
    global PHASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-url')
    parser.add_argument('--root', default='/opt/notarialease')
    parser.add_argument('--project', default='notarialease')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', default='3080')
    parser.add_argument('--github-token-file')
    parser.add_argument('--registry-user', '--github-user', dest='registry_user')
    parser.add_argument('--registry-token-file')
    args = parser.parse_args(argv)
    os.umask(0o077)
    codename = ubuntu_codename()
    original_root = Path(args.root)
    require(original_root.is_absolute() and not any(p.is_symlink() for p in [original_root, *original_root.parents]), 'ROOT_PATH_INVALID')
    root = Path(os.path.realpath(original_root))
    require(str(root) != '/' and bool(re.fullmatch('[a-z0-9][a-z0-9_-]{0,47}', args.project)), 'INSTALL_ARGUMENT_INVALID')
    require(bool(re.fullmatch('[1-9][0-9]{0,4}', args.port)) and int(args.port) <= 65535, 'HTTP_PORT_INVALID')
    ipaddress.IPv4Address(args.bind)
    existing_ready=False
    if root.exists():
        receipt=existing_receipt(root)
        existing_ready=receipt['phase']=='READY'
    github = read_token(args.github_token_file, 'GH_TOKEN', 'Token GitHub de leitura: ')
    PHASE = 'release'
    source, document = metadata(args.metadata_url, github)
    with tempfile.TemporaryDirectory(prefix='notarialease-python-') as temporary:
        directory = Path(temporary); directory.chmod(0o700)
        verified_artifacts(document, github, directory)
        env = {k:v for k,v in os.environ.items() if not k.startswith(('NEXO_','COMPOSE_','DOCKER_','DB_','PG','GH_','GITHUB_'))}
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        env['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
        PHASE = 'docker'
        ensure_runtime_tools(env)
        ensure_docker(codename, env)
        PHASE = 'registry'
        authorized = execute(['docker', 'pull', document['app_image']], env, check=False).returncode == 0
        if authorized:
            print('NEXO_PYTHON registry_access=already_authorized_digest')
        else:
            registry_user = args.registry_user or os.environ.get('GH_USERNAME') or os.environ.get('GITHUB_ACTOR')
            if not registry_user:
                try: registry_user = json.loads(download('https://api.github.com/user', github, True))['login']
                except Exception: raise Failure('REGISTRY_USER_REQUIRED_USE_GITHUB_USER') from None
            require(bool(re.fullmatch('[A-Za-z0-9_-]{1,64}', registry_user)), 'REGISTRY_USER_INVALID')
            registry_token = (read_token(args.registry_token_file, 'NEXO_GHCR_TOKEN', 'Token GHCR de leitura: ')
                              if args.registry_token_file or os.environ.get('NEXO_GHCR_TOKEN') else github)
            registry = private_registry_config(root)
            env['DOCKER_CONFIG'] = str(registry)
            execute(['docker', '--config', str(registry), 'login', 'ghcr.io', '--username', registry_user, '--password-stdin'], env, data=registry_token + '\n')
            registry_token = None
            require((registry / 'config.json').is_file(), 'REGISTRY_CONFIG_NOT_CREATED')
            (registry / 'config.json').chmod(0o600)
            print('NEXO_PYTHON registry_credentials=persisted_root_0600 config=' + str(registry))
        PHASE = 'application'
        has_controller=bool(document.get('controller_url'))
        if not existing_ready or not has_controller:
            env['GH_TOKEN'] = github
            result = subprocess.run(['bash', str(directory / 'install.sh'), '--metadata-url', source,
                                 '--root', str(root), '--project', args.project, '--bind', args.bind,
                                 '--port', args.port], env=env, check=False)
            require(result.returncode == 0, 'APPLICATION_INSTALL_FAILED')
        github = None; env.pop('GH_TOKEN', None)
        if has_controller:
            PHASE='controller';install_controller(root,document,directory,env,existing_ready)
    print('NEXO_PYTHON status=READY automatic_updates=false')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, Failure) else type(error).__name__.upper()
        print('NEXO_PYTHON status=FAILED phase=' + PHASE + ' code=' + code, file=sys.stderr)
        sys.exit(1)
