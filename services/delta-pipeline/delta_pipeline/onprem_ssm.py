"""Pinned fixed-code SSM RPC to one existing Worker. It cannot execute arbitrary commands."""
import base64
import gzip
import json
import os
from pathlib import Path
import re
import time
from uuid import UUID

from delta_pipeline.clients import ServiceUnavailable,TransientServiceError

INSTANCE='i-0e5df7881a7de8fbd'
DOCUMENT='delta-cp-onprem-bridge'

class WorkerRpc:
    def __init__(self, *, client=None, document_hash=None, sleep=time.sleep):
        if client is None:
            import boto3
            from botocore.config import Config
            client=boto3.client('ssm',region_name='ap-northeast-2',config=Config(connect_timeout=3,read_timeout=5,retries={'total_max_attempts':1}))
        self.client,self.sleep=client,sleep
        self.document=os.environ.get('DELTA_ONPREM_DOCUMENT',DOCUMENT)
        if not re.fullmatch(r'delta-cp-onprem-bridge(?:-[a-f0-9]{12})?',self.document):raise ServiceUnavailable('연결 문서 이름이 잘못됐습니다')
        self.document_hash=document_hash or os.environ.get('DELTA_ONPREM_DOCUMENT_HASH','')
        if not re.fullmatch('[a-f0-9]{64}',self.document_hash):raise ServiceUnavailable('고정된 온프레미스 연결 문서 해시가 필요합니다')

    def call(self, action, **values):
        request={'action':action,**values}
        if action not in {'catalog','context','start','status','cancel','inspect','retry_ready','resume_ready','observation_ready','absence','sast_start','sast_status','sast_report'}:
            raise ServiceUnavailable('허용되지 않은 온프레미스 동작입니다')
        raw=json.dumps(request,separators=(',',':')).encode()
        if len(raw)>10000:raise ServiceUnavailable('온프레미스 요청이 너무 큽니다')
        try:
            command=self.client.send_command(InstanceIds=[INSTANCE],DocumentName=self.document,
                DocumentVersion='1',DocumentHash=self.document_hash,DocumentHashType='Sha256',
                Parameters={'payload':[base64.b64encode(raw).decode()]},TimeoutSeconds=60)['Command']['CommandId']
            for _ in range(45):
                self.sleep(1)
                try:reply=self.client.get_command_invocation(CommandId=command,InstanceId=INSTANCE)
                except self.client.exceptions.InvocationDoesNotExist:continue
                if reply['Status'] in ('Pending','InProgress','Delayed'):continue
                if reply['Status']!='Success':raise TransientServiceError('온프레미스 연결 응답을 확인하지 못했습니다')
                output=reply['StandardOutputContent']
                if len(output)>30000:raise ValueError()
                data=json.loads(gzip.decompress(base64.b64decode(output)))
                if not data.get('ok'):raise ServiceUnavailable('온프레미스 실행기가 요청을 거부했습니다')
                return data['value']
            raise TransientServiceError('온프레미스 연결 조회 시간이 초과됐습니다')
        except (ServiceUnavailable,TransientServiceError):raise
        except Exception as error:
            name=type(error).__name__
            if name in ('EndpointConnectionError','ReadTimeoutError','ConnectTimeoutError'):
                raise TransientServiceError('온프레미스 연결이 일시적으로 응답하지 않습니다') from None
            raise ServiceUnavailable('온프레미스 연결 또는 응답을 검증하지 못했습니다') from None

class RemoteOnprem:
    def __init__(self,rpc,build_id,*,graph_id=None,state_dir=None):
        self.rpc,self.build_id=rpc,str(UUID(build_id))
        self.graph_id,self.state_dir=graph_id,Path(state_dir) if state_dir else None
        self.board=None
    def bind_board(self,board):self.board=board
    def start(self,key,**binding):
        value=self.rpc.call('start',build_id=self.build_id,key=key,binding=binding)
        self._verification_loss('start')
        return value['id']
    def get(self,identifier):
        result=self.rpc.call('status',build_id=self.build_id,id=identifier)
        self._verification_loss('read')
        if self.board is not None:
            self.board.add_detail('stage',platform_status=result.get('status'),platform_step=result.get('current_step'),
                error_code=(result.get('error') or {}).get('code'))
        return result
    def cancel(self,identifier):return self.rpc.call('cancel',build_id=self.build_id,id=identifier)
    def inspect(self,identifier):return self.rpc.call('inspect',build_id=self.build_id,id=identifier)
    def retry_ready(self,identifier,**binding):return self.rpc.call('retry_ready',build_id=self.build_id,id=identifier,binding=binding)
    def resume_ready(self,identifier,**binding):return self.rpc.call('resume_ready',build_id=self.build_id,id=identifier,binding=binding)
    def observation_ready(self,identifier,**binding):return self.rpc.call('observation_ready',build_id=self.build_id,id=identifier,binding=binding)
    def _verification_loss(self,kind):
        # Operator-only bounded reply-loss test. No public request can set or arm this file.
        if os.environ.get('DELTA_ONPREM_VERIFICATION')!='1' or not self.state_dir:return
        path=self.state_dir/'verification'/'onprem-response-loss.json'
        if path.parent.is_symlink() or not path.is_file() or path.is_symlink() or path.stat().st_size>2048:return
        try:value=json.loads(path.read_text())
        except (ValueError,OSError):return
        if not isinstance(value,dict) or type(value.get('expires_at')) not in (int,float):return
        if (value.get('graph_id')!=self.graph_id or value.get('build_id')!=self.build_id
                or not time.time()<value.get('expires_at',0)<=time.time()+1800):return
        key='remaining_'+kind
        count=value.get(key,0)
        if type(count) is not int or not 0<count<=20:return
        value[key]=count-1
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value));temporary.chmod(0o600);temporary.replace(path)
        raise TransientServiceError('검증용 호출자 응답 유실: 기존 작업 ID·키를 유지합니다')

class RemoteSast:
    def __init__(self,rpc,build_id):self.rpc,self.build_id=rpc,str(UUID(build_id))
    def start(self,key,repo,commit):return self.rpc.call('sast_start',build_id=self.build_id,key=key,binding={'repo':repo,'commit':commit})
    def scan(self,identifier):return self.rpc.call('sast_status',build_id=self.build_id,id=str(UUID(identifier)))
    def sarif(self,identifier):return self.rpc.call('sast_report',build_id=self.build_id,id=str(UUID(identifier)))
