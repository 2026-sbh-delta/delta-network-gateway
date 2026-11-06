"""Run on the fixed user AI host. IMAGE is injected by the reviewed renderer."""
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
import urllib.error
import urllib.request

def command(args, **kwargs):
    result=subprocess.run(args,text=True,capture_output=True,**kwargs)
    if result.returncode:raise RuntimeError('Release subprocess failed: '+args[0])
    return result.stdout.strip()

def file_hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}

def probe(base, token, context=None):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
    def req(path,body=None,authenticated=False):
        headers={'Content-Type':'application/json'}
        if authenticated:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(base+path,data=body,headers=headers)
        try:
            with opener.open(request,timeout=5) as response:return response.status
        except urllib.error.HTTPError as error:return error.code
    result={'health':req('/healthz')}
    for path in ('/v1/diagnoses','/v1/recovery-proposals','/v1/incident-cases'):
        result[path]={'anonymous':req(path,b'{}'),'invalid':req(path,b'{}',True)}
    if result['health']!=200 or any(result[p]!={'anonymous':401,'invalid':422} for p in ('/v1/diagnoses','/v1/recovery-proposals','/v1/incident-cases')):
        raise RuntimeError('Candidate authentication or schema validation failed')
    return result

def main(image):
    if not re.fullmatch(r'616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-ai/api@sha256:[a-f0-9]{64}',image):
        raise ValueError('Unexpected immutable image')
    base=Path('/opt/delta/cp-diagnostic-ai')
    with (base/'readiness-release.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old=json.loads(command(['docker','inspect','delta-ai']))[0]
        if old['State']['Status']!='running' or old['HostConfig']['PortBindings']!={'8090/tcp':[{'HostIp':'127.0.0.1','HostPort':'8090'}]}:
            raise RuntimeError('Unexpected user AI runtime')
        if len(old['Mounts'])!=1 or old['Mounts'][0]['Source']!=str(base/'var') or old['Mounts'][0]['Destination']!='/app/var':
            raise RuntimeError('Unexpected user AI volume')
        if globals().get('EXPECTED_OLD_IMAGE') and old['Config']['Image'] != EXPECTED_OLD_IMAGE:
            raise RuntimeError('AI runtime changed since inventory')
        env=dict(s.split('=',1) for s in old['Config']['Env'] if '=' in s)
        if not env.get('DELTA_API_TOKEN') or env.get('DELTA_LLM')!='claude' or not env.get('ANTHROPIC_API_KEY'):
            raise RuntimeError('Expected existing authenticated Claude runtime')
        tag=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
        release=base/('readiness-'+tag);release.mkdir(mode=0o700)
        envfile=release/'secrets.env'
        if any(any(c in v for c in '\n\r\0') for v in env.values()):raise ValueError('Unsafe environment value')
        with envfile.open('x') as stream:
            os.chmod(envfile,0o600);stream.write(''.join(k+'='+v+'\n' for k,v in env.items()))
        before=file_hashes(base/'var')
        tls_before=file_hashes(base/'tls')
        nginx=Path('/etc/nginx/conf.d/delta-ai.conf')
        config=nginx.read_text()
        if 'location = /v1/diagnoses {' not in config or 'location / { return 404; }' not in config:
            raise RuntimeError('Unexpected private NGINX routes')
        newconfig=config
        if 'location = /v1/recovery-proposals {' not in config:
            block='''    location = /v1/recovery-proposals {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_read_timeout 180s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
'''
            newconfig=config.replace('    location / { return 404; }',block+'    location / { return 404; }')
        if 'location = /v1/incident-cases {' not in newconfig:
            block='    location = /v1/incident-cases {\n        proxy_pass http://127.0.0.1:8090;\n        proxy_http_version 1.1;\n        proxy_read_timeout 10s;\n        proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-For $remote_addr;\n    }\n'
            newconfig=newconfig.replace('    location / { return 404; }',block+'    location / { return 404; }')
        (release/'nginx.before.conf').write_text(config)
        registry_config=release/'registry';registry_config.mkdir(mode=0o700)
        try:
            auth=command(['aws','ecr','get-login-password','--region','ap-northeast-2'])
            command(['docker','--config',str(registry_config),'login','--username','AWS','--password-stdin',image.split('/')[0]],input=auth)
            del auth
            command(['docker','--config',str(registry_config),'pull',image])
        finally:shutil.rmtree(registry_config)
        candidate='delta-ai-readiness-candidate-'+tag
        try:
            command(['docker','run','-d','--name',candidate,'--env-file',str(envfile),'-p','127.0.0.1:18090:8090',image])
            ready=False
            for _ in range(40):
                try:
                    candidate_checks=probe('http://127.0.0.1:18090',env['DELTA_API_TOKEN']);ready=True;break
                except Exception:time.sleep(1)
            if not ready:raise RuntimeError('Candidate readiness failed')
        finally:subprocess.run(['docker','rm','-f','-v',candidate],capture_output=True)
        current=json.loads(command(['docker','inspect','delta-ai']))[0]
        if current['Id']!=old['Id']:raise RuntimeError('Concurrent release detected')
        previous='delta-ai-previous-readiness-'+tag
        stopped=renamed=created=config_changed=False
        try:
            command(['docker','stop','delta-ai']);stopped=True
            shutil.copytree(base/'var',release/'state-before')
            if file_hashes(release/'state-before') != before:raise RuntimeError('AI state backup mismatch')
            command(['docker','rename','delta-ai',previous]);renamed=True
            args=['docker','run','-d','--name','delta-ai','--restart','unless-stopped',
                  '-p','127.0.0.1:8090:8090','--env-file',str(envfile),'--mount','type=bind,source='+str(base/'var')+',target=/app/var']
            logs=old['HostConfig']['LogConfig']
            if logs['Type']:
                args+=['--log-driver',logs['Type']]
                for k,v in logs.get('Config',{}).items():args+=['--log-opt',k+'='+v]
            for opt in old['HostConfig'].get('SecurityOpt') or []:args+=['--security-opt',opt]
            args+=[image];created=True;command(args)
            nginx.write_text(newconfig);config_changed=True
            command(['nginx','-t','-q']);command(['systemctl','reload','nginx'])
            context=ssl.create_default_context(cafile=str(base/'tls/ca.crt'))
            ready=False
            for _ in range(40):
                try:
                    checks=probe('https://10.90.10.90',env['DELTA_API_TOKEN'],context);ready=True;break
                except Exception:time.sleep(1)
            if not ready:raise RuntimeError('Private HTTPS readiness failed')
            if file_hashes(base/'var')!=before or file_hashes(base/'tls')!=tls_before:
                raise RuntimeError('Existing state or TLS changed')
            new=json.loads(command(['docker','inspect','delta-ai']))[0]
            if dict(s.split('=',1) for s in new['Config']['Env'] if '=' in s)!=env:
                raise RuntimeError('Environment changed')
            if new['HostConfig']['LogConfig']!=logs:raise RuntimeError('Logging changed')
            return {'image':image,'previous_container':previous,'previous_image':old['Config']['Image'],
                    'state_files_preserved':len(before),'tls_preserved':True,'env_preserved':True,
                    'logs_preserved':True,'candidate_checks':candidate_checks,'https_checks':checks}
        except Exception:
            if created:subprocess.run(['docker','rm','-f','delta-ai'],capture_output=True)
            if renamed:command(['docker','rename',previous,'delta-ai'])
            if stopped:command(['docker','start','delta-ai'])
            if config_changed:
                nginx.write_text(config);command(['nginx','-t','-q']);command(['systemctl','reload','nginx'])
            raise

if __name__=='__main__':
    try:print(base64.b64encode(gzip.compress(json.dumps(main(IMAGE)).encode())).decode())
    except Exception as error:raise SystemExit('AI release failed safely: '+type(error).__name__) from None
