#!/usr/bin/env python3
"""delta-ai 판단을 호출하고 판정을 종료 코드로 돌려준다. 표준 라이브러리만 쓴다.

응답을 받지 못하면 통과로 보지 않는다(EXIT_UNAVAILABLE). 승격 여부는 호출자가 종료 코드로 정한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

EXIT_PASS = 0
EXIT_FIX = 10
EXIT_NEEDS_HUMAN = 20
EXIT_UNAVAILABLE = 30
VERDICT_EXIT = {"pass": EXIT_PASS, "fix": EXIT_FIX, "needs_human": EXIT_NEEDS_HUMAN}

REQUEST_TIMEOUT_SECONDS = 150  # delta-ai 의 LLM 제한(60초 × 2회)보다 길어야 한다
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 3
LOG_TAIL_CHARS = 20_000
CODE_SUFFIXES = (".py", ".js", ".ts", ".java", ".go")
MAX_CODE_FILES = 50
MAX_CODE_FILE_CHARS = 256_000


class GateUnavailable(RuntimeError):
    """판단을 받지 못했다."""


def read_tail(path: Path, limit: int = LOG_TAIL_CHARS) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def read_logs(paths: Sequence[Path]) -> str:
    """판단 서비스는 끝부분만 쓰므로 원인에 가까운 로그를 뒤에 둔다."""
    return "\n".join(f"== {path.name} ==\n{read_tail(path)}" for path in paths if path.is_file())


def collect_code(root: Path) -> dict[str, str]:
    """경로는 SARIF 의 uri 와 맞도록 작업 디렉터리 기준으로 쓴다."""
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in CODE_SUFFIXES)
    collected: dict[str, str] = {}
    for path in files[:MAX_CODE_FILES]:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= MAX_CODE_FILE_CHARS:
            collected[path.as_posix()] = text
    return collected


def security_request(*, assessment_id: str, service_id: str, commit: str, sarif: dict[str, Any],
                     code_files: Mapping[str, str]) -> dict[str, Any]:
    return {"assessment_id": assessment_id, "kind": "code_security",
            "service": {"service_id": service_id, "commit": commit},
            "evidence": {"sarif": sarif, "code_files": dict(code_files)}}


def deployment_request(*, assessment_id: str, service_id: str, commit: str, stage: str, status: str,
                       target: str | None = None, traffic_percent: int | None = None, exit_code: int | None = None,
                       log_tail: str = "", metrics: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {"assessment_id": assessment_id, "kind": "deployment_gate",
            "service": {"service_id": service_id, "commit": commit},
            "evidence": {"deployment": {"stage": stage, "status": status, "target": target,
                                        "traffic_percent": traffic_percent, "exit_code": exit_code,
                                        "log_tail": log_tail, "metrics": list(metrics)}}}


def post_json(url: str, token: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def call_gate(url: str, token: str, body: dict[str, Any], *,
              poster: Callable[..., dict[str, Any]] = post_json,
              sleep: Callable[[float], None] = time.sleep,
              timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
    """503·연결 실패만 재시도한다. 401·422 는 다시 해도 같으니 바로 실패한다."""
    last_error = "호출하지 않음"
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            sleep(RETRY_DELAY_SECONDS)
        try:
            return poster(f"{url}/v1/assessments", token, body, timeout)
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code != 503:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
    raise GateUnavailable(last_error)


def exit_code_for(result: Mapping[str, Any]) -> int:
    return VERDICT_EXIT.get(str(result.get("verdict")), EXIT_UNAVAILABLE)


def summarize(result: Mapping[str, Any]) -> str:
    lines = [f"판정={result.get('verdict')} 사유={','.join(result.get('reasons', [])) or '-'}"]
    lines += [f"- [{f.get('rule_id')}] {f.get('title')}: {f.get('why') or f.get('evidence')}"
              for f in result.get("findings", [])]
    return "\n".join(lines)


def append_event(path: Path, *, run_id: str, step: str, state: str, verdict: str | None) -> None:
    """진행 그래프용 이벤트(ADR-AI-24)."""
    event = {"run_id": run_id, "step": step, "state": state, "verdict": verdict,
             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("code_security", "deployment_gate"))
    parser.add_argument("--id", required=True)
    parser.add_argument("--service", default="demo-app")
    parser.add_argument("--commit", default=os.environ.get("RELEASE_ID", "local"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=Path("events.jsonl"))
    parser.add_argument("--sarif", type=Path)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--stage")
    parser.add_argument("--status", choices=("succeeded", "failed"), default="succeeded")
    parser.add_argument("--target")
    parser.add_argument("--traffic", type=int)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--log", type=Path, action="append", default=[])
    parser.add_argument("--metrics", type=Path)
    return parser


def _request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "code_security":
        sarif = json.loads(args.sarif.read_text(encoding="utf-8"))
        return security_request(assessment_id=args.id, service_id=args.service, commit=args.commit, sarif=sarif,
                                code_files=collect_code(args.code_root))
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))["metrics"] if args.metrics else []
    return deployment_request(assessment_id=args.id, service_id=args.service, commit=args.commit,
                              stage=args.stage or "unknown", status=args.status, target=args.target,
                              traffic_percent=args.traffic, exit_code=args.exit_code,
                              log_tail=read_logs(args.log), metrics=tuple(metrics))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    step = args.stage or args.kind
    try:
        result = call_gate(os.environ["DELTA_URL"], os.environ["DELTA_API_TOKEN"], _request_from_args(args))
    except GateUnavailable as exc:
        print(f"판단을 받지 못했다: {exc}", file=sys.stderr)
        append_event(args.events, run_id=args.id, step=step, state="unavailable", verdict=None)
        return EXIT_UNAVAILABLE
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summarize(result))
    append_event(args.events, run_id=args.id, step=step, state="judged", verdict=result.get("verdict"))
    return exit_code_for(result)


if __name__ == "__main__":
    sys.exit(main())
