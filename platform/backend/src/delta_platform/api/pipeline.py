"""Install pipeline routes into the platform's existing authenticated router."""

from typing import Annotated

from fastapi import Query, Request
from pydantic import Field

from delta_platform.adapters.pipeline_relay import OrchestratorRelay
from delta_platform.core.inputs import StrictInput, request_key

DEFAULT_REPOSITORY = "https://github.com/2026-sbh-delta/delta-sample-app"


class PipelineStartInput(StrictInput):
    branch: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._/-]+$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    cutover: bool = False
    release_target: str | None = Field(default=None, max_length=64)


class PipelineAnswerInput(StrictInput):
    value: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    decision_id: str | None = Field(default=None, max_length=128)
    case_id: str | None = Field(default=None, max_length=128)


def install(protected, settings, relay=None):
    pipeline = relay or OrchestratorRelay(settings)
    repository = getattr(settings, "pipeline_repository", DEFAULT_REPOSITORY)

    @protected.get("/pipeline/runs")
    def pipeline_runs():
        return pipeline.runs()

    @protected.get("/pipeline/options")
    def pipeline_options(branch: Annotated[str | None, Query(max_length=100)] = None):
        return pipeline.options(repository, branch)

    @protected.post("/pipeline/runs", status_code=202)
    def pipeline_start(body: PipelineStartInput, request: Request):
        key = request_key(request.headers.get("idempotency-key"))
        return pipeline.start({"repo": repository, **body.model_dump()}, key=key)

    @protected.get("/pipeline/runs/{run_id}")
    def pipeline_run(run_id: str):
        return pipeline.run(run_id)

    @protected.get("/pipeline/runs/{run_id}/log/{node_id}")
    def pipeline_log(run_id: str, node_id: str):
        return pipeline.log(run_id, node_id)

    @protected.post("/pipeline/runs/{run_id}/answer")
    def pipeline_answer(run_id: str, body: PipelineAnswerInput):
        return pipeline.answer(run_id, body.value, body.decision_id, body.case_id)
