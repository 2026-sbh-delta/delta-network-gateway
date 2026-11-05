"""Trusted Jenkins entry point. Config/state live outside the disposable workspace."""

import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import boto3
import google.auth
from delta_platform.adapters.registry_copy import RegistryCopy
from delta_platform.core.deployment_targets import OPTIONS, DeploymentTarget
from google.auth.transport.requests import AuthorizedSession

ROOT = Path(__file__).resolve().parent


def run(args, *, body=None, cwd=None, timeout=600):
    result = subprocess.run(
        args,
        check=False,
        input=body,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        # Upstream Terraform/SSM/kubectl output can contain customer secrets.
        raise RuntimeError("Trusted deployment command failed: " + Path(args[0]).name)
    return result.stdout


def request(encoded, config):
    if len(encoded) > 65536:
        raise ValueError("Oversized request")
    data = json.loads(base64.b64decode(encoded, validate=True))
    if (
        data.get("schema_version") != 1
        or data.get("run_id") != os.environ["DELTA_RUN_ID"]
    ):
        raise ValueError("Request identity mismatch")
    for name in ("run_id", "environment_id", "service_id", "project_id", "plan_id"):
        UUID(data[name])
    if not re.fullmatch(r"[a-f0-9]{64}", data["plan_hash"]) or not re.fullmatch(
        r"[a-f0-9]{40}", data["commit_sha"]
    ):
        raise ValueError("Invalid plan identity")
    target = DeploymentTarget.model_validate(data["target"])
    selected = config["connections"][target.cloud]
    if data["connection"] != selected["identity"]:
        raise ValueError("Operator connection changed")
    if not 1 <= len(data["components"]) <= 2:
        raise ValueError("Invalid component count")
    for component in data["components"]:
        image = component["image"]
        if (
            not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}", image)
            or image.rsplit("@", 1)[0] + "@" not in config["allowed_image_prefixes"]
        ):
            raise ValueError("Image not allowed")
        if (
            image.rsplit("@", 1)[1] != component["image_digest"]
            or component["commit_sha"] != data["commit_sha"]
        ):
            raise ValueError("Image identity mismatch")
        if (
            type(component["container_port"]) is not int
            or not 1 <= component["container_port"] <= 65535
            or not re.fullmatch(r"/[A-Za-z0-9/_\-.]*", component["healthcheck_path"])
        ):
            raise ValueError("Invalid app port/health path")
    return data, selected


def provision(data, selected, state_root):
    cloud, target = data["target"]["cloud"], data["target"]
    directory = state_root / data["environment_id"]
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for file in (ROOT / "terraform" / cloud).glob("*.tf"):
        shutil.copyfile(file, directory / file.name)
    lockfile = ROOT / "terraform" / cloud / ".terraform.lock.hcl"
    shutil.copyfile(lockfile, directory / lockfile.name)
    params = {
        **selected["terraform"],
        "environment_id": data["environment_id"],
        "service_id": data["service_id"],
        "profile_id": target["profile_id"],
    }
    if cloud == "aws" and params["account_id"] != data["connection"]["account_id"]:
        raise ValueError("AWS account mismatch")
    if cloud == "gcp" and params["project_id"] != data["connection"]["gcp_project"]:
        raise ValueError("GCP project mismatch")
    (directory / "deployment.auto.tfvars.json").write_text(json.dumps(params))
    run(["terraform", "init", "-input=false", "-lockfile=readonly"], cwd=directory)
    run(["terraform", "plan", "-input=false", "-out=deployment.tfplan"], cwd=directory)
    plan = json.loads(
        run(["terraform", "show", "-json", "deployment.tfplan"], cwd=directory)
    )
    for change in plan.get("resource_changes", []):
        if any(
            action not in ("no-op", "create", "read")
            for action in change["change"]["actions"]
        ):
            raise ValueError("Deployment must not update/delete an existing resource")
    run(["terraform", "apply", "-input=false", "deployment.tfplan"], cwd=directory)
    return {
        key: value["value"]
        for key, value in json.loads(
            run(["terraform", "output", "-json"], cwd=directory)
        ).items()
    }


def vm(data, selected, config):
    cloud = data["target"]["cloud"]
    images = [c["image"] for c in data["components"]]
    if cloud == "gcp":
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        copier = RegistryCopy(
            SimpleNamespace(
                repository=selected["artifact_repository"],
                allowed_image_prefixes=config["allowed_image_prefixes"],
            ),
            AuthorizedSession(credentials),
        )
        images = [copier.copy(image, lambda: None) for image in images]
    output = provision(data, selected, Path(config["state_root"]))
    address = str(ipaddress.ip_address(output["private_ip"]))
    profile = next(
        p for p in OPTIONS[cloud]["profiles"] if p["id"] == data["target"]["profile_id"]
    )
    payload = {
        "environment_id": data["environment_id"],
        "cloud": cloud,
        "images": images,
        "components": data["components"],
        "memory_mb": profile["memory_mb"] - 384,
    }
    # Customer secrets must not enter Terraform state or SSM command text. This initial
    # executor supports images requiring no additional runtime secrets; a secret mount
    # contract is needed before deploying an app that requires a database/password.
    runtime = base64.b64encode((ROOT / "vm_runtime.py").read_bytes()).decode()
    payload64 = base64.b64encode(json.dumps(payload).encode()).decode()
    script = (
        "import base64,pathlib,subprocess\np=pathlib.Path('/var/lib/delta-runtime.py')\np.write_bytes(base64.b64decode('"
        + runtime
        + "'))\nsubprocess.run(['python3',str(p)],input=base64.b64decode('"
        + payload64
        + "'),check=True)"
    )
    encoded = base64.b64encode(script.encode()).decode()
    command = (
        "sudo python3 -c \"import base64;exec(base64.b64decode('" + encoded + "'))\""
    )
    if cloud == "aws":
        ssm = boto3.client("ssm", region_name="ap-northeast-2")
        for _ in range(60):
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [output["instance_id"]]}]
            )["InstanceInformationList"]
            if info and info[0]["PingStatus"] == "Online":
                break
            time.sleep(5)
        else:
            raise RuntimeError("SSM unavailable")
        reply = ssm.send_command(
            InstanceIds=[output["instance_id"]],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command], "executionTimeout": ["900"]},
        )
        for _ in range(180):
            try:
                result = ssm.get_command_invocation(
                    CommandId=reply["Command"]["CommandId"],
                    InstanceId=output["instance_id"],
                )
            except ssm.exceptions.InvocationDoesNotExist:
                time.sleep(5)
                continue
            if result["Status"] == "Success":
                break
            if result["Status"] not in ("Pending", "InProgress", "Delayed"):
                raise RuntimeError("VM deployment failed")
            time.sleep(5)
        else:
            raise RuntimeError("VM deployment timeout")
    else:
        run(
            [
                "gcloud",
                "compute",
                "ssh",
                output["instance_name"],
                "--project",
                data["connection"]["gcp_project"],
                "--zone",
                output["zone"],
                "--tunnel-through-iap",
                "--quiet",
                "--command",
                command,
            ],
            timeout=1000,
        )
    # Verify the private path independently from the deployment agent, not only localhost.
    url = f"http://{address}:8088"
    health(url + data["components"][0]["healthcheck_path"])
    if len(data["components"]) == 2:
        health(url + "/api/health")
    environment = {
        "name": "deploy-" + data["environment_id"][:12],
        "cloud": cloud,
        "region": data["target"]["region"],
        "version": data["commit_sha"],
        "instances": [
            {
                "id": str(output["instance_id"]),
                "name": output.get("instance_name", "app"),
                "zone": output.get("zone", ""),
            }
        ],
    }
    environment.update(
        {
            k: data["connection"][k]
            for k in ("account_id", "gcp_project")
            if k in data["connection"]
        }
    )
    return environment, url


def health(url):
    # No redirect can turn a failed health check into another service's healthy result.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(30):
        try:
            with opener.open(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            time.sleep(2)
            continue
        time.sleep(2)
    raise RuntimeError("Private endpoint health check failed")


def k3s_objects(data, selected):
    """Separate FE/BE Deployments preserve the existing UID-bound removal contract."""
    profile = next(
        p
        for p in OPTIONS["onprem"]["profiles"]
        if p["id"] == data["target"]["profile_id"]
    )
    count = len(data["components"])
    name = "app-" + UUID(data["environment_id"]).hex
    labels = {
        "app.kubernetes.io/managed-by": "delta-platform",
        "delta-platform/service-id": data["service_id"],
        "delta-platform/environment-id": data["environment_id"],
    }
    objects = []
    for index, component in enumerate(data["components"]):
        selected_labels = {**labels, "delta-platform/component": component["name"]}
        component_name = name + ("-be" if index else "")
        resources = {
            "cpu": str(
                (500 if data["target"]["profile_id"] == "small" else 1000) // count
            )
            + "m",
            "memory": str(profile["memory_mb"] // count) + "Mi",
        }
        container = {
            "name": component["name"],
            "image": component["image"],
            "ports": [{"containerPort": component["container_port"]}],
            "resources": {"requests": resources, "limits": resources},
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
            "readinessProbe": {
                "httpGet": {
                    "path": component["healthcheck_path"],
                    "port": component["container_port"],
                },
                "initialDelaySeconds": 5,
                "periodSeconds": 5,
            },
        }
        if not index and count == 2:
            container["env"] = [
                {"name": key, "value": value}
                for key, value in {
                    "API_HOST": name + "-be",
                    "API_PORT": "8080",
                    "API_PROTOCOL": "http",
                }.items()
            ]
        metadata = {
            "name": component_name,
            "namespace": selected["namespace"],
            "labels": selected_labels,
            "annotations": {"delta-platform/plan-hash": data["plan_hash"]},
        }
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": metadata,
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": selected_labels},
                "template": {
                    "metadata": {"labels": selected_labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "serviceAccountName": "delta-app",
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "runAsGroup": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "imagePullSecrets": [{"name": selected["image_pull_secret"]}],
                        "containers": [container],
                    },
                },
            },
        }
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "type": "ClusterIP" if index else "NodePort",
                "selector": selected_labels,
                "ports": [
                    {
                        "port": 8080 if index else 8088,
                        "targetPort": component["container_port"],
                    }
                ],
            },
        }
        objects.extend([deployment, service])
    ports = [
        {"protocol": "TCP", "port": c["container_port"]} for c in data["components"]
    ]
    within = {"podSelector": {"matchLabels": labels}}
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": name,
            "namespace": selected["namespace"],
            "labels": labels,
            "annotations": {"delta-platform/plan-hash": data["plan_hash"]},
        },
        "spec": {
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {"from": [within], "ports": ports},
                {
                    "from": [
                        {"ipBlock": {"cidr": str(ipaddress.ip_network(c))}}
                        for c in selected["ingress_cidrs"]
                    ],
                    "ports": [
                        {
                            "protocol": "TCP",
                            "port": data["components"][0]["container_port"],
                        }
                    ],
                },
            ],
            "egress": [
                {"to": [within], "ports": ports},
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": "kube-system"
                                }
                            },
                            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                        }
                    ],
                    "ports": [
                        {"protocol": "UDP", "port": 53},
                        {"protocol": "TCP", "port": 53},
                    ],
                },
            ],
        },
    }
    if not selected["ingress_cidrs"]:
        raise ValueError("Private ingress CIDRs required")
    return [policy, *objects]


def onprem(data, selected):
    namespace = selected["namespace"]
    if not re.fullmatch(r"delta-[a-z0-9-]{1,50}", namespace):
        raise ValueError("Owned namespace required")
    prefix = [
        "kubectl",
        "--kubeconfig",
        selected["kubeconfig"],
        "--request-timeout=20s",
    ]
    ns = json.loads(run(prefix + ["get", "namespace", namespace, "-o", "json"]))
    if (
        ns["metadata"]["uid"] != selected["namespace_uid"]
        or ns["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
        != "delta-platform"
    ):
        raise ValueError("Namespace identity mismatch")
    kube_system = json.loads(
        run(prefix + ["get", "namespace", "kube-system", "-o", "json"])
    )
    if kube_system["metadata"]["uid"] != selected["cluster_id"]:
        raise ValueError("Cluster identity mismatch")
    resources = []
    for item in k3s_objects(data, selected):
        kind, name = item["kind"], item["metadata"]["name"]
        existing = run(
            prefix
            + ["-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"]
        )
        if existing.strip():
            result = json.loads(existing)
            if (
                any(
                    result["metadata"].get("labels", {}).get(k) != v
                    for k, v in item["metadata"]["labels"].items()
                )
                or result["metadata"]
                .get("annotations", {})
                .get("delta-platform/plan-hash")
                != data["plan_hash"]
            ):
                raise ValueError("Existing resource ownership mismatch")
            if (
                kind == "Deployment"
                and result["spec"]["template"]["spec"]["containers"]
                != item["spec"]["template"]["spec"]["containers"]
            ):
                # Kubernetes adds defaults, so compare the pinned images and resource budget.
                actual = result["spec"]["template"]["spec"]["containers"]
                if len(actual) != len(
                    item["spec"]["template"]["spec"]["containers"]
                ) or any(
                    a["image"] != b["image"] or a["resources"] != b["resources"]
                    for a, b in zip(
                        actual,
                        item["spec"]["template"]["spec"]["containers"],
                        strict=True,
                    )
                ):
                    raise ValueError("Existing image/resources changed")
        else:
            result = json.loads(
                run(prefix + ["create", "-f", "-", "-o", "json"], body=json.dumps(item))
            )
        if kind in ("Deployment", "Service"):
            resources.append(
                {"kind": kind, "name": name, "uid": result["metadata"]["uid"]}
            )
        if kind == "Service" and result["spec"]["type"] == "NodePort":
            url = f"http://{ipaddress.ip_address(selected['private_ip'])}:{result['spec']['ports'][0]['nodePort']}"
    for resource in resources:
        if resource["kind"] == "Deployment":
            run(
                prefix
                + [
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    "deployment/" + resource["name"],
                    "--timeout=180s",
                ]
            )
    health(url + data["components"][0]["healthcheck_path"])
    if len(data["components"]) == 2:
        health(url + "/api/health")
    environment = {
        "name": "deploy-" + data["environment_id"][:12],
        "cloud": "onprem",
        "region": "onprem-local",
        "version": data["commit_sha"],
        "instances": [
            {"id": "onprem-" + UUID(selected["cluster_id"]).hex, "name": "delta-onprem"}
        ],
        "runtime": {
            "cluster_id": selected["cluster_id"],
            "namespace": namespace,
            "resources": resources,
        },
    }
    return environment, url


def main():
    os.umask(0o077)
    config = json.loads(
        Path(
            os.environ.get("DELTA_DEPLOY_CONFIG", "/etc/delta/deployment.json")
        ).read_text()
    )
    encoded = os.environ["DELTA_DEPLOYMENT"]
    if len(encoded) > 1_000_000:
        raise ValueError("Oversized request")
    payload = json.loads(base64.b64decode(encoded, validate=True))
    if payload.get("operation") == "remove":
        from remove import execute

        if payload["run_id"] != os.environ["DELTA_RUN_ID"]:
            raise ValueError("Cleanup request identity mismatch")
        UUID(payload["run_id"])
        Path("result.json").write_text(json.dumps(execute(payload, config)))
        return
    data, selected = request(encoded, config)
    if data["target"]["cloud"] == "onprem":
        environment, url = onprem(data, selected)
    else:
        environment, url = vm(data, selected, config)
    result = {
        "schema_version": 1,
        "run_id": data["run_id"],
        "plan_hash": data["plan_hash"],
        "environment_id": data["environment_id"],
        "target": data["target"],
        "images": [c["image"] for c in data["components"]],
        "commit_sha": data["commit_sha"],
        "health_passed": True,
        "environment": environment,
        "service_url": url,
    }
    Path("result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - redact provider/command bodies at the process boundary.
        print(
            "Deployment failed. Retain the environment-specific state and inspect the allocated resources; no source resources were deleted.",
            file=sys.stderr,
        )
        sys.exit(1)
