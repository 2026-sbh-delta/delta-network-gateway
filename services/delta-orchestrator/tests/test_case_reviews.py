import json

import pytest
from delta_ai.api import create_app as ai_app
from delta_ai.cases import CaseStore, IncidentCase, Resolution, VerificationCheck
from delta_ai.diagnostic_contract import DiagnosisRequest, unavailable_report
from delta_ai.store import RuleStore
from delta_orchestrator.app import create_app
from delta_orchestrator.case_reviews import CaseReviewClient
from fastapi import HTTPException
from fastapi.testclient import TestClient

ORIGIN = "https://console.example"
REVIEW = {
    "decision": "approved",
    "reviewer": "tester",
    "reason": "HTTP 재검사 확인",
    "required_terms": ["HTTP"],
    "forbidden_terms": [],
}


def fixture_case():
    request = DiagnosisRequest(
        case_id="case-one",
        run_id="run-one",
        service_id="sample",
        node_id="onprem_wait",
        trigger="execution_failure",
        context={"environment": "onprem", "error_code": "timeout"},
        observations=[
            {"evidence_id": "fact", "source": "runtime", "summary": "HTTP 503"}
        ],
    )
    return IncidentCase(
        request=request,
        report=unavailable_report(request),
        report_sha256="a" * 64,
        resolution=Resolution(
            outcome="success",
            action="retry_same_job",
            scope="onprem_wait",
            checks=(
                VerificationCheck(
                    check="http", status="pass", reference="native-one", sha256="b" * 64
                ),
            ),
        ),
    )


def console(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "case-console-test-password")
    monkeypatch.setenv("ORCH_SESSION_SECRET", "s" * 40)
    store = CaseStore(tmp_path / "cases.sqlite")
    revision = store.publish(fixture_case())
    ai = TestClient(
        ai_app(
            llm=None,
            store=RuleStore(tmp_path / "rules.json"),
            token="producer",
            review_token="operator",
            case_store=store,
        )
    )
    calls = []

    class Relay:
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            response = ai.request(
                method,
                "/v1/case-reviews" + path,
                json=body,
                headers={"Authorization": "Bearer operator"},
            )
            if response.status_code != 200:
                raise HTTPException(response.status_code, "검토 조건 확인")
            return response.json()

    return (
        TestClient(create_app(case_review_client_factory=Relay), base_url=ORIGIN),
        store,
        revision,
        calls,
    )


def login(client):
    assert (
        client.post(
            "/login",
            data={"password": "case-console-test-password"},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        ).status_code
        == 303
    )


def test_real_session_csrf_and_end_to_end_export(monkeypatch, tmp_path):
    client, store, revision, calls = console(monkeypatch, tmp_path)
    root = "/api/case-reviews"
    assert client.get(root).status_code == 401
    assert client.post(root + "/case-one/review", json={}).status_code == 401
    assert not calls
    login(client)
    assert client.get(root).json()["items"][0]["case_id"] == "case-one"
    assert (
        client.get(root + "/case-one").json()["case"]["request"]["run_id"] == "run-one"
    )
    body = {"revision": revision, "review": REVIEW}
    for headers in (
        {},
        {"Origin": "https://evil.example"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    ):
        assert (
            client.post(
                root + "/case-one/review", json=body, headers=headers
            ).status_code
            == 403
        )
    assert (
        client.post(
            root + "/case-one/review",
            content=json.dumps(body),
            headers={"Origin": ORIGIN, "Content-Type": "text/plain"},
        ).status_code
        == 415
    )
    assert (
        client.post(
            root + "/case-one/review",
            content=b"x" * 16001,
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        ).status_code
        == 413
    )
    assert (
        client.post(
            root + "/case-one/review", json=body, headers={"Origin": ORIGIN}
        ).status_code
        == 200
    )
    exported = client.post(
        root + "/case-one/export",
        json={"revision": revision},
        headers={"Origin": ORIGIN},
    )
    assert exported.status_code == 200
    assert exported.headers["cache-control"] == "no-store"
    assert (
        exported.headers["content-disposition"]
        == 'attachment; filename="evaluation-case-one.json"'
    )
    assert exported.json()["candidates"][0]["review"]["reviewer"] == "tester"
    assert (
        store.detail("case-one")["review_events"][0]["review"]["decision"] == "approved"
    )
    assert "operator" not in exported.text and "producer" not in exported.text
    # Rejecting updates retrieval eligibility and export; no graph/cloud/model call exists in this adapter.
    body["review"] = {**REVIEW, "decision": "rejected"}
    assert (
        client.post(
            root + "/case-one/review", json=body, headers={"Origin": ORIGIN}
        ).status_code
        == 200
    )
    assert (
        client.post(
            root + "/case-one/export",
            json={"revision": revision},
            headers={"Origin": ORIGIN},
        ).status_code
        == 409
    )
    assert client.get(root + "?limit=99").status_code == 422
    assert (
        client.post(
            root + "/case-one/review",
            json={"token": "hidden-value"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 422
    )
    assert (
        "hidden-value"
        not in client.post(
            root + "/case-one/review",
            json={"token": "hidden-value"},
            headers={"Origin": ORIGIN},
        ).text
    )


def test_loopback_does_not_bypass_case_review_login(monkeypatch):
    monkeypatch.delenv("ORCH_PUBLIC_ORIGIN", raising=False)
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as client:
        assert client.get("/api/case-reviews").status_code == 401


def test_disabled_or_shared_producer_secret_fails_closed(monkeypatch, tmp_path):
    client, _, _, _ = console(monkeypatch, tmp_path)
    login(client)
    monkeypatch.delenv("DELTA_CASE_REVIEW_TOKEN", raising=False)
    disabled = TestClient(create_app(), base_url=ORIGIN)
    login(disabled)
    assert disabled.get("/api/case-reviews").status_code == 503
    monkeypatch.setenv("DELTA_CASE_REVIEW_TOKEN", "same")
    monkeypatch.setenv("DELTA_DIAGNOSTIC_TOKEN", "same")
    with pytest.raises(HTTPException):
        CaseReviewClient.from_env()


def test_transport_bounds_errors_and_safe_url():
    import urllib.error

    class Opener:
        def open(self, request, timeout):
            assert (
                request.full_url == "https://ai.example/v1/case-reviews/case-one/review"
            )
            assert request.get_header("Authorization") == "Bearer review-only"
            assert timeout == 10
            raise urllib.error.HTTPError(
                request.full_url, 409, "upstream-secret", {}, None
            )

    client = CaseReviewClient("https://ai.example", "review-only")
    client._opener = Opener()
    with pytest.raises(HTTPException) as error:
        client.request("POST", "/case-one/review", {})
    assert (
        error.value.status_code == 409 and "upstream-secret" not in error.value.detail
    )
    with pytest.raises(ValueError):
        CaseReviewClient("http://public.example", "review-only")


def test_installed_console_starts_without_ai_knowledge_files(tmp_path):
    """The console needs case contracts; model resources stay in the AI service."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    services = Path(__file__).resolve().parents[2]
    for service, package in (
        ("delta-ai", "delta_ai"),
        ("delta-pipeline", "delta_pipeline"),
        ("delta-orchestrator", "delta_orchestrator"),
    ):
        shutil.copytree(
            services / service / package,
            tmp_path / package,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ORCH_", "DELTA_", "PYTHONPATH"))
    }
    code = "import sys;sys.path.insert(0,sys.argv[1]);from delta_orchestrator.app import create_app;from fastapi.testclient import TestClient;client=TestClient(create_app(),base_url='http://127.0.0.1:8080');assert client.get('/api/case-reviews').status_code==401"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
