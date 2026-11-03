"""AWS→GCP 이전 단계가 부르는 도구 계약과 시험용 대역.

그래프는 강제 종료 뒤 끝나지 못한 노드를 처음부터 다시 돌린다. 그래서 모든 호출은 같은 run_id 로 다시 불러도 안전해야 한다.
실제 구현은 동규 님(GCP·데이터)과 호원 님(네트워크·전환)의 결정 뒤에 이 계약으로 붙인다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

READY = "ready"
RUNNING = "running"
FAILED = "failed"
SYNCED = "synced"
SYNCING = "syncing"
STAND_IN_SYNC_POLLS = 3
STAND_IN_SAMPLES = 400
MAX_ERROR_RATE = 0.05
MAX_P95_MS = 2000


class MigrationTools(Protocol):
    label: str

    def provision(self, run_id: str) -> Mapping[str, Any]:
        """대상 환경을 준비한다. {"state": running|ready|failed, "detail": str}"""

    def deploy(self, run_id: str, image: str) -> Mapping[str, Any]:
        """검증한 이미지를 대상에 올리고 헬스 체크까지 본다. 응답 형식은 provision 과 같다."""

    def start_data_sync(self, run_id: str) -> Mapping[str, Any]:
        """복제를 시작하고 바로 돌아온다. 끝나기를 기다리지 않는다. 응답 형식은 provision 과 같다."""

    def data_sync_status(self, run_id: str) -> Mapping[str, Any]:
        """{"state": syncing|synced|failed, "lag_seconds": float|None}. 지연을 모르면 None 이다."""

    def set_weight(self, run_id: str, percent: int) -> None:
        """대상으로 보낼 비율을 절대값으로 정한다. 전환이 끝날 때까지 쓰기는 출발지 DB 한 곳으로 간다."""

    def measure(self, run_id: str, percent: int) -> Mapping[str, Any]:
        """대상으로 간 요청을 잰다. {"metrics": [{"name", "value", "threshold", "samples"}], "log_tail": str}"""

    def cut_over_data(self, run_id: str) -> Mapping[str, Any]:
        """쓰기를 대상 DB 로 옮긴다. 응답 형식은 provision 과 같다. 끝나면 자동으로 되돌리지 않는다."""


class StandInMigration:
    """실제 클라우드를 건드리지 않는 대역. 지표도 만든 값이라 화면에 대역이라고 표시한다."""

    label = "대역"

    def __init__(self) -> None:
        self._sync_polls = 0

    def provision(self, run_id: str) -> Mapping[str, Any]:
        return {"state": READY, "detail": "대역 환경 준비"}

    def deploy(self, run_id: str, image: str) -> Mapping[str, Any]:
        return {"state": READY, "detail": f"{image} 를 대역에 올린 것으로 친다"}

    def start_data_sync(self, run_id: str) -> Mapping[str, Any]:
        return {"state": RUNNING, "detail": "대역 복제 시작"}

    def data_sync_status(self, run_id: str) -> Mapping[str, Any]:
        self._sync_polls += 1
        if self._sync_polls < STAND_IN_SYNC_POLLS:
            return {"state": SYNCING, "lag_seconds": 30.0}
        return {"state": SYNCED, "lag_seconds": 0.0}

    def set_weight(self, run_id: str, percent: int) -> None:
        return None

    def measure(self, run_id: str, percent: int) -> Mapping[str, Any]:
        return {"metrics": [{"name": "error_rate", "value": 0.0, "threshold": MAX_ERROR_RATE, "samples": STAND_IN_SAMPLES},
                            {"name": "latency_p95_ms", "value": 50.0, "threshold": MAX_P95_MS, "samples": STAND_IN_SAMPLES}],
                "log_tail": ""}

    def cut_over_data(self, run_id: str) -> Mapping[str, Any]:
        return {"state": READY, "detail": "대역 쓰기 전환"}
