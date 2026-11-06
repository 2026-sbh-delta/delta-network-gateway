import base64,gzip,json
from types import SimpleNamespace
import pytest
from delta_pipeline.onprem_ssm import WorkerRpc,INSTANCE
from delta_pipeline.clients import ServiceUnavailable

class Ssm:
    def __init__(self,value=None):
        self.calls=[];self.value=value or {'ok':True,'value':{'id':'native1'}}
        self.exceptions=SimpleNamespace(InvocationDoesNotExist=LookupError)
    def send_command(self,**kwargs):self.calls.append(kwargs);return {'Command':{'CommandId':'command1'}}
    def get_command_invocation(self,**kwargs):
        return {'Status':'Success','StandardOutputContent':base64.b64encode(gzip.compress(json.dumps(self.value).encode())).decode()}

def test_fixed_document_hash_and_worker_no_arbitrary_commands():
    sdk=Ssm();rpc=WorkerRpc(client=sdk,document_hash='a'*64,sleep=lambda _:None)
    assert rpc.call('context',build_id='build1')=={'id':'native1'}
    sent=sdk.calls[0]
    assert sent['InstanceIds']==[INSTANCE] and sent['DocumentHash']=='a'*64 and sent['DocumentVersion']=='1'
    payload=json.loads(base64.b64decode(sent['Parameters']['payload'][0]))
    assert payload=={'action':'context','build_id':'build1'} and 'commands' not in sent['Parameters']
    with pytest.raises(ServiceUnavailable):rpc.call('execute_shell',command='rm -rf /')
    assert len(sdk.calls)==1

def test_missing_hash_refuses_before_aws_and_upstream_errors_are_opaque(monkeypatch):
    monkeypatch.delenv('DELTA_ONPREM_DOCUMENT_HASH',raising=False)
    sdk=Ssm()
    with pytest.raises(ServiceUnavailable):WorkerRpc(client=sdk)
    assert not sdk.calls
    rpc=WorkerRpc(client=Ssm({'ok':False,'error':'password=PRIVATE'}),document_hash='a'*64,sleep=lambda _:None)
    with pytest.raises(ServiceUnavailable) as error:rpc.call('status',id='native1')
    assert 'PRIVATE' not in str(error.value)

def test_unsupported_document_parameters_rejected_before_worker_settings():
    from delta_pipeline.onprem_worker_bridge import execute
    with pytest.raises(ValueError):execute({'action':'execute_shell','command':'echo private'})
