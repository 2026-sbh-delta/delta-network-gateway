"""compose-delivery-20261106: 외부 접근 없이 실행하는 독립 수용 시험."""

import pytest

from .delivery_support import canonical_hash, load_controller


@pytest.fixture
def bundle():
    digests = {"frontend": "sha256:" + "a" * 64, "backend": "sha256:" + "b" * 64}
    core = {"schema": 2, "commit": "c" * 40, "platform": "linux/amd64", "image_digests": digests}
    images = {service: f"registry.invalid/app/{service}@{digest}" for service, digest in digests.items()}
    return {**core, "images": images, "bundle_sha256": canonical_hash(core),
            "image_ref": images["frontend"], "image_digest": digests["frontend"]}


@pytest.fixture
def release_request(bundle):
    target = {
        "target_id": "fixture-gcp", "cloud": "gcp", "environment": "production",
        "project": "fixture-project", "region": "fixture-region", "zone": "fixture-zone",
        "vm_name": "fixture-vm", "private_ip": "192.0.2.10", "connection": "iap",
        "os_user": "fixture-user", "compose_project": "fixture-app",
        "install_dir": "/fixture/app", "frontend_url": "http://127.0.0.1:3000",
        "frontend_port": 3000, "backend_port": 8080,
        "db_name": "fixture_db", "db_major": 18,
        "db_secret_ref": "fixture-only-not-a-real-secret",
        "log_driver": "gcplogs", "log_project": "fixture-project",
    }
    return {"deployment_id": "deployment-a", "commit_sha": bundle["commit"],
            "bundle": bundle, "target": target, "config_sha256": canonical_hash(target)}


@pytest.fixture
def release_module():
    return load_controller("release_deploy", optional=True)
