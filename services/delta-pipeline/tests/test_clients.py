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
    post = next(r for r in opener.requests if "buildWithParameters" in r.full_url)
    assert urllib.parse.parse_qs(post.data.decode()) == {"COMMIT": ["c" * 40], "RUN_ID": ["run-1-build"]}
    # Jenkins 기본 대기(quiet period 5초) 없이 바로 큐에 넣는다
    assert urllib.parse.urlparse(post.full_url).query == "delay=0sec"
    assert post.headers["Jenkins-crumb"] == "c"
    # 공개 주소 Location 이 와도 내부 주소로만 따라간다
    assert all(r.full_url.startswith("http://jenkins/") for r in opener.requests)


@pytest.mark.parametrize("body, status", [({"building": True, "result": None}, "IN_PROGRESS"),
                                          ({"building": False, "result": None}, "IN_PROGRESS"),
                                          ({"building": False, "result": "FAILURE"}, "FAILURE")])
def test_status_uses_plain_api(body, status):
    assert _jenkins(_Opener(body=body)).status(1) == status


def test_sast_dast_staging_send_their_own_bearer_tokens(monkeypatch):
    from delta_pipeline import clients

    seen = []

    def fake_json(request, *, timeout, opener=None):
        seen.append((request.get_method(), request.full_url, request.get_header("Authorization"), request.data))
        return {"released": True}

    monkeypatch.setattr(clients, "_json", fake_json)
    clients.Sast("http://sast", "s" * 32).start("r-sast", "R", "C")
    clients.Dast("http://dast.cp.internal:8765", "p" * 32).submit("u-1", {"build_id": "b"}, {"version": "2.1.0"})
    assert clients.Staging("http://jenkins:8091", "r" * 32).release("gcp", "run-a") is True
    (_, sast_url, sast_auth, _), (dast_method, dast_url, dast_auth, dast_body), (_, rel_url, rel_auth, rel_body) = seen
    assert sast_url == "http://sast/v1/sast/scans" and sast_auth == "Bearer " + "s" * 32
    assert dast_method == "PUT" and dast_url == "http://dast.cp.internal:8765/api/platform/runs/u-1"
    assert dast_auth == "Bearer " + "p" * 32 and json.loads(dast_body) == {"binding": {"build_id": "b"},
                                                                          "report": {"version": "2.1.0"}}
    assert rel_url == "http://jenkins:8091/v1/staging/leases/gcp/release" and json.loads(rel_body) == {"holder": "run-a"}


def test_evidence_hash_matches_the_dast_platform_fingerprint():
    import hashlib

    from delta_pipeline.clients import evidence_hash

    value = {"b": [1, "가"], "a": None}
    # DAST platform_api.fingerprint 와 같은 식: sort_keys, 공백 없는 구분자, 기본 ensure_ascii
    expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert evidence_hash(value) == expected


def test_resolve_cname_reads_public_doh_answer(monkeypatch):
    from delta_pipeline import clients

    seen = []

    def fake_json(request, *, timeout, opener=None):
        seen.append((request.full_url, request.get_header("Accept")))
        return {"Answer": [{"name": "app.adajosou.site", "type": 5, "data": "GCP.adajosou.site."}]}

    monkeypatch.setattr(clients, "_json", fake_json)
    assert clients.resolve_cname("app.adajosou.site") == "gcp.adajosou.site"
    assert seen[0][0].startswith("https://cloudflare-dns.com/dns-query?") and seen[0][1] == "application/dns-json"
    monkeypatch.setattr(clients, "_json", lambda *a, **k: {"Status": 3})
    assert clients.resolve_cname("none.adajosou.site") is None
