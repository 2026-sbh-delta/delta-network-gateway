"""compose-delivery-20261106: 기존 공개 요청·Registry·HTTP 경계의 v2 계약.

새 production 모듈 import 없이 수집된다. 모든 이미지·토큰·DB 진단은 합성 값이다.
실제 클라우드/DB 동작 대신 입력·출력 계약과 ready 허용 조건만 검증한다.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

import pytest
from conftest import ECR, IMAGE
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_lease import FakeProvider

from delta_staging.api import create_app
from delta_staging.aws_ecs import Outcome
from delta_staging.contract import DeployRequest
from delta_staging.registry import Busy, Conflict, StagingRegistry

COMMIT = "0123456789abcdef0123456789abcdef01234567"
FE_DIGEST = "sha256:" + "a" * 64
BE_DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
READ_AUTH = {"Authorization": "Bearer " + "r" * 32}
WRITE_AUTH = {"Authorization": "Bearer " + "w" * 32}
DEPLOY = "/v1/staging/deployments"


def bundle_hash(commit=COMMIT, digests=None, **overrides):
    """명세의 독립 oracle: production 정규화/해시 함수를 사용하지 않는다."""
    content = {"schema": 2, "commit": commit, "platform": "linux/amd64",
               "image_digests": digests or {"frontend": FE_DIGEST, "backend": BE_DIGEST}, **overrides}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bundle_body(**overrides):
    return {"run_id": "bundle-1", "cloud": "aws", "image": IMAGE, "commit": COMMIT,
            "images": {"frontend": IMAGE, "backend": ECR + "demo-backend@" + BE_DIGEST},
            "image_digests": {"frontend": FE_DIGEST, "backend": BE_DIGEST},
            "bundle_sha256": bundle_hash(), "lease_holder": "pipeline-1", **overrides}


def request(**overrides):
    return DeployRequest.model_validate(bundle_body(**overrides))


def client_for(registry):
    return TestClient(create_app(registry=registry, read_token="r" * 32, write_token="w" * 32))


@dataclass(frozen=True)
class BundleOutcome(Outcome):
    """구현 담당과 맞출 Outcome 확장 계약. 기존 Outcome 생성자는 그대로 쓴다."""

    images: dict[str, str] = field(default_factory=dict)
    image_digests: dict[str, str] = field(default_factory=dict)
    bundle_sha256: str | None = None
    data_reset_remaining: int | None = 0
    databaseVersionNum: int | None = 180003


class BundleProvider(FakeProvider):
    """기존 provider fake를 확장. 입력 전달과 관측 결과 전달을 별도로 확인한다."""

    def __init__(self, *, target=None, evidence=None):
        super().__init__()
        self.target = target
        self.evidence = evidence or {}
        self.requests = []
        self.stops = 0

    async def deploy(self, req, report):
        self.requests.append(req)
        self.deploys.append(req.run_id)
        # 문자열은 항상 성공처럼 보이게 한다. 구조화된 remaining 검증을 우회할 수 없어야 한다.
        report("data_reset", "synthetic reset: 남은 행 0")
        report("image_digest", FE_DIGEST)
        if not getattr(req, "images", None):
            return Outcome("http://s", "synthetic-revision", FE_DIGEST, "synthetic-task")
        values = {"images": dict(req.images), "image_digests": dict(req.image_digests),
                  "bundle_sha256": req.bundle_sha256, "data_reset_remaining": 0,
                  "databaseVersionNum": 180003, **self.evidence}
        return BundleOutcome(url="http://s", revision="synthetic-revision", image_digest=FE_DIGEST,
                             task_arn="synthetic-task", **values)

    async def stop(self):
        self.stops += 1


def test_valid_v2_request_preserves_both_images_and_legacy_alias():
    req = request()
    assert req.images == bundle_body()["images"]
    assert req.image_digests == {"frontend": FE_DIGEST, "backend": BE_DIGEST}
    assert req.bundle_sha256 == bundle_hash() and req.image == req.images["frontend"] and req.pinned


def test_legacy_single_image_request_remains_valid():
    req = DeployRequest(run_id="legacy", image=IMAGE, commit="abc1234")
    assert req.image == IMAGE and req.commit == "abc1234" and req.pinned


def test_bundle_identity_excludes_registry_hostname_and_map_insertion_order():
    aws = request()
    gcp = request(cloud="gcp", image="asia-northeast3-docker.pkg.dev/delta-proj/staging/fe@" + FE_DIGEST,
                  images={"backend": "asia-northeast3-docker.pkg.dev/delta-proj/staging/be@" + BE_DIGEST,
                          "frontend": "asia-northeast3-docker.pkg.dev/delta-proj/staging/fe@" + FE_DIGEST},
                  image_digests={"backend": BE_DIGEST, "frontend": FE_DIGEST})
    assert aws.bundle_sha256 == gcp.bundle_sha256 == bundle_hash()


def invalid_bodies():
    """한 번에 하나의 의미 있는 반례. 정상 묶음 수용 뒤에 거부됨을 확인한다."""
    baseline = bundle_body()
    cases = {}
    for key in ("frontend", "backend"):
        value = copy.deepcopy(baseline)
        value["images"].pop(key)
        cases[f"missing-{key}"] = value
    cases["empty-images-no-legacy-fallback"] = {**baseline, "images": {}}
    cases["null-images-no-legacy-fallback"] = {**baseline, "images": None}
    cases["short-commit"] = {**baseline, "commit": "abc1234", "bundle_sha256": bundle_hash("abc1234")}
    cases["missing-commit"] = {**baseline, "commit": None}
    cases["wrong-commit-for-bundle"] = {**baseline, "commit": "f" * 40}
    cases["missing-bundle"] = {k: v for k, v in baseline.items() if k != "bundle_sha256"}
    cases["bundle-without-images"] = {k: v for k, v in baseline.items() if k != "images"}
    cases["wrong-bundle"] = {**baseline, "bundle_sha256": "0" * 64}
    cases["wrong-schema-in-hash"] = {**baseline, "bundle_sha256": bundle_hash(schema=1)}
    cases["wrong-platform-in-hash"] = {**baseline, "bundle_sha256": bundle_hash(platform="linux/arm64")}
    value = copy.deepcopy(baseline)
    value["images"]["backend"] = ECR + "demo-backend:latest"
    cases["tagged-backend"] = value
    value = copy.deepcopy(baseline)
    value["images"]["backend"] = ECR + "demo-backend@" + OTHER_DIGEST
    cases["backend-ref-digest-mismatch"] = value
    value = copy.deepcopy(baseline)
    value["image_digests"]["backend"] = OTHER_DIGEST
    value["bundle_sha256"] = bundle_hash(digests=value["image_digests"])
    cases["self-consistent-hash-but-wrong-backend-ref"] = value
    cases["legacy-alias-disagrees"] = {**baseline, "image": ECR + "other@" + OTHER_DIGEST}
    return cases


@pytest.mark.parametrize("case", invalid_bodies())
def test_v2_invalid_request_is_rejected_without_falling_back_to_legacy(case):
    # 이 수용 검증이 없으면 현재 extra=forbid의 일괄 422가 새 검증 PASS로 오인된다.
    request()
    with pytest.raises(ValidationError):
        DeployRequest.model_validate(invalid_bodies()[case])


async def test_registry_forwards_and_persists_bundle_outcome(tmp_path, target):
    provider = BundleProvider(target=target)
    state_path = tmp_path / "synthetic-deployments.json"
    registry = StagingRegistry({"aws": provider}, state_path)
    req = request(env={"FEATURE_X": "on"})
    await registry.start(req)
    done = await registry.wait(req.run_id)
    assert provider.requests == [req]
    assert done.state == "ready" and done.image_digest == FE_DIGEST and done.image == IMAGE
    payload = done.model_dump(mode="json")
    for key in ("images", "image_digests", "bundle_sha256"):
        assert payload[key] == bundle_body()[key]
    assert payload["data_reset_remaining"] == 0 and payload["databaseVersionNum"] == 180003
    names = [step.name for step in done.steps]
    assert "data_reset" in names and "image_digest" in names
    restored = StagingRegistry({"aws": BundleProvider(target=target)}, state_path)
    assert restored.get(req.run_id).model_dump(mode="json") == payload


def changed_body(kind, *, legacy=False):
    original = {"run_id": "legacy", "image": IMAGE, "commit": COMMIT,
                "env": {"FEATURE_X": "on"}, "lease_holder": "pipeline-1"} if legacy else bundle_body(env={"FEATURE_X": "on"})
    value = copy.deepcopy(original)
    if kind == "backend":
        value["images"]["backend"] = ECR + "demo-backend@" + OTHER_DIGEST
        value["image_digests"]["backend"] = OTHER_DIGEST
        value["bundle_sha256"] = bundle_hash(digests=value["image_digests"])
    elif kind == "commit":
        value["commit"] = "f" * 40
        if not legacy:
            value["bundle_sha256"] = bundle_hash(value["commit"])
    elif kind == "env":
        value["env"] = {"FEATURE_X": "off"}
    elif kind == "holder":
        value["lease_holder"] = "pipeline-2"
    elif kind == "cloud":
        value["cloud"] = "gcp"
    return original, value


@pytest.mark.parametrize("kind", ["backend", "commit", "env", "holder", "cloud"])
def test_same_run_changed_input_is_http_409_without_second_deploy(kind):
    provider = BundleProvider()
    registry = StagingRegistry({"aws": provider, "gcp": provider})
    original, changed = changed_body(kind)
    with client_for(registry) as client:
        accepted = client.post(DEPLOY, json=original, headers=WRITE_AUTH)
        assert accepted.status_code == 202, accepted.text
        client.portal.call(registry.wait, original["run_id"])
        replay = client.post(DEPLOY, json=original, headers=WRITE_AUTH)
        assert replay.status_code == 202
        rejected = client.post(DEPLOY, json=changed, headers=WRITE_AUTH)
        assert rejected.status_code == 409, rejected.text
        assert client.get(DEPLOY + "/bundle-1", headers=READ_AUTH).json()["commit"] == COMMIT
    assert len(provider.requests) == 1


@pytest.mark.parametrize("kind", ["commit", "env", "holder"])
def test_legacy_same_run_changed_configuration_is_also_http_409(kind):
    provider = BundleProvider()
    original, changed = changed_body(kind, legacy=True)
    registry = StagingRegistry({"aws": provider})
    with client_for(registry) as client:
        assert client.post(DEPLOY, json=original, headers=WRITE_AUTH).status_code == 202
        client.portal.call(registry.wait, original["run_id"])
        rejected = client.post(DEPLOY, json=changed, headers=WRITE_AUTH)
        assert rejected.status_code == 409, rejected.text
    assert len(provider.requests) == 1


@pytest.mark.parametrize("field_name,new_value", [("service", "other-staging"), ("environment", "production")])
async def test_same_run_changed_server_target_conflicts_after_restart(target, tmp_path, field_name, new_value):
    state_path = tmp_path / "synthetic-target-state.json"
    req = DeployRequest(run_id="target-replay", image=IMAGE, commit=COMMIT)
    first = StagingRegistry({"aws": BundleProvider(target=target)}, state_path)
    await first.start(req)
    await first.wait(req.run_id)
    changed_target = target.model_copy(update={field_name: new_value})
    provider = BundleProvider(target=changed_target)
    restarted = StagingRegistry({"aws": provider}, state_path)
    with pytest.raises(Conflict):
        await restarted.start(req)
    assert provider.requests == []


async def test_same_bundle_replay_after_restart_is_idempotent(target, tmp_path):
    state_path = tmp_path / "synthetic-bundle-state.json"
    original = StagingRegistry({"aws": BundleProvider(target=target)}, state_path)
    req = request()
    await original.start(req)
    done = await original.wait(req.run_id)
    provider = BundleProvider(target=target)
    restarted = StagingRegistry({"aws": provider}, state_path)
    replay = await restarted.start(req)
    assert replay.model_dump() == done.model_dump() and provider.requests == []


async def test_other_holder_cannot_start_v2_reset_or_deploy(target):
    provider = BundleProvider(target=target)
    registry = StagingRegistry({"aws": provider})
    await registry.start(request())
    await registry.wait("bundle-1")
    with pytest.raises(Busy):
        await registry.start(request(run_id="bundle-2", lease_holder="other-pipeline", replace=True))
    assert len(provider.requests) == 1


@pytest.mark.parametrize("remaining", [None, 1, -1, False])
async def test_v2_ready_requires_structured_integer_zero_even_when_log_says_zero(remaining):
    provider = BundleProvider(evidence={"data_reset_remaining": remaining})
    registry = StagingRegistry({"aws": provider})
    await registry.start(request())
    done = await registry.wait("bundle-1")
    assert done.state == "failed" and done.url is None and done.error
    assert provider.stops == 1


@pytest.mark.parametrize("version", [None, 160004, 170010, 190001, True])
async def test_v2_ready_requires_observed_postgres18_not_requested_configuration(version):
    provider = BundleProvider(evidence={"databaseVersionNum": version})
    registry = StagingRegistry({"aws": provider})
    await registry.start(request(env={"PG_VERSION": "18"}))
    done = await registry.wait("bundle-1")
    assert done.state == "failed" and done.url is None and done.error


@pytest.mark.parametrize("evidence", [
    {"image_digests": {"frontend": FE_DIGEST, "backend": OTHER_DIGEST}},
    {"image_digests": {"frontend": FE_DIGEST}},
    {"images": {"frontend": IMAGE, "backend": ECR + "other@" + OTHER_DIGEST}},
    {"bundle_sha256": "0" * 64},
])
async def test_v2_ready_rejects_incomplete_or_mismatched_runtime_evidence(evidence):
    provider = BundleProvider(evidence=evidence)
    registry = StagingRegistry({"aws": provider})
    await registry.start(request())
    done = await registry.wait("bundle-1")
    assert done.state == "failed" and done.url is None and done.error


async def test_legacy_outcome_without_reset_or_db_diagnostics_remains_supported():
    provider = BundleProvider()
    registry = StagingRegistry({"aws": provider})
    await registry.start(DeployRequest(run_id="legacy", image=IMAGE))
    done = await registry.wait("legacy")
    assert done.state == "ready" and done.image_digest == FE_DIGEST


def test_public_status_and_tool_schema_expose_bundle_contract():
    provider = BundleProvider()
    registry = StagingRegistry({"aws": provider})
    with client_for(registry) as client:
        # TestClient 이벤트 루프 안에서 배포 완료를 기다려 GET을 결정적으로 검사한다.
        response = client.post(DEPLOY, json=bundle_body(), headers=WRITE_AUTH)
        assert response.status_code == 202, response.text
        client.portal.call(registry.wait, "bundle-1")
        status = client.get(DEPLOY + "/bundle-1", headers=READ_AUTH)
        assert status.status_code == 200
        payload = status.json()
        assert payload["state"] == "ready"
        for key in ("images", "image_digests", "bundle_sha256"):
            assert payload[key] == bundle_body()[key]
        assert payload["image_digest"] == FE_DIGEST and payload["data_reset_remaining"] == 0
        assert payload["databaseVersionNum"] == 180003
        schema = client.get("/v1/tools", headers=READ_AUTH).json()["tools"][0]["input_schema"]
        assert {"images", "image_digests", "bundle_sha256"} <= set(schema["properties"])
