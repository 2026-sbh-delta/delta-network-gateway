"""Read bounded Kubernetes YAML/JSON into allowlisted facts. Never execute manifests."""

import hashlib
import re

import yaml
from delta_ai.mapping_contract import KubernetesInventory
from pydantic import ValidationError
from yaml.tokens import AliasToken, AnchorToken

MAX_YAML_BYTES = 60_000
MAX_OBJECTS = 200
WORKLOADS = {"Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
IGNORED = {"Secret", "ConfigMap", "Namespace", "ServiceAccount"}


class ManifestRejected(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        pairs = self.construct_pairs(node, deep=deep)
        result = {}
        for key, value in pairs:
            if not isinstance(key, str) or key in result:
                raise ManifestRejected("YAML에 중복 키 또는 문자열이 아닌 키가 있다")
            result[key] = value
        return result


def _gib(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?", str(value or ""))
    if not match:
        return None
    scale = {
        "Ki": 2**10,
        "Mi": 2**20,
        "Gi": 2**30,
        "Ti": 2**40,
        "K": 10**3,
        "M": 10**6,
        "G": 10**9,
        "T": 10**12,
        None: 1,
    }
    return float(match[1]) * scale[match[2]] / 2**30


def _volume(item, namespace, fallback=None):
    spec = item.get("spec", {})
    return {
        "name": item.get("metadata", {}).get("name") or fallback,
        "namespace": namespace,
        "size_gib": _gib(spec.get("resources", {}).get("requests", {}).get("storage")),
        "storage_class": spec.get("storageClassName"),
        "access_modes": spec.get("accessModes", []),
    }


def _objects(text):
    if len(text.encode()) > MAX_YAML_BYTES:
        raise ManifestRejected("YAML 크기 한도를 넘었다")
    try:
        depth = 0
        for token in yaml.scan(text):
            if isinstance(token, (AnchorToken, AliasToken)):
                raise ManifestRejected("YAML 앵커와 별칭은 받지 않는다")
            # Reject excessive nesting before SafeLoader recursively constructs objects.
            if isinstance(
                token,
                (
                    yaml.tokens.BlockMappingStartToken,
                    yaml.tokens.BlockSequenceStartToken,
                    yaml.tokens.FlowMappingStartToken,
                    yaml.tokens.FlowSequenceStartToken,
                ),
            ):
                depth += 1
                if depth > 40:
                    raise ManifestRejected("YAML 중첩 한도를 넘었다")
            elif isinstance(
                token,
                (
                    yaml.tokens.BlockEndToken,
                    yaml.tokens.FlowMappingEndToken,
                    yaml.tokens.FlowSequenceEndToken,
                ),
            ):
                depth -= 1
        items = []
        for item in yaml.load_all(text, Loader=StrictLoader):
            if item is None:
                continue
            if not isinstance(item, dict):
                raise ManifestRejected("Kubernetes 객체가 필요하다")
            items.extend(
                item.get("items", []) if item.get("kind") == "List" else [item]
            )
            if len(items) > MAX_OBJECTS:
                raise ManifestRejected("Kubernetes 객체 수 한도를 넘었다")
        if not items:
            raise ManifestRejected("YAML에 Kubernetes 객체가 없다")
        return items
    except (yaml.YAMLError, TypeError, RecursionError) as exc:
        raise ManifestRejected("Kubernetes YAML 형식이 잘못됐다") from exc


def extract_kubernetes(text: str) -> tuple[KubernetesInventory, str]:
    facts = {
        k: []
        for k in (
            "workloads",
            "volumes",
            "load_balancers",
            "databases",
            "network_policies",
            "unsupported_kinds",
        )
    }
    try:
        seen = set()
        items = _objects(text)
        # PVCs may follow the workloads that refer to them in a YAML stream.
        items.sort(
            key=lambda x: (
                0
                if isinstance(x, dict) and x.get("kind") == "PersistentVolumeClaim"
                else 1
            )
        )
        for item in items:
            kind = item["kind"]
            meta = item.get("metadata", {})
            name, namespace = meta.get("name"), meta.get("namespace", "default")
            if not name or not isinstance(name, str) or not isinstance(namespace, str):
                raise ManifestRejected(
                    "Kubernetes metadata.name과 namespace를 확인한다"
                )
            identity = (kind, namespace, name)
            if identity in seen:
                raise ManifestRejected("Kubernetes 객체가 중복됐다")
            seen.add(identity)
            spec = item.get("spec", {})
            if kind in IGNORED:
                continue
            if kind in WORKLOADS:
                if kind == "CronJob":
                    spec = spec.get("jobTemplate", {}).get("spec", {})
                pod = (
                    spec if kind == "Pod" else spec.get("template", {}).get("spec", {})
                )
                containers = pod.get("containers", [])
                volumes = [
                    _volume(v, namespace) for v in spec.get("volumeClaimTemplates", [])
                ]
                facts["volumes"].extend(volumes)
                claims = [
                    v["persistentVolumeClaim"]["claimName"]
                    for v in pod.get("volumes", [])
                    if "persistentVolumeClaim" in v
                ]
                facts["workloads"].append(
                    {
                        "name": name,
                        "namespace": namespace,
                        "kind": kind,
                        "container_count": len(containers),
                        "images": [c["image"] for c in containers if c.get("image")],
                        "replicas": spec.get("replicas", 1)
                        if kind in ("Deployment", "StatefulSet")
                        else None,
                        "cpu_requests": [
                            str(c["resources"]["requests"]["cpu"])
                            for c in containers
                            if c.get("resources", {}).get("requests", {}).get("cpu")
                            is not None
                        ],
                        "memory_requests": [
                            str(c["resources"]["requests"]["memory"])
                            for c in containers
                            if c.get("resources", {}).get("requests", {}).get("memory")
                            is not None
                        ],
                        "volumes": [*claims, *(v["name"] for v in volumes)],
                    }
                )
                for c in containers:
                    image = str(c.get("image", "")).split("/")[-1]
                    if re.match(r"^postgres(?:[:@]|$)", image):
                        tag = (
                            image.split("@", 1)[0].split(":", 1)[1]
                            if ":" in image.split("@", 1)[0]
                            else ""
                        )
                        version = re.match(r"^(\d+(?:\.\d+)*)(?:-|$)", tag)
                        mounted = {v.get("name") for v in c.get("volumeMounts", [])}
                        claim_names = {
                            v["persistentVolumeClaim"]["claimName"]
                            for v in pod.get("volumes", [])
                            if v.get("name") in mounted and "persistentVolumeClaim" in v
                        }
                        attached = {
                            v["name"]: v for v in volumes if v["name"] in mounted
                        }
                        attached.update(
                            {
                                v["name"]: v
                                for v in facts["volumes"]
                                if v["name"] in claim_names
                                and v["namespace"] == namespace
                            }
                        )
                        attached = list(attached.values())
                        facts["databases"].append(
                            {
                                "name": name,
                                "engine": "postgresql",
                                "version": version[1] if version else None,
                                "storage_gib": sum(v["size_gib"] for v in attached)
                                if attached and all(v["size_gib"] for v in attached)
                                else None,
                                "high_availability": False
                                if kind == "StatefulSet"
                                and spec.get("replicas", 1) == 1
                                else None,
                                "origin": "kubernetes",
                            }
                        )
            elif kind == "PersistentVolumeClaim":
                facts["volumes"].append(_volume(item, namespace))
            elif kind == "Service":
                ports = spec.get("ports", [])
                facts["load_balancers"].append(
                    {
                        "name": f"{namespace}/{name}",
                        "kind": "service",
                        "protocols": sorted({p.get("protocol", "TCP") for p in ports}),
                        "ports": [p["port"] for p in ports],
                        "internal": True
                        if spec.get("type", "ClusterIP") == "ClusterIP"
                        else None,
                        "tls": None,
                        "origin": "kubernetes",
                    }
                )
            elif kind == "Ingress":
                tls = bool(spec.get("tls"))
                facts["load_balancers"].append(
                    {
                        "name": f"{namespace}/{name}",
                        "kind": "ingress",
                        "protocols": ["HTTP", "HTTPS"] if tls else ["HTTP"],
                        "ports": [80, 443] if tls else [80],
                        "internal": None,
                        "tls": tls,
                        "origin": "kubernetes",
                    }
                )
            elif kind == "NetworkPolicy":
                facts["network_policies"].append(f"{namespace}/{name}")
            else:
                facts["unsupported_kinds"].append(str(kind))
        inventory = KubernetesInventory.model_validate(facts)
        if not any(facts.values()):
            raise ManifestRejected("매핑 가능한 Kubernetes 객체가 없다")
        return inventory, hashlib.sha256(text.encode()).hexdigest()
    except (KeyError, TypeError, AttributeError, ValidationError, ValueError) as exc:
        if isinstance(exc, ManifestRejected):
            raise
        raise ManifestRejected("Kubernetes 매핑 필드가 잘못됐다") from exc
