"""Allowlisted infrastructure facts; credentials and arbitrary manifest fields are never part of this contract."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Origin = Literal["terraform", "operator", "kubernetes"]


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceLoadBalancer(Facts):
    name: str = Field(min_length=1, max_length=253)
    kind: Literal[
        "application", "network", "classic", "service", "ingress", "unknown"
    ] = "unknown"
    protocols: tuple[Literal["HTTP", "HTTPS", "TCP", "UDP", "TLS", "TCP_UDP"], ...] = ()
    ports: tuple[Annotated[int, Field(ge=1, le=65535)], ...] = Field(
        default=(), max_length=100
    )
    internal: bool | None = None
    tls: bool | None = None
    origin: Origin = "operator"


class SourceDatabase(Facts):
    name: str = Field(min_length=1, max_length=253)
    engine: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, max_length=100)
    storage_gib: float | None = Field(default=None, gt=0)
    high_availability: bool | None = None
    origin: Origin = "operator"


class Workload(Facts):
    name: str
    kind: Literal["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"]
    namespace: str = "default"
    images: tuple[str, ...] = ()
    container_count: int = Field(default=1, ge=1, le=100)
    replicas: int | None = Field(default=None, ge=0)
    cpu_requests: tuple[str, ...] = ()
    memory_requests: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()


class Volume(Facts):
    name: str
    namespace: str = "default"
    size_gib: float | None = Field(default=None, gt=0)
    storage_class: str | None = None
    access_modes: tuple[str, ...] = ()


class KubernetesInventory(Facts):
    workloads: tuple[Workload, ...] = Field(default=(), max_length=200)
    volumes: tuple[Volume, ...] = Field(default=(), max_length=200)
    load_balancers: tuple[SourceLoadBalancer, ...] = Field(default=(), max_length=200)
    databases: tuple[SourceDatabase, ...] = Field(default=(), max_length=200)
    network_policies: tuple[str, ...] = Field(default=(), max_length=200)
    unsupported_kinds: tuple[str, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def require_facts(self):
        if not any(
            (
                self.workloads,
                self.volumes,
                self.load_balancers,
                self.databases,
                self.network_policies,
                self.unsupported_kinds,
            )
        ):
            raise ValueError("Kubernetes 매핑 사실이 하나 이상 필요하다")
        return self
