#!/usr/bin/env python3
"""Publish clean FE/BE build contexts and operate the single AWS Compose host."""
import argparse
import hashlib
import gzip
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

import release as native

HERE = Path(__file__).resolve().parent


def validate_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Invalid bundle path')
    if name == 'compose.yaml' or name in ['tooling/compose-host.py', 'tooling/backend-entrypoint.sh']:
        return
    if len(path.parts) < 2 or path.parts[0] not in ['frontend', 'backend']:
        raise ValueError('Unexpected bundle root')
    relative = path.parts[1:]
    if any(part.startswith('.') for part in relative) and relative != ('.dockerignore',):
        raise ValueError('Hidden files are not allowed')
    if len(relative) == 1 and relative[0] in ['Dockerfile', '.dockerignore', 'package.json', 'package-lock.json', 'server.mjs']:
        return
    if relative[0] in ['lib', 'scripts', 'public'] and len(relative) > 1:
        return
    raise ValueError('Unexpected application context path')


def bundle(source, output):
    if native.run(['git', '-C', source, 'status', '--porcelain']).stdout.strip():
        raise ValueError('Compose deployment requires a clean source checkout')
    commit = native.run(['git', '-C', source, 'rev-parse', 'HEAD']).stdout.strip()
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Invalid source commit')
    files = {}
    tree = native.run(['git', '-C', source, 'ls-tree', '-r', commit, 'frontend', 'backend']).stdout
    for line in tree.splitlines():
        metadata, name = line.split('\t', 1)
        try:
            validate_member(name)
        except ValueError:
            continue
        mode, kind, oid = metadata.split()
        if mode not in ['100644', '100755'] or kind != 'blob':
            raise ValueError('Context links and special files are not allowed')
        files[name] = subprocess.run(['git', '-C', str(source), 'cat-file', 'blob', oid], check=True, capture_output=True).stdout
    for role in ['frontend', 'backend']:
        for required in ['Dockerfile', 'package.json', 'server.mjs', '.dockerignore']:
            if role + '/' + required not in files:
                raise ValueError('Incomplete build context')
    files['compose.yaml'] = (HERE / 'compose.yaml').read_bytes()
    for name in ['compose-host.py', 'backend-entrypoint.sh']:
        files['tooling/' + name] = (HERE / name).read_bytes()
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    temporary = output / 'contexts.tar.gz'
    with temporary.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for name, content in sorted(files.items()):
            validate_member(name)
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    artifact = output / (digest + '.tar.gz')
    temporary.replace(artifact)
    artifact.chmod(0o600)
    return {'schema': 1, 'commit': commit, 'source_dirty': False, 'sha256': digest,
            'key': 'releases/compose/' + commit + '/' + digest + '.tar.gz'}, artifact


def validate_routes(args, info):
    result = native.aws(args, info, ['ec2', 'describe-route-tables', '--filters', 'Name=vpc-id,Values=' + info['vpc_id']])
    bridge = ipaddress.ip_network('172.30.240.0/24')
    for table in json.loads(result.stdout)['RouteTables']:
        for route in table['Routes']:
            cidr = route.get('DestinationCidrBlock')
            if cidr and cidr != '0.0.0.0/0' and bridge.overlaps(ipaddress.ip_network(cidr)):
                raise ValueError('Compose bridge overlaps an existing VPC route')


# This transport only downloads/extracts a verified bundle. The installer comes
# from that same hash-identified bundle, never from an interpolated shell string.
BOOTSTRAP = r'''import hashlib,json,os,pathlib,re,subprocess,sys,tarfile,tempfile
data=json.load(sys.stdin); info=data['inventory']; manifest=data['manifest']
digest=manifest['sha256']; commit=manifest['commit']
if not re.fullmatch('[a-f0-9]{64}',digest) or not re.fullmatch('[a-f0-9]{40}',commit): raise ValueError('Invalid identity')
if manifest['key'] != 'releases/compose/'+commit+'/'+digest+'.tar.gz': raise ValueError('Invalid destination')
root=pathlib.Path('/opt/delta-compose/releases')/digest
with tempfile.TemporaryDirectory(prefix='delta-compose-') as folder:
 archive=pathlib.Path(folder)/'bundle.tar.gz'
 subprocess.run(['aws','s3api','get-object','--region',info['region'],'--bucket',info['artifact_bucket'],'--key',manifest['key'],str(archive)],check=True,capture_output=True)
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=digest: raise ValueError('Checksum mismatch')
 with tarfile.open(archive,'r:gz') as tar:
  members=tar.getmembers()
  for item in members:
   p=pathlib.PurePosixPath(item.name)
   if not item.isfile() or p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] not in ['frontend','backend','tooling','compose.yaml']: raise ValueError('Unsafe member')
   if any(x.startswith('.') for x in p.parts) and p.name!='.dockerignore': raise ValueError('Hidden member')
  root.mkdir(parents=True,mode=0o755,exist_ok=True)
  if not (root/'release.json').exists():
   for item in members:
    target=root/item.name; target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(tar.extractfile(item).read()); target.chmod(0o644)
 config=pathlib.Path('/etc/delta-compose/config.json'); config.parent.mkdir(mode=0o700,exist_ok=True)
 temporary=config.with_suffix('.new'); temporary.write_text(json.dumps({**info,'source_commit':commit})); temporary.chmod(0o600); temporary.replace(config)
 subprocess.run(['python3',str(root/'tooling/compose-host.py'),'install','--release',str(root)],check=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='sbh-delta-workload')
    parser.add_argument('--inventory')
    commands = parser.add_subparsers(dest='action', required=True)
    deploy = commands.add_parser('deploy')
    deploy.add_argument('--sample-root', required=True)
    deploy.add_argument('--output', default=str(native.REPO / '.validation/compose-releases'))
    commands.add_parser('status')
    commands.add_parser('rollback')
    restart = commands.add_parser('restart')
    restart.add_argument('--service', required=True, choices=['frontend', 'backend'])
    args = parser.parse_args()
    info = native.inventory(args)
    if info.get('deployment_mode') != 'compose':
        raise ValueError('Apply deployment_mode=compose before using Compose operations')
    if args.action == 'deploy':
        validate_routes(args, info)
        manifest, artifact = bundle(Path(args.sample_root).resolve(), Path(args.output).resolve())
        native.aws(args, info, ['s3api', 'put-object', '--bucket', info['artifact_bucket'], '--key', manifest['key'],
                                '--body', str(artifact), '--server-side-encryption', 'AES256'])
        record = artifact.with_suffix('.manifest.json')
        record.write_text(json.dumps(manifest, indent=2) + '\n')
        record.chmod(0o600)
        # The bootstrap/host scripts never receive a secret from this operator.
        result = native.remote(args, info, 'frontend', ['sudo', 'python3', '-c', BOOTSTRAP],
                               input=json.dumps({'inventory': info, 'manifest': manifest}), remote_timeout=1800)
    else:
        command = ['sudo', 'python3', '/opt/delta-compose/bin/host.py', args.action]
        if args.action == 'restart':
            command += ['--service', args.service]
        result = native.remote(args, info, 'frontend', command)
    print(result.stdout.strip())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Compose operation failed: ' + type(error).__name__ + '; sensitive command details omitted.')
