"""고정 digest와 SSM 설정으로 플랫폼을 배포한다. 비밀값은 출력하지 않는다."""

import argparse
import hashlib
import io
import json
import os
import secrets
import shlex
import tarfile
import time
from pathlib import Path

import boto3
from argon2 import PasswordHasher
from botocore.exceptions import ClientError

parser = argparse.ArgumentParser()
parser.add_argument("--outputs", type=Path, required=True)
parser.add_argument("--images", type=Path, required=True)
parser.add_argument("--runtime-dir", type=Path, required=True)
parser.add_argument(
    "--existing-runtime",
    action="store_true",
    help="CI: 기존 SSM 설정만 사용하며 비밀값을 읽거나 생성하지 않는다.",
)
args = parser.parse_args()
outputs = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
images = json.loads(args.images.read_text())
s = boto3.Session(region_name=outputs["region"])
if s.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
    raise SystemExit("AWS 계정 불일치")
ssm = s.client("ssm")
s3 = s.client("s3")
args.runtime_dir.mkdir(mode=0o700, exist_ok=True)
parameter = outputs["runtime_parameter"]
if args.existing_runtime:
    parameters = ssm.describe_parameters(
        ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [parameter]}]
    )["Parameters"]
    if not any(
        p["Name"] == parameter and p["Type"] == "SecureString" for p in parameters
    ):
        raise SystemExit(
            "SSM SecureString 초기 설정이 필요합니다. CI는 로그인·DB 비밀값을 만들지 않습니다."
        )
else:
    try:
        runtime = json.loads(
            ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ParameterNotFound":
            raise
        pending = args.runtime_dir / "runtime-config.json"
        if pending.exists():
            runtime = json.loads(pending.read_text())
        else:
            password = secrets.token_urlsafe(18)
            db_password = secrets.token_urlsafe(32)
            runtime = {
                "db_password": db_password,
                "app_env": {
                    "PLATFORM_DATABASE_URL": f"postgresql+psycopg://platform:{db_password}@db:5432/platform",
                    "PLATFORM_LOGIN_ID": "delta",
                    "PLATFORM_PASSWORD_HASH": PasswordHasher(
                        memory_cost=19456, time_cost=2, parallelism=1
                    ).hash(password),
                    "PLATFORM_PUBLIC_ORIGIN": outputs["platform_url"],
                    "PLATFORM_LOCAL_MODE": "false",
                },
            }
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(runtime, f)
            fd = os.open(
                args.runtime_dir / "platform-login.txt",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(fd, "w") as f:
                f.write(
                    f"URL: {outputs['platform_url']}\nID: delta\nPassword: {password}\n"
                )
    runtime["app_env"]["PLATFORM_PUBLIC_ORIGIN"] = outputs["platform_url"]
    ssm.put_parameter(
        Name=parameter, Value=json.dumps(runtime), Type="SecureString", Overwrite=True
    )
root = Path(__file__).resolve().parents[1]
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode="w:gz") as tar:
    for rel in (
        "compose.yaml",
        "scripts/install-release.sh",
        "scripts/write-runtime.py",
        "scripts/read-runtime.py",
        "scripts/backup.sh",
    ):
        tar.add(root / rel, arcname=rel)
body = archive.getvalue()
release = hashlib.sha256(body).hexdigest()[:20]
key = f"releases/{release}/release.tar.gz"
s3.put_object(
    Bucket=outputs["artifact_bucket"],
    Key=key,
    Body=body,
    ServerSideEncryption="AES256",
    ChecksumAlgorithm="SHA256",
)
directory = f"/opt/delta-platform/releases/{release}"
commands = [
    "set -eu",
    "test -f /opt/delta-platform/bootstrap-ready",
    shlex.join(["mkdir", "-p", directory]),
    shlex.join(
        [
            "aws",
            "s3",
            "cp",
            f"s3://{outputs['artifact_bucket']}/{key}",
            directory + "/release.tar.gz",
            "--region",
            outputs["region"],
            "--only-show-errors",
        ]
    ),
    shlex.join(["tar", "-xzf", directory + "/release.tar.gz", "-C", directory]),
    shlex.join(
        [
            "bash",
            directory + "/scripts/install-release.sh",
            images["platform"],
            images["postgres"],
            outputs["region"],
            parameter,
            outputs["artifact_bucket"],
        ]
    ),
]
command = ssm.send_command(
    InstanceIds=[outputs["instance_id"]],
    DocumentName="AWS-RunShellScript",
    Parameters={"commands": commands, "executionTimeout": ["1200"]},
    TimeoutSeconds=1200,
    Comment="Deploy Delta platform fixed image digests",
)["Command"]["CommandId"]
(args.runtime_dir / "deployment-command.json").write_text(
    json.dumps(
        {
            "command_id": command,
            "instance_id": outputs["instance_id"],
            "release": release,
        }
    )
    + "\n"
)
print("SSM deployment started:", command, flush=True)
for _ in range(240):
    try:
        r = ssm.get_command_invocation(
            CommandId=command, InstanceId=outputs["instance_id"]
        )
    except ssm.exceptions.InvocationDoesNotExist:
        time.sleep(5)
        continue
    if r["Status"] in ("Success", "Failed", "Cancelled", "TimedOut", "Cancelling"):
        (args.runtime_dir / "deployment-status.json").write_text(
            json.dumps({"status": r["Status"], "response_code": r["ResponseCode"]})
            + "\n"
        )
        # Commands contain no secret literals. Keep raw remote logs private for diagnosis.
        log = args.runtime_dir / "deployment.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(
                r.get("StandardOutputContent", "")
                + "\n"
                + r.get("StandardErrorContent", "")
            )
        print("Deployment:", r["Status"], flush=True)
        if r["Status"] != "Success":
            raise SystemExit(1)
        break
    time.sleep(5)
else:
    raise SystemExit("SSM 배포 대기 시간 초과")
