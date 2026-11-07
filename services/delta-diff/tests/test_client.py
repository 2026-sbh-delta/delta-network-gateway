from __future__ import annotations

import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient
from test_api import FakeReader, STATE, TOKEN, _body, _client

from delta_diff.client import DiffClient, DiffUnavailable, MAX_RESPONSE_BYTES, _NoRedirect, from_ssm
from delta_diff.contract import DiffRequest


class Response(io.BytesIO):
    status = 200


class ApiOpener:
    """HTTP 어댑터의 요청을 실제 FastAPI 계약으로 보낸다."""
    def __init__(self, api: TestClient):
        self.api, self.requests = api, []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.api.request(request.method, request.full_url, content=request.data, headers=dict(request.header_items()))
        if response.status_code != 200:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "failed", {}, io.BytesIO(response.content))
        return Response(response.content)


def client_with_body(body):
    client = DiffClient("https://diff.internal", TOKEN)
    class Opener:
        def open(self, request, timeout):
            return Response(body if isinstance(body, bytes) else json.dumps(body).encode())
    client._opener = Opener()
    return client


def test_client_calls_real_api_and_preserves_needs_human(tmp_path):
    client = DiffClient("https://testserver", TOKEN)
    client._opener = ApiOpener(_client(tmp_path, FakeReader(STATE)))

    client.check_health()
    result = client.run(DiffRequest.model_validate(_body()))

    assert result.assessment.verdict == "needs_human"  # GuardDuty 상태를 state로는 알 수 없다
    assert result.source.serial == 12
    assert result.assessment.mapping.rows
    assert client._opener.requests[-1].get_header("Authorization") == "Bearer " + TOKEN


@pytest.mark.parametrize("code", [401, 403, 404, 422, 502, 503])
def test_http_failure_never_becomes_a_verdict(code):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, TOKEN, {}, io.BytesIO(TOKEN.encode()))
    client = DiffClient("https://diff.internal", TOKEN)
    client._opener = Opener()
    with pytest.raises(DiffUnavailable, match=f"HTTP {code}") as error:
        client.run(DiffRequest.model_validate(_body()))
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("health", [b"ok", {"status": "placeholder"}, {"status": "ok"}])
def test_placeholder_cannot_pass_readiness(health):
    with pytest.raises(DiffUnavailable):
        client_with_body(health).check_health()


@pytest.mark.parametrize("field", ["diff_id", "assessment_id", "kind", "key", "bucket"])
def test_response_from_another_run_or_state_is_rejected(tmp_path, field):
    result = _client(tmp_path, FakeReader(STATE)).post("/v1/diffs", json=_body(),
                                                     headers={"Authorization": "Bearer " + TOKEN}).json()
    if field == "diff_id":
        result[field] = "other"
    elif field in ("assessment_id", "kind"):
        result["assessment"][field] = "other" if field == "assessment_id" else "deployment_gate"
    else:
        result["source"][field] = "other"
    with pytest.raises(DiffUnavailable):
        client_with_body(result).run(DiffRequest.model_validate(_body()))


def test_response_has_a_size_limit():
    with pytest.raises(DiffUnavailable, match="크기"):
        client_with_body(b" " * (MAX_RESPONSE_BYTES + 1)).check_health()


@pytest.mark.parametrize("url", ["http://diff.internal:8095", "https://u:p@diff.internal", "https://diff.internal/x",
                                  "https://diff.internal?token=x", "file:///tmp/test"])
def test_unsafe_connection_configuration_is_rejected(url):
    with pytest.raises(ValueError):
        DiffClient(url, TOKEN)


def test_internal_http_requires_explicit_opt_in_and_never_allows_public_host():
    DiffClient("http://diff.cp.internal:8095", TOKEN, allow_private_http=True)
    with pytest.raises(ValueError):
        DiffClient("http://example.com", TOKEN, allow_private_http=True)


def test_redirects_are_not_followed_with_the_token():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None


def test_ssm_configuration_errors_do_not_reveal_secret_values():
    class Ssm:
        def get_parameter(self, **kw):
            assert kw["WithDecryption"]
            return {"Parameter": {"Value": json.dumps({"url": "file:///tmp/" + TOKEN, "token": TOKEN})}}
    with pytest.raises(DiffUnavailable) as error:
        from_ssm(Ssm(), "/delta-cp/shared/diff_connection")
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('direction', ['onprem-source', 'onprem-target'])
def test_manifest_hashes_are_bound_to_the_actual_request(tmp_path, direction):
    from test_resource_mapping import YAML
    body = _body()
    if direction == 'onprem-source':
        body['source'] = {'provider':'onprem','manifests':YAML}
    else:
        body['target'] = {'provider':'onprem','manifests':YAML}
    api = _client(tmp_path, FakeReader(STATE))
    response = api.post('/v1/diffs', json=body, headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 200
    result = response.json()
    client_with_body(result).run(DiffRequest.model_validate(body))
    meta = result['source'] if direction == 'onprem-source' else result['target_manifest']
    meta['sha256'] = 'a'*64
    with pytest.raises(DiffUnavailable, match='YAML'):
        client_with_body(result).run(DiffRequest.model_validate(body))
