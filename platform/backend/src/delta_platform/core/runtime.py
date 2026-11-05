"""Operator-owned resource identities, never arbitrary user-supplied Kubernetes paths."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["Deployment", "Service"]
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    uid: UUID


class OnpremRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cluster_id: UUID
    namespace: str = Field(pattern=r"^delta-[a-z0-9-]{1,50}$")
    resources: list[RuntimeResource] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_resources(self):
        if len({(r.kind, r.name) for r in self.resources}) != len(self.resources):
            raise ValueError("Duplicate runtime resource")
        if not any(r.kind == "Deployment" for r in self.resources):
            raise ValueError("Application Deployment required")
        return self
