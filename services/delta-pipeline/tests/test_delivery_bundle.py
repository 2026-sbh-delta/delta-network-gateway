"""B02·D01·D02: 기존 공개 Steps/그래프에서 FE만 통과시키는 회귀를 막는다."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from delivery_fakes import CloudDast, DeliveryOptions, Harness, RoundJenkins, bundle, stage_result
from fakes import COMMIT, FIX_COMMIT, REPO

from delta_pipeline.model import Options


def _replace(value, field, key, replacement):
    if replacement is None:
        value[field].pop(key)
    else:
        value[field][key] = replacement


def test_default_staging_cloud_is_gcp():
    assert getattr(Options(REPO, COMMIT), "staging_cloud", None) == "gcp"


def test_graph_carries_the_two_build_images_into_stage_and_dast(tmp_path):
    h = Harness(tmp_path)
    h.drive()
    assert h.snapshot()["outcome"] == "verified"
    expected = bundle()
    params = h.deploy.params[0]
    assert params.get("IMAGES_JSON") is not None, "stage job에 FE·BE JSON이 전달되지 않았다"
    assert json.loads(params["IMAGES_JSON"]) == expected["images"]
    assert params.get("BUNDLE_SHA256") == expected["bundle_sha256"]
    assert params["IMAGE"] == expected["image_ref"]  # 기존 frontend 별칭
    (binding,) = h.dast.bindings.values()
    assert binding.get("images") == expected["images"], "DAST가 FE 단일 이미지에만 결합됐다"
    assert binding["commit_sha"] == COMMIT
    assert binding["image"] == expected["image_ref"] and binding["image_digest"] == expected["image_digest"]


@pytest.mark.parametrize("damage", [
    "missing_backend_ref", "missing_backend_digest", "backend_tag", "backend_ref_digest_mismatch",
    "bad_bundle", "missing_bundle", "other_commit", "wrong_platform", "frontend_alias_mismatch",
], ids=str)
def test_invalid_v2_build_stops_before_any_stage_or_dast_call(tmp_path, damage):
    result = bundle()
    if damage == "missing_backend_ref":
        result["images"].pop("backend")
    elif damage == "missing_backend_digest":
        result["image_digests"].pop("backend")
    elif damage == "backend_tag":
        result["images"]["backend"] = "registry.example/backend:latest"
    elif damage == "backend_ref_digest_mismatch":
        result["images"]["backend"] = "registry.example/backend@sha256:" + "e" * 64
    elif damage == "bad_bundle":
        result["bundle_sha256"] = "0" * 64
    elif damage == "missing_bundle":
        result.pop("bundle_sha256")
    elif damage == "other_commit":
        result["commit"] = FIX_COMMIT
    elif damage == "wrong_platform":
        result["platform"] = "linux/arm64"
    elif damage == "frontend_alias_mismatch":
        result["image_ref"] = "registry.example/other@sha256:" + "f" * 64
    h = Harness(tmp_path, build=RoundJenkins(lambda _: result))
    h.drive()
    assert h.snapshot()["outcome"] == "stopped", f"잘못된 schema2 build를 통과시켰다: {damage}"
    assert h.deploy.params == [] and h.dast.started == []
    assert next(n for n in h.snapshot()["nodes"] if n["id"] == "build")["state"] == "fail"


@pytest.mark.parametrize("damage", [
    "missing_backend_ref", "missing_backend_digest", "backend_ref_mismatch", "backend_digest_mismatch",
    "missing_bundle", "other_bundle", "other_commit", "missing_structured_reset", "reset_nonzero",
    "missing_db_version", "db16", "db_version_string",
], ids=str)
def test_frontend_ready_does_not_hide_unproven_backend_bundle_reset_or_db(tmp_path, damage):
    result = stage_result()
    if damage == "missing_backend_ref":
        _replace(result, "images", "backend", None)
    elif damage == "missing_backend_digest":
        _replace(result, "image_digests", "backend", None)
    elif damage == "backend_ref_mismatch":
        _replace(result, "images", "backend", "registry.example/backend@sha256:" + "e" * 64)
    elif damage == "backend_digest_mismatch":
        _replace(result, "image_digests", "backend", "sha256:" + "e" * 64)
    elif damage == "missing_bundle":
        result.pop("bundle_sha256")
    elif damage == "other_bundle":
        result["bundle_sha256"] = "0" * 64
    elif damage == "other_commit":
        result["commit"] = FIX_COMMIT
    elif damage == "missing_structured_reset":
        result.pop("data_reset_remaining")
    elif damage == "reset_nonzero":
        result["data_reset_remaining"] = 1  # 기존 문자열은 여전히 '남은 행 0'
    elif damage == "missing_db_version":
        result.pop("database_version_num")
    elif damage == "db16":
        result["database_version_num"] = 160011
    elif damage == "db_version_string":
        result["database_version_num"] = "180003"
    h = Harness(tmp_path, deploy=RoundJenkins(lambda _: result))
    h.drive()
    assert h.snapshot()["outcome"] == "stopped", f"검증되지 않은 stage 결과를 통과시켰다: {damage}"
    assert h.dast.started == []
    assert next(n for n in h.snapshot()["nodes"] if n["id"] == "stage")["state"] == "fail"


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_selected_staging_cloud_binds_stage_dast_and_lease_cleanup(tmp_path, cloud):
    h = Harness(tmp_path, options=DeliveryOptions(REPO, COMMIT, staging_cloud=cloud))
    h.drive()
    assert h.snapshot()["outcome"] == "verified"
    assert h.deploy.params[0]["CLOUD"] == cloud
    assert h.staging.released == [(cloud, "delivery-test")]
    (binding,) = h.dast.bindings.values()
    assert binding["execution_cloud"] == cloud


def test_invalid_staging_cloud_stops_before_build_stage_or_dast(tmp_path):
    h = Harness(tmp_path, options=DeliveryOptions(REPO, COMMIT, staging_cloud="azure"))
    h.drive()
    assert h.snapshot()["outcome"] == "stopped"
    assert h.build.params == [] and h.deploy.params == [] and h.dast.started == []


def test_second_round_keeps_new_commit_bundle_holder_and_reverification_link(tmp_path):
    h = Harness(tmp_path, dast=CloudDast(["REPRODUCED"]))
    h.drive()
    assert h.pending()[0]["node_id"] == "dast"
    h.drive("fix")
    assert h.snapshot()["outcome"] == "verified"
    assert [p["COMMIT"] for p in h.build.params] == [COMMIT, FIX_COMMIT]
    assert [p["COMMIT"] for p in h.deploy.params] == [COMMIT, FIX_COMMIT]
    assert [p["HOLDER"] for p in h.deploy.params] == ["delivery-test", "delivery-test"]
    assert h.build.keys == ["delivery-test-build", "delivery-test-r2-build"]
    assert h.deploy.keys == ["delivery-test-stage", "delivery-test-r2-stage"]
    first_id, second_id = h.dast.bindings
    assert h.dast.reverify == {second_id: first_id}
    for commit, params, binding in zip((COMMIT, FIX_COMMIT), h.deploy.params, h.dast.bindings.values(), strict=True):
        expected = bundle(commit)
        assert params.get("IMAGES_JSON") is not None, "수정 회차의 이미지 묶음이 stage 입력에 없다"
        assert json.loads(params["IMAGES_JSON"]) == expected["images"]
        assert params.get("BUNDLE_SHA256") == expected["bundle_sha256"]
        assert binding.get("images") == expected["images"]
        assert binding["commit_sha"] == commit


@pytest.mark.parametrize("stale", ["build", "stage"])
def test_second_round_rejects_first_round_bundle_before_second_dast(tmp_path, stale):
    old = bundle()
    def build_result(commit):
        # commit 별칭만 새것이고 묶음 해시는 1차 commit에 결합된 바꿔치기 결과.
        return {**deepcopy(old), "commit": commit} if commit == FIX_COMMIT else bundle(commit)
    def deploy_result(commit):
        return {**stage_result(old), "commit": commit} if commit == FIX_COMMIT else stage_result(bundle(commit))
    h = Harness(tmp_path, dast=CloudDast(["REPRODUCED"]),
                build=RoundJenkins(build_result if stale == "build" else bundle),
                deploy=RoundJenkins(deploy_result if stale == "stage" else lambda c: stage_result(bundle(c))))
    h.drive()
    h.drive("fix")
    assert h.snapshot()["outcome"] == "stopped"
    assert len(h.dast.bindings) == 1, "1차 묶음으로 수정 후 재검증을 실행했다"
