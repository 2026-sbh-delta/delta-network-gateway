"""Run on the platform host with its existing AWS instance role, never with SA keys."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import requests
from delta_platform.adapters.gcp_sandbox import CloudRun, GcpTarget
from delta_platform.adapters.registry_copy import RegistryCopy

parser = argparse.ArgumentParser()
parser.add_argument("--outputs", type=Path, required=True)
parser.add_argument("--build", type=Path, required=True)
parser.add_argument("--record", type=Path, required=True)
args = parser.parse_args()
values = json.loads(args.outputs.read_text())["runtime_env"]["value"]
source = json.loads(args.build.read_text())
target = GcpTarget.model_validate_json(values["PLATFORM_GCP_SANDBOX_TARGET"])
provider = CloudRun(target, values["PLATFORM_GCP_CREDENTIALS_JSON"])
run_id = str(uuid4())
resource = target.resource(run_id)
image = target.repository + "@" + source["result"]["image_digest"]
commit = source["resolved_commit_sha"]
args.record.write_text(
    json.dumps(
        {"resource": resource, "run_id": run_id, "image": image, "commit": commit}
    )
)
end = time.monotonic() + 900


def guard():
    if time.monotonic() > end:
        raise TimeoutError("Validation deadline exceeded")


try:
    copied = RegistryCopy(target, provider.session).copy(
        source["result"]["image"], guard
    )
    assert copied == image
    print("image_digest_verified", flush=True)
    provider.create(
        resource,
        run_id,
        commit,
        image,
        source["input_snapshot"]["container_port"],
        guard,
    )
    provider.owned(provider.get(resource), run_id, commit, image)
    provider.authorize(resource)
    checked = provider.verify(
        resource,
        run_id,
        commit,
        image,
        source["input_snapshot"]["healthcheck_path"],
        guard,
    )
    response = requests.get(checked["url"], timeout=30, allow_redirects=False)
    assert response.status_code in (401, 403)
    print(
        json.dumps(
            {
                "authenticated_health": "passed",
                "anonymous_status": response.status_code,
                "resource": resource,
                "revision": checked["revision"],
            }
        ),
        flush=True,
    )
finally:
    current = provider.get(resource)
    if current is not None:
        provider.owned(current, run_id, commit, image)
        provider.delete(resource, current, lambda: None)
    print("cleanup_confirmed", flush=True)
