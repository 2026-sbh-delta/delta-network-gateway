"""Generate the auditable immutable SSM document from committed bridge sources."""
import base64,hashlib,json,zlib
from pathlib import Path
root=Path(__file__).resolve().parents[2]
files={'delta_pipeline/__init__.py':'','delta_ai/__init__.py':'',
       'delta_pipeline/model.py':'REPO_PREFIX = "https://github.com/2026-sbh-delta/"\n'}
for name in ('clients.py','registered_build.py','onprem.py','onprem_worker_bridge.py'):
    files['delta_pipeline/'+name]=(root/'services/delta-pipeline/delta_pipeline'/name).read_text()
for name in ('diagnostic_contract.py','secrets_pattern.py'):
    files['delta_ai/'+name]=(root/'services/delta-ai/delta_ai'/name).read_text()
package=base64.b64encode(zlib.compress(json.dumps(files).encode())).decode()
bootstrap="""import base64,json,zlib,tempfile,pathlib,sys
files=json.loads(zlib.decompress(base64.b64decode(PACKAGE)))
request=json.loads(sys.stdin.read(10001))
with tempfile.TemporaryDirectory(prefix='delta-onprem-bridge-') as folder:
 for name,body in files.items():
  path=pathlib.Path(folder)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
 sys.path.insert(0,folder)
 from delta_pipeline.onprem_worker_bridge import main
 main(request)
""".replace('PACKAGE',repr(package))
outer="""import base64,json,os,subprocess
raw=base64.b64decode(os.environ['SSM_payload'],validate=True)
if len(raw)>10000:raise SystemExit('Request too large')
json.loads(raw)
r=subprocess.run(['docker','exec','-i','delta-platform-worker-1','python','-c',BOOT],input=raw,capture_output=True,timeout=50)
if r.returncode:raise SystemExit('Fixed Worker bridge failed')
print(r.stdout.decode().strip())
""".replace('BOOT',repr(bootstrap))
doc={'schemaVersion':'2.2','description':'Fixed on-premise queue bridge; no caller supplied code or credentials',
     'parameters':{'payload':{'type':'String','allowedPattern':'^[A-Za-z0-9+/=]+$','interpolationType':'ENV_VAR'}},
     'mainSteps':[{'action':'aws:runShellScript','name':'fixedWorkerRpc','inputs':{'timeoutSeconds':'60',
        'runCommand':["python3 - <<'DELTA_FIXED_BRIDGE'\n"+outer+"\nDELTA_FIXED_BRIDGE"]}}]}
folder=root/'deploy/onprem-runtime'
(folder/'document.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
(folder/'document-source.json').write_text(json.dumps({p:hashlib.sha256(s.encode()).hexdigest() for p,s in files.items()},indent=2)+'\n')
print('Generated fixed document and source hashes')
