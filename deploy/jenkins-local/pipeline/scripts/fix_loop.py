#!/usr/bin/env python3
"""판단 서비스의 패치를 사본에 적용하고 테스트·재검사·재판단으로 확인한다.

여기서 통과해도 배포하지 않는다. 적용 여부는 파이프라인의 사람 확인이 정한다.
패치한 코드는 LLM 산출물이라 Jenkins 안에서 실행하지 않고 네트워크 없는 컨테이너에서만 실행한다.
검사 설정은 패치가 닿을 수 없는 원본에서 읽고, 스캐너가 지목한 파일만 고칠 수 있다.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import gate

EXIT_VERIFIED = 0
EXIT_STOPPED = 40
DEFAULT_MAX_ATTEMPTS = 2  # 패치가 수렴하지 않을 때 LLM 호출이 끝없이 늘지 않게 한다
SANDBOX_TIMEOUT_SECONDS = 120
SANDBOX_IMAGE = "python:3.12-alpine"  # demo-app 과 같은 기반 이미지
SCAN_TIMEOUT_SECONDS = 180
SKIPPED_DIRS = {"__pycache__"}


class PatchRejected(ValueError):
    """패치를 적용할 수 없거나 허용 범위를 벗어났다."""


@dataclass(frozen=True)
class FixReport:
    outcome: str  # verified | stopped
    attempts: int
    reason: str
    diff: str = ""


@dataclass(frozen=True)
class Tools:
    run_tests: Callable[[int], bool]
    scan: Callable[[int], dict[str, Any]]
    judge: Callable[[int, dict[str, Any]], dict[str, Any]]


def snapshot(workdir: Path) -> dict[str, str]:
    """workdir 기준 경로별 내용 해시. 심볼릭 링크는 내용을 따라가지 않고 표시만 한다."""
    files: dict[str, str] = {}
    for path in sorted(workdir.rglob("*")):
        if SKIPPED_DIRS.intersection(path.parts):
            continue
        relative = path.relative_to(workdir).as_posix()
        if path.is_symlink():
            files[relative] = "symlink"
        elif path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def sarif_paths(sarif: Mapping[str, Any]) -> set[str]:
    return {location["physicalLocation"]["artifactLocation"]["uri"]
            for run in sarif.get("runs", []) for result in run.get("results", [])
            for location in result.get("locations", [])}


def apply_diffs(workdir: Path, diffs: list[dict[str, str]], allowed: set[str]) -> None:
    """헤더가 아니라 적용 전후 트리를 비교한다. 패치가 무엇을 적었든 실제 바뀐 파일로 판단한다."""
    before = snapshot(workdir)
    for item in diffs:
        for flags in (["--check"], []):
            done = subprocess.run(["git", "apply", *flags, "-"], cwd=workdir, input=item["diff"], text=True,
                                  capture_output=True, check=False)
            if done.returncode != 0:
                raise PatchRejected(f"git apply 실패: {done.stderr.strip()[:200]}")
    after = snapshot(workdir)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    if not changed:
        raise PatchRejected("패치가 아무것도 바꾸지 않았다")
    outside = changed - allowed
    if outside:
        raise PatchRejected(f"스캐너가 지목하지 않은 파일을 바꿨다: {sorted(outside)}")
    if any(after.get(path) == "symlink" for path in changed):
        raise PatchRejected("패치가 심볼릭 링크를 만들었다")


def run_loop(workdir: Path, result: Mapping[str, Any], sarif: Mapping[str, Any], *, max_attempts: int,
             tools: Tools) -> FixReport:
    """검증되지 않으면 어떤 경우든 stopped 다. 통과로 넘어가는 예외 경로를 두지 않는다."""
    applied: list[str] = []
    for attempt in range(1, max_attempts + 1):
        diffs = (result.get("remediation") or {}).get("code_diffs") or []
        if not diffs:
            return FixReport("stopped", attempt, "판단 결과에 패치가 없다")
        try:
            apply_diffs(workdir, diffs, sarif_paths(sarif))
        except PatchRejected as exc:
            return FixReport("stopped", attempt, f"패치 거절: {exc}")
        applied.extend(item["diff"] for item in diffs)
        if not tools.run_tests(attempt):
            return FixReport("stopped", attempt, "패치 후 샌드박스 테스트 실패")
        try:
            sarif = tools.scan(attempt)
            result = tools.judge(attempt, sarif)
        except gate.GateUnavailable as exc:
            return FixReport("stopped", attempt, f"재판단을 받지 못했다: {exc}")
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return FixReport("stopped", attempt, f"재검사 실패: {type(exc).__name__}")
        verdict = result.get("verdict")
        if verdict == "pass":
            return FixReport("verified", attempt, "테스트·재검사·재판단 통과", "".join(applied))
        if verdict != "fix":
            return FixReport("stopped", attempt, f"재판단 결과 {verdict}: 사람이 정한다")
    return FixReport("stopped", max_attempts, f"{max_attempts}회 시도로 해결되지 않았다")


def sandbox_tests(workdir: Path, name: str, image: str = SANDBOX_IMAGE) -> bool:
    """네트워크·권한 없이 테스트한다. docker.sock 과 토큰이 있는 Jenkins 에서 직접 돌리지 않는다."""
    create = ["docker", "create", "--name", name, "--network", "none", "--memory", "256m", "--pids-limit", "128",
              "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534",
              "-e", "PYTHONDONTWRITEBYTECODE=1", "-w", "/tmp", image,
              "python3", "-m", "unittest", "discover", "-s", "app"]
    try:
        subprocess.run(create, check=True, capture_output=True, text=True, timeout=SANDBOX_TIMEOUT_SECONDS)
        subprocess.run(["docker", "cp", "-q", str(workdir / "app"), f"{name}:/tmp/app"], check=True,
                       capture_output=True, text=True, timeout=SANDBOX_TIMEOUT_SECONDS)
        done = subprocess.run(["docker", "start", "-a", name], capture_output=True, text=True, check=False,
                              timeout=SANDBOX_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"샌드박스 실행 실패: {type(exc).__name__}", file=sys.stderr)
        return False
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    print(done.stderr[-2000:], file=sys.stderr)
    return done.returncode == 0


def semgrep_scan(workdir: Path, config: Path, out: Path) -> dict[str, Any]:
    subprocess.run(["semgrep", "scan", "--config", str(config), "--sarif", "--output", str(out.resolve()),
                    "--metrics=off", "--disable-version-check", "app"],
                   cwd=workdir, check=True, capture_output=True, text=True, timeout=SCAN_TIMEOUT_SECONDS)
    return json.loads(out.read_text(encoding="utf-8"))


def _judge(args: argparse.Namespace, workdir: Path, attempt: int, sarif: dict[str, Any]) -> dict[str, Any]:
    run_id = f"{args.id}-{attempt}"
    with contextlib.chdir(workdir):  # 경로가 SARIF 의 app/... 와 맞아야 한다
        code = gate.collect_code(Path("app"))
    body = gate.security_request(assessment_id=f"{run_id}-{int(time.time())}", service_id=args.service,
                                 commit=args.commit, sarif=sarif, code_files=code)
    try:
        result = gate.call_gate(args.delta_url, args.token, body)
    except gate.GateUnavailable:
        gate.append_event(Path("events.jsonl"), run_id=run_id, step="fix", state="unavailable", verdict=None)
        raise
    Path(f"fix-attempt-{attempt}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    gate.append_event(Path("events.jsonl"), run_id=run_id, step="fix", state="judged", verdict=result.get("verdict"))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--sarif", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("app"))
    parser.add_argument("--workdir", type=Path, default=Path("fix"))
    parser.add_argument("--service", default="demo-app")
    parser.add_argument("--commit", default="local")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.delta_url, args.token = os.environ["DELTA_URL"], os.environ["DELTA_API_TOKEN"]
    workdir = args.workdir.resolve()
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.copytree(args.source, workdir / "app", ignore=shutil.ignore_patterns(*SKIPPED_DIRS))
    config = (args.source / ".semgrep.yml").resolve()
    tools = Tools(
        run_tests=lambda attempt: sandbox_tests(workdir, f"delta-sandbox-{args.id}-{attempt}"),
        scan=lambda attempt: semgrep_scan(workdir, config, Path(f"fix-attempt-{attempt}.sarif")),
        judge=lambda attempt, sarif: _judge(args, workdir, attempt, sarif),
    )
    report = run_loop(workdir, json.loads(args.result.read_text(encoding="utf-8")),
                      json.loads(args.sarif.read_text(encoding="utf-8")), max_attempts=args.max_attempts, tools=tools)
    Path("fix-report.json").write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    if report.diff:
        Path("fix.diff").write_text(report.diff, encoding="utf-8")
    print(f"수정 루프: {report.outcome} ({report.attempts}회) {report.reason}")
    return EXIT_VERIFIED if report.outcome == "verified" else EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
