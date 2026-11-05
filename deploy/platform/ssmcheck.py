"""SSM 명령 결과 판정과 플랫폼 연결 검사.

- wait: 명령이 끝날 때까지 기다리고, 최종 상태가 Success이고 원격 종료 코드가 0일 때만 성공으로 본다.
- links: Terraform 출력(서비스·포트·연결 목록)으로 기대 검사 목록을 만들고, 각 인스턴스에서 TCP 연결을 시도해
  항목별로 대조한다. 실행 자체가 실패하면 "차단 정상"이 아니라 검사 오류다.

사용
  python3 ssmcheck.py wait --region R --command-id C --instance-id I [--timeout 900]
  terraform output -json | python3 ssmcheck.py links
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field

DONE_STATES = {"Success", "Failed", "Cancelled", "TimedOut", "Cancelling"}
WAIT_STATES = {"Pending", "InProgress", "Delayed"}
# 연결 검사 결과: open(연결됨), closed(시간 초과 = 보안 그룹이 막음), refused(닿았지만 서비스 없음), error(DNS 등 검사 오류)
RESULTS = {"open", "closed", "refused", "error"}


class CheckError(Exception):
    pass


def aws(args: list[str]) -> str:
    done = subprocess.run(["aws", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise CheckError(f"aws {' '.join(args[:2])} 실패: {(done.stderr.strip().splitlines() or ['?'])[-1][:200]}")
    return done.stdout


def wait_command(region: str, command_id: str, instance_id: str, *, timeout: float = 900, interval: float = 5,
                 run=aws, sleep=time.sleep) -> dict:
    """끝난 명령의 결과를 돌려준다. 성공(Success + 종료 코드 0)이 아니면 CheckError."""
    deadline = time.monotonic() + timeout
    last = "조회 전"
    while True:
        try:
            raw = run(["ssm", "get-command-invocation", "--region", region, "--command-id", command_id,
                       "--instance-id", instance_id, "--output", "json"])
            inv = json.loads(raw)
        except (CheckError, json.JSONDecodeError) as exc:
            # 막 보낸 명령은 잠깐 조회되지 않을 수 있다. 한도 안에서만 다시 본다.
            inv, last = None, f"조회 실패: {exc}"
        if inv is not None:
            status = inv.get("Status")
            last = status or "상태 없음"
            if status in DONE_STATES:
                result = {"status": status, "code": inv.get("ResponseCode"),
                          "stdout": inv.get("StandardOutputContent") or "",
                          "stderr": inv.get("StandardErrorContent") or ""}
                if status != "Success":
                    raise CheckError(f"원격 명령 {status} (종료 코드 {result['code']})")
                if result["code"] != 0:
                    raise CheckError(f"원격 명령이 성공으로 표시됐지만 종료 코드가 {result['code']}다")
                return result
            if status not in WAIT_STATES:
                raise CheckError(f"알 수 없는 명령 상태: {status!r}")
        if time.monotonic() >= deadline:
            raise CheckError(f"대기 한도({int(timeout)}초) 초과, 마지막 상태: {last}")
        sleep(interval)


# ---------- 연결 검사 ----------

@dataclass(frozen=True)
class Check:
    source: str
    host: str
    port: int
    want: str  # open | closed

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"


def expected_checks(outputs: dict) -> dict[str, list[Check]]:
    """서비스마다: 다른 모든 서비스의 모든 포트에 대한 기대값. links에 있으면 open, 없으면 closed."""
    names = outputs["internal_names"]["value"]
    ports = outputs["service_ports"]["value"]  # {서비스: [포트...]}
    links = {(l["from"], l["to"], int(l["port"])) for l in outputs["links"]["value"]}
    unknown = {s for l in links for s in (l[0], l[1])} - set(names)
    if unknown:
        raise CheckError(f"연결 목록에 없는 서비스가 있다: {sorted(unknown)}")
    plan: dict[str, list[Check]] = {}
    for src in names:
        plan[src] = [Check(src, names[dst], int(port), "open" if (src, dst, int(port)) in links else "closed")
                     for dst in sorted(ports) if dst != src for port in ports[dst]]
    return plan


def remote_script(checks: list[Check]) -> str:
    """인스턴스에서 실행: 항목마다 한 줄 `RESULT <host>:<port> <open|closed|refused|error> <설명>`."""
    targets = " ".join(c.key for c in checks)
    return (
        "set -u\n"
        f"for t in {targets}; do\n"
        "  h=${t%:*}; p=${t##*:}\n"
        "  if ! getent hosts \"$h\" >/dev/null; then echo \"RESULT $t error dns\"; continue; fi\n"
        "  err=$(timeout 3 bash -c \"exec 3<>/dev/tcp/$h/$p\" 2>&1); rc=$?\n"
        "  if [ $rc -eq 0 ]; then echo \"RESULT $t open -\";\n"
        "  elif [ $rc -eq 124 ]; then echo \"RESULT $t closed timeout\";\n"
        "  elif echo \"$err\" | grep -qi refused; then echo \"RESULT $t refused -\";\n"
        "  else echo \"RESULT $t error rc=$rc\"; fi\n"
        "done\n"
        "echo END\n"
    )


@dataclass
class Tally:
    expected: int = 0
    actual: int = 0
    ok: int = 0
    fail: int = 0
    error: int = 0
    lines: list[str] = field(default_factory=list)


def judge(checks: list[Check], stdout: str, tally: Tally) -> None:
    """원격 출력을 기대 목록과 항목별로 대조한다. 누락·중복·예상 밖·형식 오류는 모두 검사 오류."""
    tally.expected += len(checks)
    want = {c.key: c for c in checks}
    seen: dict[str, str] = {}
    rows = [line for line in stdout.splitlines() if line.strip()]
    if not rows or rows[-1] != "END":
        tally.error += 1
        tally.lines.append("  ERROR 출력이 비었거나 끝 표시(END)가 없다")
    for line in rows:
        if line == "END":
            continue
        parts = line.split(" ", 3)
        if len(parts) < 3 or parts[0] != "RESULT" or parts[2] not in RESULTS:
            tally.error += 1
            tally.lines.append(f"  ERROR 형식 오류: {line[:80]!r}")
            continue
        key, got = parts[1], parts[2]
        if key not in want:
            tally.error += 1
            tally.lines.append(f"  ERROR 예상 밖 항목: {key}")
            continue
        if key in seen:
            tally.error += 1
            tally.lines.append(f"  ERROR 중복 항목: {key}")
            continue
        seen[key] = got
    for key, check in want.items():
        got = seen.get(key)
        if got is None:
            tally.error += 1
            tally.lines.append(f"  ERROR 결과 누락: {key}")
            continue
        tally.actual += 1
        if got == "error":
            tally.error += 1
            tally.lines.append(f"  ERROR {key} 검사 실패(want={check.want})")
        elif got == check.want:
            tally.ok += 1
            tally.lines.append(f"  OK    {key} want={check.want} got={got}")
        else:
            tally.fail += 1
            tally.lines.append(f"  FAIL  {key} want={check.want} got={got}")


def run_links(outputs: dict, *, run=aws, sleep=time.sleep, out=print, wait_timeout: float = 180) -> int:
    region = outputs["region"]["value"]
    instances = outputs["instance_ids"]["value"]
    plan = expected_checks(outputs)
    tally = Tally()
    for src, checks in plan.items():
        out(f"== {src} ({len(checks)}건)")
        tally.lines = []
        try:
            params = json.dumps({"commands": [remote_script(checks)]})
            command_id = json.loads(run(["ssm", "send-command", "--region", region, "--instance-ids", instances[src],
                                         "--document-name", "AWS-RunShellScript", "--parameters", params,
                                         "--output", "json"]))["Command"]["CommandId"]
            result = wait_command(region, command_id, instances[src], timeout=wait_timeout, interval=3, run=run, sleep=sleep)
            judge(checks, result["stdout"], tally)
        except (CheckError, KeyError, json.JSONDecodeError) as exc:
            tally.expected += len(checks)
            tally.error += len(checks)
            tally.lines.append(f"  ERROR 이 인스턴스의 검사 전체 실패: {exc}")
        for line in tally.lines:
            out(line)
    out(f"기대 {tally.expected}건 · 결과 {tally.actual}건 · 정상 {tally.ok} · 불일치 {tally.fail} · 검사 오류 {tally.error}")
    if tally.fail == 0 and tally.error == 0 and tally.ok == tally.expected and tally.expected > 0:
        out("모든 연결이 설계와 같다")
        return 0
    out("설계와 다르거나 확인하지 못한 연결이 있다")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait")
    w.add_argument("--region", required=True)
    w.add_argument("--command-id", required=True)
    w.add_argument("--instance-id", required=True)
    w.add_argument("--timeout", type=float, default=900)
    sub.add_parser("links")
    args = parser.parse_args(argv)
    if args.cmd == "wait":
        try:
            result = wait_command(args.region, args.command_id, args.instance_id, timeout=args.timeout)
        except CheckError as exc:
            # 실패 진단: 원격 stdout/stderr 끝부분만 보여 준다(배포 스크립트는 비밀값을 출력하지 않는다).
            try:
                inv = json.loads(aws(["ssm", "get-command-invocation", "--region", args.region, "--command-id",
                                      args.command_id, "--instance-id", args.instance_id, "--output", "json"]))
                print((inv.get("StandardOutputContent") or "")[-1500:])
                print((inv.get("StandardErrorContent") or "")[-1500:], file=sys.stderr)
            except (CheckError, json.JSONDecodeError):
                pass
            print(f"배포 실패: {exc}", file=sys.stderr)
            return 1
        print(result["stdout"].rstrip())
        return 0
    return run_links(json.load(sys.stdin))


if __name__ == "__main__":
    sys.exit(main())
