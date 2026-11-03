#!/usr/bin/env python3
"""HAProxy 런타임 API 로 두 슬롯의 트래픽 비율을 바꾼다. 가중치 합을 100 으로 두어 값이 곧 비율이다."""

from __future__ import annotations

import argparse
import socket
import sys
import time

HOST = "haproxy"
PORT = 9999
BACKEND = "demo"
SLOTS = ("blue", "green")
SOCKET_TIMEOUT_SECONDS = 5
WAIT_INTERVAL_SECONDS = 1


def run_command(command: str, host: str = HOST, port: int = PORT) -> str:
    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT_SECONDS) as conn:
        conn.sendall(f"{command}\n".encode())
        conn.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := conn.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks).decode()


def split_commands(stable: str, canary: str, percent: int) -> tuple[str, str]:
    if not 0 <= percent <= 100:
        raise ValueError(f"비율은 0~100 이어야 한다: {percent}")
    # 새 버전을 먼저 올린다. 순서가 반대면 잠깐 두 슬롯 모두 0 이 되어 503 이 날 수 있다
    return (f"set weight {BACKEND}/{canary} {percent}", f"set weight {BACKEND}/{stable} {100 - percent}")


def parse_weight(reply: str) -> int:
    return int(reply.split()[0])


def other_slot(slot: str) -> str:
    return SLOTS[1 - SLOTS.index(slot)]


def active_slot(weights: dict[str, int]) -> str:
    return max(SLOTS, key=lambda slot: weights[slot])


def server_status(stat_csv: str, slot: str) -> str | None:
    lines = [line for line in stat_csv.splitlines() if line.strip()]
    if not lines:
        return None
    header = lines[0].lstrip("# ").split(",")
    for line in lines[1:]:
        row = dict(zip(header, line.split(",")))
        if row.get("pxname") == BACKEND and row.get("svname") == slot:
            return row.get("status")
    return None


def _weights() -> dict[str, int]:
    return {slot: parse_weight(run_command(f"get weight {BACKEND}/{slot}")) for slot in SLOTS}


def _wait_up(slot: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_status(run_command("show stat"), slot) == "UP":
            return True
        time.sleep(WAIT_INTERVAL_SECONDS)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("active")
    split = sub.add_parser("split")
    split.add_argument("--stable", choices=SLOTS, required=True)
    split.add_argument("--canary", choices=SLOTS, required=True)
    split.add_argument("--percent", type=int, required=True)
    wait = sub.add_parser("wait-up")
    wait.add_argument("--slot", choices=SLOTS, required=True)
    wait.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)

    if args.action == "active":
        print(active_slot(_weights()))
        return 0
    if args.action == "wait-up":
        return 0 if _wait_up(args.slot, args.timeout) else 1
    for command in split_commands(args.stable, args.canary, args.percent):
        reply = run_command(command).strip()
        if reply:
            print(f"HAProxy 오류: {reply}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
