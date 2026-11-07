"""Fixed-host negative access checks. Session credentials never leave the container."""

import base64
import gzip
import subprocess

code = """import json,os,urllib.request,urllib.error
from urllib.parse import urlsplit
from delta_orchestrator.access import Access,COOKIE
access=Access.from_env();origin=access.public_origin;host=urlsplit(origin).netloc
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
cookie=COOKIE+'='+access.issue()
def call(path,body=None,auth=True,request_origin=origin,content_type='application/json',local=False):
 headers={'Host':'127.0.0.1:8080' if local else host,'Content-Type':content_type}
 if auth:headers['Cookie']=cookie
 if request_origin is not None:headers['Origin']=request_origin
 request=urllib.request.Request('http://127.0.0.1:8080'+path,data=body,headers=headers)
 try:
  with opener.open(request,timeout=15) as r:return r.status
 except urllib.error.HTTPError as e:return e.code
path='/api/case-reviews/case-1aa722776440495bb635c6106af1301d-first/export'
checks={'anonymous':call('/api/case-reviews',auth=False),'authenticated':call('/api/case-reviews'),
 'loopback_anonymous':call('/api/case-reviews',auth=False,local=True,request_origin=None),
 'wrong_origin':call(path,b'{}',request_origin='https://invalid.example'),
 'missing_origin':call(path,b'{}',request_origin=None),
 'non_json':call(path,b'{}',content_type='text/plain'),
 'oversized':call(path,b'x'*16001),
 'revoked_export':call(path,json.dumps({'revision':'35f8b16c0174756876da91d0ce13e3785604e906397d23d8c39feb90552d10e8'}).encode())}
assert checks=={'anonymous':401,'authenticated':200,'loopback_anonymous':401,'wrong_origin':403,'missing_origin':403,'non_json':415,'oversized':413,'revoked_export':409},checks
print(json.dumps(checks))
"""
result = subprocess.run(
    ["docker", "exec", "delta-orchestrator", "python", "-c", code],
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise SystemExit("Runtime access verification failed")
print(base64.b64encode(gzip.compress(result.stdout.encode())).decode())
