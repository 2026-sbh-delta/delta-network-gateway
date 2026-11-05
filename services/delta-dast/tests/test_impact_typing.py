"""영향 시연 페이로드는 덩어리로 빠르게 채우되, 내용은 그대로 다 들어가야 한다."""

import asyncio
import time
from types import SimpleNamespace

from dast_agent.probe_browser import ProbeBrowser


def test_impact_payload_is_typed_whole_in_short_time():
    typed = []

    async def insert_text(text):
        typed.append(text)

    fake = SimpleNamespace(page=SimpleNamespace(keyboard=SimpleNamespace(insert_text=insert_text)))
    payload = "<svg onload=" + "x" * 1340 + ">"
    started = time.monotonic()
    asyncio.run(ProbeBrowser.type_fast(fake, payload, 0.5))
    assert "".join(typed) == payload
    assert 1 < len(typed) <= 10 and time.monotonic() - started < 2


def test_direct_check_allows_the_payload_without_a_reflection_round():
    """바로 검사는 반사 마커 단계 없이 영향 시연 페이로드를 넣을 수 있다. 기존 절차는 반사 확인이 먼저다."""
    browser = ProbeBrowser.__new__(ProbeBrowser)
    browser.evidence = {"impact": None, "executed": [], "reflection": []}
    browser.direct = False
    assert not browser.impact_allowed()
    browser.direct = True
    assert browser.impact_allowed()
    browser.evidence["impact"] = "bsod_v1"  # 한 번만
    assert not browser.impact_allowed()
