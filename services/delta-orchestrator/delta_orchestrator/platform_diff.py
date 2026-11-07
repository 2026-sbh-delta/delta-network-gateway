"""Authenticated, read-only Diff relay for the existing operating platform.

Cloud state is operator-pinned. Kubernetes input is bounded and interpreted by
the existing allowlist extractor; this endpoint never executes a manifest.
"""

import hashlib
import json
import re
import urllib.error
import urllib.request
from uuid import UUID

from delta_ai.contract import AssessmentResult
from delta_orchestrator.pipelines import diff_client, diff_input
from delta_pipeline.model import REPO_PREFIX
from fastapi import APIRouter, HTTPException, Request

MAX_BODY = 64_000
MAX_RESULT = 256_000
AWS_INSTANCES = frozenset({"i-0637ad326681c7480"})


def compare(body, *, client_factory=diff_client, input_factory=diff_input):
    context = body.get("context")
    if not isinstance(context, dict) or len(context) > 24:
        raise ValueError("execution context required")
    for field in (
        "project_id",
        "service_id",
        "workflow_id",
        "run_id",
        "environment_id",
        "source_environment_id",
    ):
        UUID(context[field])
    for field in ("plan_hash", "payload_hash"):
        if not re.fullmatch(r"[a-f0-9]{64}", context[field]):
            raise ValueError("immutable context required")
    pinned = input_factory()
    if context.get("repository_url") != REPO_PREFIX + pinned.service_id:
        raise ValueError("repository has no operator-pinned Diff")
    source, target = context.get("source_provider"), context.get("target_provider")
    if (
        source not in {"aws", "onprem"}
        or target not in {"aws", "gcp", "onprem"}
        or source == target
    ):
        raise ValueError("unsupported comparison direction")
    region = body.get("target_region")
    if target != "onprem" and (
        not isinstance(region, str) or not re.fullmatch(r"[a-z]+-[a-z0-9-]+", region)
    ):
        raise ValueError("target region required")
    request = {
        "diff_id": context["run_id"],
        "service_id": pinned.service_id,
        "target": {"provider": target},
    }
    if target != "onprem":
        request["target"]["region"] = region
    if source == "aws":
        if set(body.get("source_instance_ids") or ()) != AWS_INSTANCES:
            raise ValueError(
                "registered source does not match the approved Terraform state"
            )
        request["source"] = pinned.source.model_dump(mode="json", exclude_none=True)
    else:
        manifest = body.get("source_manifests")
        if (
            not isinstance(manifest, str)
            or not manifest
            or len(manifest.encode()) > 60_000
        ):
            raise ValueError("registered on-premise inventory required")
        request["source"] = {"provider": "onprem", "manifests": manifest}
    if target == "onprem":
        manifest = body.get("target_manifests")
        if (
            not isinstance(manifest, str)
            or not manifest
            or len(manifest.encode()) > 60_000
        ):
            raise ValueError("registered target inventory required")
        request["target"]["manifests"] = manifest
    # No accepted_gaps, SCC tier or state locations come from the public platform.
    client = client_factory()
    message = urllib.request.Request(
        client._url + "/v1/diffs",
        json.dumps(request).encode(),
        headers={
            "Authorization": "Bearer " + client._token,
            "Content-Type": "application/json",
        },
    )
    with client._opener.open(message, timeout=50) as response:
        raw = response.read(MAX_RESULT + 1)
    if len(raw) > MAX_RESULT:
        raise ValueError("Diff result too large")
    result = json.loads(raw)
    assessment = AssessmentResult.model_validate(result["assessment"])
    if (
        result["diff_id"] != context["run_id"]
        or assessment.assessment_id != context["run_id"]
        or assessment.kind != "migration_mapping"
    ):
        raise ValueError("Diff identity mismatch")
    meta = result["source"]
    if source == "onprem":
        if (
            meta.get("kind") != "kubernetes_yaml"
            or meta.get("sha256")
            != hashlib.sha256(body["source_manifests"].encode()).hexdigest()
        ):
            raise ValueError("Diff source snapshot mismatch")
    elif (meta.get("bucket"), meta.get("key")) != (
        pinned.source.state.bucket,
        pinned.source.state.key,
    ):
        raise ValueError("Diff state mismatch")
    if (
        target == "onprem"
        and (result.get("target_manifest") or {}).get("sha256")
        != hashlib.sha256(body["target_manifests"].encode()).hexdigest()
    ):
        raise ValueError("Diff target snapshot mismatch")
    if not assessment.mapping or not assessment.mapping.rows or not assessment.checks:
        raise ValueError("Diff checks missing")
    rules = {c.rule_id for c in assessment.checks}
    if not {r.rule_id for r in assessment.mapping.rows} <= rules or len(rules) != len(
        assessment.checks
    ):
        raise ValueError("Diff checks missing")
    if assessment.verdict == "pass" and (
        any(r.grade in ("gap", "unknown") for r in assessment.mapping.rows)
        or any(c.state != "green" for c in assessment.checks)
    ):
        raise ValueError("Diff pass contradicts its evidence")
    return {
        "context": context,
        "source": meta,
        "assessment": assessment.model_dump(mode="json"),
    }


def router():
    routes = APIRouter()

    @routes.post("/api/platform-diff")
    async def platform_diff(request: Request):
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > MAX_BODY:
                raise HTTPException(413, "차분 입력 크기를 초과했습니다")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise TypeError()
            # Network work must not block the orchestrator event loop.
            from starlette.concurrency import run_in_threadpool

            return await run_in_threadpool(compare, body)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(
                422, "등록 환경과 차분 입력·응답의 연결을 확인하세요"
            ) from None
        except (OSError, urllib.error.HTTPError):
            raise HTTPException(503, "차분 서비스 조회를 완료하지 못했습니다") from None

    return routes
