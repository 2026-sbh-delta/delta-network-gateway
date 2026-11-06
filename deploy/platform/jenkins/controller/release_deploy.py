"""사람 승인 검증·원자 예약 후 신뢰된 단일 운영 대상에 배포한다.

배포 실행은 vm_release(동규님 방식: 환경별 Terraform VM → DB → Compose → 헬스·관측)가 맡는다.
실패 결과에는 비밀·명령 출력 없이 stage·code만 남긴다.
"""
import base64
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from delivery_contract import bundle_from_images, canonical, canonical_hash, validate_bundle
from vm_release import validate_target

FAILURE_CODES = {"inventory": "inventory_invalid", "bundle": "bundle_invalid", "approval": "approval_rejected",
                 "deploy": "deploy_error", "verify": "observation_mismatch"}


def verify_and_reserve(request, token, public_key_pem, db_path, *, now):
    """클라우드·비밀 조회 없이 검증하고 nonce/identity를 한 transaction에서 예약한다."""
    try:
        dep, target, bundle = request["deployment_id"], request["target"], request["bundle"]
        if not isinstance(dep, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dep):
            raise ValueError("deployment_id가 올바르지 않다")
        validate_bundle(bundle, request["commit_sha"])
        if (not isinstance(target, dict) or target.get("environment") != "production"
                or target.get("cloud") not in ("gcp", "aws", "onprem")
                or not isinstance(target.get("target_id"), str) or not target["target_id"]
                or canonical_hash(target) != request["config_sha256"]):
            raise ValueError("승인 대상 설정이 올바르지 않다")
        validate_target(target)
        claims = token["claims"]
        expected = {"deployment_id": dep, "commit_sha": request["commit_sha"],
                    "bundle_sha256": bundle["bundle_sha256"], "target_id": target["target_id"],
                    "environment": "production", "config_sha256": request["config_sha256"]}
        if (not isinstance(claims, dict) or any(claims.get(k) != v for k, v in expected.items())
                or type(claims.get("exp")) is not int or not now < claims["exp"] <= now + 600
                or not isinstance(claims.get("nonce"), str) or not claims["nonce"].strip()):
            raise ValueError("승인 claims 또는 만료 시간이 올바르지 않다")
        signature = token["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
            raise ValueError("승인 서명이 올바르지 않다")
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Ed25519 공개 키가 필요하다")
        key.verify(base64.urlsafe_b64decode(signature + "=="), canonical(claims))
        identity = canonical_hash(request)
    except (KeyError, TypeError, InvalidSignature, AttributeError) as exc:
        raise ValueError("운영 승인 검증 실패") from exc
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS reservations (deployment_id TEXT PRIMARY KEY, "
                   "nonce TEXT NOT NULL UNIQUE, identity TEXT NOT NULL, state TEXT NOT NULL)")
        row = db.execute("SELECT identity, state FROM reservations WHERE deployment_id=?", (dep,)).fetchone()
        if row:
            if row[0] != identity:
                raise ValueError("동일 deployment_id의 요청이 변경됐다")
            return {"deployment_id": dep, "state": row[1]}
        try:
            db.execute("INSERT INTO reservations VALUES (?, ?, ?, 'reserved')", (dep, claims["nonce"], identity))
        except sqlite3.IntegrityError as exc:
            raise ValueError("승인 nonce를 이미 사용했다") from exc
    db_path.chmod(0o600)
    return {"deployment_id": dep, "state": "reserved"}


def results_dir() -> Path:
    path = Path(os.environ.get("DELTA_RELEASE_RESULTS", "/var/jenkins_home/delta-release/results"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save_result(path: Path, result: dict) -> None:
    """배포 ID별 결과를 남긴다(비밀값 없음: 배포기 결과 계약만). 원자적으로 바꾼다."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


def main():
    result, stage = {"state": "rejected"}, "inventory"
    try:
        # 환경 파라미터로 VM/DB/비밀 참조를 받지 않는다. 서버 소유 공개 인벤토리만 사용한다.
        inventory = json.loads(Path(os.environ.get("DELTA_RELEASE_TARGETS", "/run/delta/release-targets.json")).read_text())
        target = inventory[os.environ["TARGET_ID"]]
        if target["target_id"] != os.environ["TARGET_ID"]:
            raise ValueError("인벤토리 target_id가 다르다")
        stage = "bundle"
        bundle = bundle_from_images(os.environ["COMMIT"], json.loads(os.environ["IMAGES_JSON"]),
                                    os.environ["BUNDLE_SHA256"])
        # 선택적 전체 빌드 결과를 받으면 레지스트리 복사 증거까지 교차 검증한다.
        if os.environ.get("BUNDLE_JSON"):
            full_bundle = json.loads(os.environ["BUNDLE_JSON"])
            validate_bundle(full_bundle, os.environ["COMMIT"])
            registry_images = full_bundle.get("registry_images", {})
            for refs in registry_images.values():
                bundle_from_images(os.environ["COMMIT"], refs, bundle["bundle_sha256"])
            if registry_images.get(target["cloud"], full_bundle["images"]) != bundle["images"]:
                raise ValueError("빌드 결과의 대상 레지스트리와 배포 이미지가 다르다")
            bundle["registry_images"] = registry_images
        if target["cloud"] == "aws":
            aws_images = bundle.get("registry_images", {}).get("aws")
            if aws_images != bundle["images"]:
                raise ValueError("AWS 배포에는 검증된 registry_images.aws가 필요하다")
        stage = "approval"
        request = {"deployment_id": os.environ.get("DEPLOYMENT_ID") or os.environ["RUN_ID"],
                   "commit_sha": os.environ["COMMIT"], "bundle": bundle, "target": target,
                   "config_sha256": canonical_hash(target)}
        public = Path(os.environ.get("DELTA_RELEASE_PUBLIC_KEY", "/run/delta/release-public.pem")).read_bytes()
        result = verify_and_reserve(request, json.loads(os.environ["APPROVAL"]), public,
                                    Path(os.environ.get("DELTA_RELEASE_DB", "/var/jenkins_home/delta-release.sqlite3")),
                                    now=int(time.time()))
        # 같은 deployment_id 의 검증 완료 결과가 남아 있으면 다시 배포하지 않고 그 결과를 돌려준다(응답 유실·재요청).
        # 예약이 identity(요청 전체)를 대조했으므로 같은 요청임이 보장된다. 그 밖(실패·미확인)은 다시 실행한다:
        # 배포기는 같은 환경을 create/no-op 으로만 다룬다.
        stored = results_dir() / f"{request['deployment_id']}.json"
        previous = json.loads(stored.read_text()) if stored.is_file() else None
        stage = "deploy"
        if previous and previous.get("state") == "verified":
            result = previous
        else:
            # import도 승인·예약 뒤에 한다. 배포기는 이 시점 전 클라우드에 접속하지 않는다.
            from vm_release import execute
            result = execute(request)
            save_result(stored, result)
        if result.get("state") == "verified":
            stage = "verify"
            validate_bundle(result, request["commit_sha"])
            if (result.get("target_id") != target["target_id"] or result.get("environment") != "production"
                    or result.get("images") != bundle["images"]
                    or result.get("deployment_id") != request["deployment_id"]
                    or result.get("config_sha256") != request["config_sha256"]
                    or type(result.get("database_version_num")) is not int
                    or result["database_version_num"] // 10000 != 18):
                raise ValueError("원격 운영 관측 결과가 요청과 다르다")
        else:
            # 배포기가 남긴 stage·code만 전달한다(비밀·명령 출력 없음).
            result = {**result, "state": result.get("state") if result.get("state") in ("failed", "unknown") else "unknown",
                      "stage": result.get("stage", "deploy"), "code": result.get("code", "deploy_failed")}
            result["error"] = (f"운영 배포 실패({result['stage']}/{result['code']}); 같은 deployment_id로 다시 요청하면 "
                               "검증된 결과는 그대로 돌려주고, 그 밖은 같은 환경에 다시 적용한다")
    except Exception:
        # 토큰·명령 stderr·비밀을 Jenkins 일반 로그에 내보내지 않는다. 단계·원인 코드만 남긴다.
        reserved = result.get("state") == "reserved" or stage in ("deploy", "verify")
        result = {**{k: result[k] for k in ("deployment_id", "target_id") if k in result},
                  "state": "unknown" if reserved else "rejected", "stage": stage, "code": FAILURE_CODES[stage],
                  "error": f"운영 배포 검증 또는 원격 조회 실패({stage}/{FAILURE_CODES[stage]}); 동일 deployment_id로 상태를 확인해야 한다"}
    Path("result.json").write_text(json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("state") == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
