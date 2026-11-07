"""Read allowlisted state or submitted YAML, normalize facts, then run the existing assessment library."""

from __future__ import annotations

import asyncio
import os

from delta_ai.contract import AssessmentRequest, Evidence, MappingInput, ServiceRef
from delta_ai.llm import LlmClient
from delta_ai.service import assess
from delta_ai.store import RuleStore
from pydantic import ValidationError

from delta_diff.contract import DiffRequest, DiffResult, ManifestMeta, SourceMeta
from delta_diff.kubernetes import ManifestRejected, extract_kubernetes
from delta_diff.sources import (
    StateAllowlist,
    StateLocation,
    StateNotAllowed,
    StateReader,
)
from delta_diff.terraform import extract_mapping, state_to_show

DEMO_STATE = StateLocation(
    "sbh-delta-workload-tf-616723161368", "delta/lab/workload-client/terraform.tfstate"
)
DEMO_TGW_SUBNETS = frozenset(
    {"delta-lab-client-apne2-az1-tgw", "delta-lab-client-apne2-az2-tgw"}
)


class MappingRejected(Exception):
    """Only safe error messages cross the API boundary."""


def _manifest(text):
    try:
        inventory, digest = extract_kubernetes(text)
    except ManifestRejected as exc:
        raise MappingRejected(str(exc)) from exc
    count = sum(
        len(getattr(inventory, key))
        for key in (
            "workloads",
            "volumes",
            "load_balancers",
            "network_policies",
            "unsupported_kinds",
        )
    )
    return inventory, ManifestMeta(sha256=digest, resource_count=count)


def _mapping_input(
    req: DiffRequest, extracted: dict, target_inventory=None
) -> MappingInput:
    if not any(
        extracted.get(k)
        for k in ("subnets", "servers", "load_balancers", "databases", "kubernetes")
    ) and set(extracted.get("security") or {}) <= {"origin"}:
        raise MappingRejected("매핑할 자원이 없다")
    try:
        return MappingInput.model_validate(
            {
                **extracted,
                "source_provider": req.source.provider,
                "target_provider": req.target.provider,
                "region": req.target.region,
                "source_region": req.source.region,
                "network_mode": "reuse" if req.target.existing_subnets else "create",
                "existing_subnets": req.target.existing_subnets,
                "target_kubernetes": target_inventory,
                "accepted_gaps": req.accepted_gaps,
                "scc_tier": req.scc_tier,
            }
        )
    except ValidationError as exc:
        # Never interpolate validation input or arbitrary values into responses/logs.
        raise MappingRejected(
            "출발·대상 방향 또는 매핑 필드가 지원 계약과 맞지 않는다"
        ) from exc


async def run_diff(
    req: DiffRequest,
    *,
    reader: StateReader,
    allowlist: StateAllowlist,
    llm: LlmClient | None,
    store: RuleStore,
) -> DiffResult:
    target_inventory, target_meta = (
        _manifest(req.target.manifests)
        if req.target.manifests is not None
        else (None, None)
    )
    if req.source.provider == "onprem":
        inventory, source = _manifest(req.source.manifests)
        extracted = {
            "kubernetes": inventory,
            "load_balancers": inventory.load_balancers,
            "databases": inventory.databases,
        }
    else:
        location = StateLocation(req.source.state.bucket, req.source.state.key)
        demo_scope = os.environ.get("DELTA_DIFF_DEMO_WORKLOAD_SCOPE") == "1"
        if demo_scope and location != DEMO_STATE:
            raise StateNotAllowed("시연에서 승인한 워크로드 state만 읽을 수 있다")
        if not allowlist.allows(location):
            raise StateNotAllowed(
                f"허용하지 않은 state 위치다: s3://{location.bucket}/{location.key}"
            )
        snapshot = await asyncio.to_thread(reader.read, location)
        try:
            show = state_to_show(snapshot.state)
            extracted = extract_mapping(
                show, req.source.exclude_subnets, req.source.provider
            )
            excluded_capabilities = ()
            if demo_scope and req.source.provider == "aws" and req.target.provider == "gcp":
                extracted["subnets"] = [
                    subnet for subnet in extracted["subnets"]
                    if subnet["name"] not in DEMO_TGW_SUBNETS
                ]
                if req.service_id == "delta-sample-app" and not any(
                    r["type"] == "aws_lambda_function"
                    for r in show["values"]["root_module"]["resources"]
                ):
                    security = extracted.get("security") or {}
                    plans = security.get("guardduty_plans", [])
                    if "LAMBDA_NETWORK_LOGS" in plans:
                        security["guardduty_plans"] = [p for p in plans if p != "LAMBDA_NETWORK_LOGS"]
                        excluded_capabilities = ("LAMBDA_NETWORK_LOGS",)
            source = SourceMeta(
                bucket=location.bucket,
                key=location.key,
                version_id=snapshot.version_id,
                serial=snapshot.state.get("serial"),
                lineage=snapshot.state.get("lineage"),
                terraform_version=snapshot.state.get("terraform_version"),
                resource_count=len(show["values"]["root_module"]["resources"]),
                excluded_capabilities=excluded_capabilities,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MappingRejected("Terraform state의 매핑 필드가 잘못됐다") from exc
    mapping = _mapping_input(req, extracted, target_inventory)
    request = AssessmentRequest(
        assessment_id=req.diff_id,
        kind="migration_mapping",
        service=ServiceRef(service_id=req.service_id),
        evidence=Evidence(mapping=mapping),
    )
    result = await assess(request, llm=llm, store=store)
    return DiffResult(
        diff_id=req.diff_id,
        source=source,
        target_manifest=target_meta,
        mapping_input=mapping,
        assessment=result,
    )
