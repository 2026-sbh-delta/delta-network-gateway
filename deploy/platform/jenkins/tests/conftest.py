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
    # vm_release 인벤토리 형식: 비밀·네트워크 값 없이 대상·사양·DB 선택만 둔다.
    target = {
        "target_id": "fixture-gcp", "cloud": "gcp", "environment": "production",
        "region": "asia-northeast3", "profile_id": "e2-small",
        "database": {"profile_id": "fixture-postgres18", "mode": "new"},
    }
    return {"deployment_id": "deployment-a", "commit_sha": bundle["commit"],
            "bundle": bundle, "target": target, "config_sha256": canonical_hash(target)}


@pytest.fixture
def release_module():
    return load_controller("release_deploy", optional=True)
