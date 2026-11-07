"""Native zero-candidate results follow the same scope rule as CSP staging."""
from types import SimpleNamespace
from uuid import UUID

import pytest

from delta_pipeline import onprem
from delta_pipeline.model import Board, Options
from delta_pipeline.steps import Steps

COMMIT = 'a' * 40
RUN = '733bdf80-1234-4321-8765-123456789abc'
DIGEST = 'sha256:' + 'b' * 64
IMAGE = 'example.invalid/app@' + DIGEST


def fixture(tmp_path):
    value = dict(execution_cloud='onprem', runtime='k3s', cleanup='stopped',
        runtime_check='passed', image=IMAGE, image_digest=DIGEST, commit_sha=COMMIT,
        scanner_run_id=RUN, sast_evidence_sha256='c'*64,
        resource='namespaces/delta-platform-sandbox/pods/delta-sbx-'+UUID(RUN).hex,
        revision='pod-uid', deployment_approved=False, dast_findings=[], eligible_count=0,
        requires_review=True, findings_truncated=False, coverage='sast_xss_candidates_only',
        dast_evidence_sha256='d'*64, runtime_evidence_sha256='e'*64,
        dast_check='not_run', dast_reason='no_supported_xss_candidates')
    job = dict(id=RUN, status='succeeded', kind='dast_check', result=value)
    board = Board(tmp_path/'board.json', Options('https://github.com/2026-sbh-delta/delta-sample-app', COMMIT), 'test-run')
    deps = SimpleNamespace(board=board, options=SimpleNamespace(commit=COMMIT, assistance=False),
        cancelled=lambda:False, onprem=SimpleNamespace(get=lambda rid:job), sleep=lambda _:None)
    state = dict(onprem_run_id=RUN, image=IMAGE, image_digest=DIGEST,
        onprem_evidence_sha256='c'*64, dast_targets=0, gates={'sast':'pass'})
    return Steps(deps), state, value


def test_evidenced_zero_candidates_skip_without_human_timeout(tmp_path):
    steps, state, _ = fixture(tmp_path)
    result = onprem.wait(steps, state)
    assert result.get('gates', {}).get('dast') == 'pass'
    assert not result.get('pending_gate')
    node = next(n for n in steps._board.snapshot()['nodes'] if n['id']=='dast')
    assert node['state'] == 'skipped'
    assert node['detail']['dast_check'] == 'not_run'
    assert node['detail']['deployment_approved'] is False


@pytest.mark.parametrize('change', [
    {'dast_targets':1}, {'dast_targets':None}, {'first_dast_run_id':'previous-scan'},
    {'gates':{'sast':'approved'}},
])
def test_missing_scope_or_previous_findings_still_require_review(tmp_path, change):
    steps, state, _ = fixture(tmp_path)
    state.update(change)
    assert onprem.wait(steps,state).get('pending_gate')


@pytest.mark.parametrize('change', [
    {'dast_reason':None}, {'dast_reason':'scanner_unavailable'},
    {'runtime_evidence_sha256':''}, {'runtime_evidence_gap':True},
    {'dast_check':'error'},
])
def test_missing_or_failed_evidence_is_not_a_zero_candidate_exception(tmp_path, change):
    steps, state, value = fixture(tmp_path)
    value.update(change)
    assert onprem.wait(steps,state).get('pending_gate')


def test_full_onprem_graph_finishes_without_requesting_approval_for_zero_targets(tmp_path):
    from fakes import FakeSast, FakeStaging, build_jenkins, deploy_jenkins, IMAGE_REPO
    from test_flow import _execute
    from delta_pipeline.run import Run

    _, _, value = fixture(tmp_path)
    class Native:
        def start(self, key, **binding):
            value.update(image=binding['image'], image_digest=binding['image'].split('@')[1],
                commit_sha=binding['commit'], sast_evidence_sha256=binding['sast_evidence_sha256'])
            return RUN
        def get(self, run_id):
            return dict(id=RUN, status='succeeded', kind='dast_check', result=value)
        def inspect(self, run_id):
            return {}

    class Source(FakeSast):
        def start(self, key, repo, commit):
            from uuid import uuid5, NAMESPACE_URL
            return super().start(str(uuid5(NAMESPACE_URL, key)), repo, commit)

    run = Run(Options('https://github.com/2026-sbh-delta/delta-sample-app', COMMIT,
        execution_cloud='onprem', assistance=True), sast=Source(targets=0), dast=object(),
        build_jenkins=build_jenkins(), deploy_jenkins=deploy_jenkins(), staging=FakeStaging(),
        image_repo=IMAGE_REPO, store_dir=tmp_path/'graph', onprem=Native(), sleep=lambda _:None)
    snapshot = _execute(run)
    assert snapshot['outcome'] == 'verified'
    dast = next(n for n in snapshot['nodes'] if n['id']=='dast')
    assert dast['state'] == 'skipped'
    assert dast['detail']['dast_check'] == 'not_run'
    assert not any(n.get('decision') for n in snapshot['nodes'])
