"""Analysis-only delta-ai HTTP adapter; graph/report code owns all workflow decisions."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from delta_pipeline.clients import JUDGE_TIMEOUT_SECONDS, ServiceUnavailable


class DiagnosticClient:
    def __init__(self, url: str, token: str, ca_base64: str | None = None) -> None:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or not token
            or parsed.path not in ("", "/")
        ):
            raise ValueError("AI 연결 URL에 인증정보나 query를 넣지 않는다")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            raise ValueError("진단 API는 HTTPS 또는 로컬 루프백만 허용한다")
        self._url, self._token = url.rstrip("/"), token
        context = ssl.create_default_context()
        if ca_base64:
            context.load_verify_locations(
                cadata=base64.b64decode(ca_base64, validate=True).decode()
            )
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        )

    def diagnose(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > 64_000:
            raise ServiceUnavailable("진단 요청 크기 제한을 초과했다")
        parsed = urllib.parse.urlparse(self._url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            raise ServiceUnavailable("진단 API는 HTTPS 또는 로컬 루프백만 허용한다")
        request = urllib.request.Request(
            self._url + "/v1/diagnoses",
            payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=JUDGE_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise ValueError()
                raw = response.read(128_001)
            if len(raw) > 128_000:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError()
            return result
        except Exception:  # noqa: BLE001 — the transport boundary must never expose upstream payloads
            raise ServiceUnavailable("진단 API 연결 또는 응답 검증 실패") from None
