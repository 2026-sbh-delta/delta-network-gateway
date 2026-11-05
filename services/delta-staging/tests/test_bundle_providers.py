"""기존 v2 provider의 공개 deploy 경로 회귀 시험. 클라우드는 모두 합성 응답이다.

reset 내부 메서드를 대체하지 않고 ECS/Logs·Run/Logging API 경계를 통과한다.
S06는 합성 관측 훅으로만 제공한다. 실제 DAST 종료/drain/최종 행0은 여기서 증명하지 않는다.
"""

from __future__ import annotations

import copy
import json

import pytest
from botocore.exceptions import ClientError
from conftest import ECR, NEW_IP, FakeEc2, FakeEcs, FakeElb, FakeSts
from test_bundle_contract import BE_DIGEST, COMMIT, FE_DIGEST, OTHER_DIGEST, bundle_body
from test_gcp_run import AR, JOB, RUN, SERVICE, FakeGcpWithReset

from delta_staging.aws_ecs import AwsEcsStaging, ScanSafety, StagingError
from delta_staging.config import AwsTarget, GcpTarget
from delta_staging.contract import DeployRequest
from delta_staging.gcp_run import GcpCloudRunStaging
from delta_staging.registry import StagingRegistry

LOGGING_ENTRIES = "https://logging.googleapis.com/v2/entries:list"
RESET_ARN = "arn:aws:ecs:ap-northeast-2:444455556666:task/delta-lab/reset-test"
EXECUTION = JOB + "/executions/reset-test"
RESET_ENV = {"APP_ENV": "staging", "STORAGE_BACKEND": "postgres", "PGDATABASE": "synthetic_staging",
             "APP_RESET_DATABASE": "synthetic_staging", "PGHOST": "127.0.0.1", "PGPORT": "5432"}


def refs(prefix):
    return {"frontend": prefix + "demo-app@" + FE_DIGEST, "backend": prefix + "demo-backend@" + BE_DIGEST}


def alter_backend(containers, fault, *, ecs=False):
    """관측값만 변경한다. API 요청/등록된 이미지와 독립된 반례다."""
    result = copy.deepcopy(containers)
    backend = next(c for c in result if c["name"] == "backend")
    if fault == "wrong-backend":
        if ecs:
            # ref는 올바른 채 ECS 실제 imageDigest만 달라야 digest 검증을 증명한다.
            backend["imageDigest"] = OTHER_DIGEST
        else:
            backend["image"] = backend["image"].split("@", 1)[0] + "@" + OTHER_DIGEST
    elif fault == "missing":
        result.remove(backend)
    elif fault == "duplicate":
        result.append(copy.deepcopy(backend))
    elif fault == "unpinned":
        backend["image"] = backend["image"].split("@", 1)[0] + ":latest"
    return result


class BundleEcs(FakeEcs):
    def __init__(self):
        super().__init__(rollout=["COMPLETED"])
        self.runtime_fault = None
        self.reset_env = dict(RESET_ENV)
        self.reset_exit = 0
        self.reset_revision = ""
        self.registrations = {}

    def describe_task_definition(self, **kwargs):
        response = super().describe_task_definition(**kwargs)
        spec = response["taskDefinition"]
        if kwargs["taskDefinition"] == "synthetic-reset":
            spec["family"] = "synthetic-reset"
            spec["containerDefinitions"] = [{
                "name": "reset", "image": "synthetic/old-reset:old", "essential": True,
                "environment": [{"name": k, "value": v} for k, v in self.reset_env.items()],
                "logConfiguration": {"logDriver": "awslogs", "options": {
                    "awslogs-group": "/synthetic/reset", "awslogs-region": "ap-northeast-2",
                    "awslogs-stream-prefix": "test"}},
            }]
        else:
            spec["containerDefinitions"].append({"name": "backend", "image": "synthetic/old-backend:old",
                                                  "essential": True, "environment": [
                                                      {"name": "PGDATABASE", "value": "synthetic_staging"}]})
        return response

    def register_task_definition(self, **kwargs):
        response = super().register_task_definition(**kwargs)
        arn = response["taskDefinition"]["taskDefinitionArn"]
        self.registrations[arn] = copy.deepcopy(kwargs)
        if kwargs["family"] == "synthetic-reset":
            self.reset_revision = arn
        return response

    def describe_services(self, **kwargs):
        response = super().describe_services(**kwargs)
        response["services"][0].update({
            "loadBalancers": [{"targetGroupArn": "synthetic-target-group", "containerName": "app", "containerPort": 3000}],
            "networkConfiguration": {"awsvpcConfiguration": {"subnets": ["subnet-test"],
                                                               "securityGroups": ["sg-test"], "assignPublicIp": "DISABLED"}},
        })
        return response

    def run_task(self, **kwargs):
        self.calls.append(("run_task", kwargs))
        return {"tasks": [{"taskArn": RESET_ARN}], "failures": []}

    def describe_tasks(self, **kwargs):
        if kwargs["tasks"] == [RESET_ARN]:
            self.calls.append(("describe_tasks", kwargs))
            return {"tasks": [{"taskArn": RESET_ARN, "taskDefinitionArn": self.reset_revision,
                               "lastStatus": "STOPPED", "containers": [{"name": "reset", "exitCode": self.reset_exit,
                                                                          "imageDigest": BE_DIGEST}]}]}
        response = super().describe_tasks(**kwargs)
        task = response["tasks"][0]
        images = refs(ECR)
        # 기존 FakeEcs.image는 reset 등록으로도 변경된다. 실행 증거는 그 값에서 만들지 않는다.
        task["containers"] = alter_backend([
            {"name": "app", "image": images["frontend"], "imageDigest": FE_DIGEST, "lastStatus": "RUNNING",
             "networkInterfaces": [{"privateIpv4Address": NEW_IP}]},
            {"name": "backend", "image": images["backend"], "imageDigest": BE_DIGEST, "lastStatus": "RUNNING"},
        ], self.runtime_fault, ecs=True)
        return response


class BundleLogs:
    def __init__(self):
        self.result = {"remaining_rows": 0, "database_version_num": 180003}
        self.denied = False
        self.calls = []

    def get_log_events(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "synthetic denial"}}, "GetLogEvents")
        messages = [] if self.result is None else [{"message": json.dumps(self.result)}]
        return {"events": messages}


class BundleGcp(FakeGcpWithReset):
    def __init__(self):
        super().__init__()
        self.runtime_fault = None
        self.reset_env = dict(RESET_ENV)
        self.reset_result = {"remaining_rows": 0, "database_version_num": 180003}
        self.logs_denied = False
        self.execution_failed = False
        self.execution_wrong_shape = False
        self.traffic_fault = None
        self.service_patch = None
        self.job_patch = None

    def request(self, method, url, *, params=None, body=None):
        if url == LOGGING_ENTRIES:
            self.calls.append((method, url, body))
            if self.logs_denied:
                raise StagingError("Google API 403: synthetic denial")
            return {"entries": [] if self.reset_result is None else [{"jsonPayload": copy.deepcopy(self.reset_result)}]}
        if url == f"{RUN}/{JOB}:run":
            self.calls.append((method, url, body))
            execution = {"name": EXECUTION, "succeededCount": 0 if self.execution_failed else 1,
                         "failedCount": 1 if self.execution_failed else 0, "runningCount": 0,
                         "cancelledCount": 0, "template": {"containers": [{"name": "reset", "image": refs(AR)["backend"]}]}}
            if self.execution_wrong_shape:
                execution["template"] = {"template": execution["template"]}
            return {"name": "projects/delta-proj/locations/asia-northeast3/operations/run-op",
                    "done": True, "response": execution}
        response = super().request(method, url, params=params, body=body)
        if url == f"{RUN}/{JOB}" and method == "GET":
            reset = response["template"]["template"]["containers"][0]
            reset["env"] = [{"name": k, "value": v} for k, v in self.reset_env.items()]
            response["template"]["template"]["containers"].append({"name": "proxy", "image": "synthetic/proxy:stable"})
        elif url == f"{RUN}/{JOB}" and method == "PATCH":
            self.job_patch = copy.deepcopy(body)
        elif url == f"{RUN}/{SERVICE}" and method == "PATCH":
            self.service_patch = copy.deepcopy(body)
        elif url == f"{RUN}/{SERVICE}" and method == "GET":
            response["template"]["containers"].append({"name": "backend", "image": "synthetic/old-backend:old", "env": [
                {"name": "PGDATABASE", "value": "synthetic_staging"}]})
            revision = f"{SERVICE}/revisions/{self._rev()}"
            response["trafficStatuses"] = [{"revision": revision, "percent": 100}]
            if self.traffic_fault == "partial":
                response["trafficStatuses"] = [{"revision": revision, "percent": 50}, {"revision": "old", "percent": 50}]
            elif self.traffic_fault == "old":
                response["trafficStatuses"] = [{"revision": "old", "percent": 100}]
            elif self.traffic_fault == "unspecified":
                response["trafficStatuses"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]
        elif url.startswith(f"{RUN}/{SERVICE}/revisions/"):
            response["containers"] = alter_backend([
                {"name": "app", "image": refs(AR)["frontend"], "env": [{"name": "DELTA_RUN_ID", "value": "bundle-1"}]},
                {"name": "backend", "image": refs(AR)["backend"], "env": [
                    {"name": "DELTA_RUN_ID", "value": "bundle-1"}, {"name": "PGDATABASE", "value": "synthetic_staging"}]},
            ], self.runtime_fault)
        return response


def setup_provider(cloud):
    images = refs(ECR if cloud == "aws" else AR)
    req = DeployRequest.model_validate(bundle_body(cloud=cloud, image=images["frontend"], images=images))
    health = {"status": "ok", "storage": "postgres", "environment": "staging", "version": COMMIT,
              "cloudProvider": cloud, "databaseVersionNum": 180003}
    probes = []

    def health_probe(url):
        probes.append(("health", url))
        return copy.deepcopy(health)

    async def safety_check(request, revision):
        probes.append(("safety", revision))
        return ScanSafety(run_id=request.run_id, lease_holder=request.lease_holder or request.run_id,
                          cloud=cloud, database_name="synthetic_staging", previous_dast_complete=True,
                          writes_drained=True, revision=revision, remaining_rows=0 if revision else None)

    if cloud == "aws":
        api, logs = BundleEcs(), BundleLogs()
        target = AwsTarget(region="ap-northeast-2", account_id="444455556666", cluster="delta-lab-staging",
                           service="delta-lab-staging", task_family="delta-lab-staging", container_name="app",
                           backend_container_name="backend", reset_task_family="synthetic-reset",
                           database_name="synthetic_staging",
                           target_group_arn="synthetic-target-group", url="http://synthetic-staging.invalid",
                           allowed_image_prefixes=(ECR,))
        provider = AwsEcsStaging(target, clients={"ecs": api, "logs": logs, "elbv2": FakeElb(["healthy"]),
                                                 "sts": FakeSts(), "ec2": FakeEc2()},
                                 poll_seconds=0, rollout_timeout=0, health_timeout=0, reset_timeout=0,
                                 health_probe=health_probe, safety_check=safety_check)
    else:
        api, logs = BundleGcp(), None
        target = GcpTarget(project_id="delta-proj", region="asia-northeast3", service="delta-lab-staging",
                           container_name="app", backend_container_name="backend", reset_job=JOB.rsplit("/", 1)[1],
                           database_name="synthetic_staging",
                           url="http://synthetic-staging.invalid", allowed_image_prefixes=(AR,))
        provider = GcpCloudRunStaging(target, http=api, poll_seconds=0, rollout_timeout=1,
                                      health_timeout=0, reset_timeout=0, health_probe=health_probe, probe=lambda _: True,
                                      safety_check=safety_check)
    return provider, req, api, logs, health, probes


@pytest.fixture(autouse=True)
def forbid_real_cloud(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("실제 네트워크·기본 클라우드 자격 탐색 금지")

    monkeypatch.setattr("delta_staging.aws_ecs._session", forbidden)
    monkeypatch.setattr("delta_staging.gcp_run.GoogleHttp", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_v2_public_deploy_observes_runtime_reset_and_requires_s06_hook(cloud):
    provider, req, api, logs, _, probes = setup_provider(cloud)
    registry = StagingRegistry({cloud: provider})
    await registry.start(req)
    done = await registry.wait(req.run_id)
    assert done.state == "ready", done.error
    assert done.images == req.images and done.image_digests == {"frontend": FE_DIGEST, "backend": BE_DIGEST}
    assert done.image_digest == FE_DIGEST and done.bundle_sha256 == req.bundle_sha256
    assert done.data_reset_remaining == 0 and done.databaseVersionNum == 180003 and probes
    assert [(name, value) for name, value in probes if name == "safety"] == [("safety", None), ("safety", done.revision)]
    assert done.s06_verified is True and done.pre_dast_remaining == 0 and done.handoff_required == ()
    assert any(step.name == "pre_dast_zero" for step in done.steps)
    if cloud == "aws":
        application = api.registrations[done.revision]["containerDefinitions"]
        assert {c["name"]: c["image"] for c in application} == {"app": req.images["frontend"], "backend": req.images["backend"]}
        reset = api.registrations[api.reset_revision]["containerDefinitions"][0]
        assert reset["image"] == req.images["backend"] and reset["entryPoint"] == ["node"]
        assert reset["command"] == ["scripts/reset-db.mjs"]
        run = next(kwargs for name, kwargs in api.calls if name == "run_task")
        assert run["count"] == 1 and run["taskDefinition"] == api.reset_revision
        assert logs.calls[0]["logStreamName"] == "test/reset/reset-test"
        assert sum(name == "describe_tasks" and args["tasks"] != [RESET_ARN] for name, args in api.calls) >= 2
    else:
        assert {c["name"]: c["image"] for c in api.service_patch["template"]["containers"]} == {
            "app": req.images["frontend"], "backend": req.images["backend"]}
        assert api.service_patch["traffic"] == [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]
        containers = api.job_patch["template"]["template"]["containers"]
        reset = next(c for c in containers if c["name"] == "reset")
        assert reset["image"] == req.images["backend"] and reset["command"] == ["node", "scripts/reset-db.mjs"]
        assert next(c for c in containers if c["name"] == "proxy")["image"] == "synthetic/proxy:stable"
        log_body = next(body for _, url, body in api.calls if url == LOGGING_ENTRIES)
        assert 'execution_name"="reset-test"' in log_body["filter"]
        assert sum(method == "GET" and "/revisions/" in url for method, url, _ in api.calls) >= 2


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_runtime_backend_digest_missing_duplicate_and_pin_are_checked(cloud):
    for fault in ("wrong-backend", "missing", "duplicate", "unpinned"):
        provider, req, api, _, _, _ = setup_provider(cloud)
        api.runtime_fault = fault
        steps = []
        with pytest.raises(StagingError):
            await provider.deploy(req, lambda name, detail: steps.append(name))
        assert "healthy" not in steps, fault
        if cloud == "aws":
            assert any(name == "describe_tasks" and args["tasks"] != [RESET_ARN] for name, args in api.calls), fault
        else:
            assert any("/revisions/" in url for _, url, _ in api.calls), fault


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_reset_requires_success_exit_typed_zero_rows_and_actual_pg18(cloud):
    for fault in ("missing", "pg16", "untyped", "untyped-version", "nonzero-rows", "nonzero-exit"):
        provider, req, api, logs, _, _ = setup_provider(cloud)
        result = {"remaining_rows": 0, "database_version_num": 180003}
        if fault == "missing":
            result = None
        elif fault == "pg16":
            result["database_version_num"] = 160004
        elif fault == "untyped":
            result["remaining_rows"] = False
        elif fault == "untyped-version":
            result["database_version_num"] = "180003"
        elif fault == "nonzero-rows":
            result["remaining_rows"] = 1
        if cloud == "aws":
            logs.result = result
            api.reset_exit = 1 if fault == "nonzero-exit" else 0
        else:
            api.reset_result = result
            api.execution_failed = fault == "nonzero-exit"
        steps = []
        with pytest.raises(StagingError):
            await provider.deploy(req, lambda name, detail: steps.append(name))
        assert "healthy" not in steps and "data_reset" not in steps, fault
        if cloud == "aws":
            assert any(name == "run_task" for name, _ in api.calls), fault
            assert not any(name == "update_service" for name, _ in api.calls), fault
        else:
            assert any(url == f"{RUN}/{JOB}:run" for _, url, _ in api.calls), fault
            assert api.service_patch is None, fault


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_reset_log_permission_denial_fails_closed(cloud):
    provider, req, api, logs, _, _ = setup_provider(cloud)
    if cloud == "aws":
        logs.denied = True
    else:
        api.logs_denied = True
    with pytest.raises(StagingError):
        await provider.deploy(req, lambda *_: None)
    if cloud == "aws":
        assert logs.calls and any(name == "run_task" for name, _ in api.calls)
    else:
        assert any(url == LOGGING_ENTRIES for _, url, _ in api.calls)


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_readonly_reset_db_config_mismatch_blocks_reset_and_activation(cloud):
    provider, req, api, _, _, _ = setup_provider(cloud)
    api.reset_env["APP_RESET_DATABASE"] = "another_synthetic_database"
    original = copy.deepcopy(api.reset_env)
    with pytest.raises(StagingError, match="DB"):
        await provider.deploy(req, lambda *_: None)
    assert api.reset_env == original
    if cloud == "aws":
        assert not any(name in {"run_task", "update_service"} for name, _ in api.calls)
    else:
        assert api.job_patch is None and api.service_patch is None


async def test_gcp_requires_exact_ready_revision_to_have_all_actual_traffic():
    for fault in ("partial", "old", "unspecified"):
        provider, req, api, _, _, _ = setup_provider("gcp")
        api.traffic_fault = fault
        with pytest.raises(StagingError, match="트래픽"):
            await provider.deploy(req, lambda *_: None)
        assert api.service_patch is not None, fault


async def test_gcp_execution_requires_native_template_containers_shape():
    provider, req, api, _, _, _ = setup_provider("gcp")
    api.execution_wrong_shape = True
    with pytest.raises(StagingError, match="execution"):
        await provider.deploy(req, lambda *_: None)
    assert api.job_patch is not None and api.service_patch is None


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_health_cannot_hide_memory_wrong_cloud_commit_or_actual_pg16(cloud):
    for changes in ({"storage": "memory"}, {"cloudProvider": "gcp" if cloud == "aws" else "aws"},
                    {"version": "f" * 40}, {"databaseVersionNum": 160004}):
        provider, req, _, _, health, probes = setup_provider(cloud)
        health.update(changes)
        steps = []
        with pytest.raises(StagingError, match="FE→BE→DB"):
            await provider.deploy(req, lambda name, detail: steps.append(name))
        assert any(name == "health" for name, _ in probes) and "healthy" not in steps


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
async def test_missing_s06_hook_blocks_before_reset_or_activation(cloud):
    provider, req, api, logs, _, probes = setup_provider(cloud)
    provider.safety_check = None
    with pytest.raises(StagingError, match="S06"):
        await provider.deploy(req, lambda *_: None)
    assert api.calls == [] and probes == []
    assert logs is None or logs.calls == []


def test_default_s06_check_reads_backend_health():
    """기본 S06 관측: 교체 전에는 앱이 없어도(헬스 없음) drain으로 보고, 교체 후에는 activeWrites 0·remainingRows 정수를 요구한다."""
    import asyncio

    from delta_staging.aws_ecs import ScanSafety, health_safety
    from delta_staging.contract import DeployRequest

    req = DeployRequest(run_id="r1", cloud="gcp", image="x/y@sha256:" + "a" * 64, lease_holder="h1")
    health = {}

    async def endpoint():
        return "http://stg"

    check = health_safety(endpoint, "board_staging", probe=lambda url: health)
    before = asyncio.run(check(req, None))
    assert before == ScanSafety("r1", "h1", "gcp", "board_staging", True, True, None, None)
    health.update(activeWrites=0, remainingRows=0)
    after = asyncio.run(check(req, "rev-2"))
    assert after.writes_drained and after.remaining_rows == 0 and after.revision == "rev-2"
    health.update(activeWrites=1)
    assert asyncio.run(check(req, "rev-2")).writes_drained is False
