"""규칙 공통 도우미 — finding_id 계산과 대시보드 체크 상태."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

from delta_ai.contract import CheckState, Finding


def finding_id(rule_id: str, location: Mapping[str, str]) -> str:
    raw = json.dumps([rule_id, sorted(location.items())], ensure_ascii=False)
    return f"{rule_id}:{hashlib.sha256(raw.encode()).hexdigest()[:10]}"


def check_state(findings: Iterable[Finding]) -> CheckState:
    """low 는 안내라서 상태를 바꾸지 않는다. 실패가 하나라도 있으면 red, 모르는 것만 있으면 unknown."""
    statuses = {f.status for f in findings if f.severity != "low"}
    if "fail" in statuses:
        return "red"
    if "unknown" in statuses:
        return "unknown"
    return "green"
