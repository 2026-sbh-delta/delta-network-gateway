from __future__ import annotations

import json
from typing import Any

import pytest

from delta_staging.config import GcpTarget, load_targets
from delta_staging.contract import DeployRequest
from delta_staging.gcp_run import COMPUTE, REGISTRY, RUN, GcpCloudRunStaging
from delta_staging.registry import RequestRejected, StagingRegistry

AR = "asia-northeast3-docker.pkg.dev/delta-proj/delta-lab-staging/"
DIGEST = "sha256:" + "a" * 64
IMAGE = AR + "demo-app@" + DIGEST
SERVICE = "projects/delta-proj/locations/asia-northeast3/services/delta-lab-staging"
POLICY = f"{COMPUTE}/projects/delta-proj/global/securityPolicies/delta-lab-staging-scanner"


class FakeGcp:
    """Cloud Run·Compute·Artifact Registry REST 흉내. 호출 기록과 서비스 상태를 들고 있다."""

    def __init__(self, *, operation_error: str | None = None, traffic_percent: int = 100,
                 tag_digest: str = "sha256:" + "b" * 64) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.revision = 1
        self.labels: dict[str, str] = {}
        self.image = "us-docker.pkg.dev/cloudrun/container/hello"
        self.env: list[dict[str, Any]] = [{"name": "APP_MODE", "value": "staging"}]
        self.operation_error = operation_error
        self.traffic_percent = traffic_percent
        self.tag_digest = tag_digest
        self.rules: list[dict[str, Any]] = [{"priority": 2147483647, "action": "deny(403)"}]
        self.op_polls = 0
        self.ip = "34.120.0.10"

    def _rev(self) -> str:
        return f"delta-lab-staging-{self.revision:05d}-abc"

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, url, body))
        if url == f"{RUN}/{SERVICE}" and method == "GET":
            return {"name": SERVICE, "etag": "e1", "reconciling": False,
                    "latestCreatedRevision": f"{SERVICE}/revisions/{self._rev()}",
                    "latestReadyRevision": f"{SERVICE}/revisions/{self._rev()}",
                    "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
                    "trafficStatuses": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": self.traffic_percent}],
                    "template": {"revision": "old", "labels": {"team": "delta"}, "scaling": {"minInstanceCount": 1},
                                 "containers": [{"name": "app", "image": self.image, "env": self.env,
                                                 "ports": [{"containerPort": 8080}]}]}}
        if url == f"{RUN}/{SERVICE}" and method == "PATCH":
            container = body["template"]["containers"][0]
            self.image, self.env, self.labels = container["image"], container["env"], body["template"]["labels"]
            self.revision += 1
            return {"name": "projects/delta-proj/locations/asia-northeast3/operations/op-1", "done": False}
        if url.endswith("/operations/op-1"):
            self.op_polls += 1
            return {"name": "op-1", "done": True, **({"error": {"message": self.operation_error}} if self.operation_error else {})}
        if url.startswith(f"{RUN}/{SERVICE}/revisions/"):
            return {"name": f"{SERVICE}/revisions/{self._rev()}", "labels": self.labels,
                    "containers": [{"name": "app", "image": self.image}]}
        if url.startswith(REGISTRY):
            return {"name": url, "version": f"projects/delta-proj/.../versions/{self.tag_digest}"}
        if url == POLICY:
            return {"rules": [dict(r) for r in self.rules]}
        if url == f"{POLICY}/addRule":
            self.rules.append(body)
            return {"name": "op-2", "status": "RUNNING"}
        if url == f"{POLICY}/patchRule":
            self.rules = [body if r["priority"] == params["priority"] else r for r in self.rules]
            return {"name": "op-2", "status": "DONE"}
        if url == f"{POLICY}/removeRule":
            self.rules = [r for r in self.rules if r["priority"] != params["priority"]]
            return {"name": "op-2", "status": "DONE"}
        if url == f"{COMPUTE}/projects/delta-proj/global/addresses/delta-lab-staging-ip":
            return {"name": "delta-lab-staging-ip", "address": self.ip}
        if url.endswith("/global/operations/op-2"):
            return {"name": "op-2", "status": "DONE"}
        raise AssertionError(f"예상하지 못한 호출: {method} {url}")

    def scanner_cidrs(self) -> list[list[str]]:
        return [r["match"]["config"]["srcIpRanges"] for r in self.rules if r["priority"] != 2147483647]


@pytest.fixture
def gcp_target() -> GcpTarget:
    return GcpTarget(project_id="delta-proj", region="asia-northeast3", service="delta-lab-staging", container_name="app",
                     security_policy="delta-lab-staging-scanner", dynamic_scanner_access=True,
                     address="delta-lab-staging-ip", allowed_image_prefixes=(AR,))


def _provider(target: GcpTarget, http: FakeGcp, *, reachable: list[bool] | None = None, **kwargs: Any) -> GcpCloudRunStaging:
    answers = list(reachable or [True])
    probe = lambda url: answers.pop(0) if len(answers) > 1 else answers[0]  # noqa: E731
    return GcpCloudRunStaging(target, http=http, poll_seconds=0, public_ip=lambda: "15.165.5.176", probe=probe, **kwargs)


def _req(**overrides: Any) -> DeployRequest:
    return DeployRequest.model_validate({"run_id": "Run.1", "cloud": "gcp", "image": IMAGE, "commit": "abc1234", **overrides})


async def test_deploy_swaps_image_and_waits_for_full_traffic(gcp_target):
    gcp = FakeGcp()
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp)})

    await registry.start(_req(env={"FEATURE_X": "on"}))
    done = await registry.wait("Run.1")

    assert done.state == "ready" and done.url == "http://34.120.0.10" and done.image_digest == DIGEST
    assert done.revision == f"{SERVICE}/revisions/delta-lab-staging-00002-abc"
    patch = next(body for method, _, body in gcp.calls if method == "PATCH")
    assert "revision" not in patch["template"] and patch["template"]["scaling"] == {"minInstanceCount": 1}
    assert patch["template"]["labels"] == {"team": "delta", "delta-run-id": "run-1"}
    env = {item["name"]: item["value"] for item in gcp.env}
    assert env == {"APP_MODE": "staging", "FEATURE_X": "on", "DELTA_RUN_ID": "Run.1", "DELTA_COMMIT": "abc1234"}
    assert [s.name for s in done.steps] == ["accepted", "image_digest", "service_updated", "traffic_switched", "healthy"]


async def test_tag_is_resolved_to_digest_before_deploy(gcp_target):
    gcp = FakeGcp()
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp)})

    await registry.start(_req(image=AR + "demo-app:v1"))
    done = await registry.wait("Run.1")

    assert done.state == "ready" and not done.pinned and done.image_digest == "sha256:" + "b" * 64
    assert gcp.image == AR + "demo-app@sha256:" + "b" * 64  # 개정판은 digest 로 고정된다
    tag_url = next(url for _, url, _ in gcp.calls if url.startswith(REGISTRY))
    assert tag_url.endswith("/repositories/delta-lab-staging/packages/demo-app/tags/v1")


async def test_nested_package_name_is_url_encoded(gcp_target):
    gcp = FakeGcp()
    await _provider(gcp_target, gcp)._pinned(AR + "team/demo-app:v2")

    assert next(url for _, url, _ in gcp.calls).endswith("/packages/team%2Fdemo-app/tags/v2")


async def test_failed_revision_marks_failed(gcp_target):
    gcp = FakeGcp(operation_error="container failed to start")
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp)})

    await registry.start(_req())
    done = await registry.wait("Run.1")

    assert done.state == "failed" and "container failed to start" in done.error


async def test_partial_traffic_times_out(gcp_target):
    registry = StagingRegistry({"gcp": _provider(gcp_target, FakeGcp(traffic_percent=50), health_timeout=0)})

    await registry.start(_req())
    done = await registry.wait("Run.1")

    assert done.state == "failed" and "트래픽" in done.error


async def test_auto_scanner_ip_opens_armor_and_waits_for_lb(gcp_target):
    gcp = FakeGcp()
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp, reachable=[False, False, True])})

    await registry.start(_req(scanner_ip="auto"))
    done = await registry.wait("Run.1")

    assert done.state == "ready" and done.scanner_ip == "15.165.5.176"
    assert gcp.scanner_cidrs() == [["15.165.5.176/32"]]
    assert [s.name for s in done.steps][1] == "scanner_allowed" and done.steps[-1].detail == "http://34.120.0.10"


async def test_new_runner_ip_patches_the_same_rule_and_stop_removes_it(gcp_target):
    gcp = FakeGcp()
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp)})
    await registry.start(_req(scanner_ip="auto"))
    await registry.wait("Run.1")

    await registry.start(_req(run_id="run-2", scanner_ip="3.35.120.9", replace=True))
    await registry.wait("run-2")
    assert gcp.scanner_cidrs() == [["3.35.120.9/32"]]

    await registry.stop("run-2")
    assert gcp.scanner_cidrs() == []


async def test_unreachable_lb_times_out(gcp_target):
    registry = StagingRegistry({"gcp": _provider(gcp_target, FakeGcp(), reachable=[False], health_timeout=0)})

    await registry.start(_req(scanner_ip="auto"))
    done = await registry.wait("Run.1")

    assert done.state == "failed" and "Cloud Armor" in done.error


async def test_image_outside_registry_is_rejected_before_any_call(gcp_target):
    gcp = FakeGcp()
    registry = StagingRegistry({"gcp": _provider(gcp_target, gcp)})

    with pytest.raises(RequestRejected):
        await registry.start(_req(image="docker.io/someone/app@" + DIGEST))
    assert gcp.calls == []


async def test_fixed_scanner_staging_refuses_scanner_ip(gcp_target):
    fixed = gcp_target.model_copy(update={"dynamic_scanner_access": False})
    registry = StagingRegistry({"gcp": _provider(fixed, FakeGcp())})

    with pytest.raises(RequestRejected):
        await registry.start(_req(scanner_ip="auto"))


def test_load_targets_reads_both_clouds(tmp_path, gcp_target, target):
    path = tmp_path / "staging.json"
    aws = target.model_dump(mode="json")
    aws.pop("cloud")  # 예전 설정처럼 cloud 가 없으면 aws 로 읽는다
    path.write_text(json.dumps([aws, gcp_target.model_dump(mode="json")]), encoding="utf-8")

    targets = load_targets(path)

    assert targets["aws"] == target and targets["gcp"] == gcp_target


def test_dynamic_access_needs_a_policy(gcp_target):
    with pytest.raises(ValueError):
        GcpTarget.model_validate({**gcp_target.model_dump(), "security_policy": None})


async def test_address_is_looked_up_each_time_so_recreated_lb_needs_no_config_change(gcp_target):
    http = FakeGcp()
    provider = _provider(gcp_target, http)
    assert await provider.endpoint() == "http://34.120.0.10"
    http.ip = "34.8.9.10"  # LB 를 지우고 다시 만들어 전역 IP 가 바뀜
    assert await provider.endpoint() == "http://34.8.9.10"


def test_gcp_target_needs_address_or_url():
    with pytest.raises(ValueError):
        GcpTarget(project_id="delta-proj", region="asia-northeast3", service="s", container_name="app",
                  allowed_image_prefixes=(AR,))


JOB = "projects/delta-proj/locations/asia-northeast3/jobs/delta-lab-staging-db-reset"


class FakeGcpWithReset(FakeGcp):
    """초기화 Job까지 흉내 낸다. reset_ok=False면 실행이 실패(남은 행 있음)로 끝난다."""

    def __init__(self, *, reset_ok: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reset_ok = reset_ok
        self.job_image = "us-docker.pkg.dev/cloudrun/container/job"
        self.order: list[str] = []

    def request(self, method, url, *, params=None, body=None):
        if url == f"{RUN}/{JOB}" and method == "GET":
            self.calls.append((method, url, body))
            return {"name": JOB, "template": {"taskCount": 1, "template": {"maxRetries": 0, "containers": [
                {"name": "reset", "image": self.job_image, "command": ["python", "-c", "..."]}]}}}
        if url == f"{RUN}/{JOB}" and method == "PATCH":
            self.calls.append((method, url, body))
            self.job_image = body["template"]["template"]["containers"][0]["image"]
            return {"name": "projects/delta-proj/locations/asia-northeast3/operations/job-op", "done": True}
        if url == f"{RUN}/{JOB}:run":
            self.calls.append((method, url, body))
            self.order.append("reset")
            result = {"succeededCount": 1} if self.reset_ok else {"failedCount": 1}
            return {"name": "projects/delta-proj/locations/asia-northeast3/operations/run-op", "done": True,
                    "response": result}
        if url == f"{RUN}/{SERVICE}" and method == "PATCH":
            self.order.append("service")
        return super().request(method, url, params=params, body=body)


async def test_staging_data_is_reset_with_the_same_image_before_the_new_revision(gcp_target):
    target = gcp_target.model_copy(update={"reset_job": "delta-lab-staging-db-reset"})
    gcp = FakeGcpWithReset()
    registry = StagingRegistry({"gcp": _provider(target, gcp)})
    await registry.start(_req())
    done = await registry.wait("Run.1")
    assert done.state == "ready"
    assert gcp.job_image == IMAGE and gcp.order == ["reset", "service"]
    assert "data_reset" in [s.name for s in done.steps]


async def test_failed_reset_stops_the_deploy(gcp_target):
    target = gcp_target.model_copy(update={"reset_job": "delta-lab-staging-db-reset"})
    gcp = FakeGcpWithReset(reset_ok=False)
    registry = StagingRegistry({"gcp": _provider(target, gcp)})
    await registry.start(_req())
    done = await registry.wait("Run.1")
    assert done.state == "failed" and "초기화" in (done.error or "")
    assert "service" not in gcp.order
