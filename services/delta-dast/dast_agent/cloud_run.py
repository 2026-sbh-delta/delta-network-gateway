"""Cloud Run targets are registered only after checking the owned remote resource."""

import json
import os
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

import google.auth
from google.auth.transport.requests import AuthorizedSession

_origins = {}


def settings():
    value = json.loads(os.environ.get("DAST_GCP_TARGET", "{}"))
    required = ("project", "region", "service_prefix", "repository", "runtime_service_account", "scanner_service_account")
    if not all(isinstance(value.get(k), str) and value[k] for k in required):
        raise ValueError("GCP DAST target is not configured")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value["project"]) or not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", value["region"]):
        raise ValueError("Invalid GCP project/region")
    return value


def session():
    config = os.environ.get("DAST_GCP_CREDENTIALS_JSON", "")
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials = (google.auth.load_credentials_from_dict(json.loads(config), scopes=scopes)[0]
                   if config else google.auth.default(scopes=scopes)[0])
    return AuthorizedSession(credentials)


def register(catalog, run_id, binding, *, client=None):
    cfg = settings()
    resource = (f"projects/{cfg['project']}/locations/{cfg['region']}/services/"
                f"{cfg['service_prefix']}-{UUID(run_id).hex}")
    if binding.get("runtime_resource") != resource:
        raise ValueError("GCP runtime resource does not belong to this run")
    client = client or session()
    response = client.get("https://run.googleapis.com/v2/" + resource, timeout=20)
    response.raise_for_status()
    service = response.json()
    template = service.get("template", {})
    image = cfg["repository"] + "@" + binding["image_digest"]
    if (service.get("name") != resource or service.get("uri") != binding["target_url"]
            or service.get("labels", {}).get("delta-run-id") != run_id
            or service.get("labels", {}).get("delta-commit") != binding["commit_sha"]
            or template.get("containers", [{}])[0].get("image") != image
            or template.get("serviceAccount") != cfg["runtime_service_account"]
            or service.get("invokerIamDisabled", False)
            or service.get("reconciling")
            or service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED"
            or not service.get("latestReadyRevision")
            or service["latestReadyRevision"] != service.get("latestCreatedRevision")):
        raise ValueError("GCP execution identity/readiness mismatch")
    origin = service["uri"]
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc.endswith(".run.app") or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Invalid Cloud Run origin")
    revision_name = service["latestReadyRevision"]
    if not revision_name.startswith(resource + "/revisions/" + resource.rsplit("/", 1)[-1] + "-"):
        raise ValueError("GCP revision boundary mismatch")
    revision = client.get("https://run.googleapis.com/v2/" + revision_name, timeout=20)
    revision.raise_for_status()
    if revision.json().get("containers", [{}])[0].get("image") != image:
        raise ValueError("GCP actual revision image mismatch")
    base = catalog.targets[os.environ.get("DAST_PLATFORM_TARGET_ID", "platform-sandbox")]
    target_id = "gcp-" + UUID(run_id).hex
    catalog.targets[target_id] = base.model_copy(update={"id": target_id, "label": "플랫폼 GCP 샌드박스", "origin": origin})
    _origins[origin] = {"client": client, "account": cfg["scanner_service_account"], "token": None, "until": 0}
    return target_id


def authorization(origin):
    """Only verified origins receive short-lived invocation tokens, server-side."""
    value = _origins.get(origin)
    if value is None:
        return {}
    if value["until"] < time.monotonic():
        response = value["client"].post(
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/" + value["account"] + ":generateIdToken",
            json={"audience": origin, "includeEmail": True}, timeout=20)
        response.raise_for_status()
        value["token"] = response.json()["token"]
        value["until"] = time.monotonic() + 2700
    return {"X-Serverless-Authorization": "Bearer " + value["token"]}


def forget(catalog, run_id, binding):
    if binding.get("execution_cloud") == "gcp":
        catalog.targets.pop("gcp-" + UUID(run_id).hex, None)
        _origins.pop(binding.get("target_url"), None)
