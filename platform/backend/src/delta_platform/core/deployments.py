"""Pin, approve and queue a deployment batch without mutating the source environment."""

import re
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .deployment_targets import DeploymentTarget, catalog, connection
from .environments import EnvironmentInput
from .errors import Problem
from .schema import deployment_plans as plans
from .schema import environments, runs, services
from .store import RUN_FIELDS, Store, append_event, digest, select_fields
from .workflows import WorkflowStore, identity


class DeploymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["deployment", "migration"]
    build_id: UUID
    backend_build_id: UUID | None = None
    source_environment_id: UUID | None = None
    targets: list[DeploymentTarget] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def target_count(self):
        if len({t.cloud for t in self.targets}) != len(self.targets):
            raise ValueError("서로 다른 환경을 선택하세요.")
        if self.kind == "deployment" and (len(self.targets) != 1 or self.source_environment_id):
            raise ValueError("최초 배포는 하나의 대상만 선택하세요.")
        if self.kind == "migration" and not self.source_environment_id:
            raise ValueError("이전 출발 환경이 필요합니다.")
        if self.build_id == self.backend_build_id:
            raise ValueError("FE·BE 빌드는 달라야 합니다.")
        return self


class DeploymentStore:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def _service(self, conn, service_id):
        row = (
            conn.execute(sa.select(services).where(services.c.id == service_id).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
        return row

    def _plan(self, conn, identifier):
        row = conn.execute(sa.select(plans).where(plans.c.id == identifier)).mappings().first()
        if not row:
            raise Problem("deployment_not_found", "배포 계획을 찾을 수 없습니다.", 404)
        return row

    def _component(self, conn, build_id, service, *, primary=None):
        build = conn.execute(sa.select(runs).where(runs.c.id == build_id)).mappings().first()
        owner = (
            conn.execute(sa.select(services).where(services.c.id == build["service_id"]))
            .mappings()
            .first()
            if build
            else None
        )
        if (
            not build
            or not owner
            or owner["project_id"] != service["project_id"]
            or build["kind"] != "image_build"
            or build["status"] != "succeeded"
        ):
            raise Problem("build_not_ready", "같은 프로젝트의 완료된 빌드가 필요합니다.", 409)
        source, result = build["input_snapshot"], build["result"]
        if (not primary and owner["id"] != service["id"]) or (
            primary
            and (
                source.get("repository_url") != primary["repository_url"]
                or build["resolved_commit_sha"] != primary["commit_sha"]
            )
        ):
            raise Problem(
                "build_not_owned", "같은 서비스 또는 같은 저장소·커밋의 BE 빌드를 선택하세요.", 409
            )
        if (
            not re.fullmatch(r"[a-f0-9]{40}", build["resolved_commit_sha"] or "")
            or result.get("platform") != "linux/amd64"
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", result.get("image_digest", ""))
            or not result.get("image", "").endswith("@" + result["image_digest"])
        ):
            raise Problem("image_not_pinned", "검증된 amd64 이미지 digest가 필요합니다.", 409)
        return {
            "name": "backend" if primary else "app",
            "build_id": str(build_id),
            "service_id": str(owner["id"]),
            "repository_url": source["repository_url"],
            "commit_sha": build["resolved_commit_sha"],
            "image": result["image"],
            "image_digest": result["image_digest"],
            "container_port": source["container_port"],
            "healthcheck_path": source["healthcheck_path"],
        }

    def _evidence(self, conn, components, cloud):
        evidence = []
        for component in components:
            candidates = conn.execute(
                sa.select(runs)
                .where(
                    runs.c.service_id == UUID(component["service_id"]),
                    runs.c.kind.in_(["sandbox_check", "dast_check"]),
                    runs.c.status == "succeeded",
                    runs.c.resolved_commit_sha == component["commit_sha"],
                )
                .order_by(runs.c.created_at.desc())
                .limit(100)
            ).mappings()
            found = next(
                (
                    r
                    for r in candidates
                    if r["input_snapshot"].get("build_id") == component["build_id"]
                    and r["input_snapshot"].get("image") == component["image"]
                    and r["input_snapshot"].get("sandbox_target", {}).get("cloud", "aws") == cloud
                    and r["result"].get("runtime_check") == "passed"
                    and r["result"].get("cleanup") == "stopped"
                ),
                None,
            )
            if not found:
                # A successful bundle check also covers its pinned backend image.
                if len(components) == 2 and component["name"] == "backend" and evidence:
                    check = (
                        conn.execute(sa.select(runs).where(runs.c.id == UUID(evidence[0]["id"])))
                        .mappings()
                        .one()
                    )
                    backend = check["input_snapshot"].get("backend", {})
                    if (
                        backend.get("build_id") == component["build_id"]
                        and backend.get("image") == component["image"]
                    ):
                        evidence.append(evidence[0])
                        continue
                return [], "이 대상에서 같은 이미지의 샌드박스 실행 검사를 완료하세요."
            evidence.append({"id": str(found["id"]), "result_hash": digest(found["result"])})
        return evidence, None

    def create(self, service_id, body, key):
        request_hash = digest(body.model_dump(mode="json"))
        with self.engine.begin() as conn:
            service = self._service(conn, service_id)
            old = (
                conn.execute(
                    sa.select(plans).where(
                        plans.c.service_id == service_id, plans.c.request_key == key
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if old["request_hash"] != request_hash:
                    raise Problem("idempotency_conflict", "같은 요청 키의 내용이 다릅니다.", 409)
                return self._view(conn, old), False
            Store._assert_no_removal(conn, service_id)
            source = None
            if body.source_environment_id:
                env = WorkflowStore(self.engine)._environment(
                    conn, service_id, service["project_id"], body.source_environment_id
                )
                source = {**identity(env), "version": env["version"]}
                if source["cloud"] in {t.cloud for t in body.targets}:
                    raise Problem("source_is_target", "출발 환경과 다른 대상을 선택하세요.", 422)
            elif conn.scalar(
                sa.select(environments.c.id)
                .where(
                    environments.c.service_id == service_id,
                    sa.func.coalesce(environments.c.binding["runtime_status"].astext, "active")
                    != "removed",
                )
                .limit(1)
            ):
                raise Problem(
                    "already_deployed",
                    "배포한 환경이 있습니다. 이전 메뉴에서 출발 환경을 선택하세요.",
                    409,
                )
            components = [self._component(conn, body.build_id, service)]
            if body.backend_build_id:
                components.append(
                    self._component(conn, body.backend_build_id, service, primary=components[0])
                )
                if components[1]["container_port"] != 8080:
                    raise Problem(
                        "backend_port_invalid",
                        "현재 FE·BE 배포 계약은 BE 8080 포트를 사용합니다.",
                        422,
                    )
            options = {o["cloud"]: o for o in catalog(self.settings)["items"]}
            execution = []
            from delta_platform.adapters.deployment import deployment_settings
            from delta_platform.adapters.jenkins import jenkins_target

            for target in sorted(body.targets, key=lambda t: t.cloud):
                evidence, evidence_reason = self._evidence(conn, components, target.cloud)
                reason = options[target.cloud]["reason"] or evidence_reason
                try:
                    conn_info = connection(self.settings, target.cloud)
                    runner = jenkins_target(deployment_settings(self.settings))
                except Problem:
                    conn_info, runner = None, None
                execution.append(
                    {
                        "target": target.model_dump(),
                        "environment_id": str(uuid4()),
                        "run_id": str(uuid4()),
                        "connection": conn_info,
                        "runner": runner,
                        "evidence": evidence,
                        "reason": reason,
                    }
                )
            snapshot = {
                "service_id": str(service_id),
                "project_id": str(service["project_id"]),
                "image": components[0]["image"],
                "commit_sha": components[0]["commit_sha"],
                "source": source,
                "targets": [e["target"] for e in execution],
                "components": components,
                "execution": execution,
            }
            row = (
                conn.execute(
                    plans.insert()
                    .values(
                        id=uuid4(),
                        service_id=service_id,
                        kind=body.kind,
                        request_key=key,
                        request_hash=request_hash,
                        input_snapshot=snapshot,
                        input_hash=digest(snapshot),
                    )
                    .returning(plans)
                )
                .mappings()
                .one()
            )
            return self._view(conn, row), True

    def _view(self, conn, plan):
        snapshot = plan["input_snapshot"]
        targets = []
        for target in snapshot["execution"]:
            row = (
                conn.execute(sa.select(runs).where(runs.c.id == UUID(target["run_id"])))
                .mappings()
                .first()
            )
            targets.append(
                {
                    "target": target["target"],
                    "environment_id": target["environment_id"],
                    "run": select_fields(row, RUN_FIELDS) if row else None,
                    "status": row["status"] if row else "planned",
                    "reason": (row["error"] or {}).get("message") if row else target["reason"],
                    "service_url": (row["result"] or {}).get("service_url") if row else None,
                }
            )
        states = {t["status"] for t in targets}
        state = (
            "planned"
            if not plan["approved_at"]
            else "running"
            if "running" in states
            else "queued"
            if "queued" in states
            else "succeeded"
            if states == {"succeeded"}
            else "partial"
            if "succeeded" in states
            else "failed"
        )
        return {
            **{
                k: plan[k]
                for k in ("id", "service_id", "kind", "input_hash", "created_at", "approved_at")
            },
            "status": state,
            "input_snapshot": {
                k: snapshot[k] for k in ("image", "commit_sha", "source", "targets", "components")
            },
            "targets": targets,
            "executable": state == "planned" and all(not t["reason"] for t in targets),
        }

    def get(self, identifier):
        with self.engine.connect() as conn:
            return self._view(conn, self._plan(conn, identifier))

    def list(self, service_id):
        Store(self.engine).service(service_id)
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(plans)
                    .where(plans.c.service_id == service_id)
                    .order_by(plans.c.created_at.desc())
                    .limit(30)
                )
                .mappings()
                .all()
            )
            return {"items": [self._view(conn, row) for row in rows]}

    def execute(self, identifier, expected_hash, actor):
        with self.engine.begin() as conn:
            plan = self._plan(conn, identifier)
            service = self._service(conn, plan["service_id"])
            plan = self._plan(conn, identifier)  # Read again after serializing approval.
            snapshot = plan["input_snapshot"]
            if expected_hash != plan["input_hash"] or digest(snapshot) != expected_hash:
                raise Problem("approval_mismatch", "검토한 계획과 다릅니다.", 409)
            if plan["approved_at"]:
                return self._view(conn, plan)
            Store._assert_no_removal(conn, service["id"])
            if conn.scalar(
                sa.select(runs.c.id)
                .where(runs.c.service_id == service["id"], runs.c.status.in_(["queued", "running"]))
                .limit(1)
            ):
                raise Problem("service_busy", "진행 중인 작업이 있습니다.", 409)
            if snapshot["source"]:
                source = snapshot["source"]
                current = WorkflowStore(self.engine)._environment(
                    conn, service["id"], service["project_id"], UUID(source["id"])
                )
                if {**identity(current), "version": current["version"]} != source:
                    raise Problem(
                        "source_changed", "출발 환경이 변경됐습니다. 새 계획을 검토하세요.", 409
                    )
            elif conn.scalar(
                sa.select(environments.c.id)
                .where(
                    environments.c.service_id == service["id"],
                    sa.func.coalesce(environments.c.binding["runtime_status"].astext, "active")
                    != "removed",
                )
                .limit(1)
            ):
                raise Problem(
                    "already_deployed", "이미 배포한 환경이 있습니다. 이전 계획을 만드세요.", 409
                )
            from delta_platform.adapters.deployment import deployment_settings
            from delta_platform.adapters.jenkins import jenkins_target

            runner = jenkins_target(deployment_settings(self.settings))
            for target in snapshot["execution"]:
                if (
                    target["reason"]
                    or target["connection"] != connection(self.settings, target["target"]["cloud"])
                    or target["runner"] != runner
                ):
                    raise Problem(
                        "deployment_not_ready",
                        target["reason"] or "배포 설정이 변경됐습니다. 새 계획을 만드세요.",
                        409,
                    )
                for evidence in target["evidence"]:
                    row = (
                        conn.execute(sa.select(runs).where(runs.c.id == UUID(evidence["id"])))
                        .mappings()
                        .first()
                    )
                    if (
                        not row
                        or row["status"] != "succeeded"
                        or digest(row["result"]) != evidence["result_hash"]
                    ):
                        raise Problem(
                            "evidence_changed", "검사 결과가 달라졌습니다. 다시 검사하세요.", 409
                        )
                request = {
                    "schema_version": 1,
                    "plan_id": str(identifier),
                    "plan_hash": expected_hash,
                    "service_id": str(service["id"]),
                    "project_id": str(service["project_id"]),
                    "environment_id": target["environment_id"],
                    "target": target["target"],
                    "connection": target["connection"],
                    "runner": runner,
                    "components": snapshot["components"],
                    "commit_sha": snapshot["commit_sha"],
                }
                conn.execute(
                    runs.insert().values(
                        id=UUID(target["run_id"]),
                        service_id=service["id"],
                        kind="service_deploy",
                        input_snapshot=request,
                        request_key=str(identifier) + ":" + target["target"]["cloud"],
                        request_hash=digest(request),
                        status="queued",
                        resolved_commit_sha=snapshot["commit_sha"],
                    )
                )
                append_event(
                    conn,
                    UUID(target["run_id"]),
                    "queued",
                    "대상 환경의 앱 배포를 접수했습니다. 원본 서비스는 유지합니다.",
                )
            plan = (
                conn.execute(
                    plans.update()
                    .where(plans.c.id == identifier)
                    .values(approved_by=actor, approved_at=sa.func.now())
                    .returning(plans)
                )
                .mappings()
                .one()
            )
            return self._view(conn, plan)


def validate_report(claim, report):
    """Only a trusted deployment job may provide resource identities; reject mismatched results."""
    from urllib.parse import urlsplit

    request = claim["input_snapshot"]
    expected = {
        "schema_version": 1,
        "run_id": str(claim["id"]),
        "plan_hash": request["plan_hash"],
        "environment_id": request["environment_id"],
        "target": request["target"],
        "images": [c["image"] for c in request["components"]],
        "commit_sha": request["commit_sha"],
    }
    if (
        any(report.get(k) != v for k, v in expected.items())
        or report.get("health_passed") is not True
    ):
        raise ValueError("Deployment identity/health mismatch")
    env = EnvironmentInput.model_validate(report["environment"])
    if (
        env.cloud != request["target"]["cloud"]
        or env.region != request["target"]["region"]
        or env.version != request["commit_sha"]
        or not env.instances
    ):
        raise ValueError("Deployment environment mismatch")
    conn = request["connection"]
    if (
        env.account_id != conn.get("account_id")
        or env.gcp_project != conn.get("gcp_project")
        or (env.cloud == "onprem" and not env.runtime)
    ):
        raise ValueError("Deployment account/runtime mismatch")
    url = report.get("service_url")
    if url is not None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Deployment URL invalid")
    return {
        **expected,
        "health_passed": True,
        "environment": env.model_dump(mode="json"),
        "service_url": url,
    }


def complete_deployment(conn, claim, report):
    result = validate_report(claim, report)
    env = dict(result["environment"])
    public = {k: env.pop(k) for k in ("name", "cloud", "region", "version")}
    # Environment names are derived from the approved ID, never an existing customer's name.
    public["name"] = "deploy-" + claim["input_snapshot"]["environment_id"][:12]
    identifier = UUID(claim["input_snapshot"]["environment_id"])
    if conn.scalar(sa.select(environments.c.id).where(environments.c.id == identifier)):
        raise ValueError("Environment already bound")
    conn.execute(
        environments.insert().values(
            id=identifier,
            service_id=claim["service_id"],
            project_id=UUID(claim["input_snapshot"]["project_id"]),
            **public,
            binding={
                **env,
                "deployment_run_id": str(claim["id"]),
                "service_url": result["service_url"],
            },
        )
    )
