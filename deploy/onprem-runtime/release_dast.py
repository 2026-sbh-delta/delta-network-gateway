"""Add only the registered onprem connection to the unchanged, idle CP scanner."""
import base64,gzip,hashlib,json,os,shutil,subprocess,time
from pathlib import Path

def cmd(args):
 r=subprocess.run(args,capture_output=True,text=True)
 if r.returncode:raise RuntimeError('DAST configuration subprocess failed')
 return r.stdout.strip()
def inspect():return json.loads(cmd(['docker','inspect','delta-dast']))[0]
def idle():
 code="import os,json,urllib.request;r=urllib.request.Request('http://127.0.0.1:8765/api/runs',headers={'Host':'dast.cp.internal:8765','Authorization':'Bearer '+os.environ['DAST_PLATFORM_TOKEN']});d=json.load(urllib.request.urlopen(r));assert not any(x['status']=='running'for x in d['runs']);print(len(d['runs']))"
 return int(cmd(['docker','exec','delta-dast','python','-c',code]))
def hashes(old):
 return {str(p):hashlib.sha256(p.read_bytes()).hexdigest()for m in old['Mounts']if m['Destination']in('/app/data','/app/runs')for p in Path(m['Source']).rglob('*')if p.is_file()and not p.is_symlink()}
def main():
 old=inspect()
 if old['HostConfig']['NetworkMode']!='host':raise ValueError('Unexpected scanner network')
 if old['Config']['Image']!=EXPECTED_IMAGE:raise RuntimeError('Scanner changed; review required')
 records=idle();env=dict(i.split('=',1)for i in old['Config']['Env']if '='in i)
 raw=json.loads(cmd(['aws','ssm','get-parameter','--name','/delta-cp/dast/onprem_connection','--with-decryption','--region','ap-northeast-2','--output','json']))
 if raw['Parameter']['Type']!='SecureString':raise ValueError()
 connection=json.loads(raw['Parameter']['Value']);target=json.loads(connection['DAST_ONPREM_TARGET'])
 if target['api_url']!='https://10.40.0.10:6443' or target['node_ip']!='10.40.0.10' or connection['expires_at']<time.time()+3600:raise ValueError()
 env.update({k:connection[k]for k in ('DAST_ONPREM_TARGET','DAST_ONPREM_TOKEN')})
 root=Path('/opt/delta/onprem-dast');root.mkdir(mode=0o700,exist_ok=True);file=root/'environment';file.write_text(''.join(k+'='+v+'\n'for k,v in env.items()));file.chmod(0o600)
 before=hashes(old);idle()
 if inspect()['Id']!=old['Id']:raise RuntimeError('Concurrent scanner release')
 previous='delta-dast-before-onprem-'+str(int(time.time()))
 cmd(['docker','stop','delta-dast']);cmd(['docker','rename','delta-dast',previous])
 try:
  args=['docker','run','-d','--name','delta-dast','--restart','unless-stopped','--network',old['HostConfig']['NetworkMode'],'--env-file',str(file)]
  for m in old['Mounts']:
   if m['Type']!='bind':raise ValueError()
   args+=['--mount','type=bind,source='+m['Source']+',target='+m['Destination']+(''if m['RW']else',readonly')]
  log=old['HostConfig']['LogConfig']
  if log['Type']:
   args+=['--log-driver',log['Type']]
   for k,v in log.get('Config',{}).items():args+=['--log-opt',k+'='+v]
  args+=['--shm-size',str(old['HostConfig']['ShmSize'])]
  for value in old['HostConfig'].get('SecurityOpt')or[]:args+=['--security-opt',value]
  for key,flag in [('Memory','--memory'),('CpuQuota','--cpu-quota'),('CpuPeriod','--cpu-period')]:
   if old['HostConfig'].get(key,0)>0:args+=[flag,str(old['HostConfig'][key])]
  args+=[EXPECTED_IMAGE]+old['Config']['Cmd'];cmd(args)
  for _ in range(30):
   try:idle();break
   except Exception:time.sleep(1)
  else:raise RuntimeError('Scanner readiness failed')
  if before!=hashes(old):raise RuntimeError('Existing scanner evidence changed')
  return {'image':EXPECTED_IMAGE,'previous_container':previous,'existing_records':records,'state_files_preserved':len(before),'connection_enabled':True,'token_expiry':connection['expires_at']}
 except Exception:
  subprocess.run(['docker','rm','-f','delta-dast'],capture_output=True);cmd(['docker','rename',previous,'delta-dast']);cmd(['docker','start','delta-dast']);raise
if __name__=='__main__':
 try:print(base64.b64encode(gzip.compress(json.dumps(main()).encode())).decode())
 except Exception as error:raise SystemExit('DAST configuration failed safely: '+type(error).__name__)
