import json
from pathlib import Path
import stat

from delta_ai.diagnostic_contract import DiagnosisRequest
from delta_pipeline.cloudwatch_evidence import CloudWatchEvidence


def request():
    return DiagnosisRequest(case_id="case1", run_id="run1", service_id="sample", node_id="dast",
        trigger="ambiguous_result", observations=[{"evidence_id":"scanner", "source":"dast", "summary":"REPRODUCED"}])


def event(value, stamp=95000, identity="e1"):
    return {"timestamp":stamp,"eventId":identity,"message":json.dumps(value)}


class Logs:
    def __init__(self, groups=None, error=False):
        self.groups,self.error,self.calls=groups or {},error,[]

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:raise RuntimeError("token=PRIVATE-PROVIDER-ERROR")
        return self.groups.get(kwargs["logGroupName"], {"events":[]})


def facts(result):
    return {o.evidence_id:json.loads(o.summary) for o in result.observations if o.source=="cloudwatch"}


def test_execution_binding_and_time_window_exclude_other_runs_and_stale_events(tmp_path):
    logs=Logs({"/delta-cp/platform/orchestrator":{"events":[
        event({"run_id":"run1","to":"fail","summary":"password=PRIVATE"}),
        event({"run_id":"other","to":"pass"},identity="other"),
        event({"run_id":"run1","to":"pass"},stamp=89999,identity="old"),
    ]}})
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=90)
    own=facts(result)["cw-run"]
    assert own["events"]==1 and own["discarded"]==2 and own["node_states"]=={"fail":1}
    assert logs.calls[0]["filterPattern"]=='"run1"'
    assert all(c["startTime"]==90000 and c["endTime"]==100000 for c in logs.calls)
    assert "PRIVATE" not in result.model_dump_json()
    assert "PRIVATE" not in next(tmp_path.glob('*.json')).read_text()
    assert stat.S_IMODE(next(tmp_path.glob('*.json')).stat().st_mode)==0o600


def test_environment_sample_is_labeled_and_unparsed_records_are_counted(tmp_path):
    aws={"message":json.dumps({"detail":{"severity":8,"service":{"additionalInfo":{"sample":True}}}})}
    logs=Logs({"/delta-cp/central/aws":{"events":[event(aws)]},
        "/delta-cp/central/gcp":{"events":[event({"original":{"jsonPayload":{"disposition":"DENIED"}}}),
            event({"newVendorFormat":"unrecognized"},identity="new-format")]}})
    value=facts(CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=90))
    assert value['cw-aws']['sample_events']==1 and value['cw-aws']['scope']=='environment'
    assert value['cw-gcp']['counts']=={'DENIED':1} and value['cw-gcp']['unparsed']==1
    assert '연관성 미확인' in value['cw-gcp']['limitation']


def test_access_failure_is_not_empty_or_success_and_never_echoes_provider_error(tmp_path):
    logs=Logs(error=True)
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=90)
    assert {x['status'] for x in facts(result).values()}=={'unavailable'}
    assert 'PRIVATE' not in result.model_dump_json()


def test_restart_uses_same_evidence_and_corrupt_cache_produces_explicit_gap(tmp_path):
    logs=Logs()
    first=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=90)
    assert len(logs.calls)==3
    second=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:1000).enrich(request(),started_at=90)
    assert first==second and len(logs.calls)==3
    next(tmp_path.glob('*.json')).write_text('{"observations":[]}')
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:1000).enrich(request(),started_at=90)
    assert facts(result)['cw-gap']['status']=='unavailable' and len(logs.calls)==3


def test_pagination_is_bounded_and_report_marks_partial_history(tmp_path):
    class Pages(Logs):
        def filter_log_events(self, **kwargs):
            self.calls.append(kwargs)
            return {'events':[event({'original':{'jsonPayload':{'disposition':'ALLOWED'}}},identity=str(i)) for i in range(20)],'nextToken':str(len(self.calls))}
    logs=Pages()
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=90)
    assert len(logs.calls)==6
    assert all(v['truncated'] for v in facts(result).values())


def test_missing_execution_time_does_not_make_an_unbounded_request(tmp_path):
    logs=Logs()
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:logs,now=lambda:100).enrich(request(),started_at=None)
    assert not logs.calls and facts(result)['cw-gap']['status']=='unavailable'


def test_symlink_cache_is_not_read_or_overwritten(tmp_path):
    target=tmp_path/'external';target.write_text('preserve')
    (tmp_path/'case1.json').symlink_to(target)
    result=CloudWatchEvidence(tmp_path,client_factory=lambda:Logs(),now=lambda:100).enrich(request(),started_at=90)
    assert facts(result)['cw-gap']['status']=='unavailable' and target.read_text()=='preserve'
