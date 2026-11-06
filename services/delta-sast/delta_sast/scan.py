"""빠른 모드 전체(①~⑤): 목록 → 경로 → 분류 → LLM 판단 → SARIF."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .classify import RuleCache, classify
from .graph import Project
from .judge import LlmClient, judge_all
from .locate import locate
from .model import Flow, Judgment, Mark, Secret
from .poly.engine import collect as collect_poly
from .poly.index import Workspace
from .poly.link import link as link_poly
from .policy import Policy, load_policy
from .sarif import build
from .secret_judge import attach_actions, classify_all
from .secret_judge import judge_all as judge_secrets


@dataclass
class ScanResult:
    root: Path
    project: Project | Workspace
    marks: list[Mark]
    secrets: list[Secret]
    findings: list[tuple[Flow, Judgment]]
    parse_errors: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    llm: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"secrets": len(self.secrets)}
        for secret in self.secrets:
            out[f"secret:{secret.verdict}"] = out.get(f"secret:{secret.verdict}", 0) + 1
        for _flow, judgment in self.findings:
            key = f"{judgment.by}:{judgment.verdict}"
            out[key] = out.get(key, 0) + 1
        return out

    def sarif(self, *, run_id: str, commit: str | None = None, mode: str = "fast",
              payload_mode: str = "ai", repo: str | None = None) -> dict:
        stats = {"timings": self.timings, "counts": self.counts(), "llm": self.llm,
                 "parse_errors": self.parse_errors}
        return build(self.findings, self.secrets, self.project, run_id=run_id, commit=commit, mode=mode,
                     payload_mode=payload_mode, stats=stats, repo=repo)


def analyze(root: Path, marks: list[Mark], secrets: list[Secret], policy: Policy | None = None,
            cache: RuleCache | None = None, facts=None) -> ScanResult:
    """②③: LLM 없이 경로와 결정적 분류까지."""
    policy = policy or load_policy()
    cache = cache if cache is not None else RuleCache()
    started = time.perf_counter()
    project = Project(root, marks, policy)
    flows = project.flows()
    graph_s = time.perf_counter() - started
    # 다언어 엔진(Python 밖 언어): Opengrep taint 조각을 저장소·경로·함수 요약으로 이은 흐름
    started = time.perf_counter()
    facts = collect_poly(root) if facts is None else facts
    flows += link_poly(facts)
    poly_s = time.perf_counter() - started
    space = Workspace(project, facts)
    findings = [(flow, classify(flow, policy, cache)) for flow in flows]
    classify_all(secrets, space)
    return ScanResult(root, space, marks, secrets, findings, space.parse_errors,
                      {"graph": round(graph_s, 3), "polyglot": round(poly_s, 3)})


def scan(root: Path, policy: Policy | None = None, cache: RuleCache | None = None) -> ScanResult:
    """①②③."""
    started = time.perf_counter()
    # 다언어 수집(Opengrep 두 단계)은 Python 위치 찾기와 서로 독립이라 동시에 돌린다(둘 다 외부 프로세스 대기).
    with ThreadPoolExecutor(max_workers=1) as pool:
        poly = pool.submit(collect_poly, root)
        marks, secrets = locate(root)
        locate_s = time.perf_counter() - started
        facts = poly.result()
    result = analyze(root, marks, secrets, policy, cache, facts=facts)
    result.timings = {"locate": round(locate_s, 3), **result.timings,
                      "total": round(time.perf_counter() - started, 3)}
    return result


async def run_fast(root: Path, *, client: LlmClient | None, cache: RuleCache, payload_mode: str = "ai",
                   policy: Policy | None = None) -> ScanResult:
    """①~④. 캐시는 호출한 쪽이 저장한다(cache.save)."""
    started = time.perf_counter()
    result = scan(root, policy, cache)
    judge_started = time.perf_counter()
    result.llm = await judge_all(result.findings, result.project, client, cache, payload_mode)
    result.llm |= await judge_secrets(result.secrets, result.project, client, cache)
    attach_actions(result.secrets, root)
    result.timings["judge"] = round(time.perf_counter() - judge_started, 3)
    result.timings["total"] = round(time.perf_counter() - started, 3)
    return result
