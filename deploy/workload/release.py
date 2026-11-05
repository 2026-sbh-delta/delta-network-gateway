#!/usr/bin/env python3
"""Build independent artifacts and operate the explicitly selected workload."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import subprocess
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]


def run(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, text=True, capture_output=True, **kwargs)


def inventory(args):
    if args.inventory:
        result = json.loads(Path(args.inventory).read_text())
    else:
        result = json.loads(run(['terraform', '-chdir=' + str(REPO / 'terraform/workload'), 'output', '-json', 'application'],
                                env={**os.environ, 'AWS_PROFILE': args.profile}).stdout)
    if not result:
        raise ValueError('The sample application is not enabled/applied')
    actual = json.loads(aws(args, result, ['sts', 'get-caller-identity']).stdout)['Account']
    if actual != result['account_id']:
        raise ValueError('AWS account differs from deployment inventory')
    return result


def aws(args, info, command, **kwargs):
    return run(['aws', '--profile', args.profile, '--region', info['region'], '--no-cli-pager', *command], **kwargs)


def validate_artifact_manifest(manifest):
    if manifest.get('schema') != 1 or manifest.get('service') not in ['frontend', 'backend']:
        raise ValueError('Invalid artifact service/schema')
    if not re.fullmatch(r'[a-f0-9]{40}', manifest.get('commit', '')) or not re.fullmatch(r'[a-f0-9]{64}', manifest.get('sha256', '')):
        raise ValueError('Invalid artifact identity')
    if manifest.get('key') != 'releases/{}/{}/{}.tar.gz'.format(manifest['service'], manifest['commit'], manifest['sha256']):
        raise ValueError('Invalid artifact destination')


@contextmanager
def connection(args, info, role):
    instance = info['instances'][role]
    with tempfile.TemporaryDirectory(prefix='delta-workload-ssh-') as directory:
        key = Path(directory) / 'key'
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key])
        aws(args, info, ['ec2-instance-connect', 'send-ssh-public-key', '--instance-id', instance['id'],
                         '--availability-zone', instance['az'], '--instance-os-user', 'ec2-user',
                         '--ssh-public-key', 'file://' + str(key) + '.pub'])
        proxy = shlex.join(['aws', '--profile', args.profile, '--region', info['region'], 'ec2-instance-connect',
                            'open-tunnel', '--instance-id', instance['id'], '--instance-connect-endpoint-id',
                            info['management_endpoint_id'], '--remote-port', '22'])
        known_hosts = REPO / '.validation/workload-known-hosts'
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        base = ['ssh', '-i', key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
                '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(known_hosts),
                '-o', 'HostKeyAlias=' + instance['id'], '-o', 'ProxyCommand=' + proxy]
        yield [str(a) for a in base], 'ec2-user@' + instance['id']


def build(args):
    source = Path(args.sample_root).resolve()
    commit = run(['git', '-C', source, 'rev-parse', 'HEAD']).stdout.strip()
    dirty = bool(run(['git', '-C', source, 'status', '--porcelain']).stdout.strip())
    if dirty and not args.allow_dirty:
        raise ValueError('Source has uncommitted changes; use --allow-dirty only for a reviewed local snapshot')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for service in (['frontend', 'backend'] if args.service == 'all' else [args.service]):
        run(['npm', '--prefix', source / service, 'run', 'build'])
        dist = source / service / 'dist'
        files = sorted(dist.rglob('*'))
        for path in files:
            if path.is_symlink() or any(part.startswith('.') for part in path.relative_to(dist).parts):
                raise ValueError('Artifact must not contain links, hidden files or credentials')
        with tempfile.NamedTemporaryFile(dir=output, suffix='.tar.gz', delete=False) as temporary:
            archive_path = Path(temporary.name)
        with tarfile.open(archive_path, 'w:gz') as archive:
            for path in files:
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(dist), recursive=False)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        archive_path.rename(output / (digest + '.tar.gz'))
        manifest = {'schema': 1, 'service': service, 'commit': commit, 'source_dirty': dirty, 'sha256': digest,
                    'key': 'releases/{}/{}/{}.tar.gz'.format(service, commit, digest)}
        (output / (service + '.json')).write_text(json.dumps(manifest, indent=2) + '\n')
        print('{} artifact prepared: {}{}'.format(service, digest, ' (uncommitted snapshot)' if dirty else ''))


def publish(args, info):
    manifest = json.loads(Path(args.manifest).read_text())
    validate_artifact_manifest(manifest)
    artifact = Path(args.manifest).parent / (manifest['sha256'] + '.tar.gz')
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Artifact checksum mismatch')
    aws(args, info, ['s3api', 'put-object', '--bucket', info['artifact_bucket'], '--key', manifest['key'],
                     '--body', str(artifact), '--server-side-encryption', 'AES256'])
    print('Published {} artifact {}'.format(manifest['service'], manifest['sha256']))


def remote(args, info, role, command, **kwargs):
    with connection(args, info, role) as (base, destination):
        return run([*base, destination, shlex.join(command)], timeout=kwargs.pop('remote_timeout', 600), **kwargs)


def install(args, info):
    manifest = json.loads(Path(args.manifest).read_text())
    validate_artifact_manifest(manifest)
    role = manifest['service']
    if role not in ['frontend', 'backend']:
        raise ValueError('Unknown service')
    if manifest.get('source_dirty') and not args.allow_dirty:
        raise ValueError('Manifest contains an uncommitted snapshot; explicit --allow-dirty is required')
    if role == 'frontend':
        manifest['api_host'] = info['instances']['backend']['private_ip']
    # No database credentials travel in this manifest or the remote command.
    script = """import json, os, subprocess, sys, tempfile
with tempfile.NamedTemporaryFile(mode='w',suffix='.json',delete=False) as f:
 json.dump(json.load(sys.stdin),f); name=f.name
try:
 subprocess.run(['cloud-init','status','--wait'],check=True,stdout=sys.stderr)
 subprocess.run(['python3','/opt/delta-sample/bin/install-release.py','install','--manifest',name],check=True)
finally: os.unlink(name)
"""
    result = remote(args, info, role, ['sudo', 'python3', '-c', script], input=json.dumps(manifest))
    print(result.stdout.strip())


@contextmanager
def database_tunnel(args, info):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    host = info['database']['endpoint']
    if not re.fullmatch(r'[a-z0-9.-]+', host):
        raise ValueError('Invalid database endpoint')
    with connection(args, info, info.get('service_hosts', {}).get('backend', 'backend')) as (base, destination):
        process = subprocess.Popen([*base, '-o', 'ExitOnForwardFailure=yes', '-N', '-L',
                                    '127.0.0.1:{}:{}:5432'.format(port, host), destination],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Database SSH tunnel failed')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Database SSH tunnel did not open')
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def initialize_database(args, info):
    db = info['database']
    master = json.loads(aws(args, info, ['secretsmanager', 'get-secret-value', '--secret-id', db['master_secret_arn'],
                                      '--query', 'SecretString', '--output', 'text']).stdout)
    try:
        app = json.loads(aws(args, info, ['secretsmanager', 'get-secret-value', '--secret-id', db['app_secret_arn'],
                                       '--query', 'SecretString', '--output', 'text']).stdout)
    except subprocess.CalledProcessError as error:
        if 'ResourceNotFoundException' not in error.stderr:
            raise
        app = {'username': db['app_user'], 'password': secrets.token_urlsafe(48)}
    if app['username'] != db['app_user']:
        raise ValueError('Existing application secret has a different username')
    source = Path(args.sample_root).resolve()
    with tempfile.TemporaryDirectory(prefix='delta-rds-ca-') as directory, database_tunnel(args, info) as port:
        ca = Path(directory) / 'rds-ca.pem'
        # Use the operating system's verified HTTPS trust store, including on macOS.
        run(['curl', '--fail', '--silent', '--show-error', '--retry', '3', '--max-time', '30',
             'https://truststore.pki.rds.amazonaws.com/{0}/{0}-bundle.pem'.format(info['region']), '-o', ca])
        env = {**os.environ, 'PGHOST': '127.0.0.1', 'PGPORT': str(port), 'PGDATABASE': db['name'],
               'PGUSER': master['username'], 'PGPASSWORD': master['password'], 'PGSSLMODE': 'verify-full',
               'PGSSLROOTCERT': str(ca), 'PGTLS_SERVERNAME': db['endpoint']}
        result = run(['node', source / 'backend/scripts/provision-db.mjs'], env=env,
                     input=json.dumps({'appUser': app['username'], 'appPassword': app['password']}))
        # CLI reads the value from stdin. It is not an argv value or a Terraform resource.
        aws(args, info, ['secretsmanager', 'put-secret-value', '--secret-id', db['app_secret_arn'],
                         '--secret-string', 'file:///dev/stdin'], input=json.dumps(app))
        print(result.stdout.strip())
    print('Application credential stored; no password was printed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='sbh-delta-workload')
    parser.add_argument('--inventory', help='Optional JSON from terraform output -json application')
    commands = parser.add_subparsers(dest='action', required=True)
    command = commands.add_parser('build')
    command.add_argument('--sample-root', required=True)
    command.add_argument('--service', choices=['frontend', 'backend', 'all'], default='all')
    command.add_argument('--output', default=str(REPO / '.validation/sample-releases'))
    command.add_argument('--allow-dirty', action='store_true')
    command = commands.add_parser('publish')
    command.add_argument('--manifest', required=True)
    command = commands.add_parser('install')
    command.add_argument('--manifest', required=True)
    command.add_argument('--allow-dirty', action='store_true')
    command = commands.add_parser('init-db')
    command.add_argument('--sample-root', required=True)
    for name in ['rollback', 'restart', 'check']:
        command = commands.add_parser(name)
        command.add_argument('--service', choices=['frontend', 'backend'], required=True)
    args = parser.parse_args()
    if args.action == 'build':
        build(args)
        return
    info = inventory(args)
    if info.get('deployment_mode', 'native') != 'native' and args.action != 'init-db':
        raise ValueError('Use compose.py for Compose hosts; native service operations are disabled')
    if args.action == 'publish': publish(args, info)
    elif args.action == 'install': install(args, info)
    elif args.action == 'init-db': initialize_database(args, info)
    else:
        commands = {
            'rollback': ['sudo', 'python3', '/opt/delta-sample/bin/install-release.py', 'rollback'],
            'restart': ['sudo', 'systemctl', 'restart', 'delta-sample'],
            'check': ['sudo', 'systemctl', 'status', '--no-pager', 'delta-sample'],
        }
        print(remote(args, info, args.service, commands[args.action]).stdout.strip())


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # Commands and their environments can contain credentials; never print them.
        raise SystemExit('Operation failed: {}. Inspect configuration/connectivity; sensitive command details were omitted.'.format(type(error).__name__))
