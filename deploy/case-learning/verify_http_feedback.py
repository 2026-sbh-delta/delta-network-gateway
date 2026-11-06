"""Run inside the deployed orchestrator. Controlled HTTP fault, real remote Claude/API.

This never submits a customer build/deploy/scanner/DNS job. RUN_GROUP and PHASE are
injected by the reviewed renderer; each case is explicitly a validation fixture.
"""
import base64
import gzip
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from delta_ai.cases import IncidentCase, VerificationCheck, digest
from delta_ai.diagnostic_contract import DiagnosisRequest, validate_report, DiagnosisReport
from delta_pipeline.case_learning import CaseLearning
from delta_pipeline.diagnostic_client import DiagnosticClient
from delta_pipeline.model import Board, Options
from delta_pipeline.reports import ReportStore


def main():
    assert PHASE in ("first", "next")
    assert RUN_GROUP.isalnum() and len(RUN_GROUP) == 32
    root = Path(os.environ["ORCH_STATE_DIR"]) / "case-learning-verification" / RUN_GROUP / PHASE
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    ready = {"value": False}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code = 200 if ready["value"] else 503
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"validation fixture only")
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    def probe():
        try:
            with opener.open(f"http://127.0.0.1:{server.server_port}/health", timeout=3) as response:
                return response.status
        except HTTPError as error:
            code = error.code
            error.close()
            return code
    try:
        first = probe()
        assert first == 503
        run_id = "feedback-" + RUN_GROUP + "-" + PHASE
        case_id = "case-" + RUN_GROUP + "-" + PHASE
        request = DiagnosisRequest(case_id=case_id, run_id=run_id,
            service_id="feedback-validation-" + RUN_GROUP[:12], node_id="runtime_http_check",
            trigger="execution_failure", context={"environment": "aws", "error_code": "HTTP_503"},
            observations=[{"evidence_id": "http-" + PHASE, "source": "aws-loopback-validation",
                "summary": f"명시적 검증용 HTTP 서버에서 {'첫' if PHASE == 'first' else '다음 독립'} 요청의 실제 응답은 503이다. 고객 서비스 장애가 아니다."}])
        client = DiagnosticClient(os.environ["DELTA_DIAGNOSTIC_URL"], os.environ["DELTA_DIAGNOSTIC_TOKEN"],
                                  os.environ.get("DELTA_DIAGNOSTIC_CA_BASE64"))
        started = time.monotonic()
        report = validate_report(DiagnosisReport.model_validate(client.diagnose(request.model_dump(mode="json"))), request)
        assert report.status == "analyzed" and report.model and report.model.startswith("claude")
        if PHASE == "first":
            assert not report.related_cases
        else:
            assert [c.case_id for c in report.related_cases] == ["case-" + RUN_GROUP + "-first"]
        ref = ReportStore(root / "reports").save(request, report)
        board = Board(root / "board.json", Options("https://github.com/2026-sbh-delta/delta-sample-app", "a" * 40,
                                                    diagnostics=True), run_id)
        deps = SimpleNamespace(report_dir=root / "reports", board=board, diagnostic=client)
        learning = CaseLearning(deps)
        learning.record(ref)
        action = "restore_validation_handler" if PHASE == "first" else "repeat_without_recovery"
        learning.record(ref, sequence=10, action=action, attempt=1)
        ready["value"] = PHASE == "first"
        after = probe()
        assert after == (200 if PHASE == "first" else 503)
        facts = {"validation_fixture": True, "run_id": run_id, "before_http": first,
                 "after_http": after, "action": action, "customer_resources_changed": False}
        check = VerificationCheck(check="actual-loopback-http", status="pass" if after == 200 else "fail",
            reference=run_id, sha256=digest(facts),
            details=json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        outcome = "success" if after == 200 else "failure"
        learning.record(ref, sequence=11, outcome=outcome, action=action, attempt=1, checks=(check,))
        learning.flush()
        files = list((root / "case-outbox").glob("*.json"))
        assert len(files) == len(list((root / "case-outbox").glob("*.ack"))) == 3
        record = IncidentCase.model_validate_json((root / "case-outbox" / (case_id + "-0011.json")).read_bytes())
        result = {"run_group": RUN_GROUP, "phase": PHASE, "run_id": run_id, "case_id": case_id,
            "case_revision": digest(record), "resolution": record.resolution.model_dump(mode="json"),
            "report": report.model_dump(mode="json"), "report_reference": ref.model_dump(mode="json"),
            "elapsed_seconds": round(time.monotonic() - started, 2), "published_versions": 3,
            "customer_jobs_submitted": 0, "validation_fixture": True}
        path = root / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        return result
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        print(base64.b64encode(gzip.compress(json.dumps(main(), ensure_ascii=False).encode())).decode())
    except Exception as error:
        import traceback
        raise SystemExit("Feedback verification failed: " + type(error).__name__ + " " +
                         str([(f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)])) from None
