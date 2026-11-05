"""Run separately with --with-editable ../../services/delta-ai."""

import httpx
import pytest
from delta_ai.api import create_app
from delta_ai.store import RuleStore
from fastapi.testclient import TestClient
from pydantic import SecretStr
from support import settings
from test_workflows import setup_plan

from delta_platform.adapters.assessments import AssessmentClient
from delta_platform.core.errors import Problem


def test_platform_to_real_team_ai_contract(engine, store, data, tmp_path):
    workflows, service, _, target, body = setup_plan(engine, store, data)
    wf, _ = workflows.create(service, body, "plan")
    ai = TestClient(
        create_app(llm=None, store=RuleStore(tmp_path / "rules.json"), token="bound-test")
    )
    calls = []

    def handle(request):
        calls.append(request)
        response = ai.post(
            "/v1/assessments", content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(response.status_code, json=response.json())

    config = settings().model_copy(
        update={"ai_url": "http://localhost:9000", "ai_token": SecretStr("bound-test")}
    )
    client = AssessmentClient(config, transport=httpx.MockTransport(handle))
    evidence = {
        "kind": "migration_parity",
        "source_env": {"provider": "aws"},
        "target_env": {"provider": "gcp"},
    }
    result = client.execute(workflows, wf["id"], target, evidence, "assess")
    assert result["verdict"] == "needs_human"  # Missing inventory is not silently a pass.
    assert result["execution_context"]["workflow_id"] == str(wf["id"])
    assert client.execute(workflows, wf["id"], target, evidence, "assess") == result
    assert len(workflows.executions(wf["id"])["items"]) == 1
    assert workflows.get(wf["id"])["status"] == "planned"  # AI never approves deployment.
    wrong = AssessmentClient(
        config,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={**result, "assessment_id": "wrong"})
        ),
    )
    with pytest.raises(Problem) as exc:
        wrong.execute(workflows, wf["id"], target, evidence, "new-assess")
    assert exc.value.code == "assessment_identity_mismatch"
