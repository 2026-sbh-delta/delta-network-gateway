import copy
import json
from uuid import uuid4

import pytest
from dast_agent import onprem
from dast_agent.platform_api import Binding
from dast_agent.registry import load_catalog


def fixture(monkeypatch):
    cfg = {
        "api_url": "https://10.40.0.10:6443",
        "namespace": "delta-platform-sandbox",
        "node_ip": "10.40.0.10",
        "node_name": "onprem",
        "node_port": 32080,
        "allowed_image_prefixes": ["123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/builds@"],
    }
    monkeypatch.setenv("DAST_ONPREM_TARGET", json.dumps(cfg))
    monkeypatch.setenv("DAST_PLATFORM_TARGET_ID", "local-fixture")
    run_id = str(uuid4())
    name = "delta-sbx-" + run_id.replace("-", "")
    binding = {
        "execution_cloud": "onprem",
        "runtime_resource": f"namespaces/delta-platform-sandbox/pods/{name}",
        "runtime_revision": str(uuid4()),
        "target_url": "http://10.40.0.10:32080",
        "image_digest": "sha256:" + "a" * 64,
        "commit_sha": "b" * 40,
    }
    binding["image"] = cfg["allowed_image_prefixes"][0] + binding["image_digest"]
    pod = {
        "metadata": {
            "name": name,
            "namespace": cfg["namespace"],
            "uid": binding["runtime_revision"],
            "labels": {
                "app": "delta-platform-sandbox",
                "delta-run-id": run_id,
                "delta-commit": binding["commit_sha"],
            },
        },
        "spec": {
            "serviceAccountName": "runtime",
            "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.io/hostname": "onprem"},
            "containers": [{"image": binding["image"]}],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [{"ready": True, "imageID": binding["image"]}],
        },
    }
    service = {
        "spec": {
            "selector": {"app": "delta-platform-sandbox"},
            "ports": [{"nodePort": 32080, "targetPort": "http"}],
        }
    }

    def read(path):
        return pod if "/pods/" in path else service

    return run_id, binding, pod, service, read, load_catalog()


def test_owned_pod_is_registered_and_removed(monkeypatch):
    run, binding, pod, service, read, catalog = fixture(monkeypatch)
    target = onprem.register(catalog, run, binding, read=read)
    assert catalog.targets[target].origin == binding["target_url"]
    onprem.forget(catalog, run)
    assert target not in catalog.targets


@pytest.mark.parametrize("change", ["run", "uid", "image", "ready", "selector", "url"])
def test_foreign_replaced_unready_or_redirected_target_is_rejected(monkeypatch, change):
    run, binding, pod, service, read, catalog = fixture(monkeypatch)
    if change == "run":
        pod["metadata"]["labels"]["delta-run-id"] = str(uuid4())
    if change == "uid":
        pod["metadata"]["uid"] = str(uuid4())
    if change == "image":
        pod["status"]["containerStatuses"][0]["imageID"] = "other@sha256:" + "f" * 64
    if change == "ready":
        pod["status"]["containerStatuses"][0]["ready"] = False
    if change == "selector":
        service["spec"]["selector"] = {"app": "production"}
    if change == "url":
        binding["target_url"] = "http://169.254.169.254"
    before = copy.deepcopy(catalog.targets)
    with pytest.raises(ValueError):
        onprem.register(catalog, run, binding, read=read)
    assert catalog.targets == before


def test_onprem_binding_requires_pod_uid(monkeypatch):
    _, binding, *_ = fixture(monkeypatch)
    binding.update(build_id=str(uuid4()), sast_run_id=str(uuid4()), sast_evidence_sha256="c" * 64)
    assert Binding.model_validate(binding).execution_cloud == "onprem"
    del binding["runtime_revision"]
    with pytest.raises(ValueError):
        Binding.model_validate(binding)
