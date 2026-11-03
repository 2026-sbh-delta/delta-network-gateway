"""Publish a trusted tooling image and install it using the VM's own secret access."""

import argparse
import base64
import hashlib
import io
import json
import re
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

import boto3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--component", choices=["controller", "agent"], required=True)
    p.add_argument("--outputs", type=Path, required=True)
    p.add_argument("--image", required=True)
    args = p.parse_args()
    outputs = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    session = boto3.Session(region_name=outputs["region"])
    if session.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
        raise ValueError("AWS account mismatch")
    ecr, ssm = session.client("ecr"), session.client("ssm")
    repo = outputs["ecr_repository_url"]
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", repo.split("/")[0]],
        input=password,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True
    ).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Invalid local image ID")
    tag = "jenkins-" + args.component + "-" + image_id[7:27]
    repository = repo.split("/", 1)[1]
    try:
        ecr.describe_images(repositoryName=repository, imageIds=[{"imageTag": tag}])
    except ecr.exceptions.ImageNotFoundException:
        subprocess.run(["docker", "tag", args.image, repo + ":" + tag], check=True)
        subprocess.run(["docker", "push", repo + ":" + tag], check=True)
    digest = ecr.describe_images(
        repositoryName=repository, imageIds=[{"imageTag": tag}]
    )["imageDetails"][0]["imageDigest"]
    image = repo + "@" + digest
    archive = io.BytesIO()
    root = Path(__file__).parent
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for filename in ("install.py", "connect.py"):
            tar.add(root / filename, arcname=filename)
    body = archive.getvalue()
    checksum = hashlib.sha256(body).hexdigest()
    key = f"releases/jenkins/{checksum}/release.tar.gz"
    session.client("s3").put_object(
        Bucket=outputs["artifact_bucket"],
        Key=key,
        Body=body,
        ServerSideEncryption="AES256",
    )
    directory = "/opt/delta-jenkins/releases/" + checksum
    commands = [
        "set -eu",
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
        f"printf '%s  %s\\n' {shlex.quote(checksum)} {shlex.quote(directory + '/release.tar.gz')} | sha256sum -c -",
        shlex.join(["tar", "-xzf", directory + "/release.tar.gz", "-C", directory]),
        shlex.join(
            [
                "python3",
                directory + "/install.py",
                "--component",
                args.component,
                "--image",
                image,
                "--region",
                outputs["region"],
                "--account-id",
                outputs["account_id"],
            ]
        ),
        shlex.join(["ln", "-sfn", directory, "/opt/delta-jenkins/current"]),
    ]
    instance = outputs["jenkins_instances"][args.component]
    command = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands, "executionTimeout": ["900"]},
        Comment="Install trusted Jenkins component",
    )["Command"]["CommandId"]
    print("SSM install:", command, args.component, image, flush=True)
    for _ in range(180):
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        if result["Status"] in ("Failed", "Cancelled", "TimedOut", "Success"):
            if result["Status"] != "Success":
                raise SystemExit(
                    "Install failed; inspect the SSM command using operator access"
                )
            print(
                "Install completed; application readiness requires separate verification"
            )
            return
        time.sleep(5)
    raise SystemExit("SSM install timeout")


if __name__ == "__main__":
    main()
