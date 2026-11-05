#!/usr/bin/env python3
"""클라우드 입구 WAF 대역에 차단 패턴을 넣는다. 실제 Cloud Armor·AWS WAF 가 아니다.

판단 서비스가 낸 규칙의 범주(sqli, xss …)를 같은 범주의 시험 패턴으로 바꿔 HAProxy ACL 에 넣는다.
모르는 범주는 흉내 내지 않고 실패한다. 막지 못하는 것을 막는다고 보고하지 않기 위해서다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

import haproxy_ctl

ACL_DIR = "/usr/local/etc/haproxy/waf"
EDGES = ("aws", "gcp")
# attack_probe.PAYLOADS 의 요청을 잡는 최소 패턴이다. HAProxy 가 url_dec 한 URL 에 대소문자 무시로 맞춘다.
# 런타임 API 는 공백과 ; 를 구분자로 쓰므로 패턴에 넣지 않는다(\s, \x27, \x3b 로 쓴다)
PATTERNS: dict[str, str] = {
    "sqli": r"\x27\s*or\s|union\s+select|sleep\(",
    "xss": r"<script|javascript:|onerror\s*=",
    "lfi": r"\.\./",
    "rfi": r"=\s*https?://",
    "java": r"\$\{jndi:",
    "cve": r"\$\{jndi:",
    "rce": r"\x3b\s*(cat|id|wget|curl)\b",
    "php": r"<\?php",
}


class UnsupportedCapability(ValueError):
    """대역이 흉내 낼 패턴이 없는 범주다."""


def acl_file(edge: str) -> str:
    if edge not in EDGES:
        raise ValueError(f"모르는 입구: {edge}")
    return f"{ACL_DIR}/{edge}.acl"


def patterns_for(capabilities: Iterable[str]) -> tuple[str, ...]:
    wanted = sorted(set(capabilities))
    unknown = [c for c in wanted if c not in PATTERNS]
    if unknown:
        raise UnsupportedCapability(f"대역 패턴이 없는 범주: {', '.join(unknown)}")
    return tuple(dict.fromkeys(PATTERNS[c] for c in wanted))


def escape(pattern: str) -> str:
    """런타임 API 는 백슬래시를 이스케이프로 먹으므로 두 번 쓴다(실제 HAProxy 3.0 에서 확인)."""
    if " " in pattern or ";" in pattern:
        raise ValueError(f"런타임 API 로 보낼 수 없는 패턴: {pattern}")
    return pattern.replace("\\", "\\\\")


def commands(edge: str, capabilities: Iterable[str]) -> tuple[str, ...]:
    path = acl_file(edge)
    return (f"clear acl {path}", *(f"add acl {path} {escape(p)}" for p in patterns_for(capabilities)))


def apply(edge: str, capabilities: Iterable[str], *, run=haproxy_ctl.run_command) -> None:
    """패턴을 통째로 바꾼다. 같은 입력이면 여러 번 불러도 결과가 같다."""
    for command in commands(edge, capabilities):
        reply = run(command).strip()
        if reply:
            raise RuntimeError(f"HAProxy 오류: {reply}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge", choices=EDGES, required=True)
    parser.add_argument("--capabilities", default="", help="쉼표로 구분. 비우면 모든 패턴을 지운다")
    args = parser.parse_args(argv)
    capabilities = [c for c in args.capabilities.split(",") if c]
    try:
        apply(args.edge, capabilities)
    except (UnsupportedCapability, RuntimeError, OSError) as exc:
        print(f"WAF 대역 적용 실패: {exc}", file=sys.stderr)
        return 1
    print(f"{args.edge} 입구 WAF 대역: {', '.join(sorted(set(capabilities))) or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
