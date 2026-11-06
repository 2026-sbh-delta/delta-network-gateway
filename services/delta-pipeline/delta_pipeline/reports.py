"""Graph-owned report storage. Atomic, private, idempotent bundles; never a knowledge-base publisher."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from delta_ai.diagnostic_contract import (
    DiagnosisReport,
    DiagnosisRequest,
    Identifier,
    validate_report,
)
from pydantic import BaseModel, ConfigDict, Field


class ReportError(RuntimeError):
    pass


class ReportRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: Identifier
    case_id: Identifier
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def markdown(request: DiagnosisRequest, report: DiagnosisReport) -> str:
    lines = [
        "# Delta 예외 분석 보고서",
        "",
        f"- 실행: `{request.run_id}`",
        f"- 사례: `{request.case_id}`",
        f"- 단계: `{request.node_id}`",
        f"- 분석 상태: `{report.status}`",
        "- 실행·재시도·배포·다음 단계 선택은 그래프와 사람의 책임이다.",
        "",
        "## 요약",
        "",
        report.summary,
        "",
        "## 관측 사실",
        "",
    ]
    lines += [f"- [{o.evidence_id}] {o.summary}" for o in request.observations]
    lines += ["", "## 원인 후보", ""]
    lines += [
        f"- [{h.confidence}] {h.explanation} (근거: {', '.join(h.evidence_ids)}; 문서: {', '.join(h.rule_ids) or '없음'})"
        for h in report.hypotheses
    ] or ["- 확정할 수 있는 원인 후보가 없다."]
    lines += [
        "",
        "## 추가 확인",
        "",
        *[f"- {x}" for x in report.next_checks],
        "",
        "## 한계",
        "",
        *[f"- {x}" for x in report.limitations],
        "",
    ]
    return "\n".join(lines)


class ReportStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _run_dir(self, run_id):
        if self.directory.is_symlink():
            raise ReportError("보고서 저장소의 심볼릭 링크는 허용하지 않는다")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        run = self.directory / run_id
        if run.is_symlink():
            raise ReportError("보고서 경로의 심볼릭 링크는 허용하지 않는다")
        run.mkdir(exist_ok=True, mode=0o700)
        return run

    @staticmethod
    def _write(path, body):
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sync(directory):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def save(self, request: DiagnosisRequest, report: DiagnosisReport) -> ReportRef:
        validate_report(report, request)
        body = _bytes(
            {
                "schema_version": 1,
                "request": request.model_dump(mode="json"),
                "analysis": report.model_dump(mode="json"),
            }
        )
        ref = ReportRef(
            run_id=request.run_id,
            case_id=request.case_id,
            sha256=hashlib.sha256(body).hexdigest(),
        )
        parent = self._run_dir(ref.run_id)
        dest = parent / ref.case_id
        if dest.exists() or dest.is_symlink():
            self.verify(ref)
            return ref
        temp = Path(tempfile.mkdtemp(prefix=".report-", dir=parent))
        try:
            self._write(temp / "report.json", body)
            self._write(temp / "report.md", markdown(request, report).encode())
            self._sync(temp)
            os.rename(temp, dest)
            self._sync(parent)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
        return ref

    def verify(self, ref: ReportRef) -> Path:
        path = self._run_dir(ref.run_id) / ref.case_id
        files = [path / "report.json", path / "report.md"]
        if path.is_symlink() or any(f.is_symlink() or not f.is_file() for f in files):
            raise ReportError("보고서가 없거나 안전한 파일이 아니다")
        body = files[0].read_bytes()
        if hashlib.sha256(body).hexdigest() != ref.sha256:
            raise ReportError("저장된 보고서 해시가 다르다")
        bundle = json.loads(body)
        request = DiagnosisRequest.model_validate(bundle["request"])
        report = DiagnosisReport.model_validate(bundle["analysis"])
        validate_report(report, request)
        if (request.run_id, request.case_id) != (ref.run_id, ref.case_id) or files[
            1
        ].read_text() != markdown(request, report):
            raise ReportError("보고서 식별값 또는 Markdown 내용이 다르다")
        return path

    def decide(self, ref: ReportRef, answer: str, *, action: str | None = None):
        path = self.verify(ref) / "decision.json"
        if answer not in ("approve", "stop", "fix", "reverify", "acknowledge"):
            raise ReportError("지원하지 않는 사람 확인 값이다")
        if action is not None and (answer != "reverify" or action not in ("retry_same_job", "redeploy_sandbox")):
            raise ReportError("지원하지 않는 조치 확인 값이다")
        record = {
            **({"action": action} if action is not None else {}),
            "report_sha256": ref.sha256,
            "answer": answer,
            "source": "local_operator",
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        if path.is_symlink():
            raise ReportError("확인 기록의 심볼릭 링크는 허용하지 않는다")
        if path.exists():
            saved = json.loads(path.read_text())
            if (
                saved.get("report_sha256") != ref.sha256
                or saved.get("answer") != answer
                or saved.get("action") != action
            ):
                raise ReportError("기존 사람 확인 기록을 다른 값으로 덮어쓰지 않는다")
            return
        # Linking a completed temporary file publishes it without overwriting an existing decision.
        fd, name = tempfile.mkstemp(prefix=".decision-", dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_bytes(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, path)
            self._sync(path.parent)
        finally:
            temp.unlink(missing_ok=True)
