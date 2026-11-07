"""오케스트레이터용 차분 HTTP 클라이언트. 연결 성공과 배포 허용 판정을 구분한다."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import ValidationError

from delta_diff.contract import DiffRequest, DiffResult, ManifestMeta, SourceMeta

MAX_RESPONSE_BYTES = 1_000_000


class DiffUnavailable(RuntimeError):
    """인증·연결·계약 검증 실패. 후속 적용으로 진행하지 않는다."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 인증 헤더를 다른 서버로 보내지 않는다


def _private_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".internal"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class DiffClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        ca_base64: str = "",
        allow_private_http: bool = False,
        timeout: float = 180,
    ) -> None:
        parsed = urllib.parse.urlsplit(url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not token
            or not (
                parsed.scheme == "https"
                or (
                    allow_private_http
                    and parsed.scheme == "http"
                    and _private_host(parsed.hostname)
                )
            )
        ):
            raise ValueError(
                "차분 URL·토큰을 확인한다(기본 HTTPS, 내부 HTTP는 명시적으로 허용)"
            )
        try:
            pem = (
                base64.b64decode(ca_base64, validate=True).decode()
                if ca_base64
                else None
            )
            context = ssl.create_default_context(cadata=pem)
        except (ValueError, ssl.SSLError) as exc:
            raise ValueError("차분 CA 인증서를 확인한다") from exc
        if timeout <= 0:
            raise ValueError("호출 제한 시간은 양수여야 한다")
        self._url, self._token, self._timeout = url.rstrip("/"), token, timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
            _NoRedirect(),
        )

    def _request(self, path: str, payload: dict | None = None) -> Any:
        request = urllib.request.Request(
            self._url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
            method="POST" if payload is not None else "GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise DiffUnavailable(f"차분 HTTP {response.status}")
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise DiffUnavailable(f"차분 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DiffUnavailable(
                "차분 서버 연결·TLS·호출 시간 제한을 확인한다"
            ) from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise DiffUnavailable("차분 응답 크기 초과")
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise DiffUnavailable(
                "차분 응답이 JSON이 아니다(임시 서버 여부 확인)"
            ) from None

    def check_health(self) -> None:
        health = self._request("/healthz")
        if not isinstance(health, dict) or any(
            health.get(k) != v
            for k, v in {
                "status": "ok",
                "service": "delta-diff",
                "api_version": "v1",
            }.items()
        ):
            raise DiffUnavailable(
                "실제 차분 API가 아니다(임시 health 서버는 연결 완료가 아님)"
            )

    def run(self, request: DiffRequest) -> DiffResult:
        raw = self._request("/v1/diffs", request.model_dump(mode="json"))
        try:
            result = DiffResult.model_validate(raw)
        except ValidationError:
            raise DiffUnavailable("차분 응답 계약 불일치") from None
        if (
            result.diff_id != request.diff_id
            or result.assessment.assessment_id != request.diff_id
            or result.assessment.kind != "migration_mapping"
            or result.mapping_input.source_provider != request.source.provider
            or result.mapping_input.target_provider != request.target.provider
            or (
                result.assessment.verdict in ("pass", "fix")
                and (
                    result.assessment.mapping is None
                    or not result.assessment.mapping.rows
                )
            )
        ):
            raise DiffUnavailable("차분 응답의 실행 ID·state·이전 방향이 요청과 다르다")
        if request.source.state is not None:
            if (
                not isinstance(result.source, SourceMeta)
                or result.source.bucket != request.source.state.bucket
                or result.source.key != request.source.state.key
            ):
                raise DiffUnavailable("차분 응답의 state가 요청과 다르다")
        elif (
            not isinstance(result.source, ManifestMeta)
            or result.source.sha256
            != hashlib.sha256(request.source.manifests.encode()).hexdigest()
        ):
            raise DiffUnavailable("차분 응답의 출발 YAML 해시가 요청과 다르다")
        target_yaml = request.target.manifests
        if target_yaml is not None:
            if (
                result.target_manifest is None
                or result.target_manifest.sha256
                != hashlib.sha256(target_yaml.encode()).hexdigest()
            ):
                raise DiffUnavailable("차분 응답의 대상 YAML 해시가 요청과 다르다")
        elif result.target_manifest is not None:
            raise DiffUnavailable("요청하지 않은 대상 YAML 결과다")
        return result


def from_ssm(
    ssm: Any, parameter: str, *, allow_private_http: bool = False
) -> DiffClient:
    """운영자가 정한 연결 정보만 읽는다. 비밀값은 출력하지 않는다."""
    try:
        connection = json.loads(
            ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
        )
        return DiffClient(
            connection["url"],
            connection["token"],
            ca_base64=connection.get("ca_base64", ""),
            allow_private_http=allow_private_http,
        )
    except Exception:
        # SDK/검증 예외에 포함될 수 있는 연결 정보·파라미터 원문을 노출하지 않는다.
        raise DiffUnavailable(
            "차분 연결 파라미터의 접근 권한·URL·토큰·CA를 확인한다"
        ) from None
