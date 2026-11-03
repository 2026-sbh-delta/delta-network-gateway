"""Operator-only SAST setup. Secrets stay in SSM or in memory, never tool output."""

import argparse
import base64
import hashlib
import ipaddress
import json
import shlex
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

import boto3

STAGED_PARAMETER = "/delta-platform/sast/connection"
RUNTIME_PARAMETER = "/delta-platform/runtime"


def settings(connection, expected_url):
    if connection["url"] != expected_url or not connection["token"]:
        raise ValueError("SAST connection identity mismatch")
    url = urlsplit(expected_url)
    if url.scheme != "https" or not ipaddress.IPv4Address(url.hostname).is_private:
        raise ValueError("Expected private SAST HTTPS URL")
    if len(connection["ca_base64"]) > 32768:
        raise ValueError("CA too large")
    ssl.create_default_context(
        cadata=base64.b64decode(connection["ca_base64"], validate=True).decode("ascii")
    )
    values = {
        "PLATFORM_SAST_URL": connection["url"],
        "PLATFORM_SAST_TOKEN": connection["token"],
        "PLATFORM_SAST_CA_BASE64": connection["ca_base64"],
    }
    if any(not isinstance(v, str) or "\n" in v or "\r" in v for v in values.values()):
        raise ValueError("Invalid SAST settings")
    return values


def merge(current, values):
    if set(values) != {
        "PLATFORM_SAST_URL",
        "PLATFORM_SAST_TOKEN",
        "PLATFORM_SAST_CA_BASE64",
    }:
        raise ValueError("Only SAST connection fields may change")
    return {**current, "app_env": {**current["app_env"], **values}}


def command(ssm, instance, commands):
    command_id = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands, "executionTimeout": ["600"]},
        Comment="Configure private platform SAST connection",
    )["Command"]["CommandId"]
    for _ in range(150):
        try:
            response = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        if response["Status"] == "Success":
            return response["StandardOutputContent"]
        if response["Status"] in ("Failed", "TimedOut", "Cancelled"):
            raise RuntimeError(
                f"SSM failed: {command_id}; inspect through operator access"
            )
        time.sleep(5)
    raise RuntimeError("SSM command timed out: " + command_id)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["install", "stage", "activate"])
    p.add_argument("--profile", required=True)
    p.add_argument("--outputs", type=Path, required=True)
    args = p.parse_args()
    outputs = {
        key: value["value"]
        for key, value in json.loads(args.outputs.read_text()).items()
    }
    session = boto3.Session(profile_name=args.profile, region_name=outputs["region"])
    if session.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
        raise ValueError("AWS account mismatch")
    ip = str(ipaddress.IPv4Address(urlsplit(outputs["sast_url"]).hostname))
    instance = outputs["tooling_instance_id"]
    ssm = session.client("ssm")
    remote = "/opt/delta/platform-sast-install.py"
    if args.operation == "install":
        body = Path(__file__).with_name("install.py").read_bytes()
        payload = base64.b64encode(body).decode()
        if len(payload) > 24000:
            raise ValueError("Installer exceeds SSM payload bound")
        script = f"from pathlib import Path; import base64; p=Path({remote!r}); p.write_bytes(base64.b64decode({payload!r})); p.chmod(0o700)"
        command(
            ssm,
            instance,
            [
                "set -eu",
                shlex.join(["python3", "-c", script]),
                shlex.join(["python3", remote, "--ip", ip]),
            ],
        )
        print(
            "SAST HTTPS installed. Installer SHA256:", hashlib.sha256(body).hexdigest()
        )
        return
    if args.operation == "stage":
        output = command(
            ssm,
            instance,
            [shlex.join(["python3", remote, "--ip", ip, "--export-connection"])],
        )
        connection = json.loads(output)
        settings(connection, outputs["sast_url"])
        ssm.put_parameter(
            Name=STAGED_PARAMETER,
            Type="SecureString",
            Value=json.dumps(connection),
            Overwrite=True,
        )
        print("SAST connection staged in SecureString; platform runtime unchanged")
        return
    # Run only once the CA-capable application is in main and ready for Release.
    connection = json.loads(
        ssm.get_parameter(Name=STAGED_PARAMETER, WithDecryption=True)["Parameter"][
            "Value"
        ]
    )
    values = settings(connection, outputs["sast_url"])
    original = ssm.get_parameter(Name=RUNTIME_PARAMETER, WithDecryption=True)[
        "Parameter"
    ]
    current = json.loads(original["Value"])
    merged = merge(current, values)
    if current == merged:
        print("Runtime unchanged")
        return
    metadata = ssm.describe_parameters(
        ParameterFilters=[
            {"Key": "Name", "Option": "Equals", "Values": [RUNTIME_PARAMETER]}
        ]
    )["Parameters"][0]
    if (
        ssm.get_parameter(Name=RUNTIME_PARAMETER)["Parameter"]["Version"]
        != original["Version"]
    ):
        raise ValueError("Concurrent runtime change; retry after reviewing")
    value = json.dumps(merged)
    if len(value.encode()) > 8192:
        raise ValueError("SSM Advanced parameter size limit exceeded")
    ssm.put_parameter(
        Name=RUNTIME_PARAMETER,
        Type="SecureString",
        KeyId=metadata["KeyId"],
        Tier="Advanced" if len(value.encode()) > 4096 else metadata["Tier"],
        Value=value,
        Overwrite=True,
    )
    print(
        "SAST fields merged; login/DB/Jenkins preserved. GitHub Release is the next step."
    )


if __name__ == "__main__":
    main()
