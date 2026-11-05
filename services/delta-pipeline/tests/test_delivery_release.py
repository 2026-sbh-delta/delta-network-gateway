"""R07: 실제 공개 그래프의 사람 승인·release verify·cutover 순서."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from delivery_fakes import TARGET, CloudDast, DeliveryOptions, Harness, RoundJenkins, bundle, release_result
from fakes import COMMIT, FIX_COMMIT, REPO, FakeSast
from test_cutover import CFG, Clock, FakeCloudflare, FakeInternet

from delta_pipeline.steps import Stopped


def _options(*, cutover=False):
    return DeliveryOptions(REPO, COMMIT, cutover=cutover, release_target=TARGET)


def _await_release(h):
    pending = h.pending()
    assert len(pending) == 1, "보안 검증 뒤 운영 배포 승인 interrupt가 없다"
    assert pending[0]["node_id"] == "release", "운영 검증 전에 다른 승인 단계로 넘어갔다"
    assert {value for value, _ in pending[0]["options"]} >= {"approve", "stop"}
    return pending[0]


def _with_cutover(tmp_path, **kwargs):
    cf, clock = FakeCloudflare(), Clock()
    net = FakeInternet(cf, lag=0)
    h = Harness(tmp_path, options=_options(cutover=True), cloudflare=cf, cutover=CFG,
                probe=net.probe, resolve=net.resolve, now=clock.now, sleep=clock.sleep, **kwargs)
    return h, cf, net


def test_no_release_target_keeps_the_existing_staging_only_flow(tmp_path):
    h = Harness(tmp_path)
    h.drive()
    assert h.snapshot()["outcome"] == "verified" and h.pending() == []
    assert h.release.params == []
    assert not set(h.visited) & {"ask_release", "release_start", "release_wait", "release_verify"}


def test_release_target_requires_human_interrupt_before_any_release_call(tmp_path):
    h = Harness(tmp_path, options=_options())
    h.drive()
    request = _await_release(h)
    assert TARGET in request["prompt"]
    assert h.release.params == [], "승인 전에 운영 Jenkins를 호출했다"
    assert h.snapshot()["outcome"] is None


def test_refusing_release_never_triggers_release_or_cutover(tmp_path):
    h, cf, _ = _with_cutover(tmp_path)
    h.drive()
    _await_release(h)
    h.drive("stop")
    assert h.snapshot()["outcome"] == "stopped"
    assert h.release.params == [] and cf.writes == []


def test_approval_runs_start_wait_verify_and_only_then_asks_for_cutover(tmp_path):
    h, cf, net = _with_cutover(tmp_path)
    h.drive()
    _await_release(h)
    assert h.release.params == [] and cf.writes == [] and net.calls == []
    h.drive("approve")
    assert len(h.release.params) == 1
    params = h.release.params[0]
    assert params.get("TARGET_ID") == TARGET
    assert params.get("COMMIT") == COMMIT
    assert params.get("BUNDLE_SHA256") == bundle()["bundle_sha256"]
    assert params.get("IMAGES_JSON") is not None
    assert json.loads(params["IMAGES_JSON"]) == bundle()["images"]
    assert h.visited.index("release_start") < h.visited.index("release_wait") < h.visited.index("release_verify")
    assert h.graph.get_state(h.config).values.get("gates", {}).get("release") == "pass"
    assert len(h.pending()) == 1 and h.pending()[0]["node_id"] == "cutover"
    assert cf.writes == [] and net.calls == [], "운영 verify 뒤에도 도메인 변경에는 별도 승인이 필요하다"
    h.drive("approve")
    assert h.snapshot()["outcome"] == "verified"
    assert cf.writes == [CFG.blue]


@pytest.mark.parametrize("damage", [
    "jenkins_failure", "not_verified", "missing_backend", "backend_mismatch", "bundle_mismatch", "commit_mismatch",
], ids=str)
def test_unverified_release_result_never_allows_cutover(tmp_path, damage):
    result = deepcopy(release_result())
    if damage == "not_verified":
        result["state"] = "ready"  # ready·HTTP200을 운영 verified로 오인하지 않는다
    elif damage == "missing_backend":
        result["images"].pop("backend")
        result["image_digests"].pop("backend")
    elif damage == "backend_mismatch":
        result["images"]["backend"] = "registry.example/backend@sha256:" + "e" * 64
        result["image_digests"]["backend"] = "sha256:" + "e" * 64
    elif damage == "bundle_mismatch":
        result["bundle_sha256"] = "0" * 64
    elif damage == "commit_mismatch":
        result["commit"] = FIX_COMMIT
    release = RoundJenkins(lambda _: result, final="FAILURE" if damage == "jenkins_failure" else "SUCCESS")
    h, cf, net = _with_cutover(tmp_path, release=release)
    h.drive()
    _await_release(h)
    h.drive("approve")
    assert h.snapshot()["outcome"] == "stopped"
    assert len(release.params) == 1
    assert cf.writes == [] and net.calls == [] and h.pending() == []


@pytest.mark.parametrize("unresolved", ["sast", "dast"])
def test_human_override_of_security_findings_cannot_authorize_production(tmp_path, unresolved):
    h = Harness(tmp_path, options=_options(),
                sast=FakeSast(secrets=1) if unresolved == "sast" else None,
                dast=CloudDast(["INCONCLUSIVE"]) if unresolved == "dast" else None)
    h.drive()
    assert h.pending()[0]["node_id"] == unresolved
    h.drive("approve")
    assert h.release.params == []
    assert not any(p["node_id"] == "release" for p in h.pending())
    assert h.snapshot()["outcome"] in ("stopped", "unresolved")
    assert not set(h.visited) & {"release_start", "release_wait", "release_verify"}


def test_fix_loop_releases_only_the_verified_second_round_bundle(tmp_path):
    h = Harness(tmp_path, options=_options(), dast=CloudDast(["REPRODUCED"]))
    h.drive()
    assert h.pending()[0]["node_id"] == "dast" and h.release.params == []
    h.drive("fix")
    _await_release(h)
    assert h.release.params == []
    h.drive("approve")
    assert h.snapshot()["outcome"] == "verified"
    assert len(h.release.params) == 1
    params = h.release.params[0]
    assert params.get("COMMIT") == FIX_COMMIT
    assert params.get("BUNDLE_SHA256") == bundle(FIX_COMMIT)["bundle_sha256"]


def test_cutover_public_step_rechecks_production_verify_even_if_graph_is_bypassed(tmp_path):
    h, cf, net = _with_cutover(tmp_path)
    # 기존 보안·도메인 승인은 모두 있어도 운영 verify 기록이 없다.
    state = {"gates": {"sast": "pass", "dast": "pass", "cutover_approval": "approved"},
             "image_digest": bundle()["image_digest"]}
    with pytest.raises(Stopped):
        h.steps.cutover_check(state)
    assert cf.writes == [] and net.calls == []


def test_done_public_step_cannot_report_verified_before_production_verify(tmp_path):
    h = Harness(tmp_path, options=_options())
    with pytest.raises(Stopped):
        h.steps.done({"gates": {"sast": "pass", "dast": "pass"}, "image_digest": bundle()["image_digest"]})
    assert h.snapshot()["outcome"] is None
