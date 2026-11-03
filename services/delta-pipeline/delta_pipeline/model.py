"""시나리오, 화면에 보여 줄 노드 상태, 판단 요청을 만드는 순수 함수."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CANARY_STEPS = (10, 50, 100)
SHIFT_STEPS = (10, 50, 100)
MAX_FINDINGS_SHOWN = 40
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
STATUS_ORDER = {"fail": 0, "unknown": 1, "dismissed": 2}


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    description: str
    dast_scenario: str
    jenkins_scenario: str
    migrate: bool = False


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("normal", "정상 릴리스", "DAST 통과 후 10→50→100% 무중단 승격", "fixed", "good"),
    Scenario("vulnerable", "취약한 릴리스", "XSS 실측 재현으로 배포 전에 멈추고, 수정본 재검증 후 진행", "vulnerable", "good"),
    Scenario("bad_canary", "장애 릴리스", "새 버전이 500 을 내 10%에서 자동 롤백하고 AI 가 원인을 설명", "fixed", "bad-canary"),
    Scenario("migration", "AWS→GCP 이전 (대역)", "복제를 비동기로 시작하고 비율을 옮긴 뒤 사람 승인으로 쓰기를 전환. 실제 클라우드는 건드리지 않는다",
             "fixed", "good", migrate=True),
)


@dataclass(frozen=True)
class Options:
    scenario: Scenario
    planner: str = "reviewed"
    rejudge_onprem: bool = False


@dataclass
class Node:
    id: str
    label: str
    owner: str
    state: str = "pending"  # pending·running·pass·waiting·fail·skipped
    summary: str = ""
    started_at: float | None = None
    ended_at: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] | None = None


def initial_nodes(scenario: Scenario) -> list[Node]:
    migration = [
        Node("migrate-provision", "대상 환경 준비", "이전 도구"),
        Node("migrate-deploy", "대상 배포", "이전 도구"),
        Node("migrate-data", "데이터 복제", "이전 도구 · 비동기"),
        *(Node(f"shift-{p}", f"대상 {p}%", "이전 도구 + delta-ai") for p in SHIFT_STEPS),
        Node("migrate-cutover", "쓰기 전환", "사람 승인"),
    ] if scenario.migrate else []
    return [
        Node("onprem-evidence", "온프레미스 증거", "수집기 · VM 200"),
        Node("onprem-judge", "이전·보안 판단", "delta-ai"),
        Node("dast-run", "DAST 실측", "delta-dast · Chrome"),
        Node("dast-judge", "DAST 판단", "delta-ai"),
        Node("deploy-prepare", "빌드·SAST·슬롯 배포", "Jenkins"),
        *(Node(f"canary-{p}", f"트래픽 {p}%", "Jenkins + delta-ai") for p in CANARY_STEPS),
        *migration,
        Node("result", "결과", "시연 콘솔"),
    ]


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """화면에 필요한 값만 남긴다. 문구는 그대로 두고 화면이 글자로만 그린다."""
    findings = sorted(result.get("findings", ()),
                      key=lambda f: (STATUS_ORDER.get(f.get("status"), 9), SEVERITY_ORDER.get(f.get("severity"), 9)))
    meta = result.get("meta") or {}
    return {
        "verdict": result.get("verdict"), "reasons": list(result.get("reasons", ())),
        "findings": [{key: f.get(key) for key in ("rule_id", "title", "severity", "status", "why", "evidence")}
                     for f in findings[:MAX_FINDINGS_SHOWN]],
        "finding_count": len(findings),
        "checks": list(result.get("checks", ())), "verified_controls": list(result.get("verified_controls", ())),
        "posture": result.get("posture"),
        "llm": {"used": bool(meta.get("llm_used")), "model": meta.get("model"), "duration_ms": meta.get("duration_ms")},
    }


def combined_verdict(verdicts: Sequence[str | None]) -> str:
    """하나라도 막으면 막는다. 알 수 없는 값은 통과로 보지 않는다."""
    return "pass" if verdicts and all(v == "pass" for v in verdicts) else "needs_human"


def dast_request(run: Mapping[str, Any], *, assessment_id: str, target: str) -> dict[str, Any]:
    results = [{"finding_id": r["finding_id"], "title": r.get("title", ""), "status": r["status"],
                "message": r.get("message", "")} for r in run.get("results", ())]
    return {"assessment_id": assessment_id, "kind": "dast_verification", "service": {"service_id": "board"},
            "evidence": {"dast": {"run_id": run["run_id"], "target": target, "results": results}}}


def deployment_request(run_id: str, percent: int, measured: Mapping[str, Any]) -> dict[str, Any]:
    return {"assessment_id": f"{run_id}-shift-{percent}", "kind": "deployment_gate", "service": {"service_id": "board"},
            "evidence": {"deployment": {"stage": f"migrate-traffic-{percent}", "status": "succeeded", "target": "gcp",
                                        "traffic_percent": percent, "log_tail": measured.get("log_tail", ""),
                                        "metrics": list(measured.get("metrics", ()))}}}


def stage_states(stages: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {str(s.get("name")): str(s.get("status")) for s in stages}


class Board:
    """화면이 읽는 노드 상태. 재시작해도 이어 보이도록 바뀔 때마다 파일에 남긴다.

    판단과 전이의 기준은 그래프 체크포인트다. 이 파일은 표시용이다.
    """

    def __init__(self, path: Path, options: Options, run_id: str) -> None:
        self._path, self._options, self._run_id = path, options, run_id
        self._lock = threading.Lock()
        self._nodes = {node.id: node for node in initial_nodes(options.scenario)}
        self._outcome: str | None = None
        self._started_at = time.time()
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            self._nodes = {item["id"]: Node(**item) for item in saved["nodes"]}
            self._outcome, self._started_at = saved["outcome"], saved["started_at"]
        else:
            self._save()

    def _payload(self) -> dict[str, Any]:
        return {"run_id": self._run_id, "scenario": asdict(self._options.scenario), "planner": self._options.planner,
                "rejudge_onprem": self._options.rejudge_onprem, "outcome": self._outcome, "started_at": self._started_at,
                "nodes": [deepcopy(asdict(node)) for node in self._nodes.values()]}

    def _save(self) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._payload(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._payload()

    def update(self, node_id: str, **fields: Any) -> None:
        with self._lock:
            node = self._nodes[node_id]
            for key, value in fields.items():
                setattr(node, key, value)
            if fields.get("state") == "running" and node.started_at is None:
                node.started_at = time.time()
            if fields.get("state") in ("pass", "fail", "skipped"):
                node.ended_at = time.time()
            self._save()

    def add_detail(self, node_id: str, **values: Any) -> None:
        with self._lock:
            self._nodes[node_id].detail = {**self._nodes[node_id].detail, **values}
            self._save()

    def state_of(self, node_id: str) -> str:
        with self._lock:
            return self._nodes[node_id].state

    def waiting_options(self) -> set[str]:
        with self._lock:
            waiting = next((n for n in self._nodes.values() if n.decision), None)
            return {o["value"] for o in waiting.decision["options"]} if waiting else set()

    def active_deploy_node(self) -> str:
        with self._lock:
            started = [n.id for n in self._nodes.values() if n.id.startswith("canary-") and n.state in ("running", "waiting")]
        return started[-1] if started else "deploy-prepare"

    def finish(self, outcome: str, summary: str, *, passed: bool = False) -> None:
        with self._lock:
            for node in self._nodes.values():
                if node.state == "pending" and node.id != "result":
                    node.state = "skipped"
                elif node.state in ("running", "waiting"):
                    node.state, node.ended_at, node.decision = "fail", time.time(), None
            self._outcome = outcome
            self._save()
        self.update("result", state="pass" if passed else "fail", summary=summary)
