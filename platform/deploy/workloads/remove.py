"""Trusted cleanup of per-environment state and k3s objects; shared infrastructure stays."""

import ipaddress
import json
import re
import shutil
from pathlib import Path
from uuid import UUID

import boto3

from deploy import ROOT, k3s_objects, run


def absent(data, selected):
    """A failed job can exit before creating state. Check the provider, not the directory alone."""
    env, service = data["environment_id"], data["service_id"]
    if data["target"]["cloud"] == "aws":
        session = boto3.Session(region_name=data["target"]["region"])
        if (
            session.client("sts").get_caller_identity()["Account"]
            != data["connection"]["account_id"]
        ):
            raise ValueError("AWS identity mismatch")
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
    root = Path(config["state_root"]).resolve()
    directory = root / data["environment_id"]
    if directory.resolve().parent != root or directory.is_symlink():
        raise ValueError("Invalid state directory")
    state = directory / "terraform.tfstate"
    if not state.exists():
        absent(data, selected)
        return
    variables = json.loads((directory / "deployment.auto.tfvars.json").read_text())
    if (
        variables["environment_id"] != data["environment_id"]
        or variables["service_id"] != data["service_id"]
    ):
        raise ValueError("Deployment state ownership changed")
    cloud = data["target"]["cloud"]
    identity_key = "account_id" if cloud == "aws" else "project_id"
    if (
        variables[identity_key]
        != data["connection"]["account_id" if cloud == "aws" else "gcp_project"]
    ):
        raise ValueError("Deployment account changed")
    cached_backend = directory / ".terraform/terraform.tfstate"
    if (
        cached_backend.exists()
        and json.loads(cached_backend.read_text())
        .get("backend", {})
        .get("type", "local")
        != "local"
    ):
        raise ValueError("Only local per-environment state is allowed")
    expected = {p.name for p in (ROOT / "terraform" / cloud).glob("*.tf")}
    if {p.name for p in directory.glob("*.tf")} != expected:
        raise ValueError("Unexpected Terraform configuration")
    for name in expected:
        shutil.copyfile(ROOT / "terraform" / cloud / name, directory / name)
    run(["terraform", "init", "-input=false", "-lockfile=readonly"], cwd=directory)
    run(
        ["terraform", "plan", "-destroy", "-input=false", "-out=removal.tfplan"],
        cwd=directory,
    )
    plan = json.loads(
        run(["terraform", "show", "-json", "removal.tfplan"], cwd=directory)
    )
    validate_changes(plan, data)
    run(["terraform", "apply", "-input=false", "removal.tfplan"], cwd=directory)
    remaining = run(["terraform", "state", "list"], cwd=directory).strip()
    if remaining:
        raise ValueError("Resource cleanup incomplete")
    # Keep the empty local state for retry/audit. Never remove shared state or buckets.


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
            "NetworkPolicy": "/apis/networking.k8s.io/v1",
        }[obj["kind"]]
        plural = {
            "Deployment": "deployments",
            "Service": "services",
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
