"""Platform execution identity and durable retry binding for one API process."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def payload_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: UUID
    service_id: UUID
    workflow_id: UUID
    run_id: UUID
    environment_id: UUID
    source_environment_id: UUID | None = None
    source_provider: Literal["aws", "gcp", "onprem"] | None = None
    target_provider: Literal["aws", "gcp", "onprem"]
    repository_url: str
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutionConflict(ValueError):
    pass


class AssessmentLedger:
    """Caller holds the API's async lock; raw requests/secrets are not persisted."""

    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, identifier: UUID) -> Path:
        return self.directory / f"{identifier}.json"

    def _save(self, identifier: UUID, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.directory, delete=False) as stream:
            os.chmod(stream.name, 0o600)
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stream.name, self._path(identifier))

    def bind(self, context: ExecutionContext, request: dict) -> dict | None:
        # The full request hash includes context, so changing targets cannot reuse an ID.
        hashed = payload_hash(request)
        path = self._path(context.run_id)
        if path.exists():
            record = json.loads(path.read_text())
            if record["request_hash"] != hashed:
                raise ExecutionConflict("같은 실행 ID의 입력·대상이 변경됐습니다.")
            return record.get("result")
        self._save(context.run_id, {"request_hash": hashed})
        return None

    def complete(self, context: ExecutionContext, result: dict) -> None:
        record = json.loads(self._path(context.run_id).read_text())
        record["result"] = result
        self._save(context.run_id, record)
