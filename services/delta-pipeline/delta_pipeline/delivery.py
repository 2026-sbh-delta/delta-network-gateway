"""이미지 묶음 검증과 승인 서명. 파일·환경·네트워크에서 비밀을 찾지 않는다."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from delta_pipeline.clients import evidence_hash

SERVICES = {"frontend", "backend"}
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_bundle(value: Mapping[str, Any], commit: str) -> dict[str, Any]:
    if type(value.get("schema")) is not int or value["schema"] != 2:
        raise ValueError("이미지 묶음 schema가 2가 아니다")
    if value.get("commit") != commit or value.get("platform") != "linux/amd64":
        raise ValueError("이미지 묶음의 커밋·플랫폼이 요청과 다르다")
    images, digests = value.get("images"), value.get("image_digests")
    if not isinstance(images, dict) or not isinstance(digests, dict) or set(images) != SERVICES or set(digests) != SERVICES:
        raise ValueError("frontend·backend 두 이미지와 digest가 필요하다")
    for service in SERVICES:
        image, digest = images[service], digests[service]
        if (not isinstance(image, str) or not IMAGE_RE.fullmatch(image)
                or not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest)
                or image.rsplit("@", 1)[1] != digest):
            raise ValueError("이미지 ref와 digest가 일치하지 않는다")
    core = {"schema": 2, "commit": commit, "platform": "linux/amd64", "image_digests": dict(digests)}
    digest = evidence_hash(core)
    if value.get("bundle_sha256") != digest:
        raise ValueError("이미지 묶음의 정규 JSON 해시가 다르다")
    if value.get("image_ref") != images["frontend"] or value.get("image_digest") != digests["frontend"]:
        raise ValueError("frontend 별칭이 이미지 묶음과 다르다")
    # 클라우드별 사본(AR·ECR). 레지스트리 주소만 다르고 서비스별 digest는 묶음과 같아야 한다.
    registries = value.get("registry_images") or {"gcp": dict(images)}
    if (not isinstance(registries, dict)
            or any(not isinstance(refs, dict) or set(refs) != SERVICES
                   or any(not isinstance(refs[s], str) or not IMAGE_RE.fullmatch(refs[s])
                          or refs[s].rsplit("@", 1)[1] != digests[s] for s in SERVICES)
                   for refs in registries.values())):
        raise ValueError("클라우드별 이미지 사본의 digest가 묶음과 다르다")
    return {**core, "images": dict(images), "bundle_sha256": digest,
            "image_ref": images["frontend"], "image_digest": digests["frontend"],
            "registry_images": {cloud: dict(refs) for cloud, refs in registries.items()}}


def match_runtime(value: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    """runtime 결과는 schema/platform을 생략할 수 있지만 두 이미지·커밋·해시는 필수다."""
    for key in ("commit", "images", "image_digests", "bundle_sha256", "image_digest"):
        if value.get(key) != expected[key]:
            raise ValueError(f"실행 결과의 {key}가 빌드 묶음과 다르다")
    for key in ("schema", "platform", "image_ref"):
        if key in value and value[key] != expected[key]:
            raise ValueError(f"실행 결과의 {key}가 빌드 묶음과 다르다")


def require_postgres18(value: Mapping[str, Any]) -> None:
    version = value.get("database_version_num")
    if type(version) is not int or version // 10000 != 18:
        raise ValueError("실제 연결 DB의 PostgreSQL18 정수 버전 증거가 없다")


def validate_config(target: str, config: dict[str, Any] | None) -> dict[str, Any]:
    if (not isinstance(config, dict) or config.get("target_id") != target
            or config.get("environment") != "production" or config.get("cloud") not in ("aws", "gcp", "onprem")):
        raise ValueError("운영 대상과 일치하는 production 인벤토리가 없다")
    # JSON 복사로 승인 당시의 설정을 고정한다. 비밀 원문은 호출자가 이 계약에 넣지 않는다.
    return json.loads(canonical_json(config))


def sign_approval(claims: dict[str, Any], key: bytes | None) -> dict[str, Any]:
    if not isinstance(key, bytes) or not key:
        raise ValueError("운영 승인 Ed25519 서명 키가 없다")
    try:
        private = (Ed25519PrivateKey.from_private_bytes(key) if len(key) == 32 else
                   serialization.load_pem_private_key(key, password=None))
        if not isinstance(private, Ed25519PrivateKey):
            raise ValueError("Ed25519 키가 아니다")
        signature = private.sign(canonical_json(claims).encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ValueError("운영 승인 Ed25519 서명 키를 사용할 수 없다") from exc
    return {"claims": claims, "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")}
