"""Bind a trusted SAST report to the exact commit and image in a platform build."""

import base64
import binascii
import hashlib
import json
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from delta_platform.adapters.builds import BuildError
from delta_platform.core.errors import Problem


def sast_ca(settings):
    encoded = settings.sast_ca_base64
    if not encoded:
        return None
    try:
        if len(encoded) > 32768:
            raise ValueError()
        pem = base64.b64decode(encoded, validate=True).decode("ascii")
        ssl.create_default_context(cadata=pem)
        return pem
    except (ValueError, UnicodeError, binascii.Error, ssl.SSLError):
        raise Problem("security_config_invalid", "SAST CA 인증서를 확인하세요.", 503) from None


def security_target(settings):
    if not settings.sast_url or not settings.sast_token.get_secret_value():
        raise Problem(
            "security_not_configured", "SAST 검사 서비스가 아직 연결되지 않았습니다.", 503
        )
    url = settings.sast_url.rstrip("/")
    p = urlsplit(url)
    local = settings.local_mode and p.hostname in ("127.0.0.1", "localhost", "::1")
    if (
        not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or "%" in p.path
        or ".." in p.path.split("/")
        or (p.scheme != "https" and not (local and p.scheme == "http"))
    ):
        raise Problem("security_config_invalid", "SAST 서비스 주소를 확인하세요.", 503)
    ca = sast_ca(settings)
    return {
        "url": url,
        "mode": "fast",
        "payload_mode": "preset",
        **({"ca_sha256": hashlib.sha256(ca.encode()).hexdigest()} if ca else {}),
    }


class SecurityRunner:
    def __init__(self, settings, *, transport=None, sleep=time.sleep):
        self.target = security_target(settings)
        self.token = settings.sast_token.get_secret_value()
        self.transport, self.sleep = transport, sleep
        self.verify = ssl.create_default_context(cadata=sast_ca(settings))

    def request(self, method, path, body=None):
        with httpx.Client(
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            verify=self.verify,
            transport=self.transport,
            headers={"Authorization": "Bearer " + self.token},
        ) as client:
            with client.stream(
                method, self.target["url"] + path, **({"json": body} if body is not None else {})
            ) as response:
                if response.status_code not in (200, 202):
                    raise BuildError(
                        "security_provider_error",
                        f"SAST 요청에 실패했습니다 (HTTP {response.status_code}). 서비스 상태·저장소 허용 범위를 확인하세요.",
                    )
                buf = bytearray()
                for chunk in response.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > 2_000_000:
                        raise BuildError(
                            "security_report_too_large", "SAST 결과가 허용 크기를 초과했습니다."
                        )
        try:
            result = json.loads(buf)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise BuildError("security_report_invalid", "SAST 결과 형식을 확인하세요.") from None

    def execute(self, claim, store, progress):
        source = claim["input_snapshot"]
        if source["security_target"] != self.target:
            raise BuildError(
                "security_target_changed",
                "SAST 설정이 변경돼 기존 검사를 다른 서버로 넘기지 않았습니다.",
            )
        run_id, token = claim["id"], claim["lease_token"]
        request = {
            "run_id": str(run_id),
            "repo": source["repository_url"],
            "commit": claim["resolved_commit_sha"],
            "mode": "fast",
            "payload_mode": "preset",
        }
        intent = store.prepare_submission(run_id, token, request)
        if intent["request"] != request:
            raise BuildError("security_request_changed", "저장된 검사 요청과 현재 입력이 다릅니다.")
        # The SAST contract explicitly deduplicates by run_id and rejects conflicting input.
        store.heartbeat(run_id, token)
        progress("security_scan", "빌드한 커밋의 소스를 SAST로 검사합니다.")
        scan = self.request("POST", "/v1/sast/scans", request)
        deadline = datetime.fromisoformat(intent["prepared_at"]).timestamp() + 900
        while True:
            self.identity(scan, request)
            if scan.get("state") == "failed":
                raise BuildError(
                    "security_scan_failed",
                    "SAST 검사가 실패했습니다. 검사 서비스의 해당 실행 기록을 확인하세요.",
                )
            if scan.get("state") == "done":
                break
            if scan.get("state") != "running":
                raise BuildError("security_report_invalid", "알 수 없는 SAST 검사 상태입니다.")
            if datetime.now(UTC).timestamp() >= deadline:
                raise BuildError(
                    "security_wait_timeout",
                    "SAST 대기 시간이 초과됐습니다. 원격 검사 상태를 확인하세요. 통과로 처리하지 않았습니다.",
                )
            store.heartbeat(run_id, token)
            self.sleep(2)
            scan = self.request("GET", "/v1/sast/scans/" + str(run_id))
        progress("security_evidence", "SAST 근거와 요청한 커밋을 대조합니다.")
        report = self.request("GET", f"/v1/sast/scans/{run_id}/sarif")
        evidence = self.summarize(report)
        return {
            **evidence,
            "build_id": source["build_id"],
            "image": source["image"],
            "image_digest": source["image_digest"],
            "commit_sha": claim["resolved_commit_sha"],
            "scanner_run_id": str(run_id),
            "security_check": "sast_completed",
            "dast_check": "not_run",
            "deployment_approved": False,
            "completed_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def identity(scan, expected):
        if any(scan.get(k) != v for k, v in expected.items()):
            raise BuildError(
                "security_identity_mismatch", "검사한 저장소·커밋·요청 ID가 빌드와 다릅니다."
            )

    @staticmethod
    def summarize(report):
        try:
            if (
                report["version"] != "2.1.0"
                or not isinstance(report["runs"], list)
                or not report["runs"]
            ):
                raise ValueError()
            counts = {"fail": 0, "review": 0, "pass": 0, "informational": 0}
            findings = []
            for run in report["runs"]:
                if not isinstance(run["results"], list) or any(
                    i.get("executionSuccessful") is False for i in run.get("invocations", [])
                ):
                    raise ValueError()
                for result in run["results"]:
                    kind = result.get("kind", "review")
                    if kind not in counts:
                        kind = "review"
                    counts[kind] += 1
                    if kind in ("fail", "review") and len(findings) < 100:
                        location = (result.get("locations") or [{}])[0].get("physicalLocation", {})
                        path = location.get("artifactLocation", {}).get("uri", "")
                        if (
                            not isinstance(path, str)
                            or path.startswith("/")
                            or ".." in path.split("/")
                        ):
                            path = "경로 생략"
                        line = location.get("region", {}).get("startLine")
                        findings.append(
                            {
                                "rule": str(result.get("ruleId", ""))[:160],
                                "kind": kind,
                                "file": path[:240],
                                "line": line if type(line) is int and line > 0 else None,
                            }
                        )
            return {
                "counts": counts,
                "findings": findings,
                "findings_truncated": counts["fail"] + counts["review"] > len(findings),
                "requires_review": counts["fail"] + counts["review"] > 0,
                "evidence_sha256": hashlib.sha256(
                    json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "coverage": "configured_scanner_rules_only",
            }
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BuildError(
                "security_report_invalid",
                "유효한 SARIF 근거를 확인할 수 없습니다. 통과로 처리하지 않았습니다.",
            ) from None
