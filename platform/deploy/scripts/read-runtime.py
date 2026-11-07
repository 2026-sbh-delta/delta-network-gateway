"""Merge bounded component settings without growing the main SSM value."""

import argparse
import json
import subprocess
import sys

ONPREM_KEYS = {"PLATFORM_ONPREM_SANDBOX_TARGET", "PLATFORM_ONPREM_SANDBOX_TOKEN"}
DEPLOYMENT_KEYS = {
    "PLATFORM_DEPLOYMENT_JENKINS_JOB",
    "PLATFORM_DEPLOYMENT_JENKINS_USER",
    "PLATFORM_DEPLOYMENT_JENKINS_TOKEN",
    "PLATFORM_DEPLOYMENT_CONNECTIONS",
    "PLATFORM_APPLICATION_DATABASES",
}
DEPLOYMENT_OPTIONAL_KEYS = {
    "PLATFORM_APPLICATION_PIPELINE",
    "PLATFORM_ORCHESTRATOR_PASSWORD",
    "PLATFORM_ORCHESTRATOR_TARGETS",
    "PLATFORM_ORCHESTRATOR_URL",
}


def merge_onprem(runtime, extra):
    return merge_component(runtime, extra, ONPREM_KEYS)


def merge_component(runtime, extra, keys, optional_keys=frozenset()):
    if extra is None:
        return runtime
    if (
        not isinstance(extra, dict)
        or not keys <= set(extra)
        or not set(extra) <= keys | optional_keys
        or any(
            not isinstance(v, str) or not v or "\n" in v or "\r" in v
            for v in extra.values()
        )
        or any(
            k in runtime["app_env"] and runtime["app_env"][k] != v
            for k, v in extra.items()
        )
    ):
        raise ValueError("Invalid or conflicting component runtime parameter")
    return {**runtime, "app_env": {**runtime["app_env"], **extra}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parameter", required=True)
    p.add_argument("--region", required=True)
    a = p.parse_args()
    if not a.parameter.startswith("/") or not a.parameter.endswith("/runtime"):
        raise ValueError("Expected runtime parameter path")
    prefix = a.parameter.removesuffix("/runtime")
    names = {
        prefix + "/onprem/runtime": (ONPREM_KEYS, frozenset()),
        prefix + "/deployment/runtime": (DEPLOYMENT_KEYS, DEPLOYMENT_OPTIONAL_KEYS),
    }
    runtime = json.load(sys.stdin)
    # Read only exact sibling names. Never enumerate parameters or log values.
    result = subprocess.run(
        [
            "aws",
            "ssm",
            "get-parameters",
            "--names",
            *names,
            "--with-decryption",
            "--region",
            a.region,
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Failed to read optional component runtime")
    response = json.loads(result.stdout)
    params = response.get("Parameters", [])
    seen = list(response.get("InvalidParameters", []))
    for param in params:
        name = param.get("Name")
        if name not in names or name in seen or param.get("Type") != "SecureString":
            raise ValueError("Unexpected component parameter response")
        seen.append(name)
        runtime = merge_component(runtime, json.loads(param["Value"]), *names[name])
    if len(seen) != len(names) or set(seen) != set(names):
        raise ValueError("Incomplete component parameter response")
    json.dump(runtime, sys.stdout)


if __name__ == "__main__":
    main()
