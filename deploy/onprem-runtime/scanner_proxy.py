"""Fixed host-side entrypoint embedded in the immutable scanner SSM document."""
import base64,gzip,io,json,os,re,subprocess
from uuid import UUID

def invoke(request):
    service,method,path,body=(request[k]for k in ('service','method','path','body'))
    root='/v1/sast/scans' if service=='sast' else '/api/platform/runs'
    if service not in ('sast','dast') or set(request)!={'service','method','path','body'}:raise ValueError()
    valid=service=='sast' and method=='POST' and path==root
    if path.startswith(root+'/'):
        tail=path[len(root)+1:].split('/');UUID(tail[0])
        valid=(method=='GET' and len(tail)==1 or service=='sast' and method=='GET' and tail[1:]==['sarif']
            or service=='dast' and method=='PUT' and len(tail)==1 or service=='dast' and method=='POST' and tail[1:]==['cancel'])
    if not valid:raise ValueError()
    if service=='sast' and method=='POST':
        if (set(body)!={'run_id','repo','commit','mode'} or body['repo']!='https://github.com/2026-sbh-delta/delta-sample-app'
                or not re.fullmatch('[a-f0-9]{40}',body['commit']) or body['mode']!='fast'):raise ValueError()
        UUID(body['run_id'])
    if service=='dast' and method=='PUT':
        b=body['binding']
        if (b['execution_cloud']!='onprem' or b['target_url']!='http://10.40.0.10:32080'
                or b['runtime_resource']!='namespaces/delta-platform-sandbox/pods/delta-sbx-'+UUID(tail[0]).hex
                or not re.fullmatch('[a-f0-9]{40}',b['commit_sha'])
                or not re.fullmatch('616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@sha256:[a-f0-9]{64}',b['image'])):raise ValueError()
    inner='''import gzip,base64,json,os,sys,urllib.request
r=json.loads(sys.stdin.read(120001));service=r['service'];port=8790 if service=='sast' else 8765
host=service+'.cp.internal:'+str(port)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
token=os.environ['DELTA_SAST_TOKEN' if service=='sast' else 'DAST_PLATFORM_TOKEN']
req=urllib.request.Request('http://127.0.0.1:'+str(port)+r['path'],None if r['body'] is None else json.dumps(r['body']).encode(),method=r['method'],headers={'Host':host,'Authorization':'Bearer '+token,'Content-Type':'application/json'})
try:
 with op.open(req,timeout=20)as response:
  data=response.read(120001)
  if len(data)>120000:raise ValueError()
  value={'ok':True,'value':json.loads(data)}
except urllib.error.HTTPError as error:value={'ok':False,'code':'scanner_request_unavailable','http_status':error.code}
except Exception:value={'ok':False,'code':'scanner_request_unavailable'}
raw=base64.b64encode(gzip.compress(json.dumps(value,separators=(',',':')).encode())).decode()
if len(raw)>23000:raw=base64.b64encode(gzip.compress(b'{"ok":false,"code":"scanner_response_too_large"}')).decode()
print(raw)
'''
    result=subprocess.run(['docker','exec','-i','delta-'+service,'python','-c',inner],
        input=json.dumps(request).encode(),capture_output=True,timeout=35)
    if result.returncode:raise RuntimeError()
    return result.stdout.decode().strip()

if __name__=='__main__':
    try:
        encoded=os.environ['SSM_payload']
        if len(encoded)>20000:raise ValueError()
        packed=base64.b64decode(encoded,validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed))as stream:raw=stream.read(120001)
        if len(raw)>120000:raise ValueError()
        print(invoke(json.loads(raw)))
    except Exception:
        print(base64.b64encode(gzip.compress(b'{"ok":false,"code":"scanner_request_rejected"}')).decode())
