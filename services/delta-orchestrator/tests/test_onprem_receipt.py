import copy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from delta_orchestrator.app import create_app
from delta_orchestrator.access import Access, COOKIE


def fixture():
    native, build, backend, service = [str(uuid4()) for _ in range(4)]
    image = 'registry.example/app@sha256:' + 'b'*64
    board = {'run_id': 'a'*64, 'outcome': 'unresolved', 'options': {
        'execution_cloud':'onprem', 'commit':'c'*40, 'onprem_build_id':build},
        'nodes':[{'id':'stage','state':'pass','detail':{'platform_run_id':native,
            'runtime_revision':'pod-uid','cleanup':'stopped'}},
            {'id':'dast','state':'pass','detail':{'runtime_evidence_sha256':'d'*64}}]}
    job = {'id':native,'service_id':service,'kind':'dast_check','status':'succeeded',
        'resolved_commit_sha':'c'*40, 'execution_owner':'orchestrator-private',
        'input_snapshot':{'build_id':build,'image':image,'image_digest':'sha256:'+'b'*64,
            'backend':{'build_id':backend,'image':image,'image_digest':'sha256:'+'b'*64},
            'secret':'must-not-leave'},
        'result':{'execution_cloud':'onprem','runtime':'k3s','runtime_check':'passed',
            'cleanup':'stopped','revision':'pod-uid','commit_sha':'c'*40,'image':image,
            'image_digest':'sha256:'+'b'*64,'runtime_evidence_sha256':'d'*64}}
    return board, job


def test_receipt_uses_executor_and_never_returns_private_configuration():
    from delta_orchestrator.onprem_receipt import receipt
    board, job = fixture()
    calls=[]
    rpc=SimpleNamespace(call=lambda action,**kw:calls.append((action,kw)) or job)
    result=receipt(board,rpc)
    assert calls == [('status',{'build_id':board['options']['onprem_build_id'],
                               'id':job['id']})]
    assert result['images']['backend']==job['input_snapshot']['backend']['image']
    assert result['service_id']==job['service_id']
    assert 'secret' not in str(result) and 'input_snapshot' not in result


@pytest.mark.parametrize('where,key,value',[
    ('job','id',str(uuid4())),('job','status','running'),('job','resolved_commit_sha','e'*40),
    ('snapshot','build_id',str(uuid4())),('result','cleanup','pending'),
    ('result','revision','another-pod'),('result','runtime_evidence_sha256','e'*64),
    ('result','runtime_check','failed'),('result','image','registry.example/wrong@sha256:'+'e'*64),
])
def test_receipt_rejects_wrong_or_incomplete_evidence(where,key,value):
    from delta_orchestrator.onprem_receipt import receipt
    board,job=fixture()
    target={'job':job,'snapshot':job['input_snapshot'],'result':job['result']}[where]
    target[key]=value
    with pytest.raises(ValueError):receipt(board,SimpleNamespace(call=lambda *a,**kw:job))


def test_receipt_endpoint_is_authenticated_and_bound_to_pipeline(monkeypatch):
    from delta_pipeline import onprem_ssm
    board,job=fixture();origin='https://orchestrator.example'
    monkeypatch.setenv('ORCH_PUBLIC_ORIGIN',origin)
    monkeypatch.setenv('ORCH_ADMIN_PASSWORD','test-orchestrator-password')
    monkeypatch.setenv('ORCH_SESSION_SECRET','s'*40)
    monkeypatch.setattr(onprem_ssm,'WorkerRpc',lambda:SimpleNamespace(call=lambda *a,**kw:job))
    manager=SimpleNamespace(get=lambda rid:board if rid==board['run_id'] else None)
    with TestClient(create_app(manager),base_url=origin) as client:
        path='/api/pipeline/runs/'+board['run_id']+'/onprem-evidence'
        assert client.get(path).status_code==401
        client.cookies.set(COOKIE,Access.from_env().issue())
        assert client.get(path).json()['native_run_id']==job['id']
        assert client.get('/api/pipeline/runs/foreign/onprem-evidence').status_code==404
        board['nodes'][0]['state']='running'
        assert client.get(path).status_code==409
