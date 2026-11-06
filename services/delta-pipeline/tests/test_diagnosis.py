"""멈춘 실행의 원인 진단(delta-ai deployment_gate): 판정은 바꾸지 않고 설명만 붙인다."""

from __future__ import annotations

from fakes import COMMIT, REPO

from delta_pipeline.diagnosis import diagnose, request_for
from delta_pipeline.model import Board, Options


def _stopped_board(tmp_path, *, node_summary="스테이징 배포 실패 (#21 FAILURE) 422", reason=None):
    board = Board(tmp_path / "board.json", Options(REPO, COMMIT), "run-1")
    board.update("stage", state="fail", summary=node_summary)
    board.add_detail("stage", build=21)
    board.finish("stopped", reason or node_summary)
    return board


def test_failed_run_sends_failed_stage_and_reason(tmp_path):
    board = _stopped_board(tmp_path)
    request = request_for("run-1", Options(REPO, COMMIT), board.snapshot())
    assert request["kind"] == "deployment_gate" and request["assessment_id"] == "run-1-halt"
    deployment = request["evidence"]["deployment"]
    assert deployment["stage"] == "stage" and deployment["status"] == "failed" and deployment["target"] == "gcp"
    assert "422" in deployment["log_tail"] and '"build": 21' in deployment["log_tail"]
    assert request["service"]["commit"] == COMMIT


def test_other_failed_stages_are_sent_as_evidence(tmp_path):
    board = Board(tmp_path / "board.json", Options(REPO, COMMIT), "run-1")
    board.update("build", state="fail", summary="빌드 실패 (#45 FAILURE)")
    board.update("sast", state="fail", summary="점검 실패: git fetch 실패: not our ref")
    board.finish("stopped", "빌드 실패 (#45 FAILURE)")
    log_tail = request_for("run-1", Options(REPO, COMMIT), board.snapshot())["evidence"]["deployment"]["log_tail"]
    assert "#45" in log_tail and "not our ref" in log_tail


def test_human_stop_and_success_are_not_diagnosed(tmp_path):
    human = _stopped_board(tmp_path, node_summary="DAST: 사람이 중단", reason="DAST에서 사람이 중단했다")
    assert request_for("run-1", Options(REPO, COMMIT), human.snapshot()) is None
    ok = Board(tmp_path / "ok.json", Options(REPO, COMMIT), "run-2")
    ok.finish("verified", "스테이징 검증 완료", passed=True)
    assert request_for("run-2", Options(REPO, COMMIT), ok.snapshot()) is None


def test_diagnosis_shows_why_only_and_never_changes_outcome(tmp_path):
    board = _stopped_board(tmp_path)

    def assess(_body):
        return {"verdict": "pass", "findings": [{"why": "스테이징 도구가 요청 필드를 거부했다.",
                                                  "citations": ["rules/deployment/DEP-STAGE-001.md"]}],
                "meta": {"llm_used": True}}

    diagnose(board, "run-1", Options(REPO, COMMIT), assess)
    snapshot = board.snapshot()
    result = next(n for n in snapshot["nodes"] if n["id"] == "result")
    assert snapshot["outcome"] == "stopped", "delta-ai 의 verdict 로 결과를 바꾸면 안 된다"
    assert result["detail"]["diagnosis"] == {"status": "ready", "why": "스테이징 도구가 요청 필드를 거부했다.",
                                             "citations": ["rules/deployment/DEP-STAGE-001.md"], "llm_used": True}


def test_diagnosis_failure_is_quiet(tmp_path):
    board = _stopped_board(tmp_path)

    def assess(_body):
        raise TimeoutError("delta-ai down")

    diagnose(board, "run-1", Options(REPO, COMMIT), assess)
    result = next(n for n in board.snapshot()["nodes"] if n["id"] == "result")
    assert result["detail"]["diagnosis"] == {"status": "unavailable"}
