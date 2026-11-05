"""배포 실행기가 공유하는 비밀 없는 이미지 묶음 계약."""
import hashlib
import json
import re

SERVICES = {"frontend", "backend"}
IMAGE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def bundle_from_images(commit, images, bundle_sha256):
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("full40 commit이 필요하다")
    if not isinstance(images, dict) or set(images) != SERVICES:
        raise ValueError("frontend와 backend 이미지가 모두 필요하다")
    if any(not isinstance(ref, str) or not IMAGE_RE.fullmatch(ref) for ref in images.values()):
        raise ValueError("두 이미지 모두 manifest digest로 고정해야 한다")
    digests = {name: ref.rsplit("@", 1)[1] for name, ref in images.items()}
    core = {"schema": 2, "commit": commit, "platform": "linux/amd64", "image_digests": digests}
    if canonical_hash(core) != bundle_sha256:
        raise ValueError("이미지 묶음 해시가 다르다")
    return {**core, "images": dict(images), "bundle_sha256": bundle_sha256,
            "image_ref": images["frontend"], "image_digest": digests["frontend"]}


def validate_bundle(bundle, commit):
    if not isinstance(bundle, dict):
        raise ValueError("이미지 묶음이 필요하다")
    expected = bundle_from_images(commit, bundle.get("images"), bundle.get("bundle_sha256"))
    if type(bundle.get("schema")) is not int or any(bundle.get(k) != v for k, v in expected.items()):
        raise ValueError("이미지 묶음의 commit/schema/platform/digest/별칭이 다르다")
    if "registry_images" in bundle:
        copies = bundle["registry_images"]
        if not isinstance(copies, dict) or set(copies) - {"aws", "gcp"}:
            raise ValueError("레지스트리 복사 결과가 올바르지 않다")
        for images in copies.values():
            bundle_from_images(commit, images, expected["bundle_sha256"])
    return expected
