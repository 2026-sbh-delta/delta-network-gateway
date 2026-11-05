#!/usr/bin/env python3
"""Operate the approved GCP sample app using ADC and immutable x86 artifacts."""
import argparse,base64,hashlib,json,os,re,secrets,shlex,shutil,struct,subprocess,tarfile,tempfile,time
from pathlib import Path
from urllib.parse import quote

REPO=Path(__file__).resolve().parents[2]
PRIVATE=REPO/'.validation/gcp-sample-app-20261105'
NODE_VERSION='24.19.0'
NODE_SHA='14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647'
PROXY_VERSION='2.26.0'
PROXY_SHA='a38fe97690a27490e60a7945a8a178186ff9464abb81fe2bc652a3f773443387'

def run(command,**kwargs):
    return subprocess.run([str(a)for a in command],check=True,capture_output=True,**kwargs)

def token():
    return run(['gcloud','auth','application-default','print-access-token'],text=True,timeout=45).stdout.strip()

def api(method,url,payload=None,binary=None,allow_missing=False):
    command=['curl','--silent','--show-error','--fail-with-body','--max-time','120','-X',method,'-H','Authorization: Bearer '+token()]
    if url.startswith('https://logging.googleapis.com/'):
        command+=['-H','X-Goog-User-Project: term2-delta']
    data=None
    if payload is not None:
        command+=['-H','Content-Type: application/json','--data-binary','@-'];data=json.dumps(payload).encode()
    if binary is not None:
        command+=['-H','Content-Type: application/octet-stream','--data-binary','@-'];data=binary
    result=subprocess.run(command+[url],input=data,capture_output=True,timeout=150)
    if result.returncode:
        if allow_missing:
            try:
                if json.loads(result.stdout).get('error',{}).get('code')==404:return None
            except Exception:pass
        PRIVATE.mkdir(parents=True,exist_ok=True)
        log=PRIVATE/'gcp-api-error.json';log.write_bytes(result.stdout);log.chmod(0o600)
        raise RuntimeError('GCP API request failed; protected response: '+str(log))
    return json.loads(result.stdout)if result.stdout else {}

def info(args):
    d=json.loads(Path(args.inventory).read_text())
    if d['project_id']!='term2-delta'or d['project_number']!='366844544284':raise ValueError('Unexpected deployment project')
    return d

def ssh(args,role,command,input=None,timeout=240):
    d=info(args)
    PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gcp-ssh-',dir=PRIVATE)as directory:
        access=Path(directory)/'access-token';access.write_text(token());access.chmod(0o600)
        key=PRIVATE/'ssh-key'
        if not key.exists():run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key])
        email=api('GET','https://www.googleapis.com/oauth2/v2/userinfo')['email']
        cmd=['gcloud','--access-token-file='+str(access),'--account='+email,'compute','ssh',d['instances'][role]['name'],
             '--project='+d['project_id'],'--zone='+d['zone'],'--tunnel-through-iap','--ssh-key-file='+str(key),'--quiet',
             '--command='+shlex.join(command),'--','-o','ConnectTimeout=25','-o','StrictHostKeyChecking=accept-new',
             '-o','UserKnownHostsFile='+str(PRIVATE/'known-hosts')]
        return run(cmd,input=input,text=True,timeout=timeout)

def download(url,path,digest):
    if not path.exists():run(['curl','--fail','--silent','--show-error','--location','--max-time','180',url,'-o',path],timeout=200)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Official binary checksum mismatch')

def elf_x86(binary):
    data=Path(binary).read_bytes()[:20]
    if data[:6]!=b'\x7fELF\x02\x01'or struct.unpack('<H',data[18:20])[0]!=62:raise ValueError('Expected Linux x86_64 ELF binary')

def build(args):
    source=Path(args.sample_root).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    commit=run(['git','-C',source,'rev-parse','HEAD'],text=True).stdout.strip()
    dirty=bool(run(['git','-C',source,'status','--porcelain'],text=True).stdout.strip())
    if dirty and not args.allow_dirty:raise ValueError('Reviewed local snapshot requires --allow-dirty')
    cache=PRIVATE/'binaries';cache.mkdir(parents=True,exist_ok=True)
    node_archive=cache/('node-v'+NODE_VERSION+'-linux-x64.tar.xz')
    download('https://nodejs.org/dist/v'+NODE_VERSION+'/'+node_archive.name,node_archive,NODE_SHA)
    proxy=cache/'cloud-sql-proxy.linux.amd64'
    download('https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v'+PROXY_VERSION+'/cloud-sql-proxy.linux.amd64',proxy,PROXY_SHA)
    elf_x86(proxy)
    for service in ['frontend','backend']if args.service=='all'else[args.service]:
        run(['npm','--prefix',source/service,'run','build'],text=True)
        with tempfile.TemporaryDirectory(prefix='gcp-bundle-',dir=out)as directory:
            stage=Path(directory);shutil.copytree(source/service/'dist',stage,dirs_exist_ok=True)
            if service=='backend':
                run(['npm','ci','--omit=dev','--ignore-scripts','--no-audit','--no-fund'],cwd=stage,text=True,timeout=120)
                shutil.copytree(source/service/'migrations',stage/'migrations')
                (stage/'scripts').mkdir(exist_ok=True)
                shutil.copyfile(source/service/'scripts/provision-db.mjs',stage/'scripts/provision-db.mjs')
            runtime=stage/'runtime';runtime.mkdir()
            with tarfile.open(node_archive,'r:xz')as archive:
                with archive.extractfile('node-v'+NODE_VERSION+'-linux-x64/bin/node')as src,(runtime/'node').open('wb')as dst:shutil.copyfileobj(src,dst)
            elf_x86(runtime/'node')
            if service=='backend':shutil.copyfile(proxy,runtime/'cloud-sql-proxy')
            bundle=out/(service+'.tar.gz')
            with tarfile.open(bundle,'w:gz')as archive:
                for path in sorted(stage.rglob('*')):
                    rel=path.relative_to(stage)
                    if path.is_symlink()or any(p.startswith('.')for p in rel.parts):continue
                    if path.suffix=='.node':raise ValueError('Native npm binary needs a separate x86 build')
                    if path.is_file():archive.add(path,arcname=str(rel),recursive=False)
            digest=hashlib.sha256(bundle.read_bytes()).hexdigest();bundle.rename(out/(digest+'.tar.gz'))
            manifest={'schema':1,'service':service,'commit':commit,'source_dirty':dirty,'sha256':digest,
                      'key':'releases/{}/{}/{}.tar.gz'.format(service,commit,digest),'architecture':'x86_64',
                      'version':commit+('-snapshot-'+digest[:12]if dirty else ''),'node_version':NODE_VERSION,'node_sha256':NODE_SHA}
            if service=='backend':manifest.update(proxy_version=PROXY_VERSION,proxy_sha256=PROXY_SHA)
            (out/(service+'.json')).write_text(json.dumps(manifest,indent=2)+'\n')
            print('Prepared {} x86_64 artifact {}'.format(service,digest))

def publish(args):
    d=info(args);path=Path(args.manifest);m=json.loads(path.read_text());payload=(path.parent/(m['sha256']+'.tar.gz')).read_bytes()
    if hashlib.sha256(payload).hexdigest()!=m['sha256']:raise ValueError('Artifact hash differs')
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('installer',REPO/'terraform/workload-gcp/templates/install.py');mod=module_from_spec(spec);spec.loader.exec_module(mod);mod.validate(m,m['service'])
    api('POST','https://storage.googleapis.com/upload/storage/v1/b/{}/o?uploadType=media&name={}&ifGenerationMatch=0'.format(d['artifact_bucket'],quote(m['key'],safe='')),binary=payload)
    print('Published '+m['service']+' '+m['sha256'])

def secret(d,name,value=None):
    base='https://secretmanager.googleapis.com/v1/projects/{}/secrets/{}'.format(d['project_id'],name)
    if value is None:
        result=api('GET',base+'/versions/latest:access',allow_missing=True)
        return json.loads(base64.b64decode(result['payload']['data']))if result else None
    api('POST',base+':addVersion',payload={'payload':{'data':base64.b64encode(json.dumps(value).encode()).decode()}})

def wait_sql(d,operation):
    for _ in range(120):
        result=api('GET','https://sqladmin.googleapis.com/sql/v1beta4/projects/{}/operations/{}'.format(d['project_id'],operation['name']))
        if result['status']=='DONE':
            if result.get('error'):raise RuntimeError('Cloud SQL operation failed')
            return
        time.sleep(1)
    raise TimeoutError('SQL operation timed out')

def init_database(args):
    d=info(args);db=d['database']
    admin=secret(d,db['admin_secret'])or{'username':'postgres','password':secrets.token_urlsafe(48)}
    app=secret(d,db['app_secret'])or{'username':db['user'],'password':secrets.token_urlsafe(48)}
    operation=api('PUT','https://sqladmin.googleapis.com/sql/v1beta4/projects/{}/instances/{}/users?name=postgres'.format(d['project_id'],db['name']),payload={'name':'postgres','password':admin['password']})
    wait_sql(d,operation);secret(d,db['admin_secret'],admin)
    code='''import json,os,subprocess,sys
v=json.load(sys.stdin)
env={**os.environ,'PGHOST':'127.0.0.1','PGPORT':'5432','PGDATABASE':v['database'],'PGUSER':'postgres','PGPASSWORD':v['admin_password'],'PGSSLMODE':'disable'}
p=subprocess.run(['/opt/delta-sample/current/runtime/node','/opt/delta-sample/current/scripts/provision-db.mjs'],env=env,input=json.dumps({'appUser':v['username'],'appPassword':v['password']}),text=True,capture_output=True)
print(p.stdout.strip());sys.exit(p.returncode)
'''
    payload={'database':db['database'],'admin_password':admin['password'],**app}
    result=ssh(args,'backend',['sudo','python3','-c',code],input=json.dumps(payload));print(result.stdout.strip())
    secret(d,db['app_secret'],app);print('Application database initialized; credentials stored without printing.')

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',default=str(PRIVATE/'application.json'))
    actions=parser.add_subparsers(dest='action',required=True)
    b=actions.add_parser('build');b.add_argument('--sample-root',required=True);b.add_argument('--output',default=str(PRIVATE/'releases'));b.add_argument('--service',choices=['frontend','backend','all'],default='all');b.add_argument('--allow-dirty',action='store_true')
    p=actions.add_parser('publish');p.add_argument('--manifest',required=True)
    p=actions.add_parser('install');p.add_argument('--manifest',required=True);p.add_argument('--prepare',action='store_true')
    actions.add_parser('init-db')
    for name in ['activate','rollback','restart','check']:
        p=actions.add_parser(name);p.add_argument('--service',choices=['frontend','backend'],required=True)
    args=parser.parse_args()
    if args.action=='build':build(args)
    elif args.action=='publish':publish(args)
    elif args.action=='init-db':init_database(args)
    elif args.action=='install':
        manifest=Path(args.manifest).read_text();role=json.loads(manifest)['service']
        command=['sudo','python3','/opt/delta-sample/bin/install.py','install']+(['--prepare']if args.prepare else[])
        print(ssh(args,role,command,input=manifest).stdout.strip())
    else:
        commands={'activate':['sudo','python3','/opt/delta-sample/bin/install.py','activate'],'rollback':['sudo','python3','/opt/delta-sample/bin/install.py','rollback'],'restart':['sudo','systemctl','restart','delta-sample'],'check':['sudo','systemctl','status','--no-pager','delta-sample']}
        print(ssh(args,args.service,commands[args.action]).stdout.strip())

if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as exc:
        PRIVATE.mkdir(parents=True,exist_ok=True)
        log=PRIVATE/'release-error.log';log.write_bytes((exc.stderr or b'').encode()if isinstance(exc.stderr,str)else(exc.stderr or b''));log.chmod(0o600)
        raise SystemExit('Command failed; protected diagnostic: '+str(log))
