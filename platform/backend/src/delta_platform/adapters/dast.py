"""SAST evidence and an owned ECS revision feed the existing team DAST service."""

import hashlib
import json
import re
import time
from datetime import UTC, datetime

import httpx

from delta_platform.adapters.builds import BuildError
from delta_platform.adapters.sandbox import SandboxError, SandboxRunner
from delta_platform.adapters.security import SecurityRunner, security_target
from delta_platform.core.errors import Problem


def dast_settings(settings):
    return settings.model_copy(
        update={
            "sast_url": settings.dast_url,
            "sast_token": settings.dast_token,
            "sast_ca_base64": settings.dast_ca_base64,
        }
    )


def dast_target(settings):
    try:
        return security_target(dast_settings(settings))
    except Problem:
        raise Problem(
            "dast_not_configured", "DAST 검사 서버의 연결 설정을 확인하세요.", 503
        ) from None


class DastInspector:
    def __init__(self, settings, *, transport=None, sast_transport=None, poll_seconds=3):
        self.target = dast_target(settings)
        self.http = SecurityRunner(dast_settings(settings), transport=transport)
        self.sast = SecurityRunner(settings, transport=sast_transport)
        self.poll_seconds = poll_seconds

    def request(self, method, run_id, body=None, suffix=""):
        try:
            return self.http.request(method, f"/api/platform/runs/{run_id}{suffix}", body)
        except (httpx.HTTPError, BuildError):
            raise SandboxError(
                "dast_provider_error", "DAST 서버 연결 또는 응답을 확인하지 못했습니다."
            ) from None

    def execute(self, claim, store, progress, guard, runtime):
        source, run_id = claim["input_snapshot"], str(claim["id"])
        if source["dast_target"] != self.target or source["security_target"] != self.sast.target:
            raise SandboxError(
                "dast_target_changed",
                "검사 서버 설정이 바뀌었습니다. 기존 실행의 정리를 확인하세요.",
            )
        binding = {
            "build_id": source["build_id"],
            "sast_run_id": source["security_check_id"],
            "commit_sha": claim["resolved_commit_sha"],
            "image": source["image"],
            "image_digest": runtime["image_digest"],
            "sast_evidence_sha256": source["sast_evidence_sha256"],
            "target_url": runtime.get("url") or source["sandbox_target"]["url"],
        }
        cloud = source["sandbox_target"].get("cloud", "aws")
        if cloud in ("gcp", "onprem"):
            binding.update(execution_cloud=cloud, runtime_resource=runtime["resource"])
        if cloud == "onprem":
            binding["runtime_revision"] = runtime["revision"]
        guard()
        progress("dast_evidence", "같은 빌드의 SAST 근거를 확인합니다.")
        try:
            report = self.sast.request("GET", f"/v1/sast/scans/{source['security_check_id']}/sarif")
            if self.sast.summarize(report)["evidence_sha256"] != source["sast_evidence_sha256"]:
                raise SandboxError(
                    "dast_sast_changed", "저장한 SAST 근거와 현재 보고서가 다릅니다."
                )
        except (httpx.HTTPError, BuildError):
            raise SandboxError(
                "dast_sast_unavailable", "SAST 원본 근거를 확인하지 못했습니다."
            ) from None
        progress("dast_scan", "샌드박스에서 SAST의 XSS 후보를 실제 브라우저로 확인합니다.")
        try:
            scan = self.request("PUT", run_id, {"binding": binding, "report": report})
        except SandboxError:
            # A lost response may follow a successful submission. Recover by its UUID.
            guard()
            scan = self.request("GET", run_id)
        while True:
            guard()
            if scan.get("run_id") != run_id or scan.get("binding") != binding:
                raise SandboxError(
                    "dast_identity_mismatch", "DAST 결과와 검사 대상·빌드가 다릅니다."
                )
            if scan.get("status") not in ("running",):
                break
            if (datetime.now(UTC) - claim["created_at"]).total_seconds() > 1200:
                raise SandboxError("dast_timeout", "DAST 대기 시간을 초과했습니다.")
            time.sleep(self.poll_seconds)
            scan = self.request("GET", run_id)
        if scan.get("status") not in ("done", "error", "skipped"):
            raise SandboxError(
                "dast_interrupted", "DAST 검사가 중단됐습니다. 재현 성공으로 처리하지 않았습니다."
            )
        return self.summarize(scan, source)

    def stop(self, claim):
        # Do not send cancellation to a changed endpoint (or a different trust root).
        if claim["input_snapshot"]["dast_target"] != self.target:
            return False
        try:
            value = self.request("POST", str(claim["id"]), {}, "/cancel")
            return value.get("run_id") == str(claim["id"]) and value.get("stopped") is True
        except SandboxError:
            return False

    @staticmethod
    def summarize(scan, source):
        try:
            ids = scan["finding_ids"]
            results = scan["results"]
            eligible = scan["eligible_count"]
            if (
                not isinstance(ids, list)
                or len(ids) > 10
                or len(set(ids)) != len(ids)
                or not all(
                    isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", i) for i in ids
                )
                or type(eligible) is not int
                or eligible < len(ids)
                or scan["findings_truncated"] != (eligible > len(ids))
                or scan["deployment_approved"] is not False
                or scan["coverage"] != "sast_xss_candidates_only"
                or not re.fullmatch(r"[a-f0-9]{64}", scan["evidence_sha256"])
            ):
                raise ValueError()
            if scan["status"] == "skipped":
                if eligible or ids or results:
                    raise ValueError()
            elif (
                not ids
                or len(results) != len(ids)
                or {r["finding_id"] for r in results} != set(ids)
            ):
                raise ValueError()
            counts = dict.fromkeys(("REPRODUCED", "NOT_REPRODUCED", "BLOCKED", "INCONCLUSIVE"), 0)
            findings = []
            for r in results:
                if r["status"] not in counts or not re.fullmatch(
                    r"[a-f0-9]{64}", r["evidence_sha256"]
                ):
                    raise ValueError()
                counts[r["status"]] += 1
                findings.append({k: r[k] for k in ("finding_id", "status", "evidence_sha256")})
            return {
                "execution_cloud": source["sandbox_target"].get("cloud", "aws"),
                "environment_check": source["sandbox_target"].get("cloud", "aws") + "_sandbox_only",
                "dast_check": "not_run" if scan["status"] == "skipped" else "completed",
                "dast_reason": "no_supported_xss_candidates"
                if scan["status"] == "skipped"
                else None,
                "security_check_id": source["security_check_id"],
                "scanner_run_id": scan["run_id"],
                "sast_evidence_sha256": source["sast_evidence_sha256"],
                "dast_evidence_sha256": scan["evidence_sha256"],
                "dast_response_sha256": hashlib.sha256(
                    json.dumps(scan, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "dast_counts": counts,
                "dast_findings": findings,
                "eligible_count": eligible,
                "findings_truncated": scan["findings_truncated"],
                "requires_review": bool(
                    counts["REPRODUCED"]
                    or counts["BLOCKED"]
                    or counts["INCONCLUSIVE"]
                    or scan["findings_truncated"]
                    or scan["status"] in ("error", "skipped")
                ),
                "coverage": "sast_xss_candidates_only",
                "deployment_approved": False,
            }
        except (KeyError, ValueError, TypeError, AttributeError):
            raise SandboxError(
                "dast_report_invalid", "DAST 근거 형식 또는 검사 항목이 일치하지 않습니다."
            ) from None


class DastRunner(SandboxRunner):
    def __init__(self, settings, *, inspector=None, **kwargs):
        super().__init__(settings, **kwargs)
        self.inspector = inspector or DastInspector(settings)
