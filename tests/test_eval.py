from __future__ import annotations

import pytest

from delta_ai.evaluation import load_cases, run_case
from delta_ai.fake_llm import FakeLLM


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["name"])
async def test_eval_case_with_fake_llm(case, tmp_path):
    result = await run_case(case, llm=FakeLLM(), store_dir=tmp_path)

    assert result.passed, result


def test_eval_set_has_required_coverage():
    cases = load_cases()

    assert len(cases) >= 12
    assert {"pass", "fix", "needs_human"} <= {verdict for case in cases for verdict in case["expect"]}
