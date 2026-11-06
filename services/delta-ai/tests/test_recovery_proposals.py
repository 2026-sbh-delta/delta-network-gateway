"""Proposal API is authenticated, bounded, grounded, and has no execution authority."""

import json

import pytest
from delta_ai.api import create_app
from delta_ai.diagnostic_contract import DiagnosisRequest, unavailable_report
from delta_ai.llm import LlmResponse
from delta_ai.recovery_contract import RecoveryRequest
from delta_ai.store import RuleStore
from fastapi.testclient import TestClient


def request_body():
    request = DiagnosisRequest(case_id='case1', run_id='run1', service_id='svc', node_id='onprem_wait',
        trigger='execution_failure', observations=[{'evidence_id': 'e1', 'source': 'worker', 'summary': 'HTTP timeout token=INPUT-PRIVATE'}])
    report = unavailable_report(request).model_copy(update={'status': 'analyzed'})
    return RecoveryRequest(diagnosis=request, report=report,
                           allowed_actions=('retry_same_job', 'wait_for_operator')).model_dump(mode='json')


class Model:
    def __init__(self, change=None):
        self.calls = []
        self.change = change or {}

    async def complete(self, prompt):
        self.calls.append(prompt.user)
        return LlmResponse(json.dumps({'action': 'retry_same_job', 'explanation': 'token=OUTPUT-PRIVATE 다시 조회합니다',
                                      'evidence_ids': ['e1'], **self.change}), 'fake-model')


def api(tmp_path, model):
    return TestClient(create_app(llm=None, diagnostic_llm=None, recovery_llm=model,
                                store=RuleStore(tmp_path / 'rules.json'), token='local-private-token'))


def test_unauthorized_recovery_does_not_call_model(tmp_path):
    model = Model()
    client = api(tmp_path, model)
    assert client.post('/v1/recovery-proposals', json=request_body()).status_code == 401
    assert not model.calls


def test_valid_proposal_is_bound_and_scrubbed(tmp_path):
    model = Model()
    response = api(tmp_path, model).post('/v1/recovery-proposals', json=request_body(),
                                       headers={'Authorization': 'Bearer local-private-token'})
    assert response.status_code == 200
    value = response.json()
    assert value['status'] == 'proposed' and value['action'] == 'retry_same_job'
    assert value['requires_approval'] is True and len(value['input_hash']) == 64
    assert 'INPUT-PRIVATE' not in model.calls[0] and 'OUTPUT-PRIVATE' not in response.text
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change', [
    {'action': 'redeploy_sandbox'}, {'action': 'execute_shell'}, {'evidence_ids': ['invented']},
    {'command': 'delete-all'}, {'requires_approval': False},
])
def test_invalid_or_unapproved_actions_fall_back_to_operator(tmp_path, change):
    response = api(tmp_path, Model(change)).post('/v1/recovery-proposals', json=request_body(),
                                                headers={'Authorization': 'Bearer local-private-token'})
    assert response.json()['action'] == 'wait_for_operator'
    assert response.json()['status'] == 'invalid'


def test_unavailable_model_or_analysis_cannot_offer_execution(tmp_path):
    client = api(tmp_path, None)
    response = client.post('/v1/recovery-proposals', json=request_body(),
                           headers={'Authorization': 'Bearer local-private-token'})
    assert response.json()['status'] == 'unavailable'


def test_request_validation_and_size_never_echo_secrets(tmp_path):
    client = api(tmp_path, Model())
    headers = {'Authorization': 'Bearer local-private-token'}
    response = client.post('/v1/recovery-proposals', json={'token': 'SECRET-IN-ERROR'}, headers=headers)
    assert response.status_code == 422 and 'SECRET-IN-ERROR' not in response.text
    response = client.post('/v1/recovery-proposals', content=b'x' * 65000, headers=headers)
    assert response.status_code == 413


def test_unavailable_analysis_is_not_sent_to_proposal_model(tmp_path):
    model = Model()
    body = request_body()
    body['report']['status'] = 'analysis_unavailable'
    response = api(tmp_path, model).post('/v1/recovery-proposals', json=body,
                                        headers={'Authorization': 'Bearer local-private-token'})
    assert response.json()['action'] == 'wait_for_operator'
    assert not model.calls
