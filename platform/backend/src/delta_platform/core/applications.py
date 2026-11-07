"""A durable application workflow over the existing leased execution queue."""

import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_platform.adapters.builds import build_target
from delta_platform.adapters.dast import dast_target
from delta_platform.adapters.sandbox import sandbox_target
from delta_platform.adapters.security import security_target
from delta_platform.core.deployment_targets import DeploymentTarget
from delta_platform.core.deployments import DeploymentInput, DeploymentStore
from delta_platform.core.errors import Problem
from delta_platform.core.inputs import ServiceInput
from delta_platform.core.schema import services
from delta_platform.core.store import Store, digest


class DatabaseChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    mode: str = Field(pattern=r"^(new|existing)$")


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    repository_url: str
    source_ref: str = "main"
    frontend_context: str = "frontend"
    frontend_dockerfile: str = "frontend/Dockerfile"
    backend_context: str = "backend"
    backend_dockerfile: str = "backend/Dockerfile"
    deployment_target: DeploymentTarget
    database: DatabaseChoice

    def component(self, role, name=None):
        return ServiceInput(
            name=name or self.name,
            repository_url=self.repository_url,
            source_ref=self.source_ref,
            build_context=getattr(self, role + "_context"),
            dockerfile_path=getattr(self, role + "_dockerfile"),
            container_port=3000 if role == "frontend" else 8080,
            healthcheck_path="/healthz",
            deployment_target=self.deployment_target,
        )

    @model_validator(mode="after")
    def valid_components(self):
        self.component("frontend")
        self.component("backend")
        return self


def database_catalog(settings):
    try:
        items = json.loads(settings.application_databases or "[]")
        result = []
        ids = set()
        for item in items:
            choice = DatabaseChoice(profile_id=item["id"], mode=item["modes"][0])
            server_mode = item.get("server_mode", "prepared")
            if server_mode not in {"create", "prepared"} or (
                server_mode == "create"
                and (item["modes"] != ["new"] or not set(item["clouds"]) <= {"aws", "gcp"})
            ):
                raise ValueError()
            if choice.profile_id in ids or not item["revision"] or item["engine"] != "postgres":
                raise ValueError()
            if not set(item["clouds"]) <= {"aws", "gcp", "onprem"} or not set(item["modes"]) <= {
                "new",
                "existing",
            }:
                raise ValueError()
            ids.add(choice.profile_id)
            # The catalog is public identity, never a secret or a connection string.
            result.append(
                {
                    **{
                        k: item[k] for k in ("id", "label", "engine", "revision", "clouds", "modes")
                    },
                    "server_mode": server_mode,
                }
            )
        return {"items": result}
    except (ValueError, TypeError, KeyError, IndexError):
        raise Problem(
            "database_catalog_invalid", "운영자의 PostgreSQL 연결 설정을 확인하세요.", 503
        ) from None


# Registration only stands up the current production (the migration source). Security checks
# (SAST → staging DAST → AI fix → re-check) belong to the migration pipeline, so they are not
# repeated here. The sandbox run stays: deployment plans require its runtime evidence.
STEPS = [
    ("source_frontend", "프론트엔드 소스 확인"),
    ("source_backend", "백엔드 소스 확인"),
    ("build_frontend", "Jenkins 프론트엔드 빌드"),
    ("build_backend", "Jenkins 백엔드 빌드"),
    ("sandbox_frontend", "프론트엔드 샌드박스"),
    ("sandbox_backend", "백엔드 샌드박스"),
    ("deploy", "VM·DB 준비 → 앱 배포 → 연결 확인"),
]


class ApplicationStore:
    def __init__(self, engine, settings):
        self.engine, self.settings, self.store = engine, settings, Store(engine)

    def register(self, body, key, actor):
        request_hash = digest(body.model_dump(mode="json"))
        profile = next(
            (
                p
                for p in database_catalog(self.settings)["items"]
                if p["id"] == body.database.profile_id
            ),
            None,
        )
        if (
            not profile
            or body.database.mode not in profile["modes"]
            or body.deployment_target.cloud not in profile["clouds"]
        ):
            raise Problem(
                "database_unavailable", "이 배포 환경에서 사용할 PostgreSQL을 선택하세요.", 422
            )
        with self.engine.begin() as conn:
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(72401101)"))
            old = (
                conn.execute(sa.select(services).where(services.c.request_key == key))
                .mappings()
                .first()
            )
            if old:
                if old["request_hash"] != request_hash or not old["application"]:
                    raise Problem("idempotency_conflict", "같은 요청 키의 내용이 다릅니다.", 409)
                identifier, created = old["id"], False
            else:
                if conn.scalar(sa.select(services.c.id).where(services.c.name == body.name)):
                    raise Problem("service_name_conflict", "같은 이름의 서비스가 있습니다.", 409)
                identifier, child = uuid4(), uuid4()
                application = {
                    "version": 1,
                    "template": "delta-notes",
                    "status": "queued",
                    "actor": actor,
                    "components": {"frontend": str(identifier), "backend": str(child)},
                    "database": {
                        **body.database.model_dump(),
                        "revision": profile["revision"],
                        "engine": "postgres",
                    },
                    "target": body.deployment_target.model_dump(),
                    "steps": [],
                    "reviews": [],
                    "error": None,
                }
                for role, service_id, name, parent in (
                    ("frontend", identifier, body.name, None),
                    ("backend", child, "component-" + child.hex, identifier),
                ):
                    payload = body.component(role, name).model_dump(exclude_none=True)
                    owner, repo = payload["repository_url"].split("/")[-2:]
                    conn.execute(
                        services.insert().values(
                            **payload,
                            id=service_id,
                            repo_owner=owner,
                            repo_name=repo,
                            request_key=key if parent is None else "component:" + str(child),
                            request_hash=request_hash,
                            parent_service_id=parent,
                            application=application if parent is None else None,
                        )
                    )
                created = True
        return self.store.service(identifier), created

    def _save(self, identifier, state):
        with self.engine.begin() as conn:
            conn.execute(
                services.update()
                .where(services.c.id == identifier, services.c.deletion_requested.is_(False))
                .values(application=state)
            )

    def resume(self, identifier, *, approve=False):
        # Serialize UI actions against the coordinator, including approval evidence.
        with self.engine.connect() as lock:
            lock.execute(
                sa.text("SELECT pg_advisory_lock(hashtextextended(:id, 11))"),
                {"id": str(identifier)},
            )
            try:
                state = self.store.service(identifier)["application"]
                if not state or state["status"] not in ("blocked", "review"):
                    raise Problem(
                        "application_not_paused",
                        "설정 대기 또는 검토 대기인 작업만 계속할 수 있습니다.",
                        409,
                    )
                if state["status"] == "review":
                    if not approve:
                        raise Problem("review_required", "검사 결과를 검토하고 승인하세요.", 409)
                    state["reviews"] = self._review_evidence(state)
                state.update(status="running", error=None)
                self._save(identifier, state)
            finally:
                lock.execute(
                    sa.text("SELECT pg_advisory_unlock(hashtextextended(:id, 11))"),
                    {"id": str(identifier)},
                )
        return self.store.service(identifier)

    def _review_evidence(self, state):
        evidence = []
        for step in state["steps"]:
            if step["key"].startswith(("security_", "dast_")) and step.get("run_id"):
                result = self.store.run(UUID(step["run_id"]))["result"] or {}
                if result.get("requires_review") or result.get("dast_check") == "not_run":
                    evidence.append(digest({"run_id": step["run_id"], "result": result}))
        return evidence

    def tick(self):
        with self.engine.connect() as conn:
            identifiers = conn.scalars(
                sa.select(services.c.id)
                .where(
                    services.c.application["status"].astext.in_(("queued", "running")),
                    services.c.deletion_requested.is_(False),
                )
                .limit(30)
            ).all()
        for identifier in identifiers:
            with self.engine.connect() as lock:
                params = {"id": str(identifier)}
                if not lock.scalar(
                    sa.text("SELECT pg_try_advisory_lock(hashtextextended(:id, 11))"), params
                ):
                    continue
                try:
                    self.advance(identifier)
                finally:
                    lock.execute(
                        sa.text("SELECT pg_advisory_unlock(hashtextextended(:id, 11))"), params
                    )

    def advance(self, identifier):
        service = self.store.service(identifier)
        state = service["application"]
        if (
            not state
            or state["status"] not in ("queued", "running")
            or service["deletion_requested"]
        ):
            return
        done = {s["key"]: s for s in state["steps"]}
        for key, label in STEPS:
            step = done.get(key)
            if step and step["status"] in ("succeeded", "skipped"):
                continue
            if step is None:
                step = {"key": key, "label": label, "status": "queued"}
                state["steps"].append(step)
            try:
                if key == "deploy":
                    evidence = self._review_evidence(state)
                    if evidence != state["reviews"]:
                        state.update(
                            status="review",
                            error="검사 발견 사항 또는 DAST 미실행 결과를 검토하세요.",
                        )
                        self._save(identifier, state)
                        return
                    from .deployment_targets import catalog

                    option = next(
                        o
                        for o in catalog(self.settings)["items"]
                        if o["cloud"] == state["target"]["cloud"]
                    )
                    if not option["configured"]:
                        raise Problem("deployment_not_configured", option["reason"], 503)
                    deployments = DeploymentStore(self.engine, self.settings)
                    plan, _ = deployments.create(
                        identifier,
                        DeploymentInput(
                            kind="deployment",
                            build_id=UUID(done["build_frontend"]["run_id"]),
                            backend_build_id=UUID(done["build_backend"]["run_id"]),
                            targets=[DeploymentTarget.model_validate(state["target"])],
                            database=state["database"],
                        ),
                        "application:" + str(identifier) + ":deploy",
                    )
                    if not plan["approved_at"]:
                        if not plan["executable"]:
                            raise Problem(
                                "deployment_not_ready",
                                next(t["reason"] for t in plan["targets"] if t["reason"]),
                                409,
                            )
                        plan = deployments.execute(plan["id"], plan["input_hash"], state["actor"])
                    run = plan["targets"][0]["run"]
                    state["deployment_plan_id"] = str(plan["id"])
                elif step.get("run_id"):
                    run = self.store.run(UUID(step["run_id"]))
                else:
                    kind, role = key.split("_", 1)
                    sid = UUID(state["components"][role])
                    request_key = "application:" + str(identifier) + ":" + key
                    if kind == "source":
                        sha = (
                            self.store.run(UUID(done["source_frontend"]["run_id"]))[
                                "resolved_commit_sha"
                            ]
                            if role == "backend"
                            else None
                        )
                        run, _ = self.store.enqueue(sid, request_key, source_sha=sha)
                    elif kind == "build":
                        run, _ = self.store.enqueue_build(
                            sid,
                            UUID(done["source_" + role]["run_id"]),
                            request_key,
                            build_target(self.settings),
                        )
                    elif kind == "security":
                        run, _ = self.store.enqueue_security(
                            sid,
                            UUID(done["build_" + role]["run_id"]),
                            request_key,
                            security_target(self.settings),
                        )
                    else:
                        args = {}
                        if kind == "dast":
                            args = {
                                "security_check_id": UUID(done["security_" + role]["run_id"]),
                                "dast_target": dast_target(self.settings),
                            }
                        run, _ = self.store.enqueue_sandbox(
                            sid,
                            UUID(done["build_" + role]["run_id"]),
                            request_key,
                            sandbox_target(self.settings, state["target"]["cloud"]),
                            **args,
                        )
                step.update(run_id=str(run["id"]), status=run["status"], detail=run["current_step"])
                result = run["result"] or {}
                if key.startswith(("security_", "dast_")):
                    step["report"] = {
                        k: result[k]
                        for k in (
                            "counts",
                            "findings",
                            "dast_counts",
                            "dast_findings",
                            "dast_check",
                            "dast_reason",
                            "coverage",
                            "requires_review",
                        )
                        if k in result
                    }
                if result.get("dast_check") == "not_run" and run["status"] == "succeeded":
                    step.update(status="skipped", detail="지원하는 검사 후보가 없어 DAST 미실행")
                state.update(status="running", error=None)
                if run["status"] == "failed":
                    state.update(
                        status="failed", error=(run["error"] or {}).get("message", "실행 실패")
                    )
                elif key == "deploy" and run["status"] == "succeeded":
                    state.update(status="succeeded", service_url=result.get("service_url"))
                self._save(identifier, state)
                return
            except Problem as exc:
                waiting = exc.code in ("sandbox_busy", "service_busy", "source_check_in_progress")
                state.update(status="running" if waiting else "blocked", error=exc.message)
                self._save(identifier, state)
                return
