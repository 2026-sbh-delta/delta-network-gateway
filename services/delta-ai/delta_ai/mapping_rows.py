"""매핑 표의 행과 finding 을 만드는 공통 부품. mapping 과 mapping_security 가 같이 쓴다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from delta_ai.contract import (
    Finding,
    FindingStatus,
    MappingGrade,
    MappingRow,
    Severity,
    SpecOp,
    ValueOrigin,
)
from delta_ai.findings import finding_id

RULES: dict[str, tuple[str, str]] = {  # rule_id → (category, 제목)
    "MAP-LB-001": ("network", "로드밸런서 대응"),
    "MAP-DB-001": ("database", "PostgreSQL 대응"),
    "MAP-K8S-001": ("compute", "k3s 워크로드 대응"),
    "MAP-K8S-002": ("storage", "영속 볼륨 대응"),
    "MAP-K8S-003": ("security", "클러스터 정책 대응"),
    "MAP-NET-001": ("network", "서브넷 구조 대응"),
    "MAP-NET-002": ("network", "대상 대역 충돌"),
    "MAP-NET-003": ("network", "기존 네트워크 대조"),
    "MAP-CMP-001": ("compute", "서버 스펙 대응"),
    "MAP-CMP-002": ("compute", "CPU 구조 변경"),
    "MAP-OS-001": ("compute", "운영체제 대응"),
    "MAP-IAM-001": ("identity", "권한 대응"),
    "MAP-SEC-001": ("security", "보안 도구 능력 대응"),
}


@dataclass(frozen=True)
class Part:
    rows: tuple[MappingRow, ...] = ()
    findings: tuple[Finding, ...] = ()


def make_finding(rule_id: str, *, status: FindingStatus, severity: Severity, evidence: str, location: dict[str, str],
                 ops: Sequence[SpecOp] = ()) -> Finding:
    category, title = RULES[rule_id]
    return Finding(finding_id=finding_id(rule_id, location), rule_id=rule_id, category=category, severity=severity,
                   status=status, autofix="allowed" if ops else "forbidden", title=title, location=location,
                   evidence=evidence, ops=tuple(ops))


def make_row(rule_id: str, resource: str, source: str, target: str | None, grade: MappingGrade, origin: ValueOrigin,
             differences: Iterable[str] = (), tests: Iterable[str] = ()) -> MappingRow:
    return MappingRow(resource=resource, source=source, target=target, grade=grade, differences=tuple(differences),
                      required_tests=tuple(tests), value_source=origin, rule_id=rule_id)


def graded(row: MappingRow, evidence: str) -> Part:
    """등급에 맞는 finding 을 붙인다. approximate·split 은 막지 않고 설명만 받는다."""
    location = {"resource": row.resource, "source": row.source}
    if row.grade in ("approximate", "split"):
        finding = make_finding(row.rule_id, status="unknown", severity="low", evidence=evidence, location=location)
    elif row.grade == "gap":
        finding = make_finding(row.rule_id, status="fail", severity="high", evidence=evidence, location=location)
    elif row.grade == "unknown":
        finding = make_finding(row.rule_id, status="unknown", severity="high", evidence=evidence, location=location)
    else:
        return Part((row,))
    return Part((row,), (finding,))


def merge(parts: Iterable[Part]) -> Part:
    parts = tuple(parts)
    return Part(tuple(r for p in parts for r in p.rows), tuple(f for p in parts for f in p.findings))
