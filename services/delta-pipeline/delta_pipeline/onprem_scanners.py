"""Fixed, IAM protected RPC; scanner credentials stay on their existing hosts."""
import base64
import gzip
import json
import os
import re
import time
from uuid import UUID

SAST_INSTANCE='i-01e002ddcf92c7b82'
DAST_INSTANCE='i-0f7ce2c84581c4e35'
SAST_TARGET={'url':'ssm://delta-cp/sast/v1','mode':'fast','payload_mode':'preset'}
DAST_TARGET={'url':'ssm://delta-cp/dast/v1','mode':'fast','payload_mode':'preset'}

class ScannerRpc:
    def __init__(self,client=None,*,sleep=time.sleep):
        if client is None:
            import boto3
            from botocore.config import Config
            client=boto3.client('ssm',region_name='ap-northeast-2',config=Config(connect_timeout=3,read_timeout=5,retries={'total_max_attempts':1}))
        self.client,self.sleep=client,sleep
        self.document=os.environ.get('DELTA_NATIVE_SCANNER_DOCUMENT','')
        self.hash=os.environ.get('DELTA_NATIVE_SCANNER_HASH','')
        if not re.fullmatch('delta-cp-native-scanners-[a-f0-9]{12}',self.document) or not re.fullmatch('[a-f0-9]{64}',self.hash):
            raise ValueError('Pinned scanner document required')
    def request(self,service,method,path,body=None):
        if service not in ('sast','dast'):raise ValueError('Unsupported scanner')
        root='/v1/sast/scans' if service=='sast' else '/api/platform/runs'
        valid=(service=='sast' and method=='POST' and path==root)
        if path.startswith(root+'/'):
            tail=path[len(root)+1:].split('/')
            UUID(tail[0])
            valid=((method=='GET' and len(tail)==1) or
                (service=='sast' and method=='GET' and tail[1:]==['sarif']) or
                (service=='dast' and method=='PUT' and len(tail)==1) or
                (service=='dast' and method=='POST' and tail[1:]==['cancel']))
        if not valid:raise ValueError('Unsupported scanner request')
        raw=json.dumps({'service':service,'method':method,'path':path,'body':body},separators=(',',':')).encode()
        if len(raw)>120000:raise ValueError('Scanner request too large')
        packed=base64.b64encode(gzip.compress(raw)).decode()
        if len(packed)>20000:raise ValueError('Scanner request too large')
        target=SAST_INSTANCE if service=='sast' else DAST_INSTANCE
        cid=self.client.send_command(InstanceIds=[target],DocumentName=self.document,DocumentVersion='1',
            DocumentHash=self.hash,DocumentHashType='Sha256',Parameters={'payload':[packed]},TimeoutSeconds=60)['Command']['CommandId']
        for _ in range(45):
            self.sleep(1)
            try:r=self.client.get_command_invocation(CommandId=cid,InstanceId=target)
            except self.client.exceptions.InvocationDoesNotExist:continue
            if r['Status'] in ('Pending','InProgress','Delayed'):continue
            if r['Status']!='Success':raise RuntimeError('Scanner RPC unavailable')
            output=r.get('StandardOutputContent','')
            if len(output)>24000:raise ValueError('Scanner response too large')
            raw=gzip.decompress(base64.b64decode(output))
            if len(raw)>120000:raise ValueError('Scanner response too large')
            value=json.loads(raw)
            if not value.get('ok'):raise RuntimeError('Scanner request rejected')
            return value['value']
        raise RuntimeError('Scanner RPC timed out')

class ScannerHttp:
    def __init__(self,service,rpc):self.service,self.rpc=service,rpc
    def request(self,method,path,body=None):
        from delta_platform.adapters.builds import BuildError
        try:return self.rpc.request(self.service,method,path,body)
        except Exception:raise BuildError('security_provider_error','고정 검사 서버 응답을 확인하지 못했습니다.') from None

def security_runner(settings,rpc=None):
    from delta_platform.adapters.security import SecurityRunner
    class NativeSecurity(SecurityRunner):
        def __init__(self):self.target,self.sleep=SAST_TARGET,time.sleep
        def request(self,method,path,body=None):return ScannerHttp('sast',remote).request(method,path,body)
    remote=rpc or ScannerRpc()
    return NativeSecurity()

def inspector(settings,rpc=None):
    from delta_platform.adapters.dast import DastInspector
    from delta_platform.adapters.sandbox import SandboxError
    from delta_platform.adapters.security import SecurityRunner
    class NativeInspector(DastInspector):
        def __init__(self):
            self.target=DAST_TARGET
            self.http=ScannerHttp('dast',remote)
            self.sast=ScannerHttp('sast',remote)
            self.sast.target=SAST_TARGET
            self.sast.summarize=SecurityRunner.summarize
            self.poll_seconds=2
        def request(self,method,run_id,body=None,suffix=''):
            try:return self.http.request(method,'/api/platform/runs/'+str(UUID(str(run_id)))+suffix,body)
            except Exception:raise SandboxError('dast_provider_error','고정 DAST 연결 응답을 확인하지 못했습니다.') from None
    remote=rpc or ScannerRpc()
    return NativeInspector()
