#!/usr/bin/env python3
"""Root-only Compose host operations. Never print secrets or subprocess failures."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path('/opt/delta-compose')
CONFIG = Path('/etc/delta-compose/config.json')
COMPOSE_VERSION = 'v5.7.0'
COMPOSE_SHA256 = '7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2'
DOCKER_PACKAGE = 'docker-25.0.18-1.amzn2023.0.2'
BASE_TAG = 'node:24.19.0-alpine'


def run(argv, **kwargs):
    argv = [str(x) for x in argv]
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        # These operations run before credential injection and contain no app
        # secrets. Keep build/package diagnostics on the root-only host file.
        if argv[0] in ['dnf', 'curl'] or argv[:2] in [['docker', 'build'], ['docker', 'pull']]:
            write('/var/log/delta-compose-build.log', (error.stdout or '') + (error.stderr or ''))
        raise


def write(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(value)
    temporary.chmod(mode)
    temporary.replace(path)


def link(path, target):
    temporary = path.with_name(path.name + '.new')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(path)


def compose(release, *args):
    return run(['docker', 'compose', '--project-name', 'delta-notes', '--env-file', release / '.env',
                '--file', release / 'compose.yaml', *args], timeout=240)


def firewall():
    # Create the user chain before Docker starts, so restart-policy containers
    # cannot race the boot-time metadata/DB boundary. Docker preserves this chain.
    for chain in ['DOCKER-USER', 'DELTA-COMPOSE', 'DELTA-HOST']:
        subprocess.run(['iptables', '-w', '-N', chain], capture_output=True, check=False)
    # Atomically refresh only our chains. Preserve Docker and other workloads.
    rules = [
        ['-s', '172.30.240.0/24', '-d', '169.254.169.254/32', '-j', 'DROP'],
        ['-s', '172.30.240.10/32', '-d', '172.30.240.11/32', '-p', 'tcp', '--dport', '8080', '-j', 'ACCEPT'],
        ['-s', '172.30.240.10/32', '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'RETURN'],
        ['-s', '172.30.240.10/32', '-j', 'DROP'],
        ['-j', 'RETURN'],
    ]
    lines = ['*filter', '-F DELTA-COMPOSE', '-F DELTA-HOST']
    lines += ['-A DELTA-COMPOSE ' + ' '.join(rule) for rule in rules]
    lines += ['-A DELTA-HOST -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN', '-A DELTA-HOST -j DROP', 'COMMIT']
    run(['iptables-restore', '-w', '--noflush'], input='\n'.join(lines) + '\n')
    for chain, rule in [('DOCKER-USER', ['-j', 'DELTA-COMPOSE']),
                        ('INPUT', ['-i', 'br-delta-notes', '-j', 'DELTA-HOST'])]:
        if subprocess.run(['iptables', '-w', '-C', chain, *rule], capture_output=True).returncode:
            run(['iptables', '-w', '-I', chain, '1', *rule])


def prepare():
    config = json.loads(CONFIG.read_text())
    result = run(['aws', 'secretsmanager', 'get-secret-value', '--region', config['region'],
                  '--secret-id', config['database']['app_secret_arn'], '--query', 'SecretString', '--output', 'text'], timeout=60)
    secret = json.loads(result.stdout)
    if secret['username'] != 'delta_notes_app' or not isinstance(secret['password'], str) or not secret['password']:
        raise ValueError('Invalid application credential')
    folder = Path('/run/delta-compose')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    password = folder / 'db-password'
    # Keep the existing bind-mount inode when no rotation occurred.
    if not password.exists() or password.read_text() != secret['password']:
        write(password, secret['password'], 0o400)
        os.chown(password, 1000, 1000)
    firewall()


def setup():
    if platform.machine() != 'x86_64':
        raise ValueError('An x86_64 host is required')
    run(['dnf', 'install', '-y', DOCKER_PACKAGE, 'iptables-nft'], timeout=600)
    binary = Path('/usr/local/lib/docker/cli-plugins/docker-compose')
    if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest() != COMPOSE_SHA256:
        binary.parent.mkdir(parents=True, exist_ok=True)
        temporary = binary.with_suffix('.download')
        run(['curl', '--location', '--fail', '--silent', '--show-error', '--retry', '3', '--max-time', '180',
             'https://github.com/docker/compose/releases/download/' + COMPOSE_VERSION + '/docker-compose-linux-x86_64',
             '-o', temporary], timeout=200)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != COMPOSE_SHA256:
            raise ValueError('Compose download checksum mismatch')
        temporary.chmod(0o755)
        temporary.replace(binary)
    run(['systemctl', 'enable', '--now', 'docker'], timeout=120)
    run(['docker', 'compose', 'version'])


def parse_ps(value):
    value = value.strip()
    if value.startswith('['):
        return json.loads(value)
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def build(release):
    config = json.loads(CONFIG.read_text())
    # The chosen bridge must not shadow a real host route. Exclude the default
    # route and our own network on repeat deployment.
    for route in json.loads(run(['ip', '-j', 'route']).stdout):
        destination = route.get('dst', 'default')
        if destination != 'default' and route.get('dev') != 'br-delta-notes':
            if ipaddress.ip_network(destination, strict=False).overlaps(ipaddress.ip_network('172.30.240.0/24')):
                raise ValueError('Compose bridge overlaps an existing route')
    run(['docker', 'pull', '--platform', 'linux/amd64', BASE_TAG], timeout=300)
    base = json.loads(run(['docker', 'image', 'inspect', BASE_TAG]).stdout)[0]
    digest = base['RepoDigests'][0]
    if base['Architecture'] != 'amd64' or not re.fullmatch(r'node@sha256:[a-f0-9]{64}', digest):
        raise ValueError('Unexpected base image identity')
    images = {}
    for role in ['backend', 'frontend']:
        dockerfile = release / role / 'Dockerfile'
        content = dockerfile.read_text().replace('node:24-alpine', digest)
        if 'node:24-alpine' in content or content.count(digest) != 2:
            raise ValueError('Unexpected application Dockerfile')
        dockerfile.write_text(content)
        image = 'delta-notes-' + role + ':' + release.name
        run(['docker', 'build', '--platform', 'linux/amd64', '--tag', image, release / role],
            timeout=600, env={**os.environ, 'DOCKER_BUILDKIT': '0'})
        inspected = json.loads(run(['docker', 'image', 'inspect', image]).stdout)[0]
        if inspected['Architecture'] != 'amd64' or inspected['Config']['User'] != 'node':
            raise ValueError('Application image must be amd64 and non-root')
        images[role] = {'tag': image, 'id': inspected['Id']}
    env = {'FE_IMAGE': images['frontend']['id'], 'BE_IMAGE': images['backend']['id'],
           'APP_VERSION': config['source_commit'], 'PGHOST': config['database']['endpoint'],
           'AWS_REGION': config['region'], 'HOST_ID': config['instances']['frontend']['id'],
           'FE_LOG_GROUP': config['logs']['frontend'], 'BE_LOG_GROUP': config['logs']['backend']}
    write(release / '.env', ''.join(k + '=' + v + '\n' for k, v in env.items()))
    compose(release, 'config', '--quiet')
    record = {'source_commit': config['source_commit'], 'source_dirty': False, 'bundle_sha256': release.name,
              'images': images, 'base_image': digest, 'docker': run(['docker', '--version']).stdout.strip(),
              'compose': run(['docker', 'compose', 'version', '--short']).stdout.strip()}
    write(release / 'release.json', json.dumps(record, indent=2) + '\n')


def units():
    run(['install', '-d', '-m', '0755', ROOT / 'bin'])
    write(ROOT / 'bin/host.py', Path(__file__).read_text(), 0o755)
    write('/etc/systemd/system/delta-compose-prepare.service', '''[Unit]
Description=Delta Compose credential and network preparation
Wants=network-online.target
After=network-online.target
Before=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/delta-compose/bin/host.py prepare
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''', 0o644)
    write('/etc/systemd/system/docker.service.d/delta-compose.conf', '''[Unit]
Requires=delta-compose-prepare.service
After=delta-compose-prepare.service
''', 0o644)
    write('/etc/systemd/system/delta-compose.service', '''[Unit]
Description=Delta Notes FE and BE Compose stack
Requires=docker.service delta-compose-prepare.service
After=docker.service delta-compose-prepare.service network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/bin/python3 /opt/delta-compose/bin/host.py prepare
ExecStart=/usr/bin/python3 /opt/delta-compose/bin/host.py start
ExecStop=/usr/bin/python3 /opt/delta-compose/bin/host.py stop
TimeoutStartSec=300
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
''', 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'docker', 'delta-compose-prepare', 'delta-compose'])


def activate(release):
    current = ROOT / 'current'
    old = current.resolve() if current.is_symlink() else None
    link(current, release)
    try:
        prepare()
        run(['systemctl', 'disable', '--now', 'delta-sample'], timeout=60)
        run(['systemctl', 'restart', 'delta-compose'], timeout=300)
    except Exception:
        subprocess.run(['systemctl', 'stop', 'delta-compose'], capture_output=True)
        # A failed oneshot start may leave partially created containers.
        try:
            compose(release, 'down')
        except Exception:
            pass
        if old:
            link(current, old)
            run(['systemctl', 'restart', 'delta-compose'], timeout=300)
        else:
            current.unlink(missing_ok=True)
            run(['systemctl', 'disable', 'delta-compose'])
            run(['systemctl', 'enable', '--now', 'delta-sample'], timeout=60)
        raise
    if old and old != release:
        link(ROOT / 'previous', old)
    print((release / 'release.json').read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'prepare', 'start', 'stop', 'rollback', 'status', 'restart'])
    parser.add_argument('--release')
    parser.add_argument('--service', choices=['frontend', 'backend'])
    args = parser.parse_args()
    if args.action == 'install':
        release = Path(args.release).resolve()
        if release.parent != ROOT / 'releases' or not re.fullmatch('[a-f0-9]{64}', release.name):
            raise ValueError('Invalid release path')
        setup()
        if not (release / 'release.json').exists():
            build(release)
        units()
        activate(release)
    elif args.action == 'prepare':
        prepare()
    elif args.action == 'start':
        compose((ROOT / 'current').resolve(), 'up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '120')
    elif args.action == 'stop':
        compose((ROOT / 'current').resolve(), 'stop', '--timeout', '15')
    elif args.action == 'rollback':
        if not (ROOT / 'previous').is_symlink():
            raise ValueError('No previous Compose release')
        activate((ROOT / 'previous').resolve())
    elif args.action == 'restart':
        if not args.service:
            raise ValueError('Specify a service')
        release = (ROOT / 'current').resolve()
        compose(release, 'restart', args.service)
        for _ in range(60):
            containers = parse_ps(compose(release, 'ps', '--format', 'json', '--all').stdout)
            if any(c.get('Service') == args.service and c.get('Health') == 'healthy' for c in containers):
                print(args.service + ' healthy')
                break
            time.sleep(2)
        else:
            raise RuntimeError('Restarted service did not become healthy')
    else:
        release = (ROOT / 'current').resolve()
        print(compose(release, 'ps', '--format', 'json').stdout)
        print((release / 'release.json').read_text())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Compose host operation failed: ' + type(error).__name__ + '; sensitive details omitted.', file=sys.stderr)
        sys.exit(1)
