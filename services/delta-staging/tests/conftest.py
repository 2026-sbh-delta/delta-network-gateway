from __future__ import annotations

from typing import Any

import pytest

from delta_staging.aws_ecs import AwsEcsStaging
from delta_staging.config import AwsTarget

ECR = "444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/"
IMAGE = ECR + "demo-app@sha256:" + "a" * 64


class FakeEcs:
    """ECS 응답 흉내. rollout 은 describe_services 호출 횟수로 진행시킨다."""

    def __init__(self, *, rollout: list[str] | None = None, failure_reason: str = "") -> None:
        self.rollout = rollout or ["IN_PROGRESS", "COMPLETED"]
        self.failure_reason = failure_reason
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.revision = 1
        self.current = ""

    def describe_task_definition(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_task_definition", kwargs))
        return {"taskDefinition": {
            "taskDefinitionArn": "arn:aws:ecs:ap-northeast-2:444455556666:task-definition/delta-lab-staging:1",
            "family": "delta-lab-staging", "networkMode": "awsvpc", "cpu": "256", "memory": "512",
            "requiresCompatibilities": ["FARGATE"], "executionRoleArn": "arn:aws:iam::444455556666:role/exec",
            "status": "ACTIVE", "revision": 1,
            "containerDefinitions": [{"name": "app", "image": "busybox", "essential": True,
                                      "environment": [{"name": "APP_MODE", "value": "staging"}]}],
        }}

    def register_task_definition(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("register_task_definition", kwargs))
        self.revision += 1
        arn = f"arn:aws:ecs:ap-northeast-2:444455556666:task-definition/delta-lab-staging:{self.revision}"
        return {"taskDefinition": {"taskDefinitionArn": arn}}

    def update_service(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update_service", kwargs))
        if "taskDefinition" in kwargs:
            self.current = kwargs["taskDefinition"]
        return {}

    def describe_services(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_services", kwargs))
        state = self.rollout.pop(0) if len(self.rollout) > 1 else self.rollout[0]
        deployment = {"taskDefinition": self.current, "rolloutState": state, "runningCount": 1 if state == "COMPLETED" else 0}
        if state == "FAILED":
            deployment["rolloutStateReason"] = self.failure_reason
        return {"services": [{"deployments": [deployment]}]}


class FakeElb:
    def __init__(self, states: list[str] | None = None) -> None:
        self.states = states or ["initial", "healthy"]

    def describe_target_health(self, **kwargs: Any) -> dict[str, Any]:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"TargetHealthDescriptions": [{"TargetHealth": {"State": state}}]}


class FakeSts:
    def __init__(self, account: str = "444455556666") -> None:
        self.account = account

    def get_caller_identity(self) -> dict[str, Any]:
        return {"Account": self.account}


@pytest.fixture
def target() -> AwsTarget:
    return AwsTarget(region="ap-northeast-2", account_id="444455556666", cluster="delta-lab-staging",
                     service="delta-lab-staging", task_family="delta-lab-staging", container_name="app",
                     target_group_arn="arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:targetgroup/stg/1",
                     url="http://internal-delta-lab-staging-alb.elb.amazonaws.com", allowed_image_prefixes=(ECR,))


def make_provider(target: AwsTarget, *, ecs: FakeEcs | None = None, elb: FakeElb | None = None,
                  sts: FakeSts | None = None, **kwargs: Any) -> AwsEcsStaging:
    clients = {"ecs": ecs or FakeEcs(), "elbv2": elb or FakeElb(), "sts": sts or FakeSts()}
    return AwsEcsStaging(target, clients=clients, poll_seconds=0, **kwargs)
