import json
from dataclasses import replace

import pytest

from delta_ai.diagnostic_contract import DiagnosisRequest, unavailable_report
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.model import Options
from delta_pipeline.reports import ReportRef, ReportStore
from delta_pipeline.run import Run
from fakes import COMMIT, IMAGE_REPO, REPO, FakeDast, FakeSast, FakeStaging, build_jenkins, deploy_jenkins
from test_flow import _execute


class Diagnostic:
    def __init__(self, mode='analyzed'):
        self.calls, self.mode = [], mode

    def diagnose(self, body):
        self.calls.append(body)
        if self.mode == 'outage': raise ServiceUnavailable('token=DO-NOT-LOG')
        result = unavailable_report(DiagnosisRequest.model_validate(body)).model_dump(mode='json')
        result.update(status='analyzed', model='test-diagnostic')
        if self.mode == 'invalid': result.update(requires_human=False, next_node='done')
        return result


def make_run(path, *, diagnostic=None, status='REPRODUCED', build=None):
    build = build or build_jenkins()
    deploy = deploy_jenkins()
    run = Run(Options(REPO, COMMIT, diagnostics=True), sast=FakeSast(), dast=FakeDast([status]),
              build_jenkins=build, deploy_jenkins=deploy, staging=FakeStaging(), image_repo=IMAGE_REPO,
              store_dir=path, sleep=lambda _: None, diagnostic=diagnostic or Diagnostic())
    return run, build, deploy


def test_dast_report_saved_before_original_human_gate_and_bundle_is_preserved(tmp_path):
    diagnostic = Diagnostic()
    run, build, deploy = make_run(tmp_path, diagnostic=diagnostic)
    answer = run.answer
    def inspect(value):
        ref = next(n['detail']['report']['reference'] for n in run.snapshot()['nodes'] if n['id']=='exception-report')
        directory = ReportStore(tmp_path/'reports').verify(ReportRef.model_validate(ref))
        assert not (directory/'decision.json').exists()
        request = json.loads((directory/'report.json').read_text())['request']
        assert any('REPRODUCED' in o['summary'] for o in request['observations'])
        assert any(o['evidence_id']=='scanner-execution-binding' for o in request['observations'])
        return answer(value)
    run.answer = inspect
    final = _execute(run, ['stop'])
    assert final['outcome']=='stopped' and len(diagnostic.calls)==1
    assert len(build.keys)==len(deploy.keys)==1
    assert json.loads(next(tmp_path.rglob('decision.json')).read_text())['answer']=='stop'


@pytest.mark.parametrize('mode', ['outage','invalid'])
def test_analysis_failure_is_visible_without_auto_pass(tmp_path, mode):
    run, _, _ = make_run(tmp_path, diagnostic=Diagnostic(mode))
    final = _execute(run, ['stop'])
    assert final['outcome']=='stopped'
    report = json.loads(next(tmp_path.rglob('report.json')).read_text())
    assert report['analysis']['status']=='analysis_unavailable'
    assert 'DO-NOT-LOG' not in json.dumps(report)


def test_execution_failure_only_offers_acknowledge_or_stop(tmp_path):
    build=build_jenkins()
    def fail(*a, **k): raise ServiceUnavailable('unavailable')
    build.trigger=fail
    run, _, deploy = make_run(tmp_path, build=build)
    answer=run.answer
    def inspect(value):
        review=next(n for n in run.snapshot()['nodes'] if n['id']=='exception-review')
        assert {o['value'] for o in review['decision']['options']}=={'acknowledge','stop'}
        assert not answer('approve') and not answer('fix')
        return answer(value)
    run.answer=inspect
    assert _execute(run,['acknowledge'])['outcome']=='stopped'
    assert not deploy.keys


def test_report_storage_failure_stops_before_approval(tmp_path, monkeypatch):
    def fail(*a): raise OSError('disk full')
    monkeypatch.setattr(ReportStore,'save',fail)
    run, _, _=make_run(tmp_path)
    assert _execute(run)['outcome']=='stopped'
    assert not any(n['decision'] for n in run.snapshot()['nodes'])


def test_clean_result_does_not_create_unnecessary_diagnosis(tmp_path):
    diagnostic=Diagnostic();run,_,_=make_run(tmp_path,diagnostic=diagnostic,status='NOT_REPRODUCED')
    assert _execute(run)['outcome']=='verified'
    assert not diagnostic.calls and not list(tmp_path.rglob('report.json'))


def test_unavailable_log_collection_is_saved_as_evidence_without_changing_scanner_gate(tmp_path):
    from delta_pipeline.cloudwatch_evidence import CloudWatchEvidence
    class Denied:
        def filter_log_events(self, **kwargs):raise RuntimeError('AccessDenied token=PRIVATE')
    diagnostic=Diagnostic()
    run,build,deploy=make_run(tmp_path,diagnostic=diagnostic)
    run._deps=replace(run._deps,evidence=CloudWatchEvidence(tmp_path/'log-evidence',client_factory=Denied))
    assert _execute(run,['stop'])['outcome']=='stopped'
    saved=json.loads(next(tmp_path.rglob('report.json')).read_text())
    observations=saved['request']['observations']
    assert len([o for o in observations if o['source']=='cloudwatch'])==3
    assert any(o['evidence_id']=='scanner-execution-binding' for o in observations)
    from delta_ai.diagnostic_contract import diagnosis_hash
    assert saved['analysis']['input_hash']==diagnosis_hash(DiagnosisRequest.model_validate(diagnostic.calls[0]))
    assert len(build.keys)==len(deploy.keys)==1 and 'PRIVATE' not in json.dumps(saved)
