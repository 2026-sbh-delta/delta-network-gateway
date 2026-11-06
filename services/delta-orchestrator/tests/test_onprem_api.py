from types import SimpleNamespace
from uuid import uuid4
from fastapi.testclient import TestClient
from delta_orchestrator.app import create_app
from delta_orchestrator.access import Access,COOKIE

def test_onprem_selection_is_authenticated_validated_and_bound(monkeypatch):
    origin='https://orchestrator.example'
    monkeypatch.setenv('ORCH_PUBLIC_ORIGIN',origin)
    monkeypatch.setenv('ORCH_ADMIN_PASSWORD','test-orchestrator-password')
    monkeypatch.setenv('ORCH_SESSION_SECRET','s'*40)
    monkeypatch.delenv('DELTA_ONPREM_ENABLED',raising=False)
    calls=[]
    p=SimpleNamespace(start=lambda *args,**kw:calls.append((args,kw)) or 'run1',run=None,running=lambda:False)
    with TestClient(create_app(p),base_url=origin) as client:
        assert client.get('/api/onprem/builds').status_code==401
        client.cookies.set(COOKIE,Access.from_env().issue())
        headers={'Origin':origin}
        body={'repo':'https://github.com/2026-sbh-delta/delta-sample-app','commit':'a'*40,
              'execution_cloud':'onprem','assistance':True,'onprem_build_id':str(uuid4())}
        assert client.post('/api/pipeline/runs',json={**body,'onprem_build_id':'not-a-uuid'},headers=headers).status_code==400
        assert client.post('/api/pipeline/runs',json={**body,'cutover':True},headers=headers).status_code==400
        assert not calls
        assert client.post('/api/pipeline/runs',json=body,headers=headers).status_code==202
        assert calls[0][1]['onprem_build_id']==body['onprem_build_id'] and calls[0][1]['assistance'] is True
        assert client.post('/api/pipeline/runs/foreign/cancel',headers=headers).status_code==409
