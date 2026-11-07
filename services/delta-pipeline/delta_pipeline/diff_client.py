"""Operator-pinned AWS → GCP comparison. HTTP and model output cannot grant approval."""

from __future__ import annotations

import base64
import json
import ssl
import threading
import urllib.error
import urllib.request
from concurrent.futures import Future
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from delta_ai.contract import AssessmentResult, ExistingSubnet
from delta_pipeline.clients import ServiceUnavailable, evidence_hash

MAX_REQUEST_BYTES = 64_000
MAX_RESULT_BYTES = 256_000
DIFF_TIMEOUT = 75


class StateRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    key: str = Field(max_length=1024, pattern=r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*\.tfstate$")

    @field_validator("key")
    @classmethod
    def no_relative_parts(cls, key: str) -> str:
        if any(part in (".", "..") for part in key.split("/")):
            raise ValueError("State object key must be explicit")
        return key


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["aws"] = "aws"
    state: StateRef
    region: str | None = Field(default=None, max_length=40)
    exclude_subnets: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = Field(default=(), max_length=20)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["gcp"] = "gcp"
    region: str | None = Field(default=None, max_length=40)
    existing_subnets: tuple[ExistingSubnet, ...] | None = Field(default=None, min_length=1, max_length=20)


class DiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    service_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source: Source
    target: Target = Target()

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        if self.target.existing_subnets is None:
            # Preserve the fingerprint of existing requests and checkpoints.
            del payload["target"]["existing_subnets"]
        return evidence_hash(payload)

    @classmethod
    def from_file(cls, path: str) -> DiffInput:
        try:
            with Path(path).open("rb") as source:
                body = source.read(MAX_REQUEST_BYTES + 1)
            if len(body) <= MAX_REQUEST_BYTES:
                return cls.model_validate_json(body)
        except (OSError, ValueError):
            pass
        raise ServiceUnavailable("운영자 Diff 요청 파일을 확인하세요")


class StateSnapshot(BaseModel):
    bucket: str
    key: str
    serial: int = Field(ge=0, strict=True)


class DiffResult(BaseModel):
    diff_id: str
    source: StateSnapshot
    assessment: AssessmentResult

    def verify(self, run_id: str, config: DiffInput) -> None:
        assessment = self.assessment
        mapping = assessment.mapping
        if (self.diff_id != run_id or assessment.assessment_id != run_id
                or assessment.kind != "migration_mapping"
                or (self.source.bucket, self.source.key) != (config.source.state.bucket, config.source.state.key)
                or not mapping or not mapping.rows or not assessment.checks
                or (config.target.existing_subnets and mapping.network_mode != "reuse")
                or (config.target.region and mapping.region != config.target.region)):
            raise ValueError("Diff result does not match the request")
        rules = {c.rule_id for c in assessment.checks}
        if not {row.rule_id for row in mapping.rows} <= rules or len(rules) != len(assessment.checks):
            raise ValueError("Diff result is missing check records")
        if assessment.verdict == "pass" and (
                any(r.grade in ("gap", "unknown") for r in mapping.rows)
                or any(c.state != "green" for c in assessment.checks)):
            raise ValueError("Diff pass contradicts the check records")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DiffClient:
    def __init__(self, url: str, token: str, ca: str | None = None, *, allow_private_http: bool = False):
        parsed = urlsplit(url)
        private = parsed.scheme == "http" and parsed.netloc == "diff.cp.internal:8095" and allow_private_http
        if (not token or not parsed.hostname or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ("", "/") or (parsed.scheme != "https" and not private)):
            raise ValueError("Diff HTTPS connection or configured private endpoint required")
        context = ssl.create_default_context()
        if ca:
            context.load_verify_locations(cadata=base64.b64decode(ca, validate=True).decode())
        self._url = url.rstrip("/")
        self._token = token
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(), urllib.request.HTTPSHandler(context=context),
        )

    def compare(self, config: DiffInput, run_id: str) -> DiffResult:
        data = {**config.model_dump(mode="json", exclude_none=True), "diff_id": run_id}
        body = json.dumps(data, ensure_ascii=False).encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise ServiceUnavailable("Diff 요청 크기 제한을 초과했습니다")
        request = urllib.request.Request(self._url + "/v1/diffs", body, headers={
            "Authorization": "Bearer " + self._token, "Content-Type": "application/json",
        })
        try:
            with self._opener.open(request, timeout=DIFF_TIMEOUT) as response:
                raw = response.read(MAX_RESULT_BYTES + 1)
            if len(raw) > MAX_RESULT_BYTES:
                raise ValueError("Diff response too large")
            result = DiffResult.model_validate_json(raw)
            result.verify(run_id, config)
            return result
        except urllib.error.HTTPError as exc:
            raise ServiceUnavailable(f"Diff API HTTP {exc.code}") from None
        except (OSError, ValueError):
            raise ServiceUnavailable("Diff API 연결 또는 응답 검증 실패") from None


class DiffJob:
    """The synchronous Diff API runs off the graph thread until web validation reaches the gate."""

    def __init__(self, client: DiffClient, config: DiffInput, run_id: str):
        self.config = config
        self._client = client
        self._run_id = run_id
        self._result: Future[DiffResult] | None = None

    def start(self) -> None:
        if self._result is not None:
            return
        future: Future[DiffResult] = Future()
        self._result = future

        def compare() -> None:
            try:
                value = self._client.compare(self.config, self._run_id)
                value.verify(self._run_id, self.config)
                future.set_result(value)
            except ServiceUnavailable as exc:
                future.set_exception(exc)
            except Exception:
                future.set_exception(ServiceUnavailable("Diff 실행 또는 응답 검증 실패"))

        threading.Thread(target=compare, name="delta-infrastructure-diff", daemon=True).start()

    def result(self) -> DiffResult:
        self.start()
        assert self._result is not None
        try:
            return self._result.result(timeout=DIFF_TIMEOUT + 5)
        except TimeoutError:
            raise ServiceUnavailable("Diff 응답 제한 시간을 초과했습니다") from None
