import base64,gzip,json,importlib.util,os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from delta_pipeline.onprem_scanners import ScannerRpc,SAST_INSTANCE,DAST_INSTANCE

class Client:
    exceptions=SimpleNamespace(InvocationDoesNotExist=LookupError)
    def __init__(self):self.calls=[]
    def send_command(self,**args):self.calls.append(args);return {'Command':{'CommandId':'c'}}
    def get_command_invocation(self,**args):return {'Status':'Success','StandardOutputContent':base64.b64encode(gzip.compress(b'{"ok":true,"value":{"state":"running"}}')).decode()}

@pytest.fixture
def rpc(monkeypatch):
    monkeypatch.setenv('DELTA_NATIVE_SCANNER_DOCUMENT','delta-cp-native-scanners-'+'a'*12)
    monkeypatch.setenv('DELTA_NATIVE_SCANNER_HASH','a'*64)
    sdk=Client();return ScannerRpc(sdk,sleep=lambda _:None),sdk

def test_pinned_service_instance_and_no_credentials_in_ssm(rpc):
    client,sdk=rpc;rid=str(uuid4())
    assert client.request('dast','GET','/api/platform/runs/'+rid)=={'state':'running'}
    a=sdk.calls[0];assert a['InstanceIds']==[DAST_INSTANCE] and a['DocumentVersion']=='1'and a['DocumentHash']=='a'*64
    assert json.loads(gzip.decompress(base64.b64decode(a['Parameters']['payload'][0])))=={'service':'dast','method':'GET','path':'/api/platform/runs/'+rid,'body':None}

@pytest.mark.parametrize('service,method,path',[('unknown','GET','/health'),('dast','GET','http://evil/'),('sast','POST','/v1/sast/scans/notuuid'),('dast','GET','/api/platform/runs/'+str(uuid4())+'/../tokens')])
def test_arbitrary_request_cannot_dispatch(rpc,service,method,path):
    client,sdk=rpc
    with pytest.raises(ValueError):client.request(service,method,path)
    assert not sdk.calls

def test_fixed_host_proxy_rejects_arbitrary_target_before_exec(monkeypatch):
    p=Path(__file__).resolve().parents[3]/'deploy/onprem-runtime/scanner_proxy.py'
    spec=importlib.util.spec_from_file_location('scanner_proxy',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('must not execute'))
    with pytest.raises(ValueError):module.invoke({'service':'dast','method':'GET','path':'/api/platform/runs/'+str(uuid4())+'/secrets','body':None})
