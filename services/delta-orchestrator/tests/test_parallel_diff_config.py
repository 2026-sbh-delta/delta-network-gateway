import json

import pytest

from delta_orchestrator import pipelines
from delta_pipeline.clients import ServiceUnavailable


DEMO_REPO = "https://github.com/2026-sbh-delta/delta-sample-app"
OTHER_REPO = "https://github.com/2026-sbh-delta/another-app"


def test_start_pins_operator_diff_input_without_changing_other_web_runs(tmp_path, monkeypatch):
    pool = pipelines.Pipelines(tmp_path)
    launched = []
    monkeypatch.setattr(pool, "_launch", lambda options, run_id: launched.append(options))
    request = tmp_path / "diff.json"
    request.write_text(json.dumps({"service_id": "delta-sample-app", "source": {
        "state": {"bucket": "demo-state", "key": "workload/terraform.tfstate"}}}))
    monkeypatch.setenv("DELTA_DIFF_REQUIRED", "1")
    monkeypatch.setenv("DELTA_DIFF_REQUEST_FILE", str(request))
    pool.start(DEMO_REPO, "a" * 40, cutover=True, release_target="demo-gcp")
    pool.start(OTHER_REPO, "a" * 40)
    assert launched[0].diff_request_hash == pipelines.diff_input().fingerprint
    assert launched[1].diff_request_hash is None
    with pytest.raises(RuntimeError, match="고정된 Diff 입력"):
        pool.start(OTHER_REPO, "a" * 40, cutover=True, release_target="demo-gcp")
    request.unlink()
    with pytest.raises(ServiceUnavailable, match="운영자 Diff 요청 파일"):
        pool.start(DEMO_REPO, "a" * 40)
    assert len(launched) == 2
