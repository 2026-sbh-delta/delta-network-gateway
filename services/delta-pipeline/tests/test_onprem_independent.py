import copy,json,time
from types import SimpleNamespace
from uuid import UUID,uuid4
from contextlib import contextmanager
import pytest
from delta_pipeline.onprem_independent_store import IndependentStore
from delta_pipeline.onprem_scanners import SAST_TARGET,DAST_TARGET

@pytest.fixture
def private(tmp_path):
    service=str(uuid4());build=str(uuid4());commit='a'*40
    builds={build:{'id':build,'service_id':service,'status':'succeeded','kind':'image_build','resolved_commit_sha':commit,
        'result':{'image':'ecr/image@sha256:'+'b'*64,'image_digest':'sha256:'+'b'*64,'platform':'linux/amd64'}}}
    catalog=SimpleNamespace(engine=object(),run=lambda identifier,kind:copy.deepcopy(builds[str(identifier)]))
    catalog.service=lambda identifier:{'id':str(identifier),'project_id':'project','repository_url':'https://github.com/2026-sbh-delta/delta-sample-app'}
    store=IndependentStore(catalog,tmp_path)
    store.build_source=lambda identifier:{'repository_url':'https://github.com/2026-sbh-delta/delta-sample-app','container_port':8080,'healthcheck_path':'/healthz'}
    return store,service,build,commit

def test_private_queue_never_enqueues_in_team_store_and_is_idempotent(private):
    store,service,build,commit=private
    a,created=store.enqueue_security(UUID(service),UUID(build),'graph-sast',SAST_TARGET)
    b,again=store.enqueue_security(UUID(service),UUID(build),'graph-sast',SAST_TARGET)
    assert created and not again and a['id']==b['id']
    assert a['execution_owner']=='orchestrator-private'
    changed={**SAST_TARGET,'mode':'changed'}
    with pytest.raises(Exception):store.enqueue_security(UUID(service),UUID(build),'graph-sast',changed)

def test_claim_heartbeat_checkpoint_and_restart_preserve_identity(private,tmp_path):
    store,service,build,commit=private
    queued,_=store.enqueue_security(UUID(service),UUID(build),'graph-sast',SAST_TARGET)
    job=store.claim();assert str(job['id'])==queued['id']and job['attempt']==1
    store.heartbeat(job['id'],job['lease_token'])
    assert store.sandbox_checkpoint(job['id'],job['lease_token'],{'result':{'revision':'uid'}})=={'result':{'revision':'uid'}}
    assert store.sandbox_checkpoint(job['id'],job['lease_token'],{'result':{'revision':'changed'}})=={'result':{'revision':'uid'}}
    restart=IndependentStore(store.catalog,tmp_path)
    assert restart.claim()is None
    with restart.db()as db:db.execute('UPDATE jobs SET lease_until=0')
    retry=restart.claim();assert retry['id']==job['id']and retry['attempt']==2 and retry['submission']['result']['revision']=='uid'
    with pytest.raises(Exception):store.heartbeat(job['id'],job['lease_token'])
    restart.finish(retry['id'],retry['lease_token'],result={'checked':True})
    assert restart.run(job['id'],'security_check')['status']=='succeeded'

def test_cancelled_queued_job_never_runs(private):
    store,service,build,_=private
    job,_=store.enqueue_security(UUID(service),UUID(build),'graph-sast',SAST_TARGET)
    store.cancel_sandbox(job['id'],'security_check')
    assert store.claim()is None and store.run(job['id'])['error']['code']=='sandbox_cancelled'

def test_existing_platform_process_one_operates_only_private_jobs(private):
    from delta_platform.worker.main import process_one
    store,service,build,_=private
    job,_=store.enqueue_security(UUID(service),UUID(build),'graph-sast',SAST_TARGET)
    assert process_one(store,None,security=SimpleNamespace(execute=lambda claim,st,progress:{'scanner_run_id':str(claim['id'])}))
    assert store.run(job['id'])['result']['scanner_run_id']==job['id']
    assert not process_one(store,None)

def test_bridge_accepts_real_platform_uuid_objects_in_context(private,monkeypatch):
    from delta_pipeline import onprem_independent_store as module
    from delta_pipeline.onprem_worker_bridge import execute
    store,service,build,commit=private
    store.engine=SimpleNamespace(dispose=lambda:None)
    store.catalog.service=lambda identifier:{'id':UUID(str(identifier)),'name':'registered','project_id':uuid4(),
        'repository_url':'https://github.com/2026-sbh-delta/delta-sample-app','build_context':'backend'}
    monkeypatch.setattr(module,'settings',lambda:None)
    monkeypatch.setattr(module,'catalog',lambda _:store.catalog)
    monkeypatch.setattr(module,'IndependentStore',lambda _:store)
    result=execute({'action':'sast_start','build_id':build,'key':'graph-sast','binding':{
        'repo':'https://github.com/2026-sbh-delta/delta-sample-app','commit':commit}})
    assert result['state']=='running'and store.run(result['run_id'])['kind']=='security_check'

def test_real_security_adapter_persists_submission_before_scanner_call(private):
    from delta_pipeline.onprem_scanners import security_runner
    from delta_platform.worker.main import process_one
    from delta_platform.adapters.security import SecurityRunner
    store,service,build,commit=private
    job,_=store.enqueue_security(UUID(service),UUID(build),'real-adapter',SAST_TARGET)
    report={'version':'2.1.0','runs':[{'results':[]}]}
    class Rpc:
        def request(self,scanner,method,path,body=None):
            assert scanner=='sast'
            if method=='POST':
                saved=store.run(job['id'])['submission']
                assert saved['request']==body and body['payload_mode']=='preset'
                self.body=body
            return report if path.endswith('/sarif')else {**self.body,'state':'done'}
    assert process_one(store,None,security=security_runner(None,Rpc()))
    value=store.run(job['id']);assert value['status']=='succeeded'
    assert value['result']['scanner_run_id']==job['id']
    assert value['result']['evidence_sha256']==SecurityRunner.summarize(report)['evidence_sha256']

def test_private_security_attempts_are_bounded(private):
    store,service,build,_=private
    job,_=store.enqueue_security(UUID(service),UUID(build),'stale-adapter',SAST_TARGET)
    for i in range(3):
        assert store.claim()['attempt']==i+1
        with store.db()as db:db.execute('UPDATE jobs SET lease_until=0')
    assert store.claim()is None
    assert store.run(job['id'])['error']['code']=='private_attempts_exhausted'
