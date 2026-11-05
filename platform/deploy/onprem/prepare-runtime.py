"""Prepare the platform-owned runtime namespace from verified release evidence."""

import argparse
import base64
import importlib.util
import json
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import yaml

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "release_renderer", ROOT / "render-release.py"
)
release_renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_renderer)


def main():
    p = argparse.ArgumentParser(description="검증 이미지의 전용 운영 후보 구성")
    p.add_argument("--host", required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    auth_group = p.add_mutually_exclusive_group(required=True)
    auth_group.add_argument(
        "--registry-auth", type=Path, help="registry-auth.py가 만든 읽기 전용 인증 파일"
    )
    auth_group.add_argument("--profile", help="ECR pull 권한만 가진 AWS 프로필")
    p.add_argument("--region", default="ap-northeast-2")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--instrumentation", choices=("off", "nodejs"), default="off")
    p.add_argument("--collector-endpoint")
    p.add_argument(
        "--postgres-image",
        help="선택: PostgreSQL 16 Alpine의 고정 digest. 최초 복원 전 BE를 중지한다.",
    )
    a = p.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", a.host):
        raise ValueError("Invalid host")
    if a.postgres_image and not re.fullmatch(
        r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", a.postgres_image
    ):
        raise ValueError("Pinned PostgreSQL image required")
    objects = release_renderer.render(
        json.loads(a.result.read_text()),
        preview=a.preview,
        instrumentation=a.instrumentation,
        collector_endpoint=a.collector_endpoint,
    )
    ssh = [
        "ssh",
        "-i",
        str(a.key.expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        a.host,
    ]

    def remote(command, body=None):
        r = subprocess.run(
            ssh + ["sudo -n k3s kubectl " + command],
            input=body,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if r.returncode:
            raise RuntimeError("Runtime operation failed; inspect the owned namespace")
        return r.stdout

    def owned(kind, name):
        old = remote(
            f"get {kind} {name} -n delta-notes --ignore-not-found -o json"
        ).strip()
        item = json.loads(old) if old else None
        if (
            item
            and item["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
            != "delta-platform"
        ):
            raise ValueError("Foreign resource preserved")
        return item

    for obj in objects:
        owned(obj["kind"], obj["metadata"]["name"])

    existing_backend = owned("deployment", "backend")
    if existing_backend and any(
        env.get("name") == "DATABASE_URL"
        for container in existing_backend["spec"]["template"]["spec"]["containers"]
        for env in container.get("env", [])
    ):
        raise ValueError(
            "Database-backed runtime already prepared; update its saved release YAML instead"
        )

    def apply(items):
        remote("apply -f -", yaml.safe_dump_all(items, sort_keys=False))

    apply([o for o in objects if o["kind"] not in ("Deployment", "Service")])

    def secret(name, values, kind="Opaque", refresh=False):
        existing = owned("secret", name)
        if existing and not refresh:
            return {
                k: base64.b64decode(v).decode() for k, v in existing["data"].items()
            }
        apply(
            [
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": name,
                        "namespace": "delta-notes",
                        "labels": {"app.kubernetes.io/managed-by": "delta-platform"},
                    },
                    "type": kind,
                    "stringData": values,
                }
            ]
        )
        return values

    if a.registry_auth:
        if a.registry_auth.stat().st_size > 65536:
            raise ValueError("Registry credential file is too large")
        auth = json.loads(a.registry_auth.read_text())
    else:
        session = boto3.Session(profile_name=a.profile, region_name=a.region)
        auth = session.client("ecr").get_authorization_token()["authorizationData"][0]
    registry = auth["proxyEndpoint"].removeprefix("https://")
    for obj in objects:
        if obj["kind"] == "Deployment" and not obj["spec"]["template"]["spec"][
            "containers"
        ][0]["image"].startswith(registry + "/"):
            raise ValueError("Release is not in the authenticated ECR registry")
    secret(
        "registry",
        {
            ".dockerconfigjson": json.dumps(
                {"auths": {registry: {"auth": auth["authorizationToken"]}}}
            )
        },
        "kubernetes.io/dockerconfigjson",
        refresh=True,
    )
    if a.postgres_image:
        secret("database-admin", {"password": secrets.token_urlsafe(32)})
        database = list(
            yaml.safe_load_all((ROOT / "manifests/runtime/database.yaml").read_text())
        )
        for item in database:
            owned(item["kind"], item["metadata"]["name"])
            if item["kind"] == "StatefulSet":
                item["spec"]["template"]["spec"]["containers"][0]["image"] = (
                    a.postgres_image
                )
        apply(database)
        remote("rollout status statefulset/database -n delta-notes --timeout=120s")
        credentials = secret(
            "database-app",
            {
                "url": "postgres://notes:"
                + secrets.token_urlsafe(32)
                + "@database:5432/notes"
            },
        )
        password = urlsplit(credentials["url"]).password
        if not password or not re.fullmatch(r"[A-Za-z0-9_-]{30,80}", password):
            raise ValueError("Unexpected database credential format")

        def sql(text):
            return remote(
                "exec -i database-0 -n delta-notes -- psql -U postgres -At -v ON_ERROR_STOP=1",
                text,
            ).strip()

        if sql("SELECT count(*) FROM pg_roles WHERE rolname='notes';") == "0":
            sql("CREATE ROLE notes LOGIN PASSWORD '" + password + "';")
        if sql("SELECT count(*) FROM pg_database WHERE datname='notes';") == "0":
            sql("CREATE DATABASE notes OWNER notes;")
        tables = remote(
            "exec -i database-0 -n delta-notes -- psql -U postgres -d notes -At -v ON_ERROR_STOP=1",
            "SELECT count(*) FROM pg_tables WHERE schemaname='public';",
        ).strip()
        if tables != "0":
            raise ValueError(
                "Existing database preserved; use a separate release update, not initial restore preparation"
            )
        for obj in objects:
            if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "backend":
                obj["spec"]["replicas"] = 0
                obj["spec"]["template"]["spec"]["containers"][0]["env"].append(
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": {
                            "secretKeyRef": {"name": "database-app", "key": "url"}
                        },
                    }
                )
    a.output.write_text(yaml.safe_dump_all(objects, sort_keys=False))
    apply(objects)
    print("전용 운영 후보 구성 완료. DB 사용 시 BE는 복원 확인 전 중지 상태입니다.")


if __name__ == "__main__":
    main()
