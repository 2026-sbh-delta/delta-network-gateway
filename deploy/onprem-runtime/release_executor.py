"""Create only the explicitly approved independent executor; shared Worker is immutable here."""
import base64,gzip,json,os,shutil,subprocess,time
from pathlib import Path

def cmd(args,**kwargs):
 r=subprocess.run(args,capture_output=True,text=True,**kwargs)
 if r.returncode:raise RuntimeError('Independent executor subprocess failed')
 return r.stdout.strip()
def shared():return json.loads(cmd(['docker','inspect','delta-platform-worker-1']))[0]
def main():
 old=shared()
 previous=None
 existing=subprocess.run(['docker','inspect','delta-onprem-executor'],capture_output=True,text=True)
 if existing.returncode==0:
  current=json.loads(existing.stdout)[0]
  if (current['Config']['Labels'].get('delta.owner')!='orchestrator-onprem'
    or current['Config']['Image']!=globals().get('EXPECTED_EXECUTOR_IMAGE')):raise RuntimeError('Independent executor changed; review required')
  idle="import sqlite3;c=sqlite3.connect('/var/lib/delta-platform/tmp/onprem-queue.sqlite');assert c.execute(\"SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')\").fetchone()[0]==0;print('idle')"
  cmd(['docker','exec','delta-onprem-executor','python','-c',idle])
 env0=dict(i.split('=',1)for i in old['Config']['Env']if '='in i)
 # No platform login, Jenkins, cloud credential or legacy scanner token is copied.
 keys=('PLATFORM_DATABASE_URL','PLATFORM_ONPREM_SANDBOX_TARGET','PLATFORM_ONPREM_SANDBOX_TOKEN')
 env={k:env0[k]for k in keys};env.update(DELTA_NATIVE_SCANNER_DOCUMENT=DOCUMENT,DELTA_NATIVE_SCANNER_HASH=DOCUMENT_HASH)
 if any(not v or any(c in v for c in '\r\n\0')for v in env.values()):raise ValueError('Invalid executor configuration')
 root=Path('/opt/delta/onprem-executor');root.mkdir(mode=0o700,parents=True,exist_ok=True)
 state=root/'state';state.mkdir(mode=0o700,exist_ok=True);os.chown(state,10001,10001)
 file=root/'runtime.env';file.write_text(''.join(k+'='+v+'\n'for k,v in env.items()));file.chmod(0o600)
 auth=cmd(['aws','ecr','get-login-password','--region','ap-northeast-2']);registry=root/'registry';registry.mkdir(mode=0o700)
 try:
  cmd(['docker','--config',str(registry),'login','--username','AWS','--password-stdin',IMAGE.split('/')[0]],input=auth)
  cmd(['docker','--config',str(registry),'pull',IMAGE])
 finally:shutil.rmtree(registry)
 del auth
 network=old['HostConfig']['NetworkMode']
 if network!='delta-platform_default':raise ValueError('Unexpected platform network')
 code="from delta_pipeline.onprem_independent_store import settings,catalog;from delta_platform.core.database import ready;from sqlalchemy import text;c=catalog(settings());assert ready(c.engine);conn=c.engine.connect();assert conn.scalar(text('SHOW default_transaction_read_only'))=='on';print('ready-readonly')"
 cmd(['docker','run','--rm','--network',network,'--env-file',str(file),'--entrypoint','python',IMAGE,'-c',code])
 created=False
 try:
  if existing.returncode==0:
   cmd(['docker','exec','delta-onprem-executor','python','-c',idle])
   previous='delta-onprem-executor-previous-'+str(int(time.time()))
   cmd(['docker','stop','delta-onprem-executor']);cmd(['docker','rename','delta-onprem-executor',previous])
  cmd(['docker','run','-d','--name','delta-onprem-executor','--label','delta.owner=orchestrator-onprem','--restart','unless-stopped',
   '--network',network,'--env-file',str(file),'--mount','type=bind,source='+str(state)+',target=/var/lib/delta-platform/tmp',
   '--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=64m','--security-opt','no-new-privileges','--cap-drop','ALL',
   '--pids-limit','128','--memory','512m','--cpus','0.5',IMAGE]);created=True;time.sleep(3)
  new=json.loads(cmd(['docker','inspect','delta-onprem-executor']))[0]
  if not new['State']['Running']or new['RestartCount']:raise RuntimeError('Independent executor not ready')
  check=shared()
  if check['Id']!=old['Id']or check['Config']!=old['Config']:raise RuntimeError('Shared Worker changed concurrently')
  cmd(['docker','exec','delta-onprem-executor','python','-c',"from delta_pipeline.onprem_independent_store import IndependentStore,catalog,settings;s=IndependentStore(catalog(settings()));assert s.claim()is None;print('private-queue-ready')"])
  return {'image':IMAGE,'container':'delta-onprem-executor','previous_container':previous,'shared_worker_unchanged':True,'shared_worker_image':old['Config']['Image'],
   'database_transactions_readonly':True,'private_state':str(state),'memory_limit_mib':512,'cpu_limit':0.5}
 except Exception:
  if created:subprocess.run(['docker','rm','-f','delta-onprem-executor'],capture_output=True)
  if previous:cmd(['docker','rename',previous,'delta-onprem-executor']);cmd(['docker','start','delta-onprem-executor'])
  raise
if __name__=='__main__':
 try:print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
 except Exception as error:
  import traceback
  raise SystemExit('Independent executor release failed safely: '+type(error).__name__+' '+str([(f.name,f.lineno)for f in traceback.extract_tb(error.__traceback__)]))
