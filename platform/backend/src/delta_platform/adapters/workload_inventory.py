"""Project registered runtime UIDs to a credential-free Diff inventory."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from delta_platform.adapters.onprem_removal import OnpremRemover
from delta_platform.adapters.sandbox import SandboxError
from delta_platform.core.errors import Problem
from delta_platform.core.runtime import OnpremRuntime
from delta_platform.core.store import digest

COVERAGE = "등록된 Deployment·Service만 관측했습니다. DB·PVC·네트워크 정책·IAM의 동등성은 별도 확인이 필요합니다."
SNAPSHOT = Path("/opt/delta-release/onprem-diff-snapshot.json")


def _identity(item):
    metadata = item["metadata"]
    return item["kind"], metadata["name"], metadata["uid"], metadata["namespace"]


def _expected(resource, namespace):
    return resource.kind, resource.name, str(resource.uid), namespace


def _project(item, resource, namespace):
    if _identity(item) != _expected(resource, namespace):
        raise ValueError("inventory identity mismatch")
    output = {
        "apiVersion": "apps/v1" if resource.kind == "Deployment" else "v1",
        "kind": resource.kind,
        "metadata": {"name": resource.name, "namespace": namespace, "uid": str(resource.uid)},
    }
    spec = item.get("spec") or {}
    if resource.kind == "Deployment":
        pod = spec.get("template", {}).get("spec", {})
        containers = []
        for c in pod.get("containers", []):
            container = {k: c[k] for k in ("name", "image") if k in c}
            resources = {
                group: {
                    k: c["resources"][group][k]
                    for k in ("cpu", "memory", "ephemeral-storage")
                    if k in c["resources"][group]
                }
                for group in ("requests", "limits")
                if group in c.get("resources", {})
            }
            if resources:
                container["resources"] = resources
            containers.append(container)
        output["spec"] = {
            "replicas": spec.get("replicas", 1),
            "template": {"spec": {"containers": containers}},
        }
    else:
        output["spec"] = {
            "type": spec.get("type", "ClusterIP"),
            "ports": [
                {k: port[k] for k in ("port", "targetPort", "protocol") if k in port}
                for port in spec.get("ports", [])
            ],
        }
    return output


def _manifest(items):
    manifest = json.dumps(
        {"apiVersion": "v1", "kind": "List", "items": items}, separators=(",", ":")
    )
    if len(manifest.encode()) > 50_000:
        raise ValueError("inventory too large")
    return manifest


def collect(settings, environment, *, reader_factory=OnpremRemover):
    try:
        runtime = OnpremRuntime.model_validate(environment["binding"]["runtime"])
        reader = reader_factory(settings)
        if str(runtime.cluster_id) != reader.target["cluster_id"]:
            raise ValueError()
        if runtime.namespace not in reader.target["namespaces"]:
            raise Problem(
                "diff_inventory_scope",
                "등록된 온프레미스 네임스페이스가 기존 연결의 조회 범위 밖입니다.",
                503,
            )
        items = []
        for resource in runtime.resources:
            item = reader.call("GET", runtime.namespace, resource)
            if item is None:
                raise ValueError()
            reader.owned(
                item, resource, runtime.namespace, environment["service_id"], environment["id"]
            )
            items.append(_project(item, resource, runtime.namespace))
        return _manifest(items)
    except (SandboxError, ValueError, KeyError, TypeError, OSError):
        raise Problem(
            "diff_inventory_unavailable",
            "등록된 온프레미스 구성의 조회·소유권 확인을 완료하지 못했습니다.",
            503,
        ) from None


def read_snapshot(environment, *, path=SNAPSHOT, now=None):
    """Accept only the deployed, fresh observation for this exact binding."""
    with path.open("rb") as stream:
        raw = stream.read(64_001)
    if len(raw) > 64_000:
        raise ValueError("snapshot too large")
    snapshot = json.loads(raw)
    if (
        snapshot["environment_id"] != str(environment["id"])
        or snapshot["service_id"] != str(environment["service_id"])
        or snapshot["binding_hash"] != digest(environment["binding"])
    ):
        raise ValueError("snapshot binding mismatch")
    captured = datetime.fromisoformat(snapshot["captured_at"])
    now = now or datetime.now(UTC)
    if captured.utcoffset() != timedelta(0) or not timedelta(0) <= now - captured <= timedelta(
        hours=24
    ):
        raise ValueError("snapshot timestamp outside 24-hour window")
    runtime = OnpremRuntime.model_validate(environment["binding"]["runtime"])
    indexed = {_identity(item): item for item in snapshot["items"]}
    expected = {_expected(resource, runtime.namespace) for resource in runtime.resources}
    if indexed.keys() != expected or len(indexed) != len(snapshot["items"]):
        raise ValueError("snapshot resources mismatch")
    manifest = _manifest(
        [
            _project(indexed[_expected(resource, runtime.namespace)], resource, runtime.namespace)
            for resource in runtime.resources
        ]
    )
    return manifest, f"온프레미스 읽기 전용 관측본: {captured.isoformat(timespec='seconds')} (UTC)."


def read_for_diff(settings, environment):
    try:
        return collect(settings, environment), None
    except Problem as unavailable:
        if unavailable.code != "diff_inventory_scope":
            raise
        try:
            return read_snapshot(environment)
        except (OSError, ValueError, KeyError, TypeError):
            raise unavailable from None
