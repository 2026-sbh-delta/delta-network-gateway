"""Issue a bounded scanner token directly to its intended SecureString; never output it."""
import base64,json,os,subprocess,tempfile
from pathlib import Path
from datetime import UTC,datetime
ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(Path.home()/'.config/delta/known_hosts'),'-J','pve','delta@10.40.0.10']
def remote(command):return subprocess.check_output(ssh+[command],text=True).strip()
ca=remote('sudo -n cat /var/lib/rancher/k3s/server/tls/server-ca.crt')
token=remote('sudo -n k3s kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml -n delta-platform-sandbox create token scanner --duration=24h')
expiry=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'='*((-len(token.split('.')[1]))%4)))['exp']
target={'api_url':'https://10.40.0.10:6443','namespace':'delta-platform-sandbox','node_ip':'10.40.0.10','node_name':'delta-onprem','node_port':32080,
 'allowed_image_prefixes':['616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@'],'ca_base64':base64.b64encode(ca.encode()).decode()}
value={'DAST_ONPREM_TARGET':json.dumps(target,separators=(',',':')),'DAST_ONPREM_TOKEN':token,'expires_at':expiry}
fd,name=tempfile.mkstemp(prefix='delta-scanner-')
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w')as f:json.dump(value,f)
 p=subprocess.run(['aws','ssm','put-parameter','--name','/delta-cp/dast/onprem_connection','--type','SecureString','--value','file://'+name,
  '--profile','delta-finals','--region','ap-northeast-2','--output','json','--no-cli-pager'],capture_output=True,text=True)
 if p.returncode:raise RuntimeError('SecureString publication failed; existing parameter preserved')
 print(json.dumps({'parameter':'/delta-cp/dast/onprem_connection','version':json.loads(p.stdout)['Version'],'scanner_expiry':datetime.fromtimestamp(expiry,UTC).isoformat()}))
finally:Path(name).unlink(missing_ok=True)
