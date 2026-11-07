"""Isolated HTTPS console + real HTTP AI adapter fixture; no cloud or model calls.

Generate a temporary certificate and run with --root/--cert/--key. Test-only credentials
are fixed here and must never be used as production configuration.
"""

import argparse
import os
import threading
from pathlib import Path

import uvicorn
from delta_ai.api import create_app as create_ai
from delta_ai.cases import CaseStore, IncidentCase, Resolution, VerificationCheck
from delta_ai.diagnostic_contract import DiagnosisRequest, unavailable_report
from delta_ai.store import RuleStore
from delta_orchestrator import app as console
from delta_orchestrator.pipelines import Pipelines


def seed(store):
    for index in range(32):
        outcome = "unknown" if index == 1 else "failure" if index == 2 else "success"
        request = DiagnosisRequest(
            case_id=f"case-{index:02}",
            run_id=f"run-{index:02}",
            service_id="notes-service",
            node_id="onprem_wait",
            trigger="execution_failure",
            context={"environment": "onprem", "error_code": "http_unavailable"},
            observations=[
                {
                    "evidence_id": "runtime",
                    "source": "http",
                    "summary": "HTTP 503 응답 확인",
                }
            ],
        )
        facts = '{"HTTP":200,"image_bound":true,"cleanup":"stopped"}'
        import hashlib

        checks = (
            ()
            if outcome == "unknown"
            else (
                VerificationCheck(
                    check="runtime-binding-http-cleanup",
                    status="fail" if outcome == "failure" else "pass",
                    reference=f"native-{index:02}",
                    sha256=hashlib.sha256(facts.encode()).hexdigest(),
                    details=facts,
                ),
            )
        )
        store.publish(
            IncidentCase(
                request=request,
                report=unavailable_report(request).model_copy(
                    update={
                        "summary": "HTTP 장애 후 재검사 결과를 확인하세요. <img src=x onerror=alert(1)>"
                    }
                ),
                report_sha256="b" * 64,
                resolution=Resolution(
                    outcome=outcome,
                    action="none" if outcome == "unknown" else "retry_same_job",
                    scope="onprem_wait",
                    checks=checks,
                ),
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "cert", "key"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    os.environ.update(
        ORCH_PUBLIC_ORIGIN="https://127.0.0.1:8447",
        ORCH_ADMIN_PASSWORD="fixture-console-password",
        ORCH_SESSION_SECRET="fixture-session-secret-for-local-tests-only",
        DELTA_DIAGNOSTIC_URL="http://127.0.0.1:8127",
        DELTA_DIAGNOSTIC_TOKEN="fixture-producer",
        DELTA_CASE_REVIEW_TOKEN="fixture-operator",
    )
    store = CaseStore(args.root / "cases.sqlite")
    seed(store)
    ai = create_ai(
        llm=None,
        store=RuleStore(args.root / "rules.json"),
        token="fixture-producer",
        review_token="fixture-operator",
        case_store=store,
    )

    async def status():
        return {
            "services": [],
            "checked_at": "fixture",
            "public_urls": {},
            "release_targets": [],
        }

    console.collect = status
    app = console.create_app(Pipelines(args.root / "pipeline"))
    ai_server = uvicorn.Server(
        uvicorn.Config(ai, host="127.0.0.1", port=8127, log_level="warning")
    )
    threading.Thread(target=ai_server.run, daemon=True).start()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8447,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
