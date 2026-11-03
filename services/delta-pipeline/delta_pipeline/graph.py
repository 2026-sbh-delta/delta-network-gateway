"""전이 표. 다음 단계는 여기 적힌 코드만 정한다. LLM 은 전이를 고르지 않는다."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from delta_pipeline.migration_steps import MigrationSteps
from delta_pipeline.model import SHIFT_STEPS
from delta_pipeline.steps import Deps, PipelineState, guarded

HALTED = "halted"
ROLLBACK = "migrate_rollback"


def after_step(next_node: str) -> Any:
    def route(state: PipelineState) -> str:
        return HALTED if state.get("halt") else next_node
    return route


def route_gate(ask_node: str, next_node: str) -> Any:
    """판정 대기가 남아 있으면 사람에게, 아니면 다음으로 간다."""
    def route(state: PipelineState) -> str:
        if state.get("halt"):
            return HALTED
        return ask_node if state.get("pending_gate") else next_node
    return route


def route_dast_judge(state: PipelineState) -> str:
    if state.get("halt"):
        return HALTED
    if state.get("dast_before") is not None:
        return "ask_reverify"
    return "ask_dast" if state.get("pending_gate") else "deploy_check"


def route_deploy_check(state: PipelineState) -> str:
    if state.get("halt"):
        return HALTED
    return "release_start" if state.get("phase") == "release" else "baseline_start"


def route_deploy_wait(state: PipelineState) -> str:
    if state.get("halt"):
        return HALTED
    if state.get("jenkins_input"):
        return "ask_jenkins"
    return "deploy_finish" if state.get("phase") == "release" else "baseline_done"


def route_deploy_finish(state: PipelineState) -> str:
    if state.get("halt"):
        return HALTED
    return "migrate_provision" if state.get("migrating") else END


def route_shift(state: PipelineState) -> str:
    """비율을 옮기기 시작한 뒤에 멈추면 원인과 상관없이 트래픽을 되돌린다."""
    if state.get("halt") or state.get("rollback_reason"):
        return ROLLBACK
    return "migrate_shift" if state["shift_index"] < len(SHIFT_STEPS) else "migrate_data_check"


def route_after_shift(next_node: str) -> Any:
    def route(state: PipelineState) -> str:
        return ROLLBACK if state.get("halt") or state.get("rollback_reason") else next_node
    return route


def route_end(state: PipelineState) -> str:
    return HALTED if state.get("halt") else END


STEP_ROUTES: dict[str, tuple[Any, tuple[str, ...]]] = {
    "onprem_evidence": (after_step("onprem_judge"), ("onprem_judge", HALTED)),
    "onprem_judge": (route_gate("ask_onprem", "dast_start"), ("ask_onprem", "dast_start", HALTED)),
    "ask_onprem": (after_step("dast_start"), ("dast_start", HALTED)),
    "dast_start": (after_step("dast_wait"), ("dast_wait", HALTED)),
    "dast_wait": (after_step("dast_judge"), ("dast_judge", HALTED)),
    "dast_judge": (route_dast_judge, ("ask_reverify", "ask_dast", "deploy_check", HALTED)),
    "ask_reverify": (after_step("dast_start"), ("dast_start", HALTED)),
    "ask_dast": (after_step("deploy_check"), ("deploy_check", HALTED)),
    "deploy_check": (route_deploy_check, ("baseline_start", "release_start", HALTED)),
    "baseline_start": (after_step("deploy_wait"), ("deploy_wait", HALTED)),
    "baseline_done": (after_step("release_start"), ("release_start", HALTED)),
    "release_start": (after_step("deploy_wait"), ("deploy_wait", HALTED)),
    "deploy_wait": (route_deploy_wait, ("ask_jenkins", "deploy_finish", "baseline_done", HALTED)),
    "ask_jenkins": (after_step("jenkins_answer"), ("jenkins_answer", HALTED)),
    "jenkins_answer": (after_step("deploy_wait"), ("deploy_wait", HALTED)),
    "deploy_finish": (route_deploy_finish, ("migrate_provision", END, HALTED)),
    "migrate_provision": (after_step("migrate_deploy"), ("migrate_deploy", HALTED)),
    "migrate_deploy": (after_step("migrate_data_start"), ("migrate_data_start", HALTED)),
    "migrate_data_start": (after_step("migrate_shift"), ("migrate_shift", HALTED)),
    "migrate_shift": (route_shift, ("migrate_shift", "migrate_data_check", ROLLBACK)),
    "migrate_data_check": (route_after_shift("ask_cutover"), ("ask_cutover", ROLLBACK)),
    "ask_cutover": (route_after_shift("migrate_cutover_check"), ("migrate_cutover_check", ROLLBACK)),
    "migrate_cutover_check": (route_after_shift("migrate_cutover"), ("migrate_cutover", ROLLBACK)),
    # 쓰기를 옮기기 시작한 뒤에는 되돌림으로 가는 전이가 없다
    "migrate_cutover": (route_end, (HALTED, END)),
}


def build_graph(deps: Deps, checkpointer: Any) -> Any:
    steps = MigrationSteps(deps)
    bodies = {"onprem_evidence": steps.onprem_evidence, "onprem_judge": steps.onprem_judge, "ask_onprem": steps.ask_gate,
              "dast_start": steps.dast_start, "dast_wait": steps.dast_wait, "dast_judge": steps.dast_judge,
              "ask_reverify": steps.ask_reverify, "ask_dast": steps.ask_gate, "deploy_check": steps.deploy_check,
              "baseline_start": steps.baseline_start, "baseline_done": steps.baseline_done,
              "release_start": steps.release_start, "deploy_wait": steps.deploy_wait, "ask_jenkins": steps.ask_jenkins,
              "jenkins_answer": steps.jenkins_answer, "deploy_finish": steps.deploy_finish,
              "migrate_provision": steps.migrate_provision, "migrate_deploy": steps.migrate_deploy,
              "migrate_data_start": steps.migrate_data_start, "migrate_shift": steps.migrate_shift,
              "migrate_data_check": steps.migrate_data_check, "ask_cutover": steps.ask_cutover,
              "migrate_cutover_check": steps.migrate_cutover_check, "migrate_cutover": steps.migrate_cutover}
    graph = StateGraph(PipelineState)
    for name, body in bodies.items():
        graph.add_node(name, guarded(body))
        route, targets = STEP_ROUTES[name]
        graph.add_conditional_edges(name, route, list(targets))
    graph.add_node(ROLLBACK, steps.migrate_rollback)
    graph.add_node(HALTED, steps.halted)
    graph.add_edge(START, "onprem_evidence")
    graph.add_edge(ROLLBACK, END)
    graph.add_edge(HALTED, END)
    return graph.compile(checkpointer=checkpointer)
