"""운영 배포기 시험 공용 도구(conftest 이름 충돌을 피해 별도 모듈로 둔다)."""

import importlib
import sys
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
if str(RELEASE) not in sys.path:
    sys.path.insert(0, str(RELEASE))

ENVIRONMENT_ID = "00000000-0000-4000-8000-000000000001"
SERVICE_ID = "00000000-0000-4000-8000-000000000002"


def load(name):
    """모듈 간 상태(errors.current 등)가 시험 사이에 남지 않게 새로 읽는다."""
    for module in ("errors", "state_backend", "targets", name):
        sys.modules.pop(module, None)
    return importlib.import_module(name)


def config_value(tmp_path):
    return {
        "state_bucket": "fixture-state-bucket",
        "state_region": "ap-northeast-2",
        "work_root": str(tmp_path / "work"),
        "allowed_image_prefixes": [
            "asia-northeast3-docker.pkg.dev/fixture/app/frontend@",
            "asia-northeast3-docker.pkg.dev/fixture/app/backend@",
            "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/app@",
        ],
        "connections": {
            "aws": {
                "identity": {"revision": "v1", "account_id": "111122223333"},
                "region": "ap-northeast-2",
                "assume_role_arn": "arn:aws:iam::111122223333:role/delta-prod-deploy",
                "external_id": "fixture-external-id",
                "management_transport": "ssm",
                "terraform": {
                    "account_id": "111122223333", "ami_id": "ami-0123456789abcdef0",
                    "subnet_id": "subnet-0123456789abcdef0", "security_group_ids": ["sg-0123456789abcdef0"],
                    "instance_profile": "delta-app-pull-ssm",
                },
            },
            "gcp": {
                "identity": {"revision": "v1", "gcp_project": "fixture-project"},
                "management_transport": "iap",
                "ssh_service_account": "prod-deploy@fixture-project.iam.gserviceaccount.com",
                "artifact_repository": "asia-northeast3-docker.pkg.dev/fixture/app/copied",
                "terraform": {
                    "project_id": "fixture-project", "zone": "asia-northeast3-a",
                    "subnetwork": "projects/fixture-project/regions/asia-northeast3/subnetworks/app",
                    "service_account": "app-vm@fixture-project.iam.gserviceaccount.com",
                    "image": "projects/debian-cloud/global/images/debian-12-fixture", "network_tag": "delta-app",
                },
            },
        },
        "databases": {
            "gcp-postgres18": {"revision": "v1", "modes": ["new"], "clouds": ["gcp"], "server_mode": "create",
                               "ca_pem": "PUBLIC CA", "terraform": {"project_id": "fixture-project",
                                                                     "network": "net", "subnetwork": "sub"}},
        },
    }


def components(registry="asia-northeast3-docker.pkg.dev/fixture/app"):
    return [
        {"name": "frontend", "image": f"{registry}/frontend@sha256:" + "a" * 64, "image_digest": "sha256:" + "a" * 64,
         "commit_sha": "c" * 40, "container_port": 3000, "healthcheck_path": "/healthz"},
        {"name": "backend", "image": f"{registry}/backend@sha256:" + "b" * 64, "image_digest": "sha256:" + "b" * 64,
         "commit_sha": "c" * 40, "container_port": 8080, "healthcheck_path": "/healthz"},
    ]


def data_value():
    return {
        "schema_version": 1, "run_id": "00000000-0000-4000-8000-0000000000aa",
        "environment_id": ENVIRONMENT_ID, "service_id": SERVICE_ID,
        "project_id": "00000000-0000-4000-8000-0000000000bb", "plan_id": "00000000-0000-4000-8000-0000000000cc",
        "plan_hash": "d" * 64, "commit_sha": "c" * 40,
        "target": {"cloud": "gcp", "region": "asia-northeast3", "profile_id": "e2-small"},
        "connection": {"revision": "v1", "gcp_project": "fixture-project"},
        "components": components(),
        "database": {"profile_id": "gcp-postgres18", "revision": "v1", "engine": "postgres", "mode": "new"},
    }
