#!/usr/bin/python3
"""Read only the app secret at runtime; credentials never enter metadata."""
import base64,json,os,sys
from pathlib import Path
from urllib.request import Request,urlopen

def metadata_token():
    request=Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',headers={'Metadata-Flavor':'Google'})
    with urlopen(request,timeout=10)as response:return json.load(response)['access_token']

def main():
    cfg=json.loads(Path('/etc/delta-sample/runtime.json').read_text())
    manifest=json.loads(Path('/opt/delta-sample/current/release.json').read_text())
    env={**os.environ,**{k:str(v)for k,v in cfg.items()if k in ['HOST','PORT','APP_ENV','API_ENDPOINT','API_HOST','PGHOST','PGPORT','PGDATABASE','PGUSER','PGSSLMODE','STORAGE_BACKEND']}}
    env['APP_VERSION']=manifest['version']
    if cfg['ROLE']=='frontend':env.update(API_PORT='8080',API_PROTOCOL='http')
    else:
        url='https://secretmanager.googleapis.com/v1/projects/{}/secrets/{}/versions/latest:access'.format(cfg['PROJECT'],cfg['APP_SECRET'])
        request=Request(url,headers={'Authorization':'Bearer '+metadata_token()})
        with urlopen(request,timeout=20)as response:secret=json.loads(base64.b64decode(json.load(response)['payload']['data']))
        if secret['username']!=cfg['PGUSER']or not secret['password']:raise ValueError('Credential identity mismatch')
        env['PGPASSWORD']=secret['password']
    binary='/opt/delta-sample/current/runtime/node'
    os.execve(binary,[binary,'/opt/delta-sample/current/server.mjs'],env)

if __name__=='__main__':
    try:main()
    except Exception:
        print('Application runtime configuration or credential access failed; values suppressed.',file=sys.stderr)
        sys.exit(1)
