import re
from types import SimpleNamespace

from delta_pipeline.steps import Steps


class Scanner:
    def __init__(self):
        self.ids = []

    def start(self, run_id, repo, commit):
        assert re.fullmatch(r'[A-Za-z0-9._-]{1,64}', run_id), 'SAST rejects this run_id (HTTP 422)'
        self.ids.append(run_id)
        return {'run_id': run_id}


def test_long_pipeline_id_reaches_sast_with_stable_bounded_identity():
    scanner = Scanner()
    details = []
    board = SimpleNamespace(update=lambda *a, **kw: None, add_detail=lambda *a, **kw: details.append(kw))
    steps = Steps(SimpleNamespace(run_id='a' * 64, options=SimpleNamespace(repo='https://github.com/example/app', commit='b' * 40, execution_cloud='gcp'), board=board, sast=scanner))
    first = steps.sast_start({})
    assert steps.sast_start({}) == first
    second = steps.sast_start({'round': 2})
    assert first['sast_run_id'] != second['sast_run_id']
    assert details[0]['run_id'] == first['sast_run_id'] == scanner.ids[0]
    assert len(steps._key({}, 'build')) == 64


def test_existing_short_sast_identity_is_preserved():
    steps = Steps(SimpleNamespace(run_id='existing', board=None))
    assert steps._key({}, 'sast') == 'existing-sast'
    assert steps._key({'round': 2}, 'sast') == 'existing-r2-sast'


def test_validation_error_names_field_without_echoing_input():
    import io
    import json
    import urllib.error
    import urllib.request
    import pytest
    from delta_pipeline.clients import _json, ServiceUnavailable

    def rejected(request, **kwargs):
        payload = {"detail": [{"loc": ["body", "run_id"], "input": "SECRET_VALUE", "msg": "SECRET_VALUE"}]}
        raise urllib.error.HTTPError(request.full_url, 422, "invalid", {}, io.BytesIO(json.dumps(payload).encode()))

    with pytest.raises(ServiceUnavailable) as failure:
        _json(urllib.request.Request("http://sast/v1/sast/scans"), timeout=1, opener=rejected)
    assert "run_id" in str(failure.value) and "422" in str(failure.value)
    assert "SECRET_VALUE" not in str(failure.value)


def test_sast_transport_failure_is_saved_on_the_failed_step():
    import pytest
    from delta_pipeline.clients import ServiceUnavailable
    updates = []
    def rejected(*args):
        raise ServiceUnavailable("HTTP 422 (요청 형식 오류: run_id)")
    steps = Steps(SimpleNamespace(run_id="a" * 64, options=SimpleNamespace(repo="repo", commit="b" * 40),
                                  board=SimpleNamespace(update=lambda *a, **kw: updates.append(kw)),
                                  sast=SimpleNamespace(start=rejected)))
    with pytest.raises(ServiceUnavailable):
        steps.sast_start({})
    assert updates[-1]["state"] == "fail"
    assert "422" in updates[-1]["summary"]


def test_all_stage_keys_fit_jenkins_run_id_contract():
    from pathlib import Path
    contract = Path(__file__).resolve().parents[3] / 'deploy/platform/jenkins/controller/Jenkinsfile.build'
    assert "^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$" in contract.read_text()
    steps = Steps(SimpleNamespace(run_id='a' * 64, board=None))
    keys = []
    for name in ['build', 'sast', 'stage', 'release', 'onprem']:
        for round_ in [1, 2]:
            key = steps._key({'round': round_}, name)
            assert re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,63}', key), (name, len(key))
            assert key == steps._key({'round': round_}, name)
            keys.append(key)
    assert len(set(keys)) == len(keys)


def test_onprem_recovery_identity_is_bounded_and_distinct():
    steps = Steps(SimpleNamespace(run_id='a' * 64, board=None))
    first = steps._key({'onprem_attempt': 1}, 'onprem')
    second = steps._key({'onprem_attempt': 2}, 'onprem')
    assert len(first) == len(second) == 64 and first != second
    assert first == steps._key({'onprem_attempt': 1}, 'onprem')
    short = Steps(SimpleNamespace(run_id='short', board=None))
    assert short._key({'onprem_attempt': 2}, 'onprem') == 'short-onprem-recovery2'


def test_long_pipeline_id_keeps_scan_and_dast_lookup_identity(tmp_path):
    from delta_pipeline.model import Options
    from delta_pipeline.run import Run
    from fakes import FakeSast, FakeDast, FakeStaging, build_jenkins, deploy_jenkins, IMAGE_REPO, REPO, COMMIT
    from test_flow import _execute

    class StrictSast(FakeSast):
        def start(self, run_id, repo, commit):
            assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_id)
            return super().start(run_id, repo, commit)
        def scan(self, run_id):
            assert run_id in self.started
            return super().scan(run_id)
        def sarif(self, run_id):
            assert run_id in self.started
            return super().sarif(run_id)

    sast, dast = StrictSast(), FakeDast()
    run = Run(Options(REPO, COMMIT), run_id="a" * 64, sast=sast, dast=dast,
              build_jenkins=build_jenkins(), deploy_jenkins=deploy_jenkins(),
              staging=FakeStaging(), image_repo=IMAGE_REPO, store_dir=tmp_path, sleep=lambda _:None)
    assert _execute(run)["outcome"] == "verified"
    assert dast.started == sast.started


def test_onprem_retains_executor_returned_uuid():
    from uuid import UUID
    scanner_uuid = "3dfd5bf9-e138-4190-b147-955805e03b0b"
    board = SimpleNamespace(update=lambda *a, **kw:None, add_detail=lambda *a, **kw:None)
    def start(key, repo, commit):
        assert len(key) <= 64
        return {"run_id":scanner_uuid}
    steps = Steps(SimpleNamespace(run_id="a" * 64, board=board, sast=SimpleNamespace(start=start),
        options=SimpleNamespace(repo="repo", commit="b" * 40, execution_cloud="onprem")))
    result=steps.sast_start({})
    assert result["sast_run_id"] == str(UUID(scanner_uuid))
