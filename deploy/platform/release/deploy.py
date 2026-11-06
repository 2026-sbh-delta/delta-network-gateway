"""운영 배포기. 동규님 platform/deploy/workloads/deploy.py 이식.

환경 ID마다 Terraform으로 사설 VM 생성 → DB(new/existing) 준비 → VM에서 Docker Compose 실행 → 헬스·이미지 관측.
변경점: delta_platform 패키지 의존 제거(targets.py·registry_copy.py), 우리 S3 원격 state(state_backend.py),
대상 계정 위임(aws_vm.py), PostgreSQL 18 관측, awslogs/gcplogs, 실패 시 result.json의 stage·code.
설정은 신뢰된 서버 파일(DELTA_DEPLOY_CONFIG)에서만 읽고 요청은 DELTA_DEPLOYMENT(base64 JSON)로 받는다.
"""

import base64
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import state_backend  # noqa: E402
from errors import CommandError, DeployError, current, fail, step  # noqa: E402
from targets import OPTIONS, DeploymentTarget  # noqa: E402

AR_HOST = re.compile(r"[a-z0-9-]+-docker\.pkg\.dev")
ECR_HOST = re.compile(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com")


def run(args, *, body=None, cwd=None, timeout=600, env=None):
    result = subprocess.run(
        args,
        check=False,
        input=body,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode:
        # Upstream Terraform/SSM/kubectl output can contain customer secrets.
        raise CommandError(args)
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
    if target.cloud == "aws" and selected.get("region", target["region"]) != target["region"]:
        raise ValueError("AWS region mismatch")
    if not 1 <= len(data["components"]) <= 2:
        raise ValueError("Invalid component count")
    for component in data["components"]:
        image = component["image"]
        if (
            not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}", image)
            or image.rsplit("@", 1)[0] + "@" not in config["allowed_image_prefixes"]
        ):
            raise DeployError("request", "image_not_allowed")
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
    if data.get("database"):
        from database import profile_for

        profile_for(data, config)
    return data, selected


def target_images(data, selected, config):
    """대상 레지스트리에 이미 있는 이미지(묶음의 registry_images)는 복사하지 않는다.

    GCP 대상에 ECR 이미지만 있을 때만 동규님 방식대로 같은 digest로 Artifact Registry에 복사한다.
    AWS VM은 ECR만 받는다(ECR로 복사하는 경로는 없다).
    """
    cloud = data["target"]["cloud"]
    images, copier = [], None
    for component in data["components"]:
        image = component["image"]
        host = image.split("/", 1)[0]
        if cloud == "gcp" and not AR_HOST.fullmatch(host):
            if not ECR_HOST.fullmatch(host):
                fail("registry", "image_registry_unsupported")
            if copier is None:
                import google.auth
                from registry_copy import RegistryCopy

                from database import AuthorizedSession

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
            image = copier.copy(image, lambda: None)
        elif cloud == "aws" and not ECR_HOST.fullmatch(host):
            fail("registry", "image_registry_unsupported")
        images.append(image)
    return images


def vm_params(data, selected):
    cloud, target = data["target"]["cloud"], data["target"]
    params = {
        **selected["terraform"],
        "environment_id": data["environment_id"],
        "service_id": data["service_id"],
        "profile_id": target["profile_id"],
        "region": target["region"],
    }
    if cloud == "aws":
        if params["account_id"] != data["connection"]["account_id"]:
            raise ValueError("AWS account mismatch")
        params.update(
            assume_role_arn=selected["assume_role_arn"],
            external_id=selected["external_id"],
        )
    if cloud == "gcp" and params["project_id"] != data["connection"]["gcp_project"]:
        raise ValueError("GCP project mismatch")
    return params


def provision(data, selected, config):
    cloud = data["target"]["cloud"]
    with step("provision_vm", "terraform_failed"):
        params = vm_params(data, selected)
        directory = state_backend.workdir(config, data["environment_id"], "vm", cloud)
        variables = directory / "deployment.auto.tfvars.json"
        variables.write_text(json.dumps(params))
        variables.chmod(0o600)
        environment = state_backend.env(config)
        run(state_backend.init_command(config, data["environment_id"], "vm"), cwd=directory, env=environment)
        run(["terraform", "plan", "-input=false", "-out=deployment.tfplan"], cwd=directory, env=environment)
        plan = json.loads(
            run(["terraform", "show", "-json", "deployment.tfplan"], cwd=directory, env=environment)
        )
        for change in plan.get("resource_changes", []):
            if any(
                action not in ("no-op", "create", "read")
                for action in change["change"]["actions"]
            ):
                # 기존 VM 수정·교체·삭제는 이 흐름에서 거부한다(새 환경 ID로 새 VM을 만든다).
                raise DeployError("provision_vm", "plan_not_create_only")
        run(["terraform", "apply", "-input=false", "deployment.tfplan"], cwd=directory, env=environment, timeout=1200)
        return {
            key: value["value"]
            for key, value in json.loads(
                run(["terraform", "output", "-json"], cwd=directory, env=environment)
            ).items()
        }


def logging_config(data, selected):
    """VM 컨테이너 로그를 클라우드 로그로 보낸다. 중앙 수집은 /delta-cp/central/* 경로가 맡는다."""
    if data["target"]["cloud"] == "aws":
        return {
            "driver": "awslogs",
            "options": {
                "awslogs-region": selected.get("region", data["target"]["region"]),
                "awslogs-group": "/delta/workload/" + data["environment_id"],
                "awslogs-create-group": "true",
                "mode": "non-blocking",
                "tag": "{{.Name}}",
            },
        }
    return {
        "driver": "gcplogs",
        "options": {
            "gcp-project": selected["identity"]["gcp_project"],
            "mode": "non-blocking",
            "gcp-log-cmd": "false",
        },
    }


def record_store(data, aws):
    """prepare()의 앱 DB 자격 증명 기록 위치. 대상과 같은 클라우드·계정에 둔다."""
    cloud = data["target"]["cloud"]
    if cloud == "aws":
        return aws.client("secretsmanager")
    if cloud == "gcp":
        from database import GcpSecretStore

        return GcpSecretStore(data["connection"]["gcp_project"])
    import boto3

    return boto3.client("secretsmanager", region_name=OPTIONS["aws"]["region"])


def loopback_connector(port_for):
    from database import connect

    def connector(value, **kwargs):
        # Only this connection uses loopback. Stored app credentials retain
        # their original DB hostname/port, including TLS hostname verification.
        return connect(
            {**value, "hostaddr": "127.0.0.1", "port": port_for(value)},
            **kwargs,
        )

    return connector


def last_json(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("VM runtime did not report")


def vm(data, selected, config):
    with ExitStack() as stack:
        return deploy_vm(data, selected, config, stack)


def deploy_vm(data, selected, config, stack, *, sleep=time.sleep):
    from preflight import check_prepared_database

    with step("preflight", "database_unreachable"):
        check_prepared_database(data, config)
    cloud = data["target"]["cloud"]
    with step("registry", "registry_copy_failed"):
        images = target_images(data, selected, config)
    aws = None
    if cloud == "aws":
        from aws_vm import session

        with step("management", "aws_assume_role_failed"):
            aws = session(selected)
    output = provision(data, selected, config)
    address = str(ipaddress.ip_address(output["private_ip"]))
    profile = next(
        p for p in OPTIONS[cloud]["profiles"] if p["id"] == data["target"]["profile_id"]
    )
    payload = {
        "environment_id": data["environment_id"],
        "cloud": cloud,
        "region": data["target"]["region"],
        "images": images,
        "components": data["components"],
        "memory_mb": profile["memory_mb"] - 384,
        "logging": logging_config(data, selected),
        "runtime_env": {
            c["name"]: {
                "APP_ENV": "production",
                "APP_VERSION": data["commit_sha"],
                "CLOUD_PROVIDER": cloud,
                "IMAGE_DIGEST": c["image_digest"],
                "PORT": str(c["container_port"]),
            }
            for c in data["components"]
        },
    }
    ssm = None
    if cloud == "aws":
        from aws_vm import wait_online

        with step("management", "ssm_unavailable"):
            ssm = wait_online(aws, output["instance_id"], sleep=sleep)

    database_profile = None
    if data.get("database"):
        from database import profile_for

        database_profile = profile_for(data, config)
        if database_profile.get("server_mode", "prepared") == "create":
            from database_server import provision as provision_database

            with step("database_server", "database_server_failed"):
                database_profile = provision_database(data, database_profile, config, run, selected)

    ports, connector = None, None
    if cloud == "gcp" and selected.get("management_transport") == "iap":
        from gcp_vm import forward

        endpoint = database_profile.get("iap_endpoint") if database_profile else None
        if database_profile and not endpoint:
            fail("management", "iap_database_endpoint_missing")
        with step("management", "iap_tunnel_failed"):
            # 새 VM의 sshd·OS Login이 준비될 때까지 몇 번 재시도한다.
            for attempt in range(6):
                try:
                    ports = stack.enter_context(forward(output, selected, endpoint))
                    break
                except RuntimeError:
                    if attempt == 5:
                        raise
                    sleep(15)
        if database_profile:
            connector = loopback_connector(lambda value: ports["database"])
    elif cloud == "aws" and database_profile and selected.get("management_transport") == "ssm":
        from aws_vm import forward as ssm_forward

        opened = {}

        def port_for(value):
            key = (value["host"], int(value.get("port", 5432)))
            if key not in opened:
                with step("management", "ssm_tunnel_failed"):
                    opened[key] = stack.enter_context(ssm_forward(aws, output["instance_id"], *key))
            return opened[key]

        connector = loopback_connector(port_for)

    version = None
    if database_profile:
        from database import deliver, prepare

        with step("database_prepare", "postgres_prepare_failed"):
            secret_id, value, version = prepare(
                data, database_profile, client=record_store(data, aws), connector=connector
            )
        if type(version) is not int or version // 10000 != 18:
            fail("database_prepare", "postgres_version")
        with step("database_secret", "secret_delivery_failed"):
            payload["database_secret"] = deliver(data, selected, secret_id, value)
    # Only secret references enter SSM command text. The VM resolves them with its identity.
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
    with step("vm_runtime", "vm_runtime_failed"):
        if cloud == "aws":
            from aws_vm import run_command

            stdout = run_command(ssm, output["instance_id"], command, sleep=sleep)
        else:
            from gcp_vm import ssh

            stdout = ssh(
                [
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
                selected["ssh_service_account"],
                run,
            )
    with step("observe", "observation_invalid"):
        report = last_json(stdout)
    observation = verify_observation(data, images, report, version)
    # IAP verifies the application on the destination VM, not public LB ingress.
    # Keep the real private endpoint in the environment record, never the local tunnel.
    url = f"http://{address}:8088"
    if ports:
        with step("observe", "private_health_failed"):
            check_url = f"http://127.0.0.1:{ports['health']}"
            health(check_url + data["components"][0]["healthcheck_path"])
            if len(data["components"]) == 2:
                health(check_url + "/api/health")
            if data.get("database"):
                database_health(check_url)
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
    return environment, url, {**observation, "database_version_num": version}


def verify_observation(data, images, report, version):
    """VM이 관측한 실행 이미지·FE→BE→DB 상태가 요청과 같은지 확인한다."""
    names = ["frontend", "backend"][: len(data["components"])]
    expected = dict(zip(names, images))
    if report.get("environment_id") != data["environment_id"] or report.get("images") != expected:
        fail("observe", "image_mismatch")
    health_value = report.get("health") or {}
    if len(data["components"]) == 2:
        if (
            health_value.get("status") != "ok"
            or health_value.get("version") != data["commit_sha"]
            or health_value.get("environment", "production") != "production"
            or health_value.get("cloudProvider", data["target"]["cloud"]) != data["target"]["cloud"]
        ):
            fail("observe", "health_mismatch")
        if data.get("database"):
            reported = health_value.get("databaseVersionNum")
            if health_value.get("storage") != "postgres" or (
                reported is not None and reported != version
            ):
                fail("observe", "database_health_mismatch")
    return {"images": expected, "health": health_value}


def database_health(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url + "/api/health", timeout=10) as response:
        result = json.loads(response.read(65536))
    if result.get("storage") != "postgres":
        raise RuntimeError("Application is not connected to PostgreSQL")


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
        container.setdefault("env", []).extend(
            [
                {"name": "APP_ENV", "value": "onprem"},
                {"name": "APP_VERSION", "value": data["commit_sha"]},
            ]
        )
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


def onprem(data, selected, config=None):
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
    objects = k3s_objects(data, selected)
    if data.get("database"):
        from database import egress, kubernetes_secret, prepare, profile_for

        profile = profile_for(data, config)
        _, value, _version = prepare(data, profile)
        secret = kubernetes_secret(data, selected, value)
        for item in objects:
            if item["kind"] == "NetworkPolicy":
                item["spec"]["egress"].append(egress(profile))
            if (
                item["kind"] == "Deployment"
                and item["metadata"]["labels"]["delta-platform/component"] == "backend"
            ):
                spec = item["spec"]["template"]["spec"]
                container = spec["containers"][0]
                container["env"] += [
                    {
                        "name": key,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": secret["metadata"]["name"],
                                "key": key,
                            }
                        },
                    }
                    for key in secret["stringData"]
                    if key != "ca.pem"
                ]
                container["volumeMounts"] = [
                    {"name": "db-ca", "mountPath": "/run/delta-db", "readOnly": True}
                ]
                spec["volumes"] = [
                    {
                        "name": "db-ca",
                        "secret": {
                            "secretName": secret["metadata"]["name"],
                            "items": [{"key": "ca.pem", "path": "ca.pem"}],
                        },
                    }
                ]
        objects.insert(0, secret)
    for item in objects:
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
    if data.get("database"):
        database_health(url)
    return environment, url


def deploy(data, selected, config):
    if data["target"]["cloud"] == "onprem":
        environment, url = onprem(data, selected, config)
        observation = {"images": None, "health": {}, "database_version_num": None}
    else:
        environment, url, observation = vm(data, selected, config)
    return {
        "state": "verified",
        "schema_version": 1,
        "run_id": data["run_id"],
        "plan_hash": data["plan_hash"],
        "environment_id": data["environment_id"],
        "target": data["target"],
        "images": [c["image"] for c in data["components"]],
        "commit_sha": data["commit_sha"],
        "health_passed": True,
        "observed": {"images": observation["images"], "health": observation["health"]},
        "database": {
            **data["database"],
            "connected": True,
            "retention": "retain",
            "server_version_num": observation["database_version_num"],
        }
        if data.get("database")
        else None,
        "environment": environment,
        "service_url": url,
    }


def main():
    """성공·실패 모두 result.json을 남긴다. 실패는 stage·code만 담고 명령 출력·비밀은 버린다."""
    os.umask(0o077)
    current["stage"] = "request"
    identity = {}
    try:
        with step("request", "config_unreadable"):
            config = json.loads(
                Path(
                    os.environ.get("DELTA_DEPLOY_CONFIG", "/run/delta/release-config.json")
                ).read_text()
            )
        encoded = os.environ["DELTA_DEPLOYMENT"]
        if len(encoded) > 1_000_000:
            raise DeployError("request", "request_oversized")
        with step("request", "request_invalid"):
            payload = json.loads(base64.b64decode(encoded, validate=True))
        if payload.get("operation") == "remove":
            from remove import execute

            with step("request", "request_invalid"):
                if payload["run_id"] != os.environ["DELTA_RUN_ID"]:
                    raise ValueError("Cleanup request identity mismatch")
                UUID(payload["run_id"])
            with step("remove", "cleanup_failed"):
                result = {"state": "removed", **execute(payload, config)}
        else:
            with step("request", "request_invalid"):
                data, selected = request(encoded, config)
            identity = {"run_id": data["run_id"], "environment_id": data["environment_id"]}
            result = deploy(data, selected, config)
    except DeployError as error:
        result = {"state": "failed", "stage": error.stage, "code": error.code, **identity}
    except Exception as error:  # noqa: BLE001 - redact provider/command bodies at the process boundary.
        from preflight import DeploymentPreflightError

        code = "database_unreachable" if isinstance(error, DeploymentPreflightError) else "unexpected"
        result = {"state": "failed", "stage": current["stage"], "code": code, **identity}
    Path("result.json").write_text(json.dumps(result))
    return 0 if result["state"] in ("verified", "removed") else 1


if __name__ == "__main__":
    status = main()
    if status:
        print(
            "Deployment failed. Retain the environment-specific state and inspect the allocated resources; no source resources were deleted.",
            file=sys.stderr,
        )
    sys.exit(status)
