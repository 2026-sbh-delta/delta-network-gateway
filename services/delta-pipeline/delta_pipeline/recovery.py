"""Bounded graph-owned recovery. Neither logs nor model text select an action."""

from delta_ai.diagnostic_contract import safe_text

from delta_pipeline.exception_flow import prepare_exception

NODES = {"onprem_start", "onprem_wait"}


def capture(deps, name, state, result):
    """Capture an eligible failure before the monotonic halt reducer sees it."""
    if (not deps.options.recovery or name not in NODES or state.get("halt")
            or not result.get("halt") or deps.cancelled()):
        return None
    transient = result.get("failure_type") == "TransientServiceError"
    health = name == "onprem_wait" and result.get("failure_code") == "sandbox_health_failed"
    if not (transient or health):
        return None
    return {"recovery_pending": True, "recovery_failure": {
        "node": name, "reason": safe_text(result["halt"]),
        "failure_type": result.get("failure_type"),
        "failure_code": result.get("failure_code"),
        "action": "redeploy" if health else "retry_same_job",
    }}


class RecoveryFlow:
    def __init__(self, deps, steps):
        self.deps, self.steps = deps, steps

    def _stop(self, state, reason):
        failure = state["recovery_failure"]
        events = [*state.get("recovery_events", []), {
            "node": failure["node"], "action": "human_review", "reason": reason,
            "previous_run_id": state.get("onprem_run_id"),
        }]
        self.deps.board.update("recovery", state="fail", summary=reason)
        self.deps.board.add_detail("recovery", events=events)
        result = {"halt": safe_text(f"{failure['reason']}; {reason}"),
                  "failure_type": failure["failure_type"],
                  "failure_code": failure["failure_code"],
                  "recovery_pending": False, "recovery_events": events}
        from delta_pipeline.case_learning import CaseLearning
        result.update(CaseLearning(self.deps).settled(failure["node"], state, result))
        return prepare_exception(self.deps, failure["node"], result, "halted", {**state, **result})

    def prepare(self, state):
        """Only reads/backoff here; persist the budget/key BEFORE the next enqueue."""
        failure = state["recovery_failure"]
        count = state.get("recovery_count", 0)
        if self.deps.cancelled():
            return self._stop(state, "취소 요청으로 자동 복구하지 않았습니다")
        if count >= self.deps.options.recovery_limit:
            return self._stop(state, "자동 복구 횟수를 초과했습니다. 사람 확인이 필요합니다")
        try:
            # Revalidate even on replay. No arbitrary stored destination/action is trusted.
            if failure["node"] not in NODES:
                raise ValueError()
            action = failure["action"]
            if action == "redeploy":
                if (failure["node"] != "onprem_wait"
                        or failure["failure_code"] != "sandbox_health_failed"
                        or not self.deps.onprem.retry_ready(
                            state["onprem_run_id"], key=self.steps._key(state, "onprem"),
                            commit=state.get("commit") or self.deps.options.commit,
                            image=state["image"])):
                    return self._stop(state, "이전 실행의 소유권·설정·Pod/Secret 정리 확인이 필요합니다")
            elif action != "retry_same_job" or failure["failure_type"] != "TransientServiceError":
                raise ValueError()
        except Exception:  # noqa: BLE001 — bounded fallback; never export provider text or bypass a failed proof.
            return self._stop(state, "자동 복구 조건을 확인하지 못했습니다. 사람 확인이 필요합니다")
        attempt, delay = count + 1, 2 ** count
        self.deps.board.update("recovery", state="running", summary=f"{attempt}회 복구 준비 · {delay}초 대기")
        self.deps.sleep(delay)
        if self.deps.cancelled():
            return self._stop(state, "취소 요청으로 자동 복구하지 않았습니다")
        resume = "onprem_start" if action == "redeploy" else failure["node"]
        event = {"attempt": attempt, "node": failure["node"], "action": action,
                 "resume": resume, "delay_seconds": delay,
                 "previous_run_id": state.get("onprem_run_id")}
        events = [*state.get("recovery_events", []), event]
        self.deps.board.add_detail("recovery", events=events)
        self.deps.board.update("recovery", state="pass", summary=f"{attempt}회 복구 준비 완료 · {resume} 재실행")
        # Successful revalidation still goes through the original node and every gate.
        previous = ({"recovery_previous_run_id": state["onprem_run_id"],
                     "recovery_previous_key": self.steps._key(state, "onprem")}
                    if action == "redeploy" else {})
        return {**previous, "recovery_pending": False, "recovery_count": attempt,
                "recovery_events": events, "recovery_resume": resume,
                "onprem_attempt": state.get("onprem_attempt", 0) + (action == "redeploy")}
