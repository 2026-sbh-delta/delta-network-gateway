"""Send evidence to the team's AI service without granting mutation authority."""

import base64
import json
import ssl
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from delta_platform.core.errors import Problem


class AssessmentClient:
    def __init__(self, settings, *, transport=None):
        self.url = settings.ai_url.rstrip("/")
        parsed = urlsplit(self.url)
        local = settings.local_mode and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            or not settings.ai_token.get_secret_value()
        ):
            raise Problem("ai_not_configured", "운영자가 AI 서비스 연결을 설정해야 합니다.", 503)
        try:
            pem = (
                base64.b64decode(settings.ai_ca_base64, validate=True).decode()
                if settings.ai_ca_base64
                else None
            )
            self.verify = ssl.create_default_context(cadata=pem)
        except (ValueError, ssl.SSLError):
            raise Problem("ai_config_invalid", "AI 서비스 CA 인증서를 확인하세요.", 503) from None
        self.token = settings.ai_token.get_secret_value()
        self.transport = transport

    def execute(self, store, workflow_id, environment_id, evidence, key):
        wf = store.get(workflow_id)
        plan = wf["input_snapshot"]
        target = next((item for item in plan["targets"] if item["id"] == str(environment_id)), None)
        if not target:
            raise Problem("execution_target_invalid", "계획의 판단 대상이 아닙니다.", 409)
        # Only operator/Worker code supplies observed inventory/evidence, never shell instructions.
        payload = {
            **evidence,
            "service": {
                "service_id": str(wf["service_id"]),
                "repo": plan["repository_url"],
                "commit": plan["commit_sha"],
            },
        }
        if (
            payload.get("target_env", {}).get("provider") != target["cloud"]
            or (payload.get("source_env") or {}).get("provider")
            != (plan["source"] or {}).get("cloud")
            or "assessment_id" in payload
            or "execution_context" in payload
        ):
            raise Problem(
                "assessment_target_mismatch", "관측한 환경과 계획의 환경이 다릅니다.", 409
            )
        context = store.prepare_execution(
            workflow_id, environment_id, "ai_assessment", payload, key
        )
        run_id = UUID(context["run_id"])
        context, payload = store.guard(workflow_id, run_id)
        request = {**payload, "assessment_id": str(run_id), "execution_context": context}
        with httpx.Client(
            timeout=150,
            verify=self.verify,
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                self.url + "/v1/assessments",
                json=request,
                headers={"Authorization": "Bearer " + self.token},
            ) as response:
                if response.status_code != 200:
                    raise Problem(
                        "assessment_failed",
                        f"AI 판단 요청 실패 (HTTP {response.status_code}). 같은 입력으로 재시도할 수 있습니다.",
                        502,
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise Problem("assessment_too_large", "AI 결과 크기를 초과했습니다.", 502)
        try:
            result = json.loads(body)
            if (
                result["assessment_id"] != str(run_id)
                or result["kind"] != payload["kind"]
                or result["execution_context"] != context
                or result["verdict"] not in ("pass", "fix", "needs_human")
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise Problem(
                "assessment_identity_mismatch", "AI 판단 결과가 요청한 실행과 다릅니다.", 502
            ) from None
        store.complete_execution(workflow_id, run_id, context, result)
        return result
