"""Idle-only Compose override; existing platform source/config/network/volume retained."""
import base64,fcntl,gzip,json,os,shutil,subprocess,time
from pathlib import Path

def cmd(args,**kwargs):
 r=subprocess.run(args,capture_output=True,text=True,**kwargs)
 if r.returncode:raise RuntimeError('Native worker release subprocess failed: '+args[0])
 return r.stdout.strip()
def inspect():return json.loads(cmd(['docker','inspect','delta-platform-worker-1']))[0]
def idle():
 code="from delta_platform.config import Settings;from delta_platform.core.database import engine_for;from delta_platform.core.schema import runs;from sqlalchemy import select,func;e=engine_for(Settings());c=e.connect();print(c.scalar(select(func.count()).select_from(runs).where(runs.c.status.in_(['queued','running']))))"
 if cmd(['docker','exec','delta-platform-worker-1','python','-c',code])!='0':raise RuntimeError('Existing platform jobs active; release refused')
def main():
 old=inspect()
 if old['Config']['Image']!=BASE_IMAGE:raise RuntimeError('Worker image changed; review required')
 idle();root=Path('/opt/delta-platform/native-orchestrator');root.mkdir(mode=0o700,exist_ok=True)
 with (root/'release.lock').open('w')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  labels=old['Config']['Labels'];original=labels['com.docker.compose.project.config_files']
  if ','in original or not original.startswith('/opt/delta-platform/releases/'):raise ValueError('Unexpected compose source')
  compose=['docker','compose','-p','delta-platform','--project-directory',labels['com.docker.compose.project.working_dir'],'--env-file','/etc/delta-platform/deploy.env','-f',original]
  config=json.loads(cmd(compose+['config','--format','json']))
  if config['services']['worker']['image']!=BASE_IMAGE:raise ValueError('Deployment source drift')
  overlay=root/'compose.override.json'
  overlay.write_text(json.dumps({'services':{'worker':{'image':IMAGE,'command':['python','-m','delta_pipeline.onprem_native_worker'],
   'environment':{'DELTA_NATIVE_SCANNER_DOCUMENT':DOCUMENT,'DELTA_NATIVE_SCANNER_HASH':DOCUMENT_HASH}}}}));overlay.chmod(0o600)
  merged=compose+['-f',str(overlay)]
  after=json.loads(cmd(merged+['config','--format','json']))
  a,b=config['services']['worker'].copy(),after['services']['worker'].copy()
  for value in (a,b):
   for key in ('image','command'):value.pop(key,None)
   value['environment']={k:v for k,v in value.get('environment',{}).items()if k not in ('DELTA_NATIVE_SCANNER_DOCUMENT','DELTA_NATIVE_SCANNER_HASH')}
  if a!=b:raise ValueError('Unexpected Compose difference')
  auth=cmd(['aws','ecr','get-login-password','--region','ap-northeast-2']);registry=root/'registry';registry.mkdir(mode=0o700,exist_ok=True)
  try:
   cmd(['docker','--config',str(registry),'login','--username','AWS','--password-stdin',IMAGE.split('/')[0]],input=auth)
   cmd(['docker','--config',str(registry),'pull',IMAGE])
  finally:shutil.rmtree(registry)
  del auth
  cmd(['docker','run','--rm','--network',old['HostConfig']['NetworkMode'],'--entrypoint','python',IMAGE,'-c',
   'from delta_pipeline.onprem_native_worker import main;from delta_pipeline.onprem_evidence import capture;from delta_platform.worker.main import process_one;print("ready")'])
  idle()
  if inspect()['Id']!=old['Id']:raise RuntimeError('Concurrent worker release')
  # Compose replaces only the Worker; API/DB/other platform services are untouched.
  try:
   cmd(merged+['up','-d','--no-deps','worker']);time.sleep(3)
   new=inspect();env=dict(i.split('=',1)for i in new['Config']['Env']if '='in i)
   oldenv=dict(i.split('=',1)for i in old['Config']['Env']if '='in i)
   if new['Config']['Image']!=IMAGE or not new['State']['Running']:raise RuntimeError('Worker not running')
   if any(env.get(k)!=v for k,v in oldenv.items()if k not in ('HOSTNAME','PYTHONPATH')):raise RuntimeError('Existing environment changed')
   if new['Mounts']!=old['Mounts'] or new['HostConfig']['NetworkMode']!=old['HostConfig']['NetworkMode']:raise RuntimeError('Mount/network changed')
  except Exception:
   cmd(compose+['up','-d','--no-deps','worker']);raise
  return {'image':IMAGE,'base_image':BASE_IMAGE,'compose_override':str(overlay),'existing_environment_preserved':True,'mounts_network_preserved':True,'other_services_changed':False}
if __name__=='__main__':
 try:print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
 except Exception as error:raise SystemExit('Native worker release failed safely: '+type(error).__name__)
