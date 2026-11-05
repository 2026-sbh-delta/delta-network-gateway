"""Merge the dedicated onprem parameter without growing the main SSM value."""

import argparse
import json
import subprocess
import sys

ONPREM_KEYS = {"PLATFORM_ONPREM_SANDBOX_TARGET", "PLATFORM_ONPREM_SANDBOX_TOKEN"}


def merge_onprem(runtime, extra):
    if extra is None:
        return runtime
    if (
        not isinstance(extra, dict)
        or set(extra) != ONPREM_KEYS
        or any(
            not isinstance(v, str) or not v or "\n" in v or "\r" in v
            for v in extra.values()
        )
        or any(
            k in runtime["app_env"] and runtime["app_env"][k] != v
            for k, v in extra.items()
        )
    ):
        raise ValueError("Invalid or conflicting onprem runtime parameter")
    return {**runtime, "app_env": {**runtime["app_env"], **extra}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parameter", required=True)
    p.add_argument("--region", required=True)
    a = p.parse_args()
    if not a.parameter.startswith("/") or not a.parameter.endswith("/runtime"):
        raise ValueError("Expected runtime parameter path")
    name = a.parameter.removesuffix("/runtime") + "/onprem/runtime"
    runtime = json.load(sys.stdin)
    # The VM's existing SSM agent policy supports GetParameters. Request one exact
    # sibling name and never list other parameters or print their values in logs.
    result = subprocess.run(
        [
            "aws",
            "ssm",
            "get-parameters",
            "--names",
            name,
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
        raise RuntimeError("Failed to read optional onprem runtime")
    response = json.loads(result.stdout)
    params = response.get("Parameters", [])
    if not params and response.get("InvalidParameters") == [name]:
        extra = None
    elif (
        len(params) == 1
        and params[0].get("Name") == name
        and params[0].get("Type") == "SecureString"
    ):
        extra = json.loads(params[0]["Value"])
    else:
        raise ValueError("Unexpected onprem parameter response")
    json.dump(merge_onprem(runtime, extra), sys.stdout)


if __name__ == "__main__":
    main()
