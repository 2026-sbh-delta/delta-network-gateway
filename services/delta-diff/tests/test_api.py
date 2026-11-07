from __future__ import annotations

import json

import pytest
from delta_ai.store import RuleStore
from delta_diff.api import create_app
from delta_diff.sources import (
    StateAllowlist,
    StateLocation,
    StateNotFound,
    StateSnapshot,
)
from fastapi.testclient import TestClient

TOKEN = "t" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
KEY = "delta/lab/workload-server/terraform.tfstate"
STATE = {
    "version": 4,
    "serial": 12,
    "lineage": "lin-1",
    "terraform_version": "1.16.4",
    "resources": [
        {
            "mode": "managed",
            "type": "aws_instance",
            "name": "server",
            "instances": [
                {
                    "attributes": {
                        "id": "i-1",
                        "instance_type": "t4g.nano",
                        "ami": "ami-1",
                        "iam_instance_profile": "",
                        "tags": {"Name": "web"},
                        "user_data": "SECRET-USER-DATA",
                    }
                }
            ],
        },
        {
            "mode": "data",
            "type": "aws_ami",
            "name": "server",
            "instances": [
                {
                    "attributes": {
                        "id": "ami-1",
                        "name": "al2023-ami-2023.1-kernel-6.1-arm64",
                    }
                }
            ],
        },
        {
            "mode": "managed",
            "type": "aws_subnet",
            "name": "private",
            "instances": [
                {
                    "attributes": {
                        "id": "subnet-1",
                        "cidr_block": "10.20.1.0/24",
                        "availability_zone_id": "apne2-az1",
                        "tags": {"Name": "web-a"},
                    }
                }
            ],
        },
    ],
}


class FakeReader:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state
        self.reads: list[StateLocation] = []

    def read(self, location: StateLocation) -> StateSnapshot:
        self.reads.append(location)
        if self.state is None:
            raise StateNotFound("없음")
        return StateSnapshot(state=self.state, version_id="v9")


def _client(tmp_path, reader: FakeReader) -> TestClient:
    app = create_app(
        reader=reader,
        allowlist=StateAllowlist.parse("tf-bucket/delta/lab/"),
        llm=None,
        store=RuleStore(tmp_path / "rules.json"),
        token=TOKEN,
    )
    return TestClient(app)


def _body(**overrides) -> dict:
    body = {
        "diff_id": "diff-1",
        "service_id": "web",
        "source": {
            "state": {"bucket": "tf-bucket", "key": KEY},
            "region": "ap-northeast-2",
        },
        "target": {"region": "asia-northeast3"},
    }
    return {**body, **overrides}


def test_diff_reads_state_and_returns_mapping_proposal(tmp_path):
    reader = FakeReader(STATE)

    response = _client(tmp_path, reader).post(
        "/v1/diffs", json=_body(), headers=HEADERS
    )

    assert response.status_code == 200
    result = response.json()
    assert result["source"] == {
        "bucket": "tf-bucket",
        "key": KEY,
        "version_id": "v9",
        "serial": 12,
        "lineage": "lin-1",
        "terraform_version": "1.16.4",
        "resource_count": 3,
        "excluded_capabilities": [],
    }
    assert [s["name"] for s in result["mapping_input"]["servers"]] == ["web"]
    assert result["assessment"]["kind"] == "migration_mapping"
    assert result["assessment"]["mapping"]["rows"]
    assert "SECRET-USER-DATA" not in response.text


def test_diff_requires_token(tmp_path):
    reader = FakeReader(STATE)

    response = _client(tmp_path, reader).post("/v1/diffs", json=_body())

    assert response.status_code == 401
    assert reader.reads == []


def test_diff_refuses_state_outside_allowlist_without_reading(tmp_path):
    reader = FakeReader(STATE)
    body = _body(
        source={"state": {"bucket": "tf-bucket", "key": "delta/prod/terraform.tfstate"}}
    )

    response = _client(tmp_path, reader).post("/v1/diffs", json=body, headers=HEADERS)

    assert response.status_code == 403
    assert reader.reads == []


def test_missing_state_is_404(tmp_path):
    response = _client(tmp_path, FakeReader(None)).post(
        "/v1/diffs", json=_body(), headers=HEADERS
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "state",
    [
        {"version": 4, "resources": []},
        {"version": 3, "resources": []},
        {
            "version": 4,
            "resources": [{"mode": "managed", "instances": [{"attributes": {}}]}],
        },
    ],
)
def test_state_without_mappable_resources_is_422(tmp_path, state):
    response = _client(tmp_path, FakeReader(state)).post(
        "/v1/diffs", json=_body(), headers=HEADERS
    )

    assert response.status_code == 422


def test_unknown_request_field_is_rejected(tmp_path):
    response = _client(tmp_path, FakeReader(STATE)).post(
        "/v1/diffs", json=_body(extra=1), headers=HEADERS
    )

    assert response.status_code == 422


def test_oversized_request_is_rejected(tmp_path):
    body = json.dumps(_body(accepted_gaps=["x" * 70_000]))

    response = _client(tmp_path, FakeReader(STATE)).post(
        "/v1/diffs",
        content=body,
        headers={**HEADERS, "content-type": "application/json"},
    )

    assert response.status_code == 413


def test_app_refuses_to_start_without_token(tmp_path):
    with pytest.raises(ValueError):
        create_app(
            reader=FakeReader(STATE),
            allowlist=StateAllowlist.parse("b/p/"),
            llm=None,
            store=RuleStore(tmp_path / "r.json"),
            token="",
        )


def test_diff_maps_to_registered_existing_gcp_subnet(tmp_path):
    from copy import deepcopy

    state = deepcopy(STATE)
    state["resources"].append({
        "mode": "managed", "type": "aws_route_table_association", "name": "private",
        "instances": [{"attributes": {"subnet_id": "subnet-1", "route_table_id": "rtb-1"}}],
    })
    response = _client(tmp_path, FakeReader(state)).post(
        "/v1/diffs",
        json=_body(target={"region": "asia-northeast3", "existing_subnets": [
            {"name": "app", "cidr": "10.32.2.0/24", "tier": "private"},
        ]}),
        headers=HEADERS,
    )
    assert response.status_code == 200
    mapping = response.json()["assessment"]["mapping"]
    assert mapping["network_mode"] == "reuse"
    row = next(row for row in mapping["rows"] if row["resource"] == "subnet")
    assert (row["source"], row["target"], row["grade"]) == ("web-a", "app (10.32.2.0/24)", "approximate")
