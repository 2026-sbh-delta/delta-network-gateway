"""Trusted cleanup of per-environment VMs and k3s objects; shared infrastructure stays.

동규님 platform/deploy/workloads/remove.py 이식. state는 배포와 같은 S3 키(state_backend.state_key)를 쓴다.
DB 서버(prevent_destroy)와 공용 네트워크·버킷은 지우지 않는다.
"""

import ipaddress
import json
import re
from uuid import UUID

import state_backend
from deploy import k3s_objects, run, vm_params


def absent(data, selected):
    """A failed job can exit before creating state. Check the provider, not the directory alone."""
    env, service = data["environment_id"], data["service_id"]
    if data["target"]["cloud"] == "aws":
        from aws_vm import session as target_session

        # 위임 세션 생성 시 대상 계정 ID를 sts로 확인한다.
        session = target_session(selected)
        rows = session.client("ec2").describe_instances(
            Filters=[
                {"Name": "tag:ServiceId", "Values": [service]},
                {"Name": "tag:Environment", "Values": [env]},
                {
                    "Name": "instance-state-name",
                    "Values": [
                        "pending",
                        "running",
                        "stopping",
                        "stopped",
                        "shutting-down",
                    ],
                },
            ]
        )["Reservations"]
        if any(r["Instances"] for r in rows):
            raise ValueError("Resource exists without deployment state")
    else:
        rows = json.loads(
            run(
                [
                    "gcloud",
                    "compute",
                    "instances",
                    "list",
                    "--project",
                    data["connection"]["gcp_project"],
                    "--filter",
                    f"labels.service-id={service} AND labels.environment={env}",
                    "--format=json(name)",
                ]
            )
        )
        if rows:
            raise ValueError("Resource exists without deployment state")


def validate_changes(plan, data):
    cloud = data["target"]["cloud"]
    address = "aws_instance.app" if cloud == "aws" else "google_compute_instance.app"
    for item in plan.get("resource_changes", []):
        if item.get("mode") == "data":
            continue
        if item["address"] != address or item["change"]["actions"] not in (
            ["delete"],
            ["no-op"],
        ):
            raise ValueError("Cleanup plan contains an unrelated resource")
        before = item["change"]["before"] or {}
        tags = before.get("tags" if cloud == "aws" else "labels", {})
        expected = (
            {
                "ManagedBy": "delta-platform",
                "ServiceId": data["service_id"],
                "Environment": data["environment_id"],
            }
            if cloud == "aws"
            else {
                "managed-by": "delta-platform",
                "service-id": data["service_id"],
                "environment": data["environment_id"],
            }
        )
        if any(tags.get(k) != v for k, v in expected.items()):
            raise ValueError("VM ownership changed")
        if before.get("ebs_block_device" if cloud == "aws" else "attached_disk"):
            raise ValueError("Additional storage requires operator review")


def vm(data, selected, config):
    cloud = data["target"]["cloud"]
    params = vm_params(data, selected)
    directory = state_backend.workdir(config, data["environment_id"], "vm", cloud)
    variables = directory / "deployment.auto.tfvars.json"
    variables.write_text(json.dumps(params))
    variables.chmod(0o600)
    environment = state_backend.env(config)
    run(
        state_backend.init_command(config, data["environment_id"], "vm"),
        cwd=directory,
        env=environment,
    )
    # 원격 state가 비어 있으면 실패한 배포가 state 없이 자원을 남겼는지 공급자에서 확인한다.
    if not run(["terraform", "state", "list"], cwd=directory, env=environment).strip():
        absent(data, selected)
        return
    run(
        ["terraform", "plan", "-destroy", "-input=false", "-out=removal.tfplan"],
        cwd=directory,
        env=environment,
    )
    plan = json.loads(
        run(["terraform", "show", "-json", "removal.tfplan"], cwd=directory, env=environment)
    )
    validate_changes(plan, data)
    run(["terraform", "apply", "-input=false", "removal.tfplan"], cwd=directory, env=environment)
    remaining = run(["terraform", "state", "list"], cwd=directory, env=environment).strip()
    if remaining:
        raise ValueError("Resource cleanup incomplete")
    # Keep the empty remote state for retry/audit. Never remove shared state or buckets.


def onprem(data, selected):
    namespace = selected["namespace"]
    if not re.fullmatch(r"delta-[a-z0-9-]{1,50}", namespace):
        raise ValueError("Dedicated namespace required")
    ipaddress.ip_address(selected["private_ip"])
    prefix = [
        "kubectl",
        "--kubeconfig",
        selected["kubeconfig"],
        "--request-timeout=20s",
    ]
    for name, uid in (
        (namespace, selected["namespace_uid"]),
        ("kube-system", selected["cluster_id"]),
    ):
        item = json.loads(run(prefix + ["get", "namespace", name, "-o", "json"]))
        if item["metadata"]["uid"] != uid:
            raise ValueError("Cluster/namespace identity mismatch")
    objects = k3s_objects(data, selected)
    if data.get("database"):
        objects.append(
            {
                "kind": "Secret",
                "metadata": {
                    "name": "db-" + UUID(data["environment_id"]).hex,
                    "labels": {
                        "app.kubernetes.io/managed-by": "delta-platform",
                        "delta-platform/environment-id": data["environment_id"],
                        "delta-platform/service-id": data["service_id"],
                    },
                    "annotations": {"delta-platform/plan-hash": data["plan_hash"]},
                },
            }
        )
    registered = []
    for obj in objects:
        kind, name = obj["kind"], obj["metadata"]["name"]
        raw = run(
            prefix
            + ["-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"]
        )
        if not raw.strip():
            continue
        actual = json.loads(raw)
        meta = actual["metadata"]
        if (
            any(
                meta.get("labels", {}).get(k) != v
                for k, v in obj["metadata"]["labels"].items()
            )
            or meta.get("annotations", {}).get("delta-platform/plan-hash")
            != data["plan_hash"]
        ):
            raise ValueError("Application ownership changed")
        if kind == "Deployment":
            pod = actual["spec"]["template"]["spec"]
            if len(pod["containers"]) != 1 or any(
                "persistentVolumeClaim" in v or "ephemeral" in v
                for v in pod.get("volumes", [])
            ):
                raise ValueError("Storage ownership review required")
        registered.append((obj, meta))
    # Validate all objects before deleting any of them, using API UID/version preconditions.
    for obj, meta in registered:
        group = {
            "Deployment": "/apis/apps/v1",
            "Service": "/api/v1",
            "Secret": "/api/v1",
            "NetworkPolicy": "/apis/networking.k8s.io/v1",
        }[obj["kind"]]
        plural = {
            "Deployment": "deployments",
            "Service": "services",
            "Secret": "secrets",
            "NetworkPolicy": "networkpolicies",
        }[obj["kind"]]
        path = f"{group}/namespaces/{namespace}/{plural}/{meta['name']}"
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "propagationPolicy": "Foreground",
            "preconditions": {
                "uid": meta["uid"],
                "resourceVersion": meta["resourceVersion"],
            },
        }
        run(prefix + ["delete", "--raw", path, "-f", "-"], body=json.dumps(options))
    for obj, meta in registered:
        run(
            prefix
            + [
                "-n",
                namespace,
                "wait",
                "--for=delete",
                f"{obj['kind']}/{meta['name']}",
                "--timeout=180s",
            ]
        )


def execute(data, config):
    if data.get("schema_version") != 1 or not re.fullmatch(
        r"[a-f0-9]{64}", data["snapshot_hash"]
    ):
        raise ValueError("Invalid cleanup request")
    UUID(data["service_id"])
    for deployment in data["deployments"]:
        if (
            deployment["service_id"] != data["service_id"]
            or str(UUID(deployment["environment_id"])) != deployment["environment_id"]
        ):
            raise ValueError("Project identity mismatch")
        selected = config["connections"][deployment["target"]["cloud"]]
        if selected["identity"] != deployment["connection"]:
            raise ValueError("Connection identity changed")
        if deployment["target"]["cloud"] == "onprem":
            onprem(deployment, selected)
        else:
            vm(deployment, selected, config)
    return {
        "run_id": data["run_id"],
        "snapshot_hash": data["snapshot_hash"],
        "cleanup": "complete",
    }
