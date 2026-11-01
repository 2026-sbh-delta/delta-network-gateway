#!/usr/bin/env python3
"""게이트웨이로 요청을 보내 새 버전의 지표를 잰다. 기준을 넘으면 EXIT_EXCEEDED 로 끝나 즉시 롤백하게 한다."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

EXIT_OK = 0
EXIT_EXCEEDED = 3
REQUEST_TIMEOUT_SECONDS = 5
WORKERS = 16
MIN_SAMPLES_FOR_ROLLBACK = 20  # 표본이 적을 때는 자동 롤백 대신 판단 서비스가 사람에게 넘긴다


@dataclass(frozen=True)
class Sample:
    version: str | None  # 앱까지 닿지 못한 응답은 버전을 모른다
    status: int
    seconds: float


def fetch(url: str) -> Sample:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return Sample(response.headers.get("X-App-Version"), response.status, time.monotonic() - started)
    except urllib.error.HTTPError as exc:
        return Sample(exc.headers.get("X-App-Version"), exc.code, time.monotonic() - started)
    except (urllib.error.URLError, TimeoutError):
        return Sample(None, 0, time.monotonic() - started)


def p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)]


def summarize(samples: Sequence[Sample], version: str, *, max_error_rate: float, max_p95_ms: float) -> dict:
    """버전을 모르는 실패는 새 버전 탓으로 친다. 게이트웨이 오류를 놓치는 쪽이 더 위험하다."""
    canary = [s for s in samples if s.version == version]
    unattributed = [s for s in samples if s.version is None]
    measured = [*canary, *unattributed]
    errors = sum(1 for s in measured if s.status == 0 or s.status >= 500)
    latency = p95([s.seconds * 1000 for s in canary])
    return {
        "version": version,
        "total_requests": len(samples),
        "canary_requests": len(canary),
        "unattributed_failures": len(unattributed),
        "metrics": [
            {"name": "error_rate", "value": errors / len(measured) if measured else None,
             "threshold": max_error_rate, "samples": len(measured)},
            {"name": "latency_p95_ms", "value": latency, "threshold": max_p95_ms, "samples": len(canary)},
        ],
    }


def exceeded(summary: dict) -> bool:
    return any(m["value"] is not None and m["samples"] >= MIN_SAMPLES_FOR_ROLLBACK and m["value"] > m["threshold"]
               for m in summary["metrics"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--max-error-rate", type=float, default=0.05)
    parser.add_argument("--max-p95-ms", type=float, default=500)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        samples = list(pool.map(fetch, [args.url] * args.requests))
    summary = summarize(samples, args.version, max_error_rate=args.max_error_rate, max_p95_ms=args.max_p95_ms)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return EXIT_EXCEEDED if exceeded(summary) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
