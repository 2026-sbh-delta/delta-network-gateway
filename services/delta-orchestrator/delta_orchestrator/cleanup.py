"""Expose cleanup of verified platform targets through the trusted Jenkins executor."""
import base64
import json
import os
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from delta_pipeline.clients import Jenkins
from .pipelines import release_signing_key


def release(board):
    if not board or board.get("outcome") is None:
        raise ValueError("진행 중인 파이프라인은 정리할 수 없습니다")
    node = next((n for n in board["nodes"] if n["id"] == "release" and n["state"] == "pass"), {})
    result = node.get("detail", {}).get("verified_result", {})
    if result.get("state") != "verified" or not result.get("target_id", "").startswith("platform-"):
        raise ValueError("플랫폼 전용 운영 배포의 검증 기록이 필요합니다")
    return result


def client():
    return Jenkins(f"http://jenkins.{os.environ.get('DELTA_INTERNAL_ZONE','cp.internal')}:8080",
                   "platform", os.environ.get("JENKINS_PLATFORM_PASSWORD", ""), job="delta-release-cleanup")


def execute(board, *, submit=False):
    result = release(board)
    jenkins = client()
    key = "cleanup-" + board["run_id"]
    build = jenkins.find_build(key)
    if submit and build is not None and jenkins.status(build) in ("FAILURE", "ABORTED", "UNSTABLE", "NOT_BUILT"):
        build = None  # Explicit user retry; verified tombstone keeps cleanup idempotent.
    if build is None and submit:
        claims = {"action":"remove", "target_id":result["target_id"],
                  "deployment_id":result["deployment_id"], "config_sha256":result["config_sha256"],
                  "exp":int(time.time())+600}
        private = serialization.load_pem_private_key(release_signing_key(),password=None)
        if not isinstance(private,Ed25519PrivateKey):
            raise ValueError("서명 설정을 확인하세요")
        canonical = json.dumps(claims,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        token = dict(claims=claims,signature=base64.urlsafe_b64encode(private.sign(canonical)).decode().rstrip('='))
        build = jenkins.trigger({"APPROVAL":json.dumps(token)},key)
    if build is None:
        return {"state":"not_started"}
    status = jenkins.status(build)
    if status is None or status in ("RUNNING", "QUEUED", "IN_PROGRESS"):
        return {"state":"running"}
    if status != "SUCCESS":
        return {"state":"failed", "message":"실행 자원 정리 결과를 확인하지 못했습니다. 등록 정보는 보존합니다."}
    observed = jenkins.file(build,"result.json")
    if (observed.get("state") != "removed" or observed.get("target_id") != result["target_id"]
            or observed.get("deployment_id") != result["deployment_id"]
            or observed.get("environment_id") != result["environment_id"]):
        raise ValueError("자원 정리 결과가 등록된 배포와 다릅니다")
    return {k:observed[k] for k in ("state","target_id","deployment_id","environment_id","database")}
