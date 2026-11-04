#!/usr/bin/env python3
"""HAProxy 런타임 API 로 두 서버의 트래픽 비율을 바꾼다. 가중치 합을 100 으로 두어 값이 곧 비율이다.

카나리 시험의 blue/green 과 클라우드 이전의 aws/gcp 가 같은 함수를 쓴다(traffic-control 도 이 파일을 복사해 쓴다).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from collections.abc import Mapping

HOST = os.environ.get("HAPROXY_HOST", "haproxy")
PORT = int(os.environ.get("HAPROXY_PORT", "9999"))
MAX_WEIGHT = 100
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


def weight_commands(backend: str, weights: Mapping[str, int]) -> tuple[str, ...]:
    """새 값이 큰 서버부터 바꾼다. 순서가 반대면 잠깐 모든 서버가 0 이 되어 503 이 날 수 있다(100% → 0% 롤백)."""
    for server, weight in weights.items():
        if not 0 <= weight <= MAX_WEIGHT:
            raise ValueError(f"비율은 0~{MAX_WEIGHT} 이어야 한다: {server}={weight}")
    if sum(weights.values()) != MAX_WEIGHT:
        raise ValueError(f"가중치 합은 {MAX_WEIGHT} 이어야 한다: {dict(weights)}")
    order = sorted(weights, key=lambda server: -weights[server])
    return tuple(f"set weight {backend}/{server} {weights[server]}" for server in order)


def split_commands(stable: str, canary: str, percent: int, backend: str = BACKEND) -> tuple[str, ...]:
    if not 0 <= percent <= MAX_WEIGHT:
        raise ValueError(f"비율은 0~{MAX_WEIGHT} 이어야 한다: {percent}")
    return weight_commands(backend, {canary: percent, stable: MAX_WEIGHT - percent})


def apply_weights(backend: str, weights: Mapping[str, int], *, host: str = HOST, port: int = PORT) -> None:
    """HAProxy 가 거절하면 RuntimeError. 이미 바뀐 앞 서버는 되돌리지 않으므로 호출자가 다시 맞춘다."""
    for command in weight_commands(backend, weights):
        reply = run_command(command, host, port).strip()
        if reply:
            raise RuntimeError(f"HAProxy 오류: {reply}")


def read_weights(backend: str, servers: tuple[str, ...], *, host: str = HOST, port: int = PORT) -> dict[str, int]:
    return {server: parse_weight(run_command(f"get weight {backend}/{server}", host, port)) for server in servers}


def parse_weight(reply: str) -> int:
    return int(reply.split()[0])


def other_slot(slot: str) -> str:
    return SLOTS[1 - SLOTS.index(slot)]


def active_slot(weights: dict[str, int]) -> str:
    return max(SLOTS, key=lambda slot: weights[slot])


def server_status(stat_csv: str, slot: str, backend: str = BACKEND) -> str | None:
    lines = [line for line in stat_csv.splitlines() if line.strip()]
    if not lines:
        return None
    header = lines[0].lstrip("# ").split(",")
    for line in lines[1:]:
        row = dict(zip(header, line.split(",")))
        if row.get("pxname") == backend and row.get("svname") == slot:
            return row.get("status")
    return None


def _weights() -> dict[str, int]:
    return read_weights(BACKEND, SLOTS)


def _wait_up(slot: str, timeout: float, backend: str = BACKEND) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_status(run_command("show stat"), slot, backend) == "UP":
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
    wait.add_argument("--slot", required=True, help="서버 이름(blue·green, aws·gcp, app)")
    wait.add_argument("--backend", default=BACKEND)
    wait.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)

    if args.action == "active":
        print(active_slot(_weights()))
        return 0
    if args.action == "wait-up":
        return 0 if _wait_up(args.slot, args.timeout, args.backend) else 1
    try:
        apply_weights(BACKEND, {args.canary: args.percent, args.stable: MAX_WEIGHT - args.percent})
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
