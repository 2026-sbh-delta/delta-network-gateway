#!/usr/bin/python3
"""Root-only offline Compose installation on the existing private x86 host."""
import argparse,base64,hashlib,io,json,os,re,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path('/opt/delta-compose')
ETC=Path('/etc/delta-compose')
DOCKER_SHA='3fb5d1f13014c18cc54945bc177c58a9056a482dada031e17d12cf2e6922c973'
COMPOSE_SHA='7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2'
NETWORK='172.28.91.0/24'

def run(cmd,**kwargs):
    return subprocess.run([str(x)for x in cmd],check=True,capture_output=True,text=True,**kwargs)

def write(path,value,mode=0o644):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value);path.chmod(mode)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def extract(path,target):
    with tarfile.open(path)as archive:
        members=archive.getmembers()
        for m in members:
            p=Path(m.name)
            if p.is_absolute()or '..'in p.parts or not(m.isfile()or m.isdir()):
                raise ValueError('Unsafe archive member')
        archive.extractall(target,members=members,filter='data')

def metadata_token():
    req=Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',headers={'Metadata-Flavor':'Google'})
    with urlopen(req,timeout=10)as r:return json.load(r)['access_token']

def refresh_secret():
    cfg=json.loads((ETC/'config.json').read_text())
    url='https://secretmanager.googleapis.com/v1/projects/term2-delta/secrets/'+cfg['database']['app_secret']+'/versions/latest:access'
    with urlopen(Request(url,headers={'Authorization':'Bearer '+metadata_token()}),timeout=25)as r:
        secret=json.loads(base64.b64decode(json.load(r)['payload']['data']))
    if secret['username']!='delta_app'or not secret['password']:raise ValueError('Credential mismatch')
    path=ETC/'db_password';write(path,secret['password'],0o400);os.chown(path,10001,10001)

def compose(*args):return run(['docker','compose','-p','delta-sample','-f',ETC/'compose.json',*args],timeout=240)

def firewall():
    # Source rules also cover published-port hairpin paths; FE can only initiate BE HTTP.
    ip=['iptables','-w','10']
    subprocess.run(ip+['-N','DOCKER-USER'],capture_output=True)
    subprocess.run(ip+['-N','DELTA_COMPOSE_EGRESS'],capture_output=True)
    run(ip+['-F','DELTA_COMPOSE_EGRESS'])
    for rule in [
        ['-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','RETURN'],
        ['-s','172.28.91.2','-d','172.28.91.3','-p','tcp','--dport','8080','-j','RETURN'],
        ['-s','172.28.91.2','-j','DROP'],['-j','RETURN']]:
        run(ip+['-A','DELTA_COMPOSE_EGRESS',*rule])
    if subprocess.run(ip+['-C','DOCKER-USER','-j','DELTA_COMPOSE_EGRESS'],capture_output=True).returncode:
        run(ip+['-I','DOCKER-USER','1','-j','DELTA_COMPOSE_EGRESS'])
    subprocess.run(ip+['-N','DELTA_COMPOSE_INPUT'],capture_output=True)
    run(ip+['-F','DELTA_COMPOSE_INPUT'])
    for rule in [['-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','RETURN'],['-s','172.28.91.2','-j','DROP'],['-j','RETURN']]:
        run(ip+['-A','DELTA_COMPOSE_INPUT',*rule])
    if subprocess.run(ip+['-C','INPUT','-j','DELTA_COMPOSE_INPUT'],capture_output=True).returncode:
        run(ip+['-I','INPUT','1','-j','DELTA_COMPOSE_INPUT'])

def copy_file(source,target):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists()and digest(source)==digest(target):return
    shutil.copyfile(source,target);target.chmod(0o755 if os.access(source,os.X_OK)else 0o644)

def image(stage,service,manifest):
    recipe=digest(__file__)[:12]
    tag='delta-sample-'+service+':'+manifest['sha256'][:12]+'-'+recipe
    found=subprocess.run(['docker','image','inspect',tag],capture_output=True,text=True)
    if found.returncode==0:return tag,json.loads(found.stdout)[0]['Id']
    with tempfile.TemporaryDirectory(prefix='rootfs-',dir=ROOT)as directory:
        root=Path(directory)
        for name in ['app','tmp','etc','usr/local/bin']:(root/name).mkdir(parents=True,exist_ok=True)
        (root/'tmp').chmod(0o1777)
        write(root/'etc/passwd','app:x:10001:10001:app:/app:/nonexistent\n')
        write(root/'etc/group','app:x:10001:\n')
        copy_file('/etc/ssl/certs/ca-certificates.crt',root/'etc/ssl/certs/ca-certificates.crt')
        if service=='proxy':
            copy_file(stage/'runtime/cloud-sql-proxy',root/'usr/local/bin/cloud-sql-proxy')
            (root/'usr/local/bin/cloud-sql-proxy').chmod(0o755)
            entry=['/usr/local/bin/cloud-sql-proxy']
        else:
            binary=stage/'runtime/node';binary.chmod(0o755)
            copy_file(binary,root/'usr/local/bin/node')
            libraries=set(re.findall(r'(/[^\s()]+)',run(['ldd',binary]).stdout))
            # libc can load NSS modules dynamically; preserve its local resolver support.
            libraries.update(str(x)for x in Path('/lib/x86_64-linux-gnu').glob('libnss_*.so.2'))
            for lib in sorted(libraries):copy_file(lib,root/lib.lstrip('/'))
            for path in stage.iterdir():
                if path.name in ['runtime','release.json']:continue
                target=root/'app'/path.name
                if path.is_dir():shutil.copytree(path,target)
                else:shutil.copyfile(path,target)
            for path in (root/'app').rglob('*'):path.chmod(0o755 if path.is_dir()else 0o644)
            if service=='backend':
                write(root/'app/read-secret.mjs',"""import {readFileSync} from 'node:fs';
import net from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
process.env.PGPASSWORD=readFileSync('/run/secrets/db_password','utf8');
for(let n=0;n<120;n++){
 const ready=await new Promise(resolve=>{const s=net.createConnection({host:'127.0.0.1',port:5432});s.setTimeout(1000);s.on('connect',()=>{s.destroy();resolve(true)});s.on('error',()=>resolve(false));s.on('timeout',()=>{s.destroy();resolve(false)})});
 if(ready)break;
 if(n===119)throw new Error('Local database proxy unavailable');
 await delay(500);
}
""")
                entry=['/usr/local/bin/node','--import','/app/read-secret.mjs','/app/server.mjs']
            else:entry=['/usr/local/bin/node','/app/server.mjs']
        archive=ROOT/(service+'-rootfs.tar')
        # Host tooling uses umask 077; every image parent must be traversable by UID 10001.
        for path in root.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        (root/'tmp').chmod(0o1777)
        with tarfile.open(archive,'w')as tar:
            for path in sorted(root.rglob('*')):tar.add(path,arcname=str(path.relative_to(root)),recursive=False)
        result=run(['docker','image','import','--platform','linux/amd64',
                    '--change','USER 10001:10001','--change','WORKDIR /app',
                    '--change','ENV PATH=/usr/local/bin:/usr/bin:/bin',
                    '--change','ENTRYPOINT '+json.dumps(entry),
                    '--change','LABEL org.opencontainers.image.revision='+manifest['commit'],archive,tag],timeout=180)
        archive.unlink()
        return tag,result.stdout.strip()

def configuration(cfg,images):
    db=cfg['database']
    def service(role,ip):
        return dict(image=images[role]['tag'],platform='linux/amd64',pull_policy='never',
                    user='10001:10001',read_only=True,cap_drop=['ALL'],security_opt=['no-new-privileges:true'],
                    tmpfs=['/tmp:rw,noexec,nosuid,size=16m'],restart='unless-stopped',init=True,
                    networks={'app':{'ipv4_address':ip}},
                    mem_limit='384m',pids_limit=100,
                    logging={'driver':'json-file','options':{'max-size':'10m','max-file':'3'}})
    fe=service('frontend','172.28.91.2');be=service('backend','172.28.91.3')
    for s,port in [(fe,3000),(be,8080)]:
        s['environment']={'HOST':'0.0.0.0','PORT':str(port),'APP_ENV':'gcp','NODE_ENV':'production','API_ENDPOINT':'/api'}
        s['healthcheck']={'test':['CMD','/usr/local/bin/node','-e',"fetch('http://127.0.0.1:"+str(port)+"/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
                          'interval':'10s','timeout':'4s','start_period':'20s','retries':12}
    fe['environment'].update(API_HOST='172.28.91.3',API_PORT='8080',API_PROTOCOL='http',APP_VERSION=images['frontend']['version'],APP_NAME='Delta Board',CLOUD_PROVIDER='auto')
    fe['ports']=['10.32.1.20:3000:3000'];fe['depends_on']={'backend':{'condition':'service_healthy'}}
    be['environment'].update(STORAGE_BACKEND='postgres',PGHOST='127.0.0.1',PGPORT='5432',PGDATABASE=db['database'],PGUSER=db['user'],PGSSLMODE='disable',APP_VERSION=images['backend']['version'])
    be['secrets']=['db_password']
    proxy=service('proxy','172.28.91.4');proxy.pop('networks');proxy.pop('init');proxy['mem_limit']='128m'
    proxy.update(network_mode='service:backend',depends_on={'backend':{'condition':'service_started'}},
                 command=['--psc','--address','127.0.0.1','--port','5432','--structured-logs',db['connection_name']])
    return {'services':{'backend':be,'proxy':proxy,'frontend':fe},
            'secrets':{'db_password':{'file':str(ETC/'db_password')}},
            'networks':{'app':{'driver':'bridge','driver_opts':{'com.docker.network.bridge.name':'br-delta-app'},'ipam':{'config':[{'subnet':NETWORK}]}}}}

def prepare(bundle):
    if os.uname().machine!='x86_64':raise ValueError('x86 host required')
    ROOT.mkdir(parents=True,exist_ok=True);ETC.mkdir(mode=0o700,parents=True,exist_ok=True)
    b=Path(bundle);cfg=json.loads((b/'config.json').read_text())
    for name,sha in [('docker.tgz',DOCKER_SHA),('docker-compose',COMPOSE_SHA)]:
        if digest(b/name)!=sha:raise ValueError('Pinned runtime hash mismatch')
    with tempfile.TemporaryDirectory(dir=ROOT)as directory:
        extract(b/'docker.tgz',directory)
        for src in (Path(directory)/'docker').iterdir():copy_file(src,Path('/usr/local/bin')/src.name)
    copy_file(b/'docker-compose',Path('/usr/local/lib/docker/cli-plugins/docker-compose'))
    Path('/usr/local/lib/docker/cli-plugins/docker-compose').chmod(0o755)
    write('/etc/modules-load.d/delta-compose.conf','overlay\nbr_netfilter\n')
    run(['modprobe','overlay']);run(['modprobe','br_netfilter'])
    write('/etc/sysctl.d/90-delta-compose.conf','net.ipv4.ip_forward=1\nnet.bridge.bridge-nf-call-iptables=1\n')
    run(['sysctl','-p','/etc/sysctl.d/90-delta-compose.conf'])
    write('/etc/docker/daemon.json',json.dumps({'storage-driver':'overlay2','features':{'containerd-snapshotter':False},'log-driver':'json-file','log-opts':{'max-size':'10m','max-file':'3'}}))
    # Install the source restrictions before Docker restores any containers on reboot.
    copy_file(Path(__file__),ROOT/'host.py')
    write('/etc/systemd/system/delta-compose-firewall.service','''[Unit]
Description=Delta container source restrictions
Before=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /opt/delta-compose/host.py firewall
''')
    write('/etc/systemd/system/docker.service','''[Unit]
Description=Docker Engine (pinned offline x86 release)
Requires=delta-compose-firewall.service
After=network-online.target delta-compose-firewall.service
Wants=network-online.target
[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd --host=unix:///var/run/docker.sock
Restart=always
RestartSec=5
Delegate=yes
KillMode=process
LimitNOFILE=infinity
TasksMax=infinity
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','docker'])
    images={}
    for role in ['frontend','backend']:
        manifest=json.loads((b/(role+'.json')).read_text());archive=b/(role+'.tar.gz')
        if digest(archive)!=manifest['sha256']:raise ValueError('Artifact hash mismatch')
        with tempfile.TemporaryDirectory(dir=ROOT)as directory:
            stage=Path(directory);extract(archive,stage)
            tag,identifier=image(stage,role,manifest)
            images[role]={'tag':tag,'id':identifier,'version':manifest['version'],'source':manifest}
            if role=='backend':
                tag,identifier=image(stage,'proxy',manifest)
                images['proxy']={'tag':tag,'id':identifier,'version':manifest['proxy_version']}
    # Exercise the real nonroot images before touching the native FE service.
    for role,item in images.items():
        command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
                 '--security-opt','no-new-privileges:true','--entrypoint',
                 '/usr/local/bin/cloud-sql-proxy'if role=='proxy'else'/usr/local/bin/node',item['tag']]
        result=run(command+(['--version']if role=='proxy'else['-p','process.arch']),timeout=30)
        if role!='proxy'and result.stdout.strip()!='x64':raise ValueError('Nonroot x86 image smoke check failed')
    write(ETC/'config.json',json.dumps(cfg),0o600)
    write(ETC/'images.json',json.dumps(images,indent=2),0o600)
    write(ETC/'compose.json',json.dumps(configuration(cfg,images),indent=2),0o600)
    write('/etc/systemd/system/delta-compose.service','''[Unit]
Description=Delta FE/BE Compose and Cloud SQL proxy
Requires=docker.service
After=docker.service network-online.target
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/bin/python3 /opt/delta-compose/host.py secret
ExecStartPre=/usr/bin/python3 /opt/delta-compose/host.py firewall
ExecStart=/usr/local/bin/docker compose -p delta-sample -f /etc/delta-compose/compose.json up -d --wait --wait-timeout 180
ExecStop=/usr/local/bin/docker compose -p delta-sample -f /etc/delta-compose/compose.json stop
TimeoutStartSec=240
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl','daemon-reload']);refresh_secret();compose('config','--quiet')
    firewall()
    print(json.dumps({'prepared':True,'images':images,'docker':run(['docker','version','--format','{{.Server.Version}}']).stdout.strip(),'compose':run(['docker','compose','version','--short']).stdout.strip()}))

def activate():
    write('/etc/systemd/system/delta-sample.service.d/compose.conf','[Unit]\nConditionPathExists=!/etc/delta-compose/active\n')
    write(ETC/'active','compose\n')
    run(['systemctl','daemon-reload']);run(['systemctl','disable','--now','delta-sample'])
    try:
        run(['systemctl','enable','delta-compose'])
        run(['systemctl','restart','delta-compose'],timeout=260)
        run(['systemctl','is-active','--quiet','delta-compose'])
        compose('up','-d','--wait','--wait-timeout','180')
    except Exception:
        compose('stop');(ETC/'active').unlink(missing_ok=True)
        run(['systemctl','enable','--now','delta-sample'])
        raise
    print('Compose activated; native FE disabled.')

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','activate','secret','firewall','status']);p.add_argument('--bundle');a=p.parse_args()
    if a.action=='prepare':prepare(a.bundle)
    elif a.action=='activate':activate()
    elif a.action=='secret':refresh_secret()
    elif a.action=='firewall':firewall()
    else:print(compose('ps','--format','json').stdout)

if __name__=='__main__':
    try:main()
    except Exception:
        print('Compose operation failed; inspect protected systemd diagnostics on the host.',file=sys.stderr)
        sys.exit(1)
