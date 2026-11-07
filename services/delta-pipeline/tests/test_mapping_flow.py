from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from delta_ai.api import create_app
from delta_ai.store import RuleStore
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.mapping import MappingFlow, normalize_mapping_request
from delta_pipeline.model import Board, Options
from delta_pipeline.run import Run
from fakes import COMMIT, IMAGE_REPO, REPO, FakeDast, FakeSast, FakeStaging, build_jenkins, deploy_jenkins


def request(instance="t4g.nano"):
    return {"assessment_id": "uploaded-id", "kind": "migration_mapping", "service": {"service_id": "web"},
            "evidence": {"mapping": {"source_provider": "aws", "target_provider": "gcp",
                "source_region": "ap-northeast-2", "region": "asia-northeast3",
                "servers": [{"name": "web", "instance_type": instance, "os": "ubuntu",
                    "multi_arch_image": True, "instance_profile": False}]}}}


def run_with(tmp_path, body, assessor, **kwargs):
    return Run(Options(REPO, COMMIT, mapping_request=body), sast=FakeSast(), dast=FakeDast(),
               build_jenkins=kwargs.pop("build", build_jenkins()), deploy_jenkins=deploy_jenkins(),
               staging=FakeStaging(), image_repo=IMAGE_REPO, store_dir=tmp_path,
               sleep=lambda _: None, mapping_assess=assessor, **kwargs)


def node(run):
    return next(n for n in run.snapshot()["nodes"] if n["id"] == "map")


@pytest.fixture
def ai(tmp_path):
    with TestClient(create_app(llm=None, store=RuleStore(tmp_path / "ai.sqlite"), token="test-token")) as client:
        calls = []
        def assess(body):
            calls.append(deepcopy(body))
            response = client.post("/v1/migration-mapping", json=body, headers={"Authorization": "Bearer test-token"})
            assert response.status_code == 200, response.text
            return response.json()
        yield assess, calls


def test_real_mapping_api_result_is_saved_before_build_and_survives_reload(tmp_path, ai):
    assess, calls = ai
    run = run_with(tmp_path / "run", request(), assess)
    run.execute()
    assert run.snapshot()["outcome"] == "verified"
    saved = node(run)
    cost = saved["detail"]["mapping"]["cost"]
    assert saved["state"] == "pass"
    assert (cost["source_total"], cost["target_total"], cost["difference_total"]) == (3.80, 7.84, 4.04)
    assert len(calls) == 1
    assert calls[0]["assessment_id"] == f"map-{run.run_id}"
    assert calls[0]["service"]["commit"] == COMMIT
    build = next(n for n in run.snapshot()["nodes"] if n["id"] == "build")
    assert saved["ended_at"] <= build["started_at"]
    reloaded = run_with(tmp_path / "run", request(), assess, run_id=run.run_id)
    assert node(reloaded)["detail"] == saved["detail"]
    MappingFlow(reloaded._deps).assess({})
    assert len(calls) == 1  # crash after Board write but before graph checkpoint
    assert "test-token" not in (tmp_path / "run" / "runs" / f"{run.run_id}.json").read_text()


@pytest.mark.parametrize("failure", [ServiceUnavailable("private-upstream-error"), {}, {"assessment_id": "other", "kind": "migration_mapping", "verdict": "pass"}])
def test_mapping_failure_stops_before_build_and_never_persists_upstream_errors(tmp_path, failure):
    def assess(_):
        if isinstance(failure, Exception):
            raise failure
        return failure
    build = build_jenkins()
    run = run_with(tmp_path, request(), assess, build=build)
    run.execute()
    assert run.snapshot()["outcome"] == "stopped"
    assert node(run)["state"] == "fail"
    assert "mapping" not in node(run)["detail"]
    assert build.params == []
    assert "private-upstream-error" not in str(run.snapshot())


def test_mapping_only_persists_prices_without_triggering_build(tmp_path, ai):
    assess, calls = ai
    build = build_jenkins()
    options = Options(REPO, COMMIT, mapping_request=request(), mapping_only=True)
    run = Run(options, sast=FakeSast(), dast=FakeDast(), build_jenkins=build,
              deploy_jenkins=deploy_jenkins(), staging=FakeStaging(), image_repo=IMAGE_REPO,
              store_dir=tmp_path, mapping_assess=assess)
    run.execute()
    assert run.snapshot()["outcome"] == "verified"
    assert node(run)["detail"]["mapping"]["cost"]["difference_total"] == 4.04
    assert build.params == []
    assert len(calls) == 1
    with pytest.raises(ValueError):
        Options(REPO, COMMIT, mapping_request=request(), mapping_only=True, cutover=True)


def test_unknown_price_remains_null_and_mapping_verdict_is_preserved(tmp_path, ai):
    assess, _ = ai
    run = run_with(tmp_path, request("z1d.metal"), assess)
    run.execute()
    detail = node(run)["detail"]
    assert node(run)["state"] == "warn"
    assert detail["verdict"] == "needs_human"
    assert detail["mapping"]["cost"]["source_total"] is None
    assert detail["mapping"]["cost"]["difference_total"] is None


def test_reverse_mapping_uses_the_actual_api_prices(tmp_path, ai):
    assess, _ = ai
    body = request("e2-small")
    mapping = body["evidence"]["mapping"]
    mapping.update(source_provider="gcp", target_provider="aws", source_region="asia-northeast3", region="ap-northeast-2")
    mapping["servers"][0].pop("instance_profile")
    mapping["servers"][0]["service_account"] = False
    run = run_with(tmp_path, body, assess)
    run.execute()
    cost = node(run)["detail"]["mapping"]["cost"]
    assert cost["lines"][0]["target_type"] == "t3.small"
    assert cost["source_total"] == 15.69
    assert cost["target_total"] == 18.98


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(values={"secret": "raw-state"}),
    lambda b: b.update(kind="deployment_gate"),
    lambda b: b["evidence"].update(report={"secret": "secret"}),
    lambda b: b["service"].update(commit="b" * 40),
    lambda b: b["evidence"]["mapping"]["servers"][0].update(password="secret"),
])
def test_non_mapping_or_unbound_input_is_rejected(mutate):
    body = request()
    mutate(body)
    with pytest.raises(ValueError):
        normalize_mapping_request(body, repo=REPO, commit=COMMIT)


def test_old_run_without_mapping_does_not_gain_a_fake_price_node(tmp_path):
    options = Options(REPO, COMMIT)
    board = Board(tmp_path / "old.json", options, "old")
    assert all(n["id"] != "map" for n in board.snapshot()["nodes"])
