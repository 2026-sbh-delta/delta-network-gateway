#!/usr/bin/python3
"""Install an immutable, self-contained GCS release and support rollback."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tarfile,tempfile,time
from pathlib import Path,PurePosixPath
from urllib.parse import quote
from urllib.request import Request,urlopen
ROOT=Path('/opt/delta-sample')

def validate(manifest,role):
    if manifest.get('schema')!=1 or manifest.get('service')!=role:raise ValueError('Wrong release role')
    if not re.fullmatch('[a-f0-9]{40}',manifest.get('commit',''))or not re.fullmatch('[a-f0-9]{64}',manifest.get('sha256','')):raise ValueError('Wrong release identity')
    if manifest.get('key')!='releases/{}/{}/{}.tar.gz'.format(role,manifest['commit'],manifest['sha256']):raise ValueError('Wrong artifact path')
    if manifest.get('architecture')!='x86_64':raise ValueError('Wrong artifact architecture')

def extract(archive,destination):
    with tarfile.open(archive,'r:gz')as source:
        members=source.getmembers()
        if sum(m.size for m in members)>350*1024*1024:raise ValueError('Archive too large')
        for m in members:
            p=PurePosixPath(m.name)
            if p.is_absolute()or'..'in p.parts or not(m.isfile()or m.isdir()):raise ValueError('Unsafe archive')
            if not p.parts or p.parts[0]not in ['server.mjs','lib','public','package.json','package-lock.json','node_modules','runtime','scripts','migrations']:raise ValueError('Unexpected archive entry')
            if any(x.startswith('.env')for x in p.parts):raise ValueError('Environment file in artifact')
        for m in members:
            path=destination/m.name
            if m.isdir():path.mkdir(parents=True,exist_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                with source.extractfile(m)as src,path.open('wb')as dst:shutil.copyfileobj(src,dst)
                path.chmod(0o755 if m.name.startswith('runtime/')else 0o644)
    if not(destination/'server.mjs').is_file()or not(destination/'runtime/node').is_file():raise ValueError('Incomplete release')

def point(path):
    temporary=ROOT/'.next'
    temporary.unlink(missing_ok=True);temporary.symlink_to(path);os.replace(temporary,ROOT/'current')

def activate(cfg,version):
    if cfg['ROLE']=='backend':subprocess.run(['systemctl','enable','--now','delta-sql-proxy'],check=True,capture_output=True)
    subprocess.run(['systemctl','enable','delta-sample'],check=True,capture_output=True)
    subprocess.run(['systemctl','restart','delta-sample'],check=True,capture_output=True)
    for _ in range(45):
        try:
            with urlopen('http://127.0.0.1:{}/healthz'.format(cfg['PORT']),timeout=2)as response:
                if response.status==200 and json.load(response).get('version')==version:return
        except Exception:pass
        time.sleep(1)
    raise RuntimeError('Application did not become healthy')

def main():
    if os.geteuid()!=0:raise ValueError('Run as root')
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['install','activate','rollback'])
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args()
    cfg=json.loads(Path('/etc/delta-sample/runtime.json').read_text())
    previous=(ROOT/'current').resolve()if(ROOT/'current').is_symlink()else None
    if args.action=='install':
        m=json.load(sys.stdin);validate(m,cfg['ROLE'])
        target=ROOT/'releases'/m['sha256']
        if not target.exists():
            from runtime import metadata_token
            with tempfile.TemporaryDirectory(dir=ROOT/'releases',prefix='.stage-')as directory:
                stage=Path(directory);archive=stage/'bundle.tar.gz'
                url='https://storage.googleapis.com/storage/v1/b/{}/o/{}?alt=media'.format(cfg['ARTIFACT_BUCKET'],quote(m['key'],safe=''))
                request=Request(url,headers={'Authorization':'Bearer '+metadata_token()})
                with urlopen(request,timeout=120)as response,archive.open('wb')as dst:shutil.copyfileobj(response,dst)
                if hashlib.sha256(archive.read_bytes()).hexdigest()!=m['sha256']:raise ValueError('Checksum mismatch')
                files=stage/'files';files.mkdir();extract(archive,files)
                (files/'release.json').write_text(json.dumps(m));files.rename(target)
        if previous and previous!=target:
            link=ROOT/'previous';link.unlink(missing_ok=True);link.symlink_to(previous)
        point(target)
        if cfg['ROLE']=='backend':subprocess.run(['systemctl','restart','delta-sql-proxy'],check=True,capture_output=True)
        if args.prepare:
            print('Release prepared; activation is a separate step.');return
    elif args.action=='rollback':
        target=(ROOT/'previous').resolve(strict=True);point(target)
    m=json.loads((ROOT/'current/release.json').read_text())
    try:activate(cfg,m['version'])
    except Exception:
        if previous and args.action=='install':point(previous);activate(cfg,json.loads((previous/'release.json').read_text())['version'])
        raise
    print('Release active and healthy: '+m['version'])

if __name__=='__main__':
    try:main()
    except Exception:
        print('Release operation failed; credential/command contents suppressed.',file=sys.stderr);sys.exit(1)
