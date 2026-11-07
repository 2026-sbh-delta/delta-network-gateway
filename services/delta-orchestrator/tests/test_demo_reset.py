"""시연 초기화: 수정 전 main 빌드를 고르고, 실행 중에는 시작하지 않는다."""

import json
import os
import time

import pytest

from delta_orchestrator import pipelines as mod
from delta_pipeline.clients import evidence_hash

REPO = "https://github.com/2026-sbh-delta/delta-sample-app"


def bundle(commit: str, tag: str) -> dict:
    digests = {"frontend": "sha256:" + tag * 64, "backend": "sha256:" + "e" * 64}
    images = {k: f"asia-northeast3-docker.pkg.dev/p/r/{k}@{v}" for k, v in digests.items()}
    core = {"schema": 2, "commit": commit, "platform": "linux/amd64", "image_digests": digests}
    return {**core, "images": images, "bundle_sha256": evidence_hash(core), "image_ref": images["frontend"],
            "image_digest": digests["frontend"], "registry_images": {"gcp": images}}


def board(path, run_id: str, commit: str, built: str, tag: str, at: float, cloud: str = "gcp",
          branch: str = "main") -> None:
    data = {"run_id": run_id, "options": {"repo": REPO, "commit": commit, "execution_cloud": cloud, "branch": branch},
            "nodes": [{"id": "build", "state": "pass", "detail": {"bundle": bundle(built, tag)}}]}
    file = path / mod.BOARD_DIR / f"{run_id}.json"
    file.write_text(json.dumps(data))
    os.utime(file, (at, at))


class Idle:
    def __init__(self, store_dir, busy=False):
        self.store_dir, self.busy = store_dir, busy

    def running(self, cloud=None):
        return self.busy


def test_picks_the_unfixed_build_of_the_current_main_commit(tmp_path):
    (tmp_path / mod.BOARD_DIR).mkdir()
    now = time.time()
    board(tmp_path, "main", "a" * 40, "a" * 40, "1", now - 300)
    board(tmp_path, "fixed", "a" * 40, "c" * 40, "2", now - 100)  # 수정 후 재빌드: 커밋이 바뀜
    board(tmp_path, "older", "b" * 40, "b" * 40, "4", now - 80)  # 예전 커밋(취약 게시판 이전)
    board(tmp_path, "onprem", "a" * 40, "a" * 40, "3", now - 50, cloud="onprem")
    board(tmp_path, "branch", "e" * 40, "e" * 40, "5", now - 10, branch="feat/x")  # 작업 브랜치 빌드
    reset = mod.DemoReset(Idle(tmp_path), mod.DomainRevert())
    heads = {"main": "a" * 40, "feat/x": "e" * 40}
    found, repo = reset.vulnerable_bundle(head=lambda repo, branch: heads[branch])
    assert found["commit"] == "a" * 40 and found["image_digests"]["frontend"] == "sha256:" + "1" * 64
    assert repo == REPO
    with pytest.raises(RuntimeError, match="main 현재 커밋"):
        reset.vulnerable_bundle(head=lambda repo, branch: "f" * 40)


def test_does_not_start_while_a_pipeline_runs(tmp_path):
    reset = mod.DemoReset(Idle(tmp_path, busy=True), mod.DomainRevert())
    with pytest.raises(RuntimeError, match="파이프라인 실행 중"):
        reset.start()
    assert reset.view()["state"] == "idle"


def test_fix_cleanup_repo_does_not_need_a_build_of_the_current_main(tmp_path):
    (tmp_path / mod.BOARD_DIR).mkdir()
    board(tmp_path, "old", "a" * 40, "a" * 40, "1", time.time())
    reset = mod.DemoReset(Idle(tmp_path), mod.DomainRevert())
    with pytest.raises(RuntimeError):
        reset.vulnerable_bundle(head=lambda repo, branch: "f" * 40)  # main 이 바뀌어 빌드 기록 없음
    assert reset.latest_repo() == REPO


class BuildJenkins:
    def __init__(self, result):
        self.result, self.params = result, None

    def trigger(self, params, key):
        self.params = params
        return 7

    def status(self, build):
        return "SUCCESS"

    def file(self, build, name):
        return self.result


def test_without_an_unfixed_record_reset_builds_main_itself(tmp_path, monkeypatch):
    (tmp_path / mod.BOARD_DIR).mkdir()
    board(tmp_path, "fixed", "a" * 40, "c" * 40, "2", time.time())  # 수정 재빌드만 남은 실행
    reset = mod.DemoReset(Idle(tmp_path), mod.DomainRevert())
    monkeypatch.setattr(mod.DemoReset, "branch_head", staticmethod(lambda repo, branch: "a" * 40))
    jenkins = BuildJenkins(bundle("a" * 40, "9"))
    found, repo = reset.build_main(jenkins)
    assert found["commit"] == "a" * 40 and repo == REPO
    assert jenkins.params["REPO"] == REPO and jenkins.params["COMMIT"] == "a" * 40
