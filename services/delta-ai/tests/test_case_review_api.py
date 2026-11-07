import pytest
from delta_ai.api import create_app
from delta_ai.case_evaluation import EvaluationDataset
from delta_ai.cases import CaseReview, CaseStore
from delta_ai.store import RuleStore
from fastapi.testclient import TestClient
from test_case_knowledge import incident, query

PRODUCER = {"Authorization": "Bearer producer-test"}
OPERATOR = {"Authorization": "Bearer operator-test"}
REVIEW = {
    "decision": "approved",
    "reviewer": "operator",
    "reason": "HTTP 재검사 근거 확인",
    "required_terms": ["HTTP"],
    "forbidden_terms": ["전체 이전 완료"],
}


def setup(tmp_path, review_token="operator-test"):
    store = CaseStore(tmp_path / "cases.sqlite")
    app = create_app(
        llm=None,
        store=RuleStore(tmp_path / "rules.json"),
        token="producer-test",
        case_store=store,
        review_token=review_token,
    )
    return store, TestClient(app)


def test_producer_cannot_read_review_or_export_and_operator_cannot_publish(tmp_path):
    store, client = setup(tmp_path)
    case = incident()
    revision = store.publish(case)
    for headers in ({}, PRODUCER):
        for path in ("", "/case-old"):
            assert (
                client.get("/v1/case-reviews" + path, headers=headers).status_code
                == 401
            )
        for action in ("review", "export"):
            assert (
                client.post(
                    "/v1/case-reviews/case-old/" + action,
                    headers=headers,
                    json={"revision": revision, "review": REVIEW},
                ).status_code
                == 401
            )
    assert (
        client.post(
            "/v1/incident-cases", headers=OPERATOR, json=case.model_dump(mode="json")
        ).status_code
        == 401
    )
    assert not store.search(query())


def test_review_export_revoke_and_new_result_requires_review(tmp_path):
    store, client = setup(tmp_path)
    revision = store.publish(incident())
    listing = client.get("/v1/case-reviews", headers=OPERATOR).json()
    assert listing["items"][0]["approvable"] and not listing["items"][0]["exportable"]
    assert "case" not in listing["items"][0] and "payload" not in listing["items"][0]
    path = "/v1/case-reviews/case-old"
    assert (
        client.post(
            path + "/export", headers=OPERATOR, json={"revision": revision}
        ).status_code
        == 409
    )
    result = client.post(
        path + "/review",
        headers=OPERATOR,
        json={"revision": revision, "review": REVIEW},
    )
    assert result.status_code == 200
    detail = client.get(path, headers=OPERATOR).json()
    assert detail["exportable"] and len(detail["review_events"]) == 1
    assert detail["case"]["resolution"]["checks"][0]["status"] == "pass"
    result = client.post(
        path + "/export", headers=OPERATOR, json={"revision": revision}
    )
    assert (
        result.status_code == 200
        and "attachment" in result.headers["content-disposition"]
    )
    dataset = EvaluationDataset.model_validate(result.json())
    assert dataset.candidates[0].review.required_terms == ("HTTP",)
    assert dataset.candidates[0].revision == revision
    assert store.search(query())
    # A new immutable result never inherits approval; stale browser writes and exports conflict.
    updated = store.publish(incident(sequence=1, outcome="failure"))
    for action in ("review", "export"):
        body = {
            "revision": revision,
            **({"review": REVIEW} if action == "review" else {}),
        }
        assert (
            client.post(path + "/" + action, headers=OPERATOR, json=body).status_code
            == 409
        )
    assert not client.get(path, headers=OPERATOR).json()["exportable"]
    client.post(
        path + "/review", headers=OPERATOR, json={"revision": updated, "review": REVIEW}
    )
    assert store.search(query())
    rejected = {**REVIEW, "decision": "rejected", "reason": "근거 재검토 필요"}
    assert (
        client.post(
            path + "/review",
            headers=OPERATOR,
            json={"revision": updated, "review": rejected},
        ).status_code
        == 200
    )
    assert not store.search(query())
    assert (
        client.post(
            path + "/export", headers=OPERATOR, json={"revision": updated}
        ).status_code
        == 409
    )
    assert len(client.get(path, headers=OPERATOR).json()["review_events"]) == 3


@pytest.mark.parametrize("outcome", ["unknown", "stopped"])
def test_unverified_outcomes_cannot_be_approved(tmp_path, outcome):
    store, client = setup(tmp_path)
    revision = store.publish(incident(outcome=outcome))
    assert (
        client.post(
            "/v1/case-reviews/case-old/review",
            headers=OPERATOR,
            json={"revision": revision, "review": REVIEW},
        ).status_code
        == 409
    )


def test_bounds_masking_and_missing_review_capability(tmp_path):
    store, client = setup(tmp_path)
    revision = store.publish(incident())
    for querystring in ("?limit=51", "?offset=-1", "?offset=100001"):
        assert (
            client.get("/v1/case-reviews" + querystring, headers=OPERATOR).status_code
            == 422
        )
    assert client.get("/v1/case-reviews/absent", headers=OPERATOR).status_code == 404
    secret = "token=super-secret-value"
    response = client.post(
        "/v1/case-reviews/case-old/review",
        headers=OPERATOR,
        json={
            "revision": revision,
            "review": {**REVIEW, "reason": secret, "unknown": secret},
        },
    )
    assert response.status_code == 422 and "super-secret-value" not in response.text
    assert (
        client.post(
            "/v1/case-reviews/case-old/review",
            headers=OPERATOR,
            json={"revision": revision, "review": {**REVIEW, "reason": secret}},
        ).status_code
        == 200
    )
    assert (
        "super-secret-value"
        not in client.get("/v1/case-reviews/case-old", headers=OPERATOR).text
    )
    assert (
        client.post(
            "/v1/case-reviews/case-old/export", headers=OPERATOR, content=b"x" * 64001
        ).status_code
        == 413
    )
    _, disabled = setup(tmp_path / "disabled", None)
    assert disabled.get("/v1/case-reviews", headers=OPERATOR).status_code == 401
    with pytest.raises(ValueError, match="생산자"):
        setup(tmp_path / "same", "producer-test")


@pytest.mark.parametrize(
    "changes",
    [{"reason": "   "}, {"required_terms": [" "]}, {"forbidden_terms": ["\n"]}],
)
def test_review_rejects_empty_human_labels(tmp_path, changes):
    store, client = setup(tmp_path)
    revision = store.publish(incident())
    assert (
        client.post(
            "/v1/case-reviews/case-old/review",
            headers=OPERATOR,
            json={"revision": revision, "review": {**REVIEW, **changes}},
        ).status_code
        == 422
    )


def test_detail_caps_version_and_audit_history(tmp_path):
    store, client = setup(tmp_path)
    for sequence in range(22):
        revision = store.publish(incident(sequence=sequence))
    for index in range(22):
        store.review(
            "case-old",
            revision,
            CaseReview(**{**REVIEW, "reason": f"검토 기록 {index}"}),
        )
    data = client.get("/v1/case-reviews/case-old", headers=OPERATOR).json()
    assert len(data["versions"]) == len(data["review_events"]) == 20
    assert data["versions_truncated"] and data["review_events_truncated"]
    assert data["versions"][0]["sequence"] == 21
