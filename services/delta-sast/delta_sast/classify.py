"""③ 분류: 경로마다 결정적 규칙으로 판정하고, 판정이 안 나는 경로만 LLM으로 넘긴다.

순서(처음 걸리는 줄에서 끝난다)
1. 경로에 필터가 없다 → 후보 확정
2. 필터가 안전 목록에 있고 맥락이 맞다 → 양호 확정
3. 규칙 캐시에 (필터 코드 해시, 맥락) 판정이 있다 → 캐시 판정
4. 그 밖 → LLM 판단 대기
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .model import Flow, Judgment
from .policy import Policy

CACHE_VERDICTS = {"effective": "safe", "ineffective": "vulnerable"}


def cache_key(flow: Flow) -> str:
    return "+".join(s.code_hash for s in flow.sans) + "|" + flow.context


class RuleCache:
    """JSON 파일 하나에 두 가지를 둔다.

    - entries: (필터 코드 해시, 맥락) → 효과 있음/없음. LLM 판정과 DAST 결과가 맞을 때만 쌓인다(learn).
    - llm: LLM 입력 해시 → 응답. 같은 코드를 다시 점검할 때 LLM을 다시 부르지 않아 결과가 고정된다.
    """

    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: dict[str, dict] = {}
        self.llm: dict[str, dict] = {}
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries = data.get("entries", {})
            self.llm = data.get("llm", {})

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, entry: dict) -> None:
        if entry.get("verdict") not in CACHE_VERDICTS:
            raise ValueError(f"캐시 판정은 {sorted(CACHE_VERDICTS)} 중 하나여야 한다")
        self.entries[key] = entry

    def drop(self, key: str) -> None:
        self.entries.pop(key, None)

    def llm_get(self, key: str) -> dict | None:
        return self.llm.get(key)

    def llm_put(self, key: str, answer: dict) -> None:
        self.llm[key] = answer

    def learn(self, flow: Flow, llm_verdict: str, dast_status: str, run_id: str) -> str:
        """LLM 판정과 DAST 재현 결과가 맞으면 규칙으로 굳히고, 어긋나면 지운다. 결과 상태를 돌려준다."""
        key = cache_key(flow)
        agree = {("vulnerable", "REPRODUCED"): "ineffective", ("safe", "NOT_REPRODUCED"): "effective"}
        verdict = agree.get((llm_verdict, dast_status))
        if verdict is None:
            self.drop(key)
            return "conflict"
        self.put(key, {"sanitizers": [s.name for s in flow.sans], "context": flow.context, "verdict": verdict,
                       "evidence": {"llm": run_id, "dast": f"{run_id}:{dast_status}"}})
        return verdict

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"version": 1, "entries": self.entries, "llm": self.llm},
                          ensure_ascii=False, indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cache-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        os.replace(tmp, self.path)


def classify(flow: Flow, policy: Policy, cache: RuleCache) -> Judgment:
    if not flow.sans:
        return Judgment("rule", "candidate", f"입력이 필터 없이 {flow.context} 맥락의 싱크에 닿는다")
    safe = [s for s in flow.sans if policy.safe_for(s.name, flow.context)]
    if safe:
        return Judgment("rule", "safe", f"{safe[0].name}이(가) {flow.context} 맥락의 안전 목록에 있다")
    key = cache_key(flow)
    entry = cache.get(key)
    if entry is not None:
        verdict = CACHE_VERDICTS[entry["verdict"]]
        names = ", ".join(s.name for s in flow.sans)
        return Judgment("cache", verdict, f"이전 판정 재사용: {names} → {entry['verdict']}", cache_key=key)
    names = ", ".join(s.name for s in flow.sans)
    return Judgment("pending", "uncertain", f"안전 목록·캐시에 없는 필터({names})라 LLM 판단이 필요하다",
                    cache_key=key)
