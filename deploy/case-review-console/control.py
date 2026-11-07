"""Fixed account/region SSM driver. Remote scripts return redacted JSON only."""

import argparse
import base64
import gzip
import json
import time
from pathlib import Path

import boto3

HOSTS = {"ai": "i-0fd2e6679bb863d6a", "orchestrator": "i-0a59e05ff19f10bf6"}
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT.parent / "notes/case-console-aws-20261106"


def run(host, source, label):
    session = boto3.Session(profile_name="delta-finals", region_name="ap-northeast-2")
    assert session.client("sts").get_caller_identity()["Account"] == "616723161368"
    client = session.client("ssm")
    payload = base64.b64encode(gzip.compress(source.encode())).decode()
    command = (
        "python3 - <<'PY'\nimport base64,gzip\nexec(compile(gzip.decompress(base64.b64decode("
        + repr(payload)
        + ")), '<case-console-release>', 'exec'))\nPY"
    )
    response = client.send_command(
        InstanceIds=[HOSTS[host]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command], "executionTimeout": ["1200"]},
        TimeoutSeconds=60,
        Comment="User-approved case review console " + label,
    )
    identifier = response["Command"]["CommandId"]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / (label + "-command.json")).write_text(
        json.dumps({"command_id": identifier, "host": host}) + "\n"
    )
    print(
        json.dumps({"command_id": identifier, "host": host, "label": label}), flush=True
    )
    deadline = time.monotonic() + 1250
    while time.monotonic() < deadline:
        try:
            result = client.get_command_invocation(
                CommandId=identifier, InstanceId=HOSTS[host]
            )
        except client.exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        if result["Status"] not in ("Pending", "InProgress", "Delayed"):
            if result["Status"] != "Success":
                # Remote scripts sanitize failures; never print raw environment or credentials.
                raise RuntimeError(
                    "Remote "
                    + label
                    + " failed: "
                    + result["StandardErrorContent"][-1500:]
                )
            value = json.loads(
                gzip.decompress(
                    base64.b64decode(result["StandardOutputContent"].strip())
                )
            )
            (EVIDENCE / (label + ".json")).write_text(
                json.dumps(value, indent=2) + "\n"
            )
            print(
                json.dumps({"label": label, "saved": True, "fields": list(value)}),
                flush=True,
            )
            return value
        time.sleep(3)
    raise TimeoutError(identifier)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=HOSTS)
    parser.add_argument("script", type=Path)
    parser.add_argument("label")
    args = parser.parse_args()
    run(args.host, args.script.read_text(), args.label)
