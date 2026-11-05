from __future__ import annotations

from typing import Any

import pytest

from delta_staging.aws_ecs import AwsEcsStaging
from delta_staging.config import AwsTarget

ECR = "444455556666.dkr.ecr.ap-northeast-2.amazonaws.com/"
IMAGE = ECR + "demo-app@sha256:" + "a" * 64
NEW_IP = "10.0.1.20"


class FakeEcs:
    """ECS 응답 흉내. rollout 은 describe_services 호출 횟수로 진행시킨다."""

    def __init__(self, *, rollout: list[str] | None = None, failure_reason: str = "",
                 running_digest: str | None = None) -> None:
        self.rollout = rollout or ["IN_PROGRESS", "COMPLETED"]
        self.failure_reason = failure_reason
        self.running_digest = running_digest
        self.image = ""
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
        self.image = kwargs["containerDefinitions"][0]["image"]
        self.revision += 1
        arn = f"arn:aws:ecs:ap-northeast-2:444455556666:task-definition/delta-lab-staging:{self.revision}"
        return {"taskDefinition": {"taskDefinitionArn": arn}}

    def update_service(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update_service", kwargs))
        if "taskDefinition" in kwargs:
            self.current = kwargs["taskDefinition"]
        return {}

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_tasks", kwargs))
        return {"taskArns": ["arn:aws:ecs:ap-northeast-2:444455556666:task/delta-lab/abc"] if self.current else []}

    def describe_tasks(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_tasks", kwargs))
        digest = self.running_digest or (self.image.rsplit("@", 1)[1] if "@sha256:" in self.image else "sha256:" + "b" * 64)
        return {"tasks": [{"taskArn": kwargs["tasks"][0], "taskDefinitionArn": self.current, "lastStatus": "RUNNING",
                           "containers": [{"name": "app", "imageDigest": digest,
                                           "networkInterfaces": [{"privateIpv4Address": NEW_IP}]}]}]}

    def describe_services(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_services", kwargs))
        state = self.rollout.pop(0) if len(self.rollout) > 1 else self.rollout[0]
        deployment = {"taskDefinition": self.current, "rolloutState": state, "runningCount": 1 if state == "COMPLETED" else 0}
        if state == "FAILED":
            deployment["rolloutStateReason"] = self.failure_reason
        return {"services": [{"deployments": [deployment]}]}


class FakeElb:
    """대상 그룹 흉내. states 는 새 태스크 상태, old 는 같은 시점의 이전 태스크 상태(None 이면 대상에서 빠짐)."""

    def __init__(self, states: list[str] | None = None, old: list[str | None] | None = None) -> None:
        self.states = states or ["initial", "healthy"]
        self.old = old or [None]

    dns = "delta-lab-staging-alb-1.ap-northeast-2.elb.amazonaws.com"

    def describe_target_groups(self, **kwargs: Any) -> dict[str, Any]:
        return {"TargetGroups": [{"LoadBalancerArns": ["arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:loadbalancer/app/x/1"]}]}

    def describe_load_balancers(self, **kwargs: Any) -> dict[str, Any]:
        return {"LoadBalancers": [{"DNSName": self.dns}]}

    def describe_target_health(self, **kwargs: Any) -> dict[str, Any]:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        old = self.old.pop(0) if len(self.old) > 1 else self.old[0]
        targets = [{"Target": {"Id": NEW_IP}, "TargetHealth": {"State": state}}]
        if old:
            targets.append({"Target": {"Id": "10.0.1.10"}, "TargetHealth": {"State": old}})
        return {"TargetHealthDescriptions": targets}


class FakeEc2:
    """보안 그룹 규칙 흉내. 도구가 단 태그가 붙은 규칙만 돌려준다."""

    def __init__(self) -> None:
        self.rules: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def describe_security_group_rules(self, **kwargs: Any) -> dict[str, Any]:
        return {"SecurityGroupRules": [dict(rule) for rule in self.rules]}

    def authorize_security_group_ingress(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("authorize")
        cidr = kwargs["IpPermissions"][0]["IpRanges"][0]["CidrIp"]
        self.rules.append({"SecurityGroupRuleId": f"sgr-{len(self.calls)}", "CidrIpv4": cidr, "IsEgress": False})
        return {}

    def revoke_security_group_ingress(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("revoke")
        gone = set(kwargs["SecurityGroupRuleIds"])
        self.rules = [rule for rule in self.rules if rule["SecurityGroupRuleId"] not in gone]
        return {}


class FakeSts:
    def __init__(self, account: str = "444455556666") -> None:
        self.account = account

    def get_caller_identity(self) -> dict[str, Any]:
        return {"Account": self.account}


@pytest.fixture
def public_target(target: AwsTarget) -> AwsTarget:
    return target.model_copy(update={"alb_security_group_id": "sg-00000000000000001", "dynamic_scanner_access": True,
                                     "url": "http://delta-lab-staging-alb.ap-northeast-2.elb.amazonaws.com"})


@pytest.fixture
def target() -> AwsTarget:
    return AwsTarget(region="ap-northeast-2", account_id="444455556666", cluster="delta-lab-staging",
                     service="delta-lab-staging", task_family="delta-lab-staging", container_name="app",
                     target_group_arn="arn:aws:elasticloadbalancing:ap-northeast-2:444455556666:targetgroup/stg/1",
                     url="http://internal-delta-lab-staging-alb.elb.amazonaws.com", allowed_image_prefixes=(ECR,))


def make_provider(target: AwsTarget, *, ecs: FakeEcs | None = None, elb: FakeElb | None = None,
                  sts: FakeSts | None = None, ec2: FakeEc2 | None = None, public_ip: str = "52.78.10.20",
                  **kwargs: Any) -> AwsEcsStaging:
    clients = {"ecs": ecs or FakeEcs(), "elbv2": elb or FakeElb(), "sts": sts or FakeSts(), "ec2": ec2 or FakeEc2()}
    return AwsEcsStaging(target, clients=clients, poll_seconds=0, public_ip=lambda: public_ip, **kwargs)
