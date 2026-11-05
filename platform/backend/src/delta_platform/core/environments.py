"""Only deployment/operator code can register cloud resource bindings."""

import argparse
import json
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from delta_platform.core.errors import Problem
from delta_platform.core.runtime import OnpremRuntime
from delta_platform.core.schema import environments
from delta_platform.core.store import Store

WORKSPACE_ID = UUID("496562be-f67b-5f7e-a16b-929fd347b9ad")
FIELDS = ("id", "name", "cloud", "region", "version")


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^(?:i-[0-9a-f]{8,17}|[0-9]{1,24}|onprem-[0-9a-f]{32})$")
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    zone: str = Field(default="", pattern=r"^(?:[a-z]+-[a-z]+[0-9]-[a-z])?$")


class EnvironmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    cloud: Literal["aws", "gcp", "onprem"]
    region: str = Field(pattern=r"^[a-z]+-[a-z0-9-]+$")
    version: str | None = Field(default=None, max_length=128)
    account_id: str | None = Field(default=None, pattern=r"^[0-9]{12}$")
    gcp_project: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    instances: list[Instance] = Field(default_factory=list, max_length=3)
    runtime: OnpremRuntime | None = None

    @model_validator(mode="after")
    def resource_identity(self):
        if self.runtime and self.cloud != "onprem":
            raise ValueError("Only on-premise runtime removal is supported")
        if self.cloud == "aws" and (not self.account_id or self.gcp_project):
            raise ValueError("AWS account required")
        if self.cloud == "gcp" and (not self.gcp_project or self.account_id):
            raise ValueError("GCP project required")
        if len({i.id for i in self.instances}) != len(self.instances):
            raise ValueError("Duplicate instance")
        for i in self.instances:
            if self.cloud == "aws" and (not i.id.startswith("i-") or i.zone):
                raise ValueError("AWS instance required")
            if self.cloud == "gcp" and (not i.id.isdecimal() or not i.zone):
                raise ValueError("GCP instance ID and zone required")
            if self.cloud == "onprem" and (not i.id.startswith("onprem-") or i.zone):
                raise ValueError("Registered on-premise host ID required")
        if self.cloud == "onprem" and (self.account_id or self.gcp_project):
            raise ValueError("On-premise binding cannot contain a cloud account")
        return self


class EnvironmentStore:
    def __init__(self, engine):
        self.engine = engine

    def list(self, service_id):
        Store(self.engine).service(service_id)
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(*[environments.c[f] for f in FIELDS], environments.c.binding)
                .where(environments.c.service_id == service_id)
                .order_by(environments.c.created_at)
                .limit(20)
            ).mappings()
            return {
                "items": [
                    {
                        **{f: row[f] for f in FIELDS},
                        "runtime_registered": bool(row["binding"].get("runtime")),
                        "runtime_status": row["binding"].get("runtime_status", "active"),
                    }
                    for row in rows
                ]
            }

    def get(self, service_id, environment_id):
        Store(self.engine).service(service_id)
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(environments).where(
                        environments.c.service_id == service_id,
                        environments.c.id == environment_id,
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise Problem("environment_not_found", "이 서비스에 연결된 환경이 아닙니다.", 404)
            return dict(row)

    def bind(self, service_id, data, *, environment_id=None):
        # This operator-only entry point is not exposed by the public HTTP API.
        service = Store(self.engine).service(service_id)
        payload = data.model_dump(mode="json")
        public = {f: payload.pop(f) for f in ("name", "cloud", "region", "version")}
        with self.engine.begin() as conn:
            from delta_platform.core.removals import assert_no_removal
            from delta_platform.core.schema import services

            conn.execute(
                sa.select(services.c.id).where(services.c.id == service_id).with_for_update()
            ).one()
            assert_no_removal(conn, service_id)
            row = (
                conn.execute(
                    sa.select(environments)
                    .where(
                        environments.c.service_id == service_id,
                        environments.c.name == data.name,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row:
                if row["binding"].get("runtime_status") == "removed":
                    raise Problem("environment_removed", "삭제한 앱은 새 환경으로 등록하세요.", 409)
                if environment_id and row["id"] != environment_id:
                    raise Problem("environment_conflict", "기존 환경 ID와 다릅니다.", 409)
                if row["cloud"] != data.cloud or row["region"] != data.region:
                    raise Problem(
                        "environment_conflict", "다른 실행 위치는 새 환경으로 등록하세요.", 409
                    )
                conn.execute(
                    environments.update()
                    .where(environments.c.id == row["id"])
                    .values(
                        **public,
                        binding=payload,
                        updated_at=sa.func.now(),
                    )
                )
                return row["id"]
            identifier = environment_id or uuid4()
            conn.execute(
                environments.insert().values(
                    id=identifier,
                    project_id=service["project_id"],
                    service_id=service_id,
                    **public,
                    binding=payload,
                )
            )
            return identifier


def main():
    from delta_platform.config import Settings
    from delta_platform.core.database import engine_for

    parser = argparse.ArgumentParser(description="운영자가 검증한 고객 VM 환경 연결")
    parser.add_argument("--service-id", required=True, type=UUID)
    parser.add_argument("--environment-id", type=UUID)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    data = EnvironmentInput.model_validate_json(args.input.read_text())
    engine = engine_for(Settings())
    try:
        identifier = EnvironmentStore(engine).bind(
            args.service_id, data, environment_id=args.environment_id
        )
        print(json.dumps({"environment_id": str(identifier)}))
    finally:
        engine.dispose()
