"""Durable case outbox. Observes verified results; never changes a gate or runs a tool."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from delta_ai.cases import IncidentCase, Resolution, VerificationCheck, digest
from delta_ai.diagnostic_contract import DiagnosisReport, DiagnosisRequest
from delta_pipeline.reports import ReportRef, ReportStore


class CaseLearning:
    def __init__(self, deps):
        self.deps = deps
        self.reports = ReportStore(deps.report_dir)
        self.root = Path(deps.report_dir).parent / "case-outbox"

    def record(self, ref, *, sequence=0, outcome="unknown", action="none", attempt=0, checks=()):
        try:
            ref = ReportRef.model_validate(ref)
            directory = self.reports.verify(ref)
            bundle = json.loads((directory / "report.json").read_bytes())
            request = DiagnosisRequest.model_validate(bundle["request"])
            case = IncidentCase(request=request, report=DiagnosisReport.model_validate(bundle["analysis"]),
                report_sha256=ref.sha256, sequence=sequence,
                resolution=Resolution(outcome=outcome, action=action, attempt=attempt,
                                      scope=request.node_id, checks=checks))
            if self.root.is_symlink():
                raise ValueError("unsafe case outbox")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.root / f"{ref.case_id}-{sequence:04}.json"
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_bytes() != case.model_dump_json().encode():
                    raise ValueError("conflicting case outbox")
            else:
                fd, name = tempfile.mkstemp(prefix=".case-", dir=self.root)
                temp = Path(name)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(case.model_dump_json().encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temp, path)
                    directory_fd = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    temp.unlink(missing_ok=True)
            sent = True
            try:
                self.flush(case_id=ref.case_id)
            except Exception:
                sent = False
            self.deps.board.add_detail("exception-report", case_learning={"status": "recorded",
                "case_id": ref.case_id, "outcome": outcome, "scope": request.node_id,
                "publication": "sent" if sent and (path.with_suffix('.ack')).exists() else "pending"})
        except Exception:
            # Storage/model/transport failures cannot grant approval or hide the original failure.
            self.deps.board.add_detail("exception-report", case_learning={"status": "unavailable"})

    def flush(self, *, case_id=None):
        port = self.deps.diagnostic
        if port is None or not hasattr(port, "publish_case") or not self.root.exists():
            return
        if self.root.is_symlink():
            raise ValueError("unsafe case outbox")
        paths = sorted(self.root.glob(f"{case_id}-*.json" if case_id else "*.json"))
        pending = [p for p in paths if not p.is_symlink() and not p.with_suffix('.ack').exists()]
        deadline = time.monotonic() + 5
        for path in pending[:100]:
            if time.monotonic() >= deadline:
                break
            ack = path.with_suffix(".ack")
            case = IncidentCase.model_validate_json(path.read_bytes())
            result = port.publish_case(case.model_dump(mode="json"))
            if result.get("revision") != digest(case) or result.get("case_id") != case.request.case_id:
                raise ValueError("case publication binding mismatch")
            # Repeating after an acknowledgement write failure is safe on the server.
            fd = os.open(ack, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            os.close(fd)

    def settled(self, name, state, result):
        try:
            return self._settled(name, state, result)
        except Exception:
            self.deps.board.add_detail("exception-report", case_learning={"status": "unavailable"})
            return {}

    def _settled(self, name, state, result):
        pending = state.get("learning_pending")
        if not pending:
            return {}
        failed = bool(result.get("halt"))
        if not failed and name != pending["target"]:
            return {}
        if self.deps.cancelled():
            status, outcome = "unknown", "stopped"
        elif failed:
            status, outcome = "fail", "failure"
        elif name == "dast_wait":
            passed = result.get("gates", {}).get("dast") == "pass"
            reproduced = any(r.get("status") in ("REPRODUCED", "BLOCKED") for n in
                self.deps.board.snapshot()["nodes"] if n["id"] == "dast" for r in n.get("detail", {}).get("results", []))
            status, outcome = ("pass", "success") if passed else ("fail", "failure") if reproduced else ("unknown", "unknown")
        elif name == "onprem_wait":
            # This proves runtime binding/HTTP/cleanup, not a DAST or whole-service pass.
            passed = self.deps.board.state_of("stage") == "pass"
            status, outcome = ("pass", "success") if passed else ("unknown", "unknown")
        else:
            status, outcome = "unknown", "unknown"
        facts = {"node": name, "status": status, "run_id": self.deps.run_id,
                 "commit": result.get("commit") or state.get("commit") or self.deps.options.commit,
                 "image_digest": state.get("image_digest"), "onprem_run_id": state.get("onprem_run_id"),
                 "dast_run_id": state.get("dast_run_id"), "gates": result.get("gates"),
                 "failure_code": result.get("failure_code")}
        check = VerificationCheck(check="runtime-binding-http-cleanup" if name == "onprem_wait" else name,
            status=status, reference=state.get("onprem_run_id") or state.get("dast_run_id") or self.deps.run_id,
            sha256=digest(facts), details=json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False))
        self.record(pending["ref"], sequence=pending["attempt"] * 10 + 1, outcome=outcome,
            action=pending["action"], attempt=pending["attempt"], checks=(check,))
        return {"learning_pending": None}

    def unfinished(self, state):
        pending = state.get("learning_pending")
        if pending:
            self.record(pending["ref"], sequence=pending["attempt"] * 10 + 1,
                        outcome="stopped" if self.deps.cancelled() else "unknown",
                        action=pending["action"], attempt=pending["attempt"])


def begin_attempt(deps, ref, *, action, attempt, target):
    """Only called after the graph/operator has validated an allowed action."""
    CaseLearning(deps).record(ref, sequence=attempt * 10, action=action, attempt=attempt)
    return {"ref": ref, "action": action, "attempt": attempt, "target": target}
