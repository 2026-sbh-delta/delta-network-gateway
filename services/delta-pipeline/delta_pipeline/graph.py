"""전이 표. 다음 단계는 여기 적힌 코드만 정한다. LLM 은 전이를 고르지 않는다.

웹 갈래 최소 연결(명세 v1.1 6-2, 작업 계획 1-0): prepare → (build ∥ sast) → join → stage → dast → done.
조치 루프(1-B): dast 재현 → 사람이 '수정 적용' → fix_apply → (build ∥ sast) … dast 재검증 → done. 1회만 돈다.
cutover 를 켠 실행은 dast 뒤에 서비스 도메인 전환(6-3)을 거친다: ask_cutover → cutover_check → cutover_switch → cutover_verify.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from delta_pipeline.steps import Deps, PipelineState, Steps, branch, guarded
from delta_pipeline.exception_flow import ExceptionFlow, HUMAN_GATES, prepare_exception

HALTED = "halted"
BRANCHES = ("build_start", "sast_start")


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


def route_prepare(state: PipelineState) -> Any:
    return HALTED if state.get("halt") else list(BRANCHES)


def step_routes(cutover: bool, release: bool = False) -> dict[str, tuple[Any, tuple[str, ...]]]:
    after_release = "ask_cutover" if cutover else "done"
    after_dast = "ask_release" if release else "done"

    def route_dast_start(state: PipelineState) -> str:
        if state.get("halt"):
            return HALTED
        return "dast_wait" if state.get("dast_run_id") else after_dast

    def route_ask_dast(state: PipelineState) -> str:
        if state.get("halt"):
            return HALTED
        return "fix_apply" if state.get("fix_target") else after_dast

    routes = {
        "prepare": (route_prepare, (*BRANCHES, HALTED)),
        # 조치 루프(1-B): 수정 커밋으로 빌드 ∥ SAST 부터 다시 돈다(최대 1회, 2차 게이트에는 수정 선택지가 없다)
        "fix_apply": (route_prepare, (*BRANCHES, HALTED)),
        "join": (route_gate("ask_sast", "stage_start"), ("ask_sast", "stage_start", HALTED)),
        "ask_sast": (after_step("stage_start"), ("stage_start", HALTED)),
        "stage_start": (after_step("stage_wait"), ("stage_wait", HALTED)),
        "stage_wait": (after_step("dast_start"), ("dast_start", HALTED)),
        "dast_start": (route_dast_start, ("dast_wait", after_dast, HALTED)),
        "dast_wait": (route_gate("ask_dast", after_dast), ("ask_dast", after_dast, HALTED)),
        "ask_dast": (route_ask_dast, ("fix_apply", after_dast, HALTED)),
        "done": (after_step(END), (END, HALTED)),
    }
    if release:
        routes |= {
            "ask_release": (after_step("release_start"), ("release_start", HALTED)),
            "release_start": (after_step("release_wait"), ("release_wait", HALTED)),
            "release_wait": (after_step("release_verify"), ("release_verify", HALTED)),
            "release_verify": (after_step(after_release), (after_release, HALTED)),
        }
    if cutover:
        routes |= {
            "ask_cutover": (after_step("cutover_check"), ("cutover_check", HALTED)),
            "cutover_check": (after_step("cutover_switch"), ("cutover_switch", HALTED)),
            "cutover_switch": (after_step("cutover_verify"), ("cutover_verify", HALTED)),
            "cutover_verify": (after_step("done"), ("done", HALTED)),
        }
    return routes


def build_graph(deps: Deps, checkpointer: Any) -> Any:
    steps = Steps(deps)
    bodies = {"prepare": steps.prepare, "join": lambda _: {}, "ask_sast": steps.ask_gate,
              "stage_start": steps.stage_start, "stage_wait": steps.stage_wait, "dast_start": steps.dast_start,
              "dast_wait": steps.dast_wait, "ask_dast": steps.ask_gate, "fix_apply": steps.fix_apply,
              "done": steps.done}
    release = deps.options.release_target is not None
    if release:
        bodies |= {"ask_release": steps.ask_release, "release_start": steps.release_start,
                   "release_wait": steps.release_wait, "release_verify": steps.release_verify}
    if deps.options.cutover:
        bodies |= {"ask_cutover": steps.ask_cutover, "cutover_check": steps.cutover_check,
                   "cutover_switch": steps.cutover_switch, "cutover_verify": steps.cutover_verify}
    routes = step_routes(deps.options.cutover, release)
    graph = StateGraph(PipelineState)
    def diagnosed(name, body, route):
        execute = guarded(body)
        if not deps.options.diagnostics:
            return execute
        def run(state):
            result = execute(state)
            merged = {**state, **result}
            target = route(merged)
            if target == HALTED and state.get("halt") and not result.get("halt"):
                result = {**result, "halt": state["halt"]}
            return prepare_exception(deps, name, result, target, merged)
        return run

    def diagnostic_route(route):
        def run(state):
            if deps.options.diagnostics and state.get("diagnosis_pending"):
                return "exception_cleanup" if state.get("halt") else "diagnose_exception"
            return route(state)
        return run

    for name, body in bodies.items():
        route, targets = routes[name]
        graph.add_node(name, diagnosed(name, body, route))
        graph.add_conditional_edges(name, diagnostic_route(route), list(targets) +
                                    (["exception_cleanup", "diagnose_exception"] if deps.options.diagnostics else []))
    # 병렬 갈래: 갈래 안에서는 멈춰도 합류(join)까지 간 뒤 한 곳에서 판단한다(동시 전이 충돌을 피한다)
    graph.add_node("build_start", guarded(steps.build_start))
    graph.add_node("build_wait", guarded(branch(steps.build_wait)))
    graph.add_node("sast_start", guarded(steps.sast_start))
    graph.add_node("sast_wait", guarded(branch(steps.sast_wait)))
    graph.add_edge("build_start", "build_wait")
    graph.add_edge("sast_start", "sast_wait")
    graph.add_edge(["build_wait", "sast_wait"], "join")
    graph.add_node(HALTED, steps.halted)
    if deps.options.diagnostics:
        flow = ExceptionFlow(deps)
        def cleanup(state):
            if state.get("deploy_build") is not None and not state.get("exception_cleanup_done"):
                steps._release()
            return {"exception_cleanup_done": True}
        graph.add_node("exception_cleanup", cleanup)
        graph.add_node("diagnose_exception", flow.analyze)
        graph.add_node("save_diagnostic_report", flow.save)
        graph.add_node("review_failed_execution", guarded(flow.review_failure))
        graph.add_node("exception_finish", flow.finish)
        graph.add_edge("exception_cleanup", "diagnose_exception")
        graph.add_edge("diagnose_exception", "save_diagnostic_report")
        def after_report(state):
            if state.get("report_failed"):
                return HALTED
            resume = state.get("diagnosis_resume")
            return resume if resume in HUMAN_GATES else "review_failed_execution"
        graph.add_conditional_edges("save_diagnostic_report", after_report,
                                    [HALTED, "review_failed_execution", *HUMAN_GATES])
        graph.add_conditional_edges("review_failed_execution",
                                   lambda state: HALTED if state.get("report_failed") else "exception_finish",
                                   [HALTED, "exception_finish"])
        graph.add_edge("exception_finish", END)
    graph.add_edge(START, "prepare")
    graph.add_edge(HALTED, END)
    return graph.compile(checkpointer=checkpointer)
