#!/usr/bin/env python3
"""배포 전에 판단 서비스에 닿는지 확인한다. 카나리에 트래픽을 준 뒤에야 장애를 알게 되는 일을 막는다.

토큰이 필요한 조회를 부르므로 주소와 토큰을 함께 확인한다. LLM 은 부르지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import gate

READY_TIMEOUT_SECONDS = 10


def get_json(url: str, token: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_ready(url: str, token: str, *, getter: Callable[..., Any] = get_json,
                sleep: Callable[[float], None] = time.sleep) -> None:
    gate.retrying(lambda: getter(f"{url}/v1/rule-candidates", token, READY_TIMEOUT_SECONDS), sleep=sleep)


def main() -> int:
    try:
        check_ready(os.environ["DELTA_URL"], os.environ["DELTA_API_TOKEN"], sleep=time.sleep)
    except gate.GateUnavailable as exc:
        print(f"판단 서비스에 닿지 못했다: {exc}", file=sys.stderr)
        return gate.EXIT_UNAVAILABLE
    print("판단 서비스 확인 완료")
    return gate.EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
