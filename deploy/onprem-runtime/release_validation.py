"""Loopback-only verification instance of the existing orchestrator; primary stays intact."""
import base64,gzip,json,os,shutil,subprocess,time
from pathlib import Path

def cmd(args,**kwargs):
 r=subprocess.run(args,capture_output=True,text=True,**kwargs)
 if r.returncode:raise RuntimeError('Validation subprocess failed')
 return r.stdout.strip()
def main():
 primary=json.loads(cmd(['docker','inspect','delta-orchestrator']))[0]
 if subprocess.run(['docker','inspect','delta-onprem-validation'],capture_output=True).returncode==0:raise RuntimeError('Validation instance already exists')
 env=dict(i.split('=',1)for i in primary['Config']['Env']if '='in i)
 connection=json.loads(json.loads(cmd(['aws','ssm','get-parameter','--name','/delta-cp/orchestrator/diagnostic_ai_connection','--with-decryption','--region','ap-northeast-2','--output','json']))['Parameter']['Value'])
 env.update(DELTA_DIAGNOSTIC_URL=connection['url'],DELTA_DIAGNOSTIC_TOKEN=connection['token'],DELTA_DIAGNOSTIC_CA_BASE64=connection.get('ca_base64',''),
  DELTA_DIAGNOSTICS_REQUIRED='1',DELTA_LOG_EVIDENCE_ENABLED='1',DELTA_ONPREM_ENABLED='1',DELTA_ONPREM_VERIFICATION='0',
  DELTA_ONPREM_DOCUMENT=DOCUMENT,DELTA_ONPREM_DOCUMENT_HASH=DOCUMENT_HASH,ORCH_STATE_DIR='/state')
 root=Path('/opt/delta/onprem-validation');root.mkdir(mode=0o700,exist_ok=True)
 state=root/'state';state.mkdir(mode=0o700,exist_ok=True);os.chown(state,10001,10001)
 file=root/'environment';file.write_text(''.join(k+'='+v+'\n'for k,v in env.items()));file.chmod(0o600)
 auth=cmd(['aws','ecr','get-login-password','--region','ap-northeast-2']);registry=root/'registry';registry.mkdir(mode=0o700)
 try:
  cmd(['docker','--config',str(registry),'login','--username','AWS','--password-stdin',IMAGE.split('/')[0]],input=auth)
  cmd(['docker','--config',str(registry),'pull',IMAGE])
 finally:shutil.rmtree(registry)
 del auth
 cmd(['docker','run','--rm','--network','host','--env-file',str(file),'--entrypoint','python',IMAGE,'-m','delta_orchestrator.preflight'])
 cmd(['docker','run','-d','--name','delta-onprem-validation','--label','delta.owner=orchestrator-onprem-validation','--restart','unless-stopped',
  '--network','host','--env-file',str(file),'--mount','type=bind,source='+str(state)+',target=/state',
  '--memory','512m','--cpus','0.5',IMAGE,'uvicorn','delta_orchestrator.app:create_app','--factory','--host','127.0.0.1','--port','8081','--log-level','warning'])
 for _ in range(30):
  if subprocess.run(['curl','--max-time','1','--fail','--silent','http://127.0.0.1:8081/healthz'],capture_output=True).returncode==0:break
  time.sleep(1)
 else:raise RuntimeError('Validation readiness failed')
 after=json.loads(cmd(['docker','inspect','delta-orchestrator']))[0]
 if primary['Id']!=after['Id']or primary['Config']!=after['Config']:raise RuntimeError('Primary changed concurrently')
 return {'image':IMAGE,'container':'delta-onprem-validation','bind':'127.0.0.1:8081','private_state':str(state),'primary_unchanged':True,'primary_image':primary['Config']['Image']}
if __name__=='__main__':
 try:print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
 except Exception as error:raise SystemExit('Loopback validation release failed: '+type(error).__name__)
