#!/usr/bin/env python3
"""Root-only, hash-verified release installation and health-checked rollback."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.request import urlopen


ROOT = Path('/opt/delta-sample')
CONFIG = Path('/etc/delta-sample/runtime.json')


def validate_manifest(manifest, role):
    if manifest.get('schema') != 1 or manifest.get('service') != role:
        raise ValueError('Manifest service/schema mismatch')
    if not re.fullmatch(r'[a-f0-9]{40}', manifest.get('commit', '')):
        raise ValueError('A full source commit is required')
    if not re.fullmatch(r'[a-f0-9]{64}', manifest.get('sha256', '')):
        raise ValueError('A SHA-256 is required')
    expected = 'releases/{}/{}/{}.tar.gz'.format(role, manifest['commit'], manifest['sha256'])
    if manifest.get('key') != expected:
        raise ValueError('Invalid artifact key')
    if role == 'frontend':
        host = ipaddress.ip_address(manifest.get('api_host', ''))
        if host.version != 4 or not host.is_private:
            raise ValueError('Frontend must target a private backend IPv4 address')


def extract_archive(archive, target):
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if sum(m.size for m in members) > 100 * 1024 * 1024:
            raise ValueError('Artifact exceeds 100 MiB')
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe artifact path/type')
            if any(part.startswith('.') for part in path.parts) or path.parts[0] not in {'server.mjs', 'lib', 'public', 'package.json', 'package-lock.json'}:
                raise ValueError('Unexpected artifact content')
        for member in members:
            path = target / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as reader, path.open('wb') as writer:
                    shutil.copyfileobj(reader, writer)
                path.chmod(0o644)
    if not (target / 'server.mjs').is_file():
        raise ValueError('Missing application entry point')


def point_to(path):
    temporary = ROOT / '.current-next'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(path)
    os.replace(temporary, ROOT / 'current')


def restart_and_check(port, commit):
    subprocess.run(['systemctl', 'enable', 'delta-sample'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'restart', 'delta-sample'], check=True, capture_output=True)
    for _ in range(30):
        try:
            with urlopen('http://127.0.0.1:{}/healthz'.format(port), timeout=2) as response:
                if response.status == 200 and json.load(response).get('version') == commit:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Release health check failed')


def activate(target, port):
    previous = (ROOT / 'current').resolve() if (ROOT / 'current').is_symlink() else None
    release = json.loads((target / 'release.json').read_text())
    point_to(target)
    try:
        restart_and_check(port, release['commit'])
    except Exception:
        if previous:
            point_to(previous)
            restart_and_check(port, json.loads((previous / 'release.json').read_text())['commit'])
        else:
            subprocess.run(['systemctl', 'stop', 'delta-sample'], check=False, capture_output=True)
            (ROOT / 'current').unlink(missing_ok=True)
        raise
    if previous and previous != target:
        link = ROOT / 'previous'
        link.unlink(missing_ok=True)
        link.symlink_to(previous)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run through sudo as root')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'rollback'])
    parser.add_argument('--manifest')
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.action == 'rollback':
        previous = ROOT / 'previous'
        if not previous.is_symlink():
            raise SystemExit('No previous release')
        activate(previous.resolve(), config['PORT'])
        print('Previous release restored and healthy.')
        return
    manifest = json.loads(Path(args.manifest).read_text())
    validate_manifest(manifest, config['ROLE'])
    release_id = '{}-{}'.format(manifest['commit'], manifest['sha256'][:12])
    # FE target is part of the release identity even when its source did not change.
    if config['ROLE'] == 'frontend':
        release_id += '-' + manifest['api_host'].replace('.', '_')
    target = ROOT / 'releases' / release_id
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='.release-', dir=ROOT / 'releases') as temporary:
            stage = Path(temporary)
            archive = stage / 'artifact.tar.gz'
            subprocess.run(['aws', 's3api', 'get-object', '--region', config['REGION'], '--bucket', config['ARTIFACT_BUCKET'],
                            '--key', manifest['key'], str(archive)], check=True, capture_output=True)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
                raise ValueError('Artifact checksum mismatch')
            files = stage / 'files'
            files.mkdir()
            extract_archive(archive, files)
            if config['ROLE'] == 'backend':
                subprocess.run(['/usr/local/bin/npm', 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'],
                                cwd=files, check=True, capture_output=True,
                                env={**os.environ, 'PATH': '/usr/local/bin:/usr/bin:/bin'})
            (files / 'release.json').write_text(json.dumps(manifest) + '\n')
            files.rename(target)
    activate(target, config['PORT'])
    print('Release installed and healthy: {}'.format(manifest['commit']))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Release installation failed; previous release was retained when possible. Check systemd status.')
