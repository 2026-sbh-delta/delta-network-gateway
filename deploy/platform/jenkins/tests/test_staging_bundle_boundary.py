"""기존 main → urllib Request → result.json 경계에서 v2 동작을 검사한다."""

import io
import json
import urllib.request

import pytest

from .delivery_support import load_controller


def invoke(monkeypatch, tmp_path, bundle, *, images=None, reply=None):
    staging = load_controller("staging_deploy")
    monkeypatch.chdir(tmp_path)
    env = {"RUN_ID": "round-2", "IMAGE": bundle["image_ref"], "COMMIT": bundle["commit"],
           "CLOUD": "gcp", "HOLDER": "scan-owner", "DELTA_STAGING_WRITE_TOKEN": "fixture-only",
           "IMAGES_JSON": json.dumps(bundle["images"] if images is None else images),
           "BUNDLE_SHA256": bundle["bundle_sha256"]}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []
    response = reply or {
        "run_id": "round-2", "state": "ready", "url": "https://staging.invalid",
        "revision": "fixture-revision", "image_digest": bundle["image_digest"],
        "images": bundle["images"], "image_digests": bundle["image_digests"],
        "bundle_sha256": bundle["bundle_sha256"], "data_reset_remaining": 0,
        "steps": [{"name": "data_reset", "detail": "초기화: 남은 행 0"},
                  {"name": "image_digest", "detail": "two observed images"}],
    }

    def urlopen(request, **kwargs):
        assert isinstance(request, urllib.request.Request)
        calls.append((request.method, request.full_url, json.loads(request.data) if request.data else None))
        return io.BytesIO(json.dumps(response if request.method == "GET" else {"state": "deploying"}).encode())

    monkeypatch.setattr(staging.urllib.request, "urlopen", urlopen)
    error = None
    try:
        staging.main()
    except SystemExit as exc:
        error = exc.code
    result = json.loads((tmp_path / "result.json").read_text())
    return calls, result, error


def test_main_posts_both_images_and_bundle_not_only_frontend(monkeypatch, tmp_path, bundle):
    calls, _, error = invoke(monkeypatch, tmp_path, bundle)
    body = next(body for method, _, body in calls if method == "POST")
    assert body.get("images") == bundle["images"], "실제 HTTP body에서 backend 이미지가 누락됐다"
    assert body.get("bundle_sha256") == bundle["bundle_sha256"]
    assert body["lease_holder"] == "scan-owner"
    assert error is None


def test_result_preserves_observed_bundle_and_structured_reset(monkeypatch, tmp_path, bundle):
    _, result, error = invoke(monkeypatch, tmp_path, bundle)
    assert error is None
    for key in ("images", "image_digests", "bundle_sha256"):
        assert result.get(key) == bundle[key], f"result.json에서 관측 {key}가 손실됐다"
    assert result.get("data_reset_remaining") == 0
    assert [step["name"] for step in result["steps"]] == ["data_reset", "image_digest"]


@pytest.mark.parametrize("bad_images", [
    {"frontend": "registry.invalid/app/frontend@sha256:" + "a" * 64},
    {"frontend": "registry.invalid/app/frontend@sha256:" + "a" * 64,
     "backend": "registry.invalid/app/backend:latest"},
])
def test_invalid_v2_does_not_fall_back_to_legacy_image(monkeypatch, tmp_path, bundle, bad_images):
    calls, result, error = invoke(monkeypatch, tmp_path, bundle, images=bad_images)
    assert not calls, "잘못된 두 이미지 입력으로 HTTP 배포를 시작했다"
    assert error and result.get("error")


def test_legacy_single_image_still_works(monkeypatch, tmp_path, bundle):
    staging = load_controller("staging_deploy")
    monkeypatch.chdir(tmp_path)
    for key, value in {"RUN_ID": "old-client", "IMAGE": bundle["image_ref"], "COMMIT": bundle["commit"],
                       "CLOUD": "aws", "DELTA_STAGING_WRITE_TOKEN": "fixture-only"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("IMAGES_JSON", raising=False)
    monkeypatch.delenv("BUNDLE_SHA256", raising=False)
    calls = []

    def urlopen(request, **kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps({"state": "ready", "run_id": "old-client", "steps": []}).encode())

    monkeypatch.setattr(staging.urllib.request, "urlopen", urlopen)
    staging.main()
    assert json.loads(calls[0].data)["image"] == bundle["image_ref"]
