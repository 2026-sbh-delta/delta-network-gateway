#!/usr/bin/env python3
"""Deliver pinned offline Compose assets through GCS and existing ADC/IAP access."""
import argparse,hashlib,json,os,subprocess,sys,tarfile
from pathlib import Path
from urllib.parse import quote
import release

BASE=release.REPO/'.validation/gcp-compose-20261105'

def bundle(args):
    BASE.mkdir(mode=0o700,exist_ok=True)
    stage=BASE/'bundle';stage.mkdir(exist_ok=True)
    for name,source in [('docker.tgz',BASE/'binaries/docker-29.9.3.tgz'),('docker-compose',BASE/'binaries/docker-compose'),('host.py',Path(__file__).with_name('compose-host.py'))]:
        (stage/name).write_bytes(source.read_bytes())
    for role in ['frontend','backend']:
        m=release.PRIVATE/'releases'/(role+'.json');manifest=json.loads(m.read_text())
        (stage/(role+'.json')).write_bytes(m.read_bytes())
        (stage/(role+'.tar.gz')).write_bytes((m.parent/(manifest['sha256']+'.tar.gz')).read_bytes())
    (stage/'config.json').write_text(json.dumps(release.info(args)))
    archive=BASE/'bundle.tar.gz'
    with tarfile.open(archive,'w:gz')as tar:
        for path in sorted(stage.iterdir()):tar.add(path,arcname=path.name,recursive=False)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    m={'sha256':sha,'key':'releases/frontend/compose/'+sha+'.tar.gz','bucket':release.info(args)['artifact_bucket']}
    (BASE/'bundle.json').write_text(json.dumps(m,indent=2)+'\n')
    print(json.dumps(m))

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inventory',default=str(release.PRIVATE/'application.json'));p.add_argument('action',choices=['bundle','publish','prepare','activate','restart','status']);a=p.parse_args()
    if a.action=='bundle':bundle(a);return
    if a.action=='publish':
        m=json.loads((BASE/'bundle.json').read_text());payload=(BASE/'bundle.tar.gz').read_bytes()
        if hashlib.sha256(payload).hexdigest()!=m['sha256']:raise ValueError('Bundle hash mismatch')
        release.api('POST','https://storage.googleapis.com/upload/storage/v1/b/{}/o?uploadType=media&name={}&ifGenerationMatch=0'.format(m['bucket'],quote(m['key'],safe='')),binary=payload)
        print('Published pinned Compose bundle '+m['sha256']);return
    if a.action=='prepare':
        # This small bootstrap validates the entire bundle before running the reviewed host tool.
        m=json.loads((BASE/'bundle.json').read_text())
        code='''import hashlib,json,os,sys,tarfile
from pathlib import Path
from urllib.request import Request,urlopen
os.umask(0o077);m=json.load(sys.stdin)
root=Path('/opt/delta-compose');root.mkdir(exist_ok=True)
req=Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',headers={'Metadata-Flavor':'Google'})
with urlopen(req,timeout=10)as r:token=json.load(r)['access_token']
req=Request('https://storage.googleapis.com/'+m['bucket']+'/'+m['key'],headers={'Authorization':'Bearer '+token})
with urlopen(req,timeout=120)as r:data=r.read()
assert hashlib.sha256(data).hexdigest()==m['sha256'],'Bundle hash mismatch'
archive=root/(m['sha256']+'.tar.gz');archive.write_bytes(data)
stage=root/m['sha256'];stage.mkdir(exist_ok=True)
with tarfile.open(archive)as tar:
 for member in tar.getmembers():
  p=Path(member.name)
  assert len(p.parts)==1 and not p.is_absolute()and member.isfile(),'Unsafe bundle'
 tar.extractall(stage,filter='data')
os.execv('/usr/bin/python3',['python3',str(stage/'host.py'),'prepare','--bundle',str(stage)])
'''
        result=release.ssh(a,'frontend',['sudo','python3','-c',code],input=json.dumps(m),timeout=600)
    elif a.action=='restart':
        result=release.ssh(a,'frontend',['sudo','systemctl','restart','delta-compose'],timeout=360)
    else:result=release.ssh(a,'frontend',['sudo','python3','/opt/delta-compose/host.py',a.action],timeout=360)
    log=BASE/(a.action+'-result.json');log.write_text(result.stdout);log.chmod(0o600)
    print(result.stdout.strip())

if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError as e:
        BASE.mkdir(mode=0o700,exist_ok=True);log=BASE/'compose-error.log';log.write_text((e.stdout or '')+'\n'+(e.stderr or ''));log.chmod(0o600)
        raise SystemExit('Compose command failed; protected diagnostic: '+str(log))
