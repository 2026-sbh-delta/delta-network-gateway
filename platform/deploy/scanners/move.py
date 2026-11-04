"""Operator-only copy into dedicated platform scanner VMs. Original hosts are retained."""

import argparse
import base64
import io
import json
import shlex
import tarfile
import time
import uuid
from pathlib import Path

import boto3


def command(ssm, instance, lines):
    cid = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": lines, "executionTimeout": ["900"]},
        Comment="Copy scanner into dedicated platform subnet",
    )["Command"]["CommandId"]
    print("SSM", instance, cid, flush=True)
    for _ in range(180):
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(CommandId=cid, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] == "Success":
            return result["StandardOutputContent"]
        if result["Status"] in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError(
                f"Scanner transfer failed; inspect SSM command {cid} with operator access"
            )
    raise TimeoutError("Scanner transfer timed out")


def install_helper(ssm, instance):
    body = base64.b64encode(Path(__file__).with_name("transfer.py").read_bytes()).decode()
    script = (
        'import base64,os; p="/var/tmp/platform-scanner-transfer.py"; f=os.open(p,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600); os.write(f,base64.b64decode('
        + repr(body)
        + ")); os.close(f)"
    )
    command(ssm, instance, [shlex.join(["python3", "-c", script])])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--source-instance", required=True)
    parser.add_argument("--component", required=True, choices=["sast", "dast"])
    args = parser.parse_args()
    outputs = {key: value["value"] for key, value in json.loads(args.outputs.read_text()).items()}
    session = boto3.Session(profile_name=args.profile, region_name=outputs["region"])
    if session.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
        raise ValueError("Account mismatch")
    destination = outputs["scanners"][args.component]["instance_id"]
    ec2 = session.client("ec2")
    instances = {
        i["InstanceId"]: i
        for r in ec2.describe_instances(InstanceIds=[args.source_instance, destination])[
            "Reservations"
        ]
        for i in r["Instances"]
    }
    for instance, expected in (
        (args.source_instance, "delta-lab-tooling-host"),
        (destination, "delta-platform-" + args.component),
    ):
        tags = {t["Key"]: t["Value"] for t in instances[instance]["Tags"]}
        if tags.get("Name") != expected:
            raise ValueError("Unexpected source or destination VM")
    s3, ssm = session.client("s3"), session.client("ssm")
    bucket = "delta-platform-artifacts-" + outputs["account_id"]
    key = "releases/scanner-transfer/" + str(uuid.uuid4()) + "/" + args.component + ".tar.gz"
    parameter = "/delta-platform/scanners/transfers/" + str(uuid.uuid4())
    config = {
        "account_id": outputs["account_id"],
        "region": outputs["region"],
        "component": args.component,
        "upload_url": s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ServerSideEncryption": "AES256"},
            ExpiresIn=1800,
        ),
        "download_url": s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=1800
        ),
    }
    if args.component == "dast":
        sast = json.loads(
            ssm.get_parameter(Name="/delta-platform/scanners/sast/connection", WithDecryption=True)[
                "Parameter"
            ]["Value"]
        )
        config["sast_ca_base64"] = sast["ca_base64"]
    ssm.put_parameter(
        Name=parameter,
        Type="SecureString",
        Tier="Advanced",
        Value=json.dumps(config),
        Overwrite=False,
    )
    completed = False
    try:
        install_helper(ssm, args.source_instance)
        lines = [
            shlex.join(
                [
                    "python3",
                    "/var/tmp/platform-scanner-transfer.py",
                    "export",
                    "--component",
                    args.component,
                    "--parameter",
                    parameter,
                ]
            )
        ]
        snapshot = json.loads(command(ssm, args.source_instance, lines).strip())
        config["sha256"] = snapshot["sha256"]
        ssm.put_parameter(
            Name=parameter,
            Type="SecureString",
            Tier="Advanced",
            Value=json.dumps(config),
            Overwrite=True,
        )
        install_helper(ssm, destination)
        lines = [
            shlex.join(
                [
                    "python3",
                    "/var/tmp/platform-scanner-transfer.py",
                    "restore",
                    "--component",
                    args.component,
                    "--parameter",
                    parameter,
                ]
            )
        ]
        restored = json.loads(command(ssm, destination, lines).strip())
        # Capture the existing token only in operator memory, not command output.
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            manifest = json.load(tar.extractfile("manifest.json"))
        env = dict(v.split("=", 1) for v in manifest["Config"]["Env"])
        token = env["DELTA_SAST_TOKEN" if args.component == "sast" else "DAST_PLATFORM_TOKEN"]
        connection = {
            "url": outputs["scanners"][args.component]["api_url"],
            "token": token,
            "ca_base64": restored["ca_base64"],
        }
        ssm.put_parameter(
            Name="/delta-platform/scanners/" + args.component + "/connection",
            Type="SecureString",
            Value=json.dumps(connection),
            Overwrite=True,
        )
        completed = True
        print(
            json.dumps(
                {
                    "component": args.component,
                    "snapshot_records": snapshot["records"],
                    "snapshot_bytes": snapshot["bytes"],
                    "image": restored["image"],
                    "new_url": connection["url"],
                    "platform_activated": False,
                }
            )
        )
    finally:
        if completed:
            # Delete only versions of this newly created temporary object in our bucket.
            versions = s3.list_object_versions(Bucket=bucket, Prefix=key)
            objects = [
                {"Key": v["Key"], "VersionId": v["VersionId"]}
                for group in ("Versions", "DeleteMarkers")
                for v in versions.get(group, [])
                if v["Key"] == key
            ]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            ssm.delete_parameter(Name=parameter)
        else:
            print(
                "Transfer stopped before activation. Original service retained. Temporary encrypted snapshot retained in platform artifact bucket for recovery."
            )


if __name__ == "__main__":
    main()
