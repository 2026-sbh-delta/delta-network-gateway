"""Register an on-premise origin only after read-only Kubernetes verification."""

import base64
import ipaddress
import json
import os
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

import requests


def register(catalog, run_id, binding, *, read=None):
    cfg = json.loads(os.environ.get("DAST_ONPREM_TARGET", "{}"))
    namespace = cfg.get("namespace")
    url = urlsplit(cfg.get("api_url", ""))
    if (
        namespace != "delta-platform-sandbox"
        or url.scheme != "https"
        or url.path
        or url.query
        or url.fragment
        or url.username
    ):
        raise ValueError("Invalid on-premise Kubernetes origin")
    for host in (url.hostname, cfg.get("node_ip")):
        ip = ipaddress.ip_address(host)
        if ip.version != 4 or not ip.is_private or ip.is_link_local or ip.is_unspecified:
            raise ValueError("Private registered address required")
    if not 30000 <= cfg["node_port"] <= 32767:
        raise ValueError("Invalid on-premise service port")
    name = "delta-sbx-" + UUID(run_id).hex
    resource = f"namespaces/{namespace}/pods/{name}"
    origin = f"http://{cfg['node_ip']}:{cfg['node_port']}"
    if (
        binding.get("runtime_resource") != resource
        or binding["target_url"] != origin
        or binding["image"].rsplit("@", 1)[0] + "@" not in cfg["allowed_image_prefixes"]
    ):
        raise ValueError("On-premise target ownership mismatch")

    def fetch(path):
        token = os.environ.get("DAST_ONPREM_TOKEN", "")
        if not token:
            raise ValueError("On-premise scanner credentials missing")
        with tempfile.NamedTemporaryFile() as ca:
            ca.write(base64.b64decode(cfg["ca_base64"], validate=True))
            ca.flush()
            with requests.Session() as client:
                client.trust_env = False
                response = client.get(
                    cfg["api_url"] + "/api/v1/" + path,
                    headers={"Authorization": "Bearer " + token},
                    verify=ca.name,
                    timeout=10,
                    allow_redirects=False,
                    stream=True,
                )
                try:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise ValueError("Oversized Kubernetes response")
                    return json.loads(raw)
                finally:
                    response.close()

    read = read or fetch
    pod = read(resource)
    meta, spec = pod.get("metadata", {}), pod.get("spec", {})
    containers = spec.get("containers", [])
    status = pod.get("status", {}).get("containerStatuses", [])
    labels = meta.get("labels", {})
    if (
        meta.get("name") != name
        or meta.get("namespace") != namespace
        or meta.get("uid") != binding.get("runtime_revision")
        or meta.get("deletionTimestamp")
        or labels.get("delta-run-id") != run_id
        or labels.get("delta-commit") != binding["commit_sha"]
        or labels.get("app") != "delta-platform-sandbox"
        or spec.get("serviceAccountName") != "runtime"
        or spec.get("automountServiceAccountToken") is not False
        or spec.get("nodeSelector", {}).get("kubernetes.io/hostname") != cfg["node_name"]
        or spec.get("hostNetwork", False)
        or spec.get("hostPID", False)
        or len(containers) != 1
        or containers[0].get("image") != binding["image"]
        or pod.get("status", {}).get("phase") != "Running"
        or len(status) != 1
        or status[0].get("ready") is not True
        or not status[0].get("imageID", "").endswith("@" + binding["image_digest"])
    ):
        raise ValueError("On-premise run identity/readiness mismatch")
    svc = read(f"namespaces/{namespace}/services/sandbox")["spec"]
    if (
        svc.get("selector") != {"app": "delta-platform-sandbox"}
        or len(svc.get("ports", [])) != 1
        or svc["ports"][0].get("nodePort") != cfg["node_port"]
        or svc["ports"][0].get("targetPort") != "http"
    ):
        raise ValueError("On-premise service mismatch")
    base = catalog.targets[os.environ.get("DAST_PLATFORM_TARGET_ID", "platform-sandbox")]
    target_id = "onprem-" + UUID(run_id).hex
    catalog.targets[target_id] = base.model_copy(
        update={
            "id": target_id,
            "label": "플랫폼 온프레미스 샌드박스",
            "origin": origin,
        }
    )
    return target_id


def forget(catalog, run_id):
    catalog.targets.pop("onprem-" + UUID(run_id).hex, None)
