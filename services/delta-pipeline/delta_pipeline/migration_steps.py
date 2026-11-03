"""이전 단계 노드 본문. 복제는 비동기로 시작하고, 트래픽은 판정을 받아 옮기며, 쓰기 전환 직전에 사람이 정한다.

쓰기 전환 전까지는 비율을 0 으로 돌리면 원래 상태다. 전환 뒤에는 대상 DB 에 새로 쓴 값이 돌아오지 않으므로 자동으로 되돌리지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from langgraph.types import interrupt

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.migration import FAILED, READY, SYNCED, MigrationTools
from delta_pipeline.model import SHIFT_STEPS, deployment_request, summarize_result
from delta_pipeline.steps import PROCEED, PipelineState, Steps, Stopped, assert_gates_passed

POLL_SECONDS = 2
STEP_DEADLINE_SECONDS = 900
DATA_DEADLINE_SECONDS = 600
MAX_CUTOVER_LAG_SECONDS = 5.0


def sync_is_ready(status: Mapping[str, Any]) -> bool:
    """지연을 모르면 맞춰졌다고 보지 않는다."""
    lag = status.get("lag_seconds")
    return status.get("state") == SYNCED and lag is not None and lag <= MAX_CUTOVER_LAG_SECONDS


class MigrationSteps(Steps):
    @property
    def _tools(self) -> MigrationTools:
        tools = self._deps.migration
        if tools is None:
            raise Stopped("이전 도구가 연결되지 않아 이전하지 않는다")
        return tools

    def _until_ready(self, call: Callable[[], Mapping[str, Any]], *, what: str) -> Mapping[str, Any]:
        deadline = time.time() + STEP_DEADLINE_SECONDS
        while time.time() < deadline:
            result = self._read(call)
            if result.get("state") == READY:
                return result
            if result.get("state") == FAILED:
                raise Stopped(f"{what} 실패: {result.get('detail', '사유 없음')}")
            self._deps.sleep(POLL_SECONDS)
        raise ServiceUnavailable(f"{what}이 제한 시간 안에 끝나지 않았다")

    def migrate_provision(self, state: PipelineState) -> dict[str, Any]:
        assert_gates_passed(state.get("gates") or {})
        tools = self._tools
        image = self._read(lambda: self._deps.jenkins.file(state["build"], "image.json"))
        if not image or not image.get("image_id"):
            raise Stopped("검증한 빌드의 이미지 기록이 없어 이전하지 않는다")
        self._board.update("migrate-provision", state="running", summary=f"[{tools.label}] 대상 환경 준비 중")
        result = self._until_ready(lambda: tools.provision(self._deps.run_id), what="대상 환경 준비")
        self._board.update("migrate-provision", state="pass", summary=f"[{tools.label}] {result.get('detail', '준비 완료')}")
        return {"image": str(image["image_id"])}

    def migrate_deploy(self, state: PipelineState) -> dict[str, Any]:
        tools, image = self._tools, state["image"]
        self._board.update("migrate-deploy", state="running", summary=f"[{tools.label}] 대상에 배포 중")
        result = self._until_ready(lambda: tools.deploy(self._deps.run_id, image), what="대상 배포")
        self._board.add_detail("migrate-deploy", image=image)
        self._board.update("migrate-deploy", state="pass", summary=f"[{tools.label}] {result.get('detail', '배포 완료')}")
        return {"shift_index": 0}

    def migrate_data_start(self, _: PipelineState) -> dict[str, Any]:
        tools = self._tools
        result = self._read(lambda: tools.start_data_sync(self._deps.run_id))
        if result.get("state") == FAILED:
            self._board.update("migrate-data", state="fail", summary=f"복제 시작 실패: {result.get('detail', '사유 없음')}")
            raise Stopped("데이터 복제를 시작하지 못해 트래픽을 옮기지 않는다")
        self._board.update("migrate-data", state="running", summary=f"[{tools.label}] 복제 진행 중, 트래픽은 기다리지 않는다")
        return {"halt": None}

    def migrate_shift(self, state: PipelineState) -> dict[str, Any]:
        tools, index = self._tools, state["shift_index"]
        percent, node_id = SHIFT_STEPS[index], f"shift-{SHIFT_STEPS[index]}"
        self._board.update(node_id, state="running", summary=f"[{tools.label}] 비율을 옮기고 측정하는 중")
        self._read(lambda: tools.set_weight(self._deps.run_id, percent))
        measured = self._read(lambda: tools.measure(self._deps.run_id, percent))
        self._board.add_detail(node_id, metrics=measured.get("metrics", ()))
        result = self._deps.delta.assess(deployment_request(self._deps.run_id, percent, measured))
        self._board.add_detail(node_id, results={"deployment_gate": summarize_result(result)})
        if result.get("verdict") != "pass":
            self._board.update(node_id, state="fail", summary=f"판정 {result.get('verdict')}: 되돌린다")
            return {"rollback_reason": f"대상 {percent}% 에서 판정 {result.get('verdict')}"}
        self._board.update(node_id, state="pass", summary=f"[{tools.label}] {percent}% 판정 통과")
        return {"shift_index": index + 1}

    def migrate_data_check(self, _: PipelineState) -> dict[str, Any]:
        tools = self._tools
        deadline = time.time() + DATA_DEADLINE_SECONDS
        while time.time() < deadline:
            status = self._read(lambda: tools.data_sync_status(self._deps.run_id))
            self._board.add_detail("migrate-data", lag_seconds=status.get("lag_seconds"))
            if status.get("state") == FAILED:
                self._board.update("migrate-data", state="fail", summary="복제 실패")
                return {"rollback_reason": "데이터 복제가 실패했다"}
            if sync_is_ready(status):
                self._board.update("migrate-data", state="pass", summary=f"복제 지연 {status['lag_seconds']}초")
                return {"rollback_reason": None}
            self._deps.sleep(POLL_SECONDS)
        self._board.update("migrate-data", state="fail", summary="제한 시간 안에 복제가 맞춰지지 않았다")
        return {"rollback_reason": "데이터 복제가 제한 시간 안에 맞춰지지 않았다"}

    def ask_cutover(self, _: PipelineState) -> dict[str, Any]:
        answer = interrupt({"node_id": "migrate-cutover", "options": [PROCEED, ("stop", "중단·트래픽 되돌리기")],
                            "prompt": "쓰기를 대상 DB 로 옮깁니다. 이후 대상에 쓴 데이터는 자동으로 되돌리지 않습니다."})
        return {} if answer == PROCEED[0] else {"rollback_reason": "쓰기 전환 전에 사람이 중단했다"}

    def migrate_cutover_check(self, _: PipelineState) -> dict[str, Any]:
        """전환 노드와 나눈다. 전환 중에 죽어 재개했을 때 복제 상태를 다시 보고 트래픽만 되돌리면 데이터가 갈라진다."""
        tools = self._tools
        # 사람을 기다리는 동안 복제가 밀렸을 수 있다
        status = self._read(lambda: tools.data_sync_status(self._deps.run_id))
        if not sync_is_ready(status):
            return {"rollback_reason": f"쓰기 전환 직전 복제 상태가 맞지 않다: {status.get('state')}, {status.get('lag_seconds')}"}
        return {"rollback_reason": None}

    def migrate_cutover(self, _: PipelineState) -> dict[str, Any]:
        tools = self._tools
        self._board.update("migrate-cutover", state="running", summary=f"[{tools.label}] 쓰기 전환 중")
        try:
            result = self._until_ready(lambda: tools.cut_over_data(self._deps.run_id), what="쓰기 전환")
        except (Stopped, ServiceUnavailable) as exc:
            self._board.update("migrate-cutover", state="fail", summary="쓰기 전환 상태를 확인하지 못했다")
            raise Stopped(f"{exc}. 어느 DB 가 쓰기를 받는지 사람이 확인한다. 자동으로 되돌리지 않았다") from exc
        self._board.update("migrate-cutover", state="pass", summary=f"[{tools.label}] {result.get('detail', '전환 완료')}")
        self._board.finish("migrated", f"[{tools.label}] 이전 완료. 트래픽과 쓰기가 대상에 있다", passed=True)
        return {"halt": None}

    def migrate_rollback(self, state: PipelineState) -> dict[str, Any]:
        reason = state.get("rollback_reason") or state.get("halt") or "사유 없음"
        tools = self._tools
        try:
            self._read(lambda: tools.set_weight(self._deps.run_id, 0))
        except ServiceUnavailable as exc:
            self._board.finish("stopped", f"{reason}. 비율을 되돌리지 못했다({exc}). 사람이 확인한다")
            return {"halt": str(reason)}
        self._board.finish("rolled_back", f"{reason}. 트래픽을 출발지로 되돌렸고 쓰기는 옮기지 않았다")
        return {"halt": None}
