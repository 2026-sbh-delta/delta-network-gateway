"""Fixed-service, reversible replacement preserving existing env, logs and mounts.

IMAGE is injected by the local renderer. Credentials are resolved only on host.
"""
import base64
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import time
import urllib.request


def command(args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Release subprocess failed: ' + args[0])
    return result.stdout.strip()


def inspect():
    return json.loads(command(['docker', 'inspect', 'delta-orchestrator']))[0]


def idle(root):
    for path in (root / 'runs').glob('*.json'):
        if path.is_symlink() or json.loads(path.read_text()).get('outcome') not in ('verified', 'unresolved', 'stopped'):
            raise RuntimeError('Unfinished execution; replacement refused')


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def main(image):
    if not re.fullmatch(r'616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-orchestrator@sha256:[a-f0-9]{64}', image):
        raise ValueError('Expected fixed repository and immutable digest')
    base = Path('/opt/delta/orchestrator')
    base.mkdir(exist_ok=True, mode=0o700)
    with (base / 'diagnostic-release.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = inspect()
        env = dict(v.split('=', 1) for v in old['Config']['Env'] if '=' in v)
        if old['HostConfig']['NetworkMode'] != 'host' or env.get('ORCH_STATE_DIR') != '/state':
            raise RuntimeError('Unexpected network/state configuration')
        mount = next(m for m in old['Mounts'] if m['Destination'] == '/state')
        root = Path(mount['Source'])
        if root != Path('/opt/delta/state/orchestrator'):
            raise RuntimeError('Unexpected durable volume')
        idle(root)
        before = hashes(root)
        raw = json.loads(command(['aws', 'ssm', 'get-parameter', '--region', 'ap-northeast-2',
            '--name', '/delta-cp/orchestrator/diagnostic_ai_connection', '--with-decryption', '--output', 'json']))
        if raw['Parameter']['Type'] != 'SecureString':
            raise RuntimeError('Diagnostic connection must be protected')
        connection = json.loads(raw['Parameter']['Value'])
        # DiagnosticClient candidate preflight performs full URL/CA/auth validation.
        env.update(DELTA_DIAGNOSTIC_URL=connection['url'], DELTA_DIAGNOSTIC_TOKEN=connection['token'],
                   DELTA_DIAGNOSTIC_CA_BASE64=connection.get('ca_base64', ''), DELTA_DIAGNOSTICS_REQUIRED='1')
        tag = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
        release = base / ('diagnostics-' + tag)
        release.mkdir(mode=0o700)
        envfile = release / 'secrets.env'
        if any(any(c in str(v) for c in '\n\r\0') for v in env.values()):
            raise RuntimeError('Environment values must be single-line')
        with envfile.open('x') as stream:
            os.chmod(envfile, 0o600)
            stream.write(''.join(k+'='+v+'\n' for k,v in env.items() if k != 'ORCH_STATE_DIR'))
        # Resolve the same existing private registry using host IAM, without printing its password.
        auth = command(['aws', 'ecr', 'get-login-password', '--region', 'ap-northeast-2'])
        registry = image.split('/')[0]
        registry_config = release / 'docker'
        registry_config.mkdir(mode=0o700)
        login = subprocess.run(['docker', '--config', str(registry_config), 'login', '--username', 'AWS', '--password-stdin', registry],
                               input=auth, text=True, capture_output=True)
        del auth
        if login.returncode: raise RuntimeError('Registry authentication failed')
        try:
            command(['docker', '--config', str(registry_config), 'pull', image])
        finally:
            shutil.rmtree(registry_config)
        candidate = command(['docker', 'run', '--rm', '--network', 'host', '--env-file', str(envfile),
                             '--entrypoint', 'python', image, '-m', 'delta_orchestrator.preflight'])
        if json.loads(candidate).get('diagnostics') != 'ready':
            raise RuntimeError('Candidate not ready')
        idle(root)
        current = inspect()
        if current['Id'] != old['Id'] or current['Config']['Image'] != old['Config']['Image']:
            raise RuntimeError('Another release replaced the service')
        previous = 'delta-orchestrator-previous-diagnostics-' + tag
        stopped = renamed = created = False
        try:
            command(['docker', 'stop', 'delta-orchestrator']); stopped = True
            idle(root)  # Catch a run accepted between the final idle check and stop.
            command(['docker', 'rename', 'delta-orchestrator', previous]); renamed = True
            args = ['docker', 'run', '-d', '--name', 'delta-orchestrator', '--restart', 'unless-stopped',
                    '--network', 'host', '--env-file', str(envfile), '-e', 'ORCH_STATE_DIR=/state']
            for m in old['Mounts']:
                if m['Type'] != 'bind': raise RuntimeError('Unknown mount; preserve original runtime')
                spec = 'type=bind,source='+m['Source']+',target='+m['Destination']
                if not m['RW']: spec += ',readonly'
                args += ['--mount', spec]
            logs = old['HostConfig']['LogConfig']
            if logs['Type']:
                args += ['--log-driver', logs['Type']]
                for k,v in logs.get('Config', {}).items(): args += ['--log-opt', k+'='+v]
            for option in old['HostConfig'].get('SecurityOpt') or []: args += ['--security-opt', option]
            for name, flag in [('Memory','--memory'),('CpuQuota','--cpu-quota'),('CpuPeriod','--cpu-period')]:
                if old['HostConfig'].get(name, 0) > 0: args += [flag,str(old['HostConfig'][name])]
            args += [image]
            command(args); created = True
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            ready = False
            for _ in range(40):
                try:
                    with opener.open('http://127.0.0.1:8080/healthz', timeout=2) as r:
                        ready = r.status == 200
                    if ready: break
                except Exception: pass
                time.sleep(1)
            if not ready: raise RuntimeError('New service readiness failed')
            after = hashes(root)
            if before != after: raise RuntimeError('Existing durable state changed during replacement')
            new = inspect()
            newenv = dict(v.split('=',1) for v in new['Config']['Env'] if '=' in v)
            if any(newenv.get(k) != v for k,v in env.items()): raise RuntimeError('Runtime environment changed')
            if new['HostConfig']['LogConfig'] != logs: raise RuntimeError('Logging configuration changed')
            return {'image':image,'previous_container':previous,'previous_image':old['Config']['Image'],
                    'existing_state_files_preserved':len(before),'candidate':json.loads(candidate),
                    'logging_preserved':True,'existing_env_preserved':True,'diagnostics_required':True}
        except Exception:
            if created: command(['docker','rm','-f','delta-orchestrator'])
            if renamed: command(['docker','rename',previous,'delta-orchestrator'])
            if stopped: command(['docker','start','delta-orchestrator'])
            raise


if __name__ == '__main__':
    try:
        result = main(IMAGE)
        print(base64.b64encode(gzip.compress(json.dumps(result).encode())).decode())
    except Exception as error:
        raise SystemExit('Diagnostic release failed safely: '+type(error).__name__) from None
