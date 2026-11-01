"""평가셋 실행. python scripts/eval.py [fake|none|claude]  claude 는 ANTHROPIC_API_KEY 가 필요하다."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from delta_ai.evaluation import load_cases, run_case
from delta_ai.fake_llm import FakeLLM
from delta_ai.judge import LlmOutput
from delta_ai.llm import ClaudeLLM


def _llm(mode: str):
    if mode == "claude":
        return ClaudeLLM(output=LlmOutput)
    return FakeLLM() if mode == "fake" else None


async def main(mode: str) -> int:
    llm = _llm(mode)
    failures = input_tokens = output_tokens = llm_calls = 0
    with tempfile.TemporaryDirectory() as store_dir:
        for case in load_cases():
            result = await run_case(case, llm=llm, store_dir=Path(store_dir))
            failures += not result.passed
            llm_calls += result.llm_used
            input_tokens += result.usage.get("input_tokens", 0) + result.usage.get("cache_read_input_tokens", 0) \
                + result.usage.get("cache_creation_input_tokens", 0)
            output_tokens += result.usage.get("output_tokens", 0)
            mark = "OK " if result.passed else "BAD"
            print(f"{mark} {result.name:<36} {result.verdict:<12} {','.join(result.reasons)} {','.join(result.missing_rules)}")
    print(f"실패 {failures} · LLM 호출 {llm_calls} · 입력 토큰 {input_tokens} · 출력 토큰 {output_tokens}")
    if mode == "claude" and llm_calls == 0:
        print("실제 LLM 을 한 번도 호출하지 못했다 — 키나 크레딧을 확인한다. 이 결과는 Claude 평가가 아니다")
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fake")))
