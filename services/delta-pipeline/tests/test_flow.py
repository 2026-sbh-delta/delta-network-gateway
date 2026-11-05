from __future__ import annotations

import threading
import time

import pytest
from fakes import COMMIT, DIGEST, IMAGE_REPO, REPO, FakeDast, FakeSast, FakeStaging, build_jenkins, deploy_jenkins

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.model import Options
from delta_pipeline.run import Run
from delta_pipeline.steps import READ_ATTEMPTS, Stopped, _rows_remaining, assert_gates_passed


def _run(tmp_path, *, sast=None, dast=None, build=None, deploy=None, staging=None, repo=REPO, commit=COMMIT):
    return Run(Options(repo, commit), sast=sast or FakeSast(), dast=dast or FakeDast(), build_jenkins=build or build_jenkins(),
               deploy_jenkins=deploy or deploy_jenkins(), staging=staging or FakeStaging(), image_repo=IMAGE_REPO,
               store_dir=tmp_path / "store", sleep=lambda _: None)


def _execute(run, answers=()):
    """실행하면서 사람 확인이 뜨면 정해 둔 답을 차례로 준다."""
    thread = threading.Thread(target=run.execute, daemon=True)
    thread.start()
    pending = list(answers)
    deadline = time.time() + 10
    while thread.is_alive() and time.time() < deadline:
        if pending and any(n["decision"] for n in run.snapshot()["nodes"]) and run.answer(pending[0]):
            pending.pop(0)
            time.sleep(0.05)
        time.sleep(0.01)
    thread.join(timeout=2)
    assert not thread.is_alive()
    return run.snapshot()


def _states(snapshot):
    return {n["id"]: n["state"] for n in snapshot["nodes"]}


def test_clean_commit_runs_build_sast_stage_dast_and_verifies(tmp_path):
    build, deploy, dast = build_jenkins(), deploy_jenkins(), FakeDast()

    snapshot = _execute(_run(tmp_path, build=build, deploy=deploy, dast=dast))

    assert snapshot["outcome"] == "verified"
    assert set(_states(snapshot).values()) == {"pass"}
    assert build.params == [{"REPO": REPO, "COMMIT": COMMIT, "IMAGE_REPO": IMAGE_REPO}]
    # 스테이징에는 빌드가 만든 digest 고정 이미지를 그대로 올린다
    assert deploy.params[0]["IMAGE"] == f"{IMAGE_REPO}@{DIGEST}"
    stage = next(n for n in snapshot["nodes"] if n["id"] == "stage")
    assert stage["detail"]["rows_remaining"] == 0
    assert dast.started == [f"{snapshot['run_id']}-sast"]


def test_reproduced_finding_asks_a_person_and_stop_never_verifies(tmp_path):
    snapshot = _execute(_run(tmp_path, dast=FakeDast(["REPRODUCED", "NOT_REPRODUCED"])), answers=["stop"])

    assert snapshot["outcome"] == "stopped"
    assert _states(snapshot)["dast"] == "fail"


def test_inconclusive_is_not_auto_pass_but_person_can_approve(tmp_path):
    snapshot = _execute(_run(tmp_path, dast=FakeDast(["INCONCLUSIVE"])), answers=["approve"])

    # 사람이 넘겨도 보안 검증 통과가 아니다: 끝까지 연결은 됐지만 '미해결'로 끝난다
    assert snapshot["outcome"] == "unresolved"
    assert "사람 승인" in next(n for n in snapshot["nodes"] if n["id"] == "dast")["summary"]
    result = next(n for n in snapshot["nodes"] if n["id"] == "result")
    assert result["state"] == "warn" and "보안 미해결(DAST" in result["summary"]


def test_reproduced_finding_approved_by_a_person_is_unresolved_not_verified(tmp_path):
    snapshot = _execute(_run(tmp_path, dast=FakeDast(["REPRODUCED", "BLOCKED"])), answers=["approve"])

    assert snapshot["outcome"] == "unresolved"
    assert _states(snapshot)["result"] == "warn"


def test_no_dast_targets_skips_dast_without_failing(tmp_path):
    dast = FakeDast()

    snapshot = _execute(_run(tmp_path, sast=FakeSast(targets=0), dast=dast))

    assert snapshot["outcome"] == "verified"
    assert _states(snapshot)["dast"] == "skipped" and dast.started == []


def test_secret_needs_a_person_before_staging(tmp_path):
    deploy = deploy_jenkins()

    snapshot = _execute(_run(tmp_path, sast=FakeSast(secrets=1), deploy=deploy), answers=["stop"])

    assert snapshot["outcome"] == "stopped" and deploy.keys == []


@pytest.mark.parametrize("kwargs, node", [
    ({"build": build_jenkins(final="FAILURE")}, "build"),
    ({"sast": FakeSast(state="failed")}, "sast"),
    ({"sast": FakeSast(commit="0" * 40)}, "sast"),
    ({"deploy": deploy_jenkins(final="FAILURE")}, "stage"),
    ({"deploy": deploy_jenkins(digest="sha256:" + "b" * 64)}, "stage"),
    ({"deploy": deploy_jenkins(rows="3")}, "stage"),
    ({"deploy": deploy_jenkins(rows=None)}, "stage"),
    ({"dast": FakeDast(status="error")}, "dast"),
    ({"dast": FakeDast(status="interrupted")}, "dast"),
])
def test_any_failed_or_unproven_step_stops_the_run(tmp_path, kwargs, node):
    snapshot = _execute(_run(tmp_path, **kwargs))

    assert snapshot["outcome"] == "stopped"
    assert _states(snapshot)[node] == "fail"


def test_build_failure_never_stages(tmp_path):
    deploy = deploy_jenkins()

    _execute(_run(tmp_path, build=build_jenkins(final="FAILURE"), deploy=deploy))

    assert deploy.keys == []


def test_untrusted_repo_or_branch_name_is_rejected_before_any_call(tmp_path):
    build, sast = build_jenkins(), FakeSast()
    for repo, commit in (("https://github.com/evil/app", COMMIT), (REPO, "main")):
        snapshot = _execute(_run(tmp_path / repo[-3:] / commit[:4], build=build, sast=sast, repo=repo, commit=commit))
        assert snapshot["outcome"] == "stopped"
    assert build.keys == [] and sast.started == []


def test_service_down_stops_and_is_not_a_pass(tmp_path):
    snapshot = _execute(_run(tmp_path, sast=FakeSast(fail=True)))

    assert snapshot["outcome"] == "stopped"
    assert "통과로 보지 않는다" in next(n for n in snapshot["nodes"] if n["id"] == "result")["summary"]


def test_gates_are_rechecked_before_staging():
    with pytest.raises(Stopped):
        assert_gates_passed({}, ("sast",))
    assert_gates_passed({"sast": "approved"}, ("sast",))


def test_reset_rows_read_from_the_staging_step():
    assert _rows_remaining({"name": "data_reset", "detail": "job: 남은 행 0"}) == 0
    assert _rows_remaining({"name": "data_reset", "detail": "job"}) is None
    assert _rows_remaining(None) is None


def test_reads_are_retried_before_giving_up(tmp_path):
    class Flaky(FakeSast):
        calls = 0

        def scan(self, run_id):
            Flaky.calls += 1
            if Flaky.calls < READ_ATTEMPTS:
                raise ServiceUnavailable("잠깐 끊김")
            return super().scan(run_id)

    assert _execute(_run(tmp_path, sast=Flaky()))["outcome"] == "verified"


def test_stage_holds_the_lease_and_releases_it_when_done_or_stopped(tmp_path):
    deploy, staging = deploy_jenkins(), FakeStaging()
    snapshot = _execute(_run(tmp_path / "ok", deploy=deploy, staging=staging))
    assert deploy.params[0]["HOLDER"] == snapshot["run_id"]
    assert staging.released == [("gcp", snapshot["run_id"])]

    staging = FakeStaging()
    stopped = _execute(_run(tmp_path / "stop", dast=FakeDast(["REPRODUCED"]), staging=staging), answers=["stop"])
    assert stopped["outcome"] == "stopped" and staging.released == [("gcp", stopped["run_id"])]

    # 빌드에서 멈추면 스테이징을 잡지 않았으니 풀 것도 없다
    staging = FakeStaging()
    _execute(_run(tmp_path / "build", build=build_jenkins(final="FAILURE"), staging=staging))
    assert staging.released == []


def test_release_failure_does_not_change_a_verified_result(tmp_path):
    assert _execute(_run(tmp_path, staging=FakeStaging(fail=True)))["outcome"] == "verified"


def test_missing_dast_result_is_not_a_pass(tmp_path):
    snapshot = _execute(_run(tmp_path, dast=FakeDast(["NOT_REPRODUCED"], expected=2)), answers=["stop"])
    assert snapshot["outcome"] == "stopped"
    assert "누락 1" in next(n for n in snapshot["nodes"] if n["id"] == "dast")["summary"]


def test_dast_binds_the_build_digest_staging_url_and_sarif_hash(tmp_path):
    dast = FakeDast()
    snapshot = _execute(_run(tmp_path, dast=dast))
    (binding,) = dast.bindings.values()
    assert binding["image_digest"] == DIGEST and binding["commit_sha"] == COMMIT
    assert binding["target_url"] == "http://136.82.112.37" and binding["runtime_revision"] == "r1"
    assert binding["build_id"] == f"{snapshot['run_id']}-build"


@pytest.mark.parametrize("sast, reason", [
    (FakeSast(no_summary=True), "결과 요약 없음"),
    (FakeSast(scope_commit="0" * 40), "실제 점검 커밋 다름"),
    (FakeSast(files=0, targets=0), "점검한 소스 파일 0개"),
])
def test_unproven_sast_scope_is_not_a_clean_zero(tmp_path, sast, reason):
    deploy = deploy_jenkins()
    snapshot = _execute(_run(tmp_path, sast=sast, deploy=deploy), answers=["stop"])
    assert snapshot["outcome"] == "stopped" and deploy.keys == []
    assert reason in next(n for n in snapshot["nodes"] if n["id"] == "sast")["summary"]


def test_truncated_dast_candidates_are_not_a_pass(tmp_path):
    snapshot = _execute(_run(tmp_path, dast=FakeDast(["NOT_REPRODUCED"] * 10, eligible=11)), answers=["stop"])
    assert snapshot["outcome"] == "stopped"
    assert "미점검 1" in next(n for n in snapshot["nodes"] if n["id"] == "dast")["summary"]
