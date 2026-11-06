import copy,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from delta_pipeline.onprem_evidence import capture,load,redacted

@pytest.fixture
def context():
    rid=str(uuid4());target=SimpleNamespace(namespace='delta-platform-sandbox',api_url='https://10.40.0.10:6443')
    target.name=lambda _: 'delta-sbx-'+rid.replace('-','');target.resource=lambda _: 'namespaces/'+target.namespace+'/pods/'+target.name(rid)
    claim={'id':rid,'resolved_commit_sha':'a'*40,'input_snapshot':{'build_id':str(uuid4()),'image':'repo@sha256:'+'b'*64,
        'image_digest':'sha256:'+'b'*64,'sandbox_target':{'api_url':target.api_url,'namespace':target.namespace}},'submission':{'result':{'revision':'uid1'}}}
    pod={'metadata':{'uid':'uid1'}}
    p=SimpleNamespace(target=target,call=lambda *a,**k:copy.deepcopy(pod),owned=lambda *a:None)
    return claim,p,pod

def test_redacted_bounded_logs_survive_cleanup_and_replay(tmp_path,context):
    claim,p,pod=context
    value=capture(claim,p,root=tmp_path,reader=lambda *_:('password=PRIVATE Bearer HIDDEN mail@example.com https://user:secret@host/path\nrequest200',False))
    raw=json.dumps(value)
    assert all(x not in raw for x in ('PRIVATE','HIDDEN','mail@example.com','user:secret'))
    assert value['record']['containers'][0]['uid']=='uid1'
    assert value['record']['before_cleanup'] is True
    p.call=lambda *a,**k:None
    assert capture(claim,p,root=tmp_path,reader=lambda *_:pytest.fail('must not recapture deleted pod'))==value
    assert load(claim,root=tmp_path)==value
    assert os.stat(next(tmp_path.glob('*.json'))).st_mode&0o077==0
    assert hashlib.sha256(next(tmp_path.glob('*.json')).read_bytes()).hexdigest()==value['sha256']

def test_replaced_verified_uid_never_reads_logs(tmp_path,context):
    claim,p,pod=context;pod['metadata']['uid']='foreign'
    result=capture(claim,p,root=tmp_path,reader=lambda *_:pytest.fail('replaced pod'))
    assert result['record']['containers'][0]['status']=='unavailable'
    assert 'text'not in result['record']['containers'][0]

def test_replaced_between_reads_discards_text(tmp_path,context):
    claim,p,pod=context;calls=[]
    def get(*a,**k):
        calls.append(1);return {'metadata':{'uid':'uid1'if len(calls)==1 else 'foreign'}}
    p.call=get
    result=capture(claim,p,root=tmp_path,reader=lambda *_:('do not keep',False))
    assert 'do not keep'not in json.dumps(result)

@pytest.mark.parametrize('field',['image_digest','build_id','target','native_run_id'])
def test_tampered_binding_is_rejected(tmp_path,context,field):
    claim,p,_=context;capture(claim,p,root=tmp_path,reader=lambda *_:('ok',False))
    path=next(tmp_path.glob('*.json'));data=json.loads(path.read_bytes());data[field]='changed';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):load(claim,root=tmp_path)

def test_log_permission_failure_is_explicit_gap(tmp_path,context):
    claim,p,_=context
    def denied(*_):raise PermissionError('token=DO_NOT_EXPORT')
    out=capture(claim,p,root=tmp_path,reader=denied)
    assert out['record']['containers'][0]['status']=='unavailable'
    assert 'DO_NOT_EXPORT'not in json.dumps(out)
