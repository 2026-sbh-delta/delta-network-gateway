"""DAST 결과 → SAST 피드백(`POST /v1/sast/scans/{run_id}/dast-results`) 변환.

DAST는 SARIF를 받을 때 finding id를 `sast-<경로 ID>`로 만든다. 그 id로 경로를 되찾는다.

상태 대응
- REPRODUCED → REPRODUCED
- BLOCKED → REPRODUCED: 필터는 못 막았고 CSP가 막았다. 필터 판정은 "효과 없음"이다.
- NOT_REPRODUCED → NOT_REPRODUCED
- INCONCLUSIVE·그 밖 → ERROR: 규칙으로 굳히지 않는다.
"""

from __future__ import annotations

import re

from .contract import DastFeedback, DastResult

PREFIX = "sast-"
FINGERPRINT = re.compile(r"^[0-9a-f]{32}$")
STATUS = {"REPRODUCED": "REPRODUCED", "BLOCKED": "REPRODUCED", "NOT_REPRODUCED": "NOT_REPRODUCED"}


def feedback_from_dast(run_view: dict) -> DastFeedback | None:
    """DAST `GET /api/runs/{run_id}`·`/view` 응답에서 SAST가 낸 결과만 골라 피드백으로 바꾼다."""
    items = []
    for result in run_view.get("results", []):
        finding_id = str(result.get("finding_id", ""))
        fingerprint = finding_id[len(PREFIX):] if finding_id.startswith(PREFIX) else ""
        if not FINGERPRINT.match(fingerprint):
            continue
        items.append(DastResult(fingerprint=fingerprint, status=STATUS.get(result.get("status"), "ERROR")))
    return DastFeedback(results=items) if items else None
