from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from delta_pipeline.clients import Jenkins, ServiceUnavailable


class _Opener:
    def __init__(self, *, code: int | None = None, body: object = None) -> None:
        self._code, self._body = code, body

    def open(self, request, timeout):
        if self._code is not None:
            raise urllib.error.HTTPError(request.full_url, self._code, "error", {}, io.BytesIO())
        return io.BytesIO(json.dumps(self._body).encode())


def _jenkins(opener: _Opener) -> Jenkins:
    jenkins = Jenkins("http://jenkins", "admin", "pw", job="delta-build")
    jenkins._opener = opener
    return jenkins


def test_artifact_that_is_not_uploaded_yet_reads_as_none():
    assert _jenkins(_Opener(code=404)).file(1, "metrics-10.json") is None


def test_artifact_lookup_failure_is_not_treated_as_missing():
    with pytest.raises(ServiceUnavailable):
        _jenkins(_Opener(code=503)).file(1, "metrics-10.json")


def test_artifact_is_read_as_json():
    assert _jenkins(_Opener(body={"image_id": "sha256:abc"})).file(1, "image.json") == {"image_id": "sha256:abc"}


class _Recorder:
    """요청을 기록하고 경로별로 정한 응답을 준다."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, request, timeout):
        self.requests.append(request)
        path = urllib.parse.urlparse(request.full_url).path
        status, body, headers = self.routes[path]
        response = io.BytesIO(json.dumps(body).encode())
        response.headers = headers
        return response


def test_trigger_sends_params_with_run_id_and_follows_queue_by_path_only():
    opener = _Recorder({
        "/crumbIssuer/api/json": (200, {"crumbRequestField": "Jenkins-Crumb", "crumb": "c"}, {}),
        "/job/delta-build/buildWithParameters": (201, {}, {"Location": "https://public.example/queue/item/7/"}),
        "/queue/item/7/api/json": (200, {"executable": {"number": 3}}, {}),
    })
    jenkins = _jenkins(opener)
    jenkins._opener = opener

    assert jenkins.trigger({"COMMIT": "c" * 40}, "run-1-build") == 3
    post = next(r for r in opener.requests if r.full_url.endswith("buildWithParameters"))
    assert urllib.parse.parse_qs(post.data.decode()) == {"COMMIT": ["c" * 40], "RUN_ID": ["run-1-build"]}
    assert post.headers["Jenkins-crumb"] == "c"
    # 공개 주소 Location 이 와도 내부 주소로만 따라간다
    assert all(r.full_url.startswith("http://jenkins/") for r in opener.requests)


@pytest.mark.parametrize("body, status", [({"building": True, "result": None}, "IN_PROGRESS"),
                                          ({"building": False, "result": None}, "IN_PROGRESS"),
                                          ({"building": False, "result": "FAILURE"}, "FAILURE")])
def test_status_uses_plain_api(body, status):
    assert _jenkins(_Opener(body=body)).status(1) == status


def test_sast_and_dast_send_their_own_bearer_tokens(monkeypatch):
    from delta_pipeline import clients

    seen = []

    def fake_json(request, *, timeout, opener=None):
        seen.append((request.full_url, request.get_header("Authorization"), request.data))
        return {"run_id": "d1"}

    monkeypatch.setattr(clients, "_json", fake_json)
    clients.Sast("http://sast", "s" * 32).start("r-sast", "R", "C")
    clients.Dast("http://dast.cp.internal:8765", "m" * 32).start({"version": "2.1.0"}, target_id="staging-gcp",
                                                               sast_run_id="r-sast", planner="agent")
    (sast_url, sast_auth, _), (dast_url, dast_auth, dast_body) = seen
    assert sast_url == "http://sast/v1/sast/scans" and sast_auth == "Bearer " + "s" * 32
    assert dast_url == "http://dast.cp.internal:8765/api/runs" and dast_auth == "Bearer " + "m" * 32
    assert json.loads(dast_body)["auto_fix"] is False
