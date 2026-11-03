"""Operator-only initial secrets and API connection; keep values out of CI and logs."""

import argparse
import base64
import importlib.util
import ipaddress
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import boto3


def certificate(ip, password):
    with tempfile.TemporaryDirectory(prefix="delta-jenkins-tls-") as directory:
        root = Path(directory)
        (root / "leaf.ext").write_text(
            f"subjectAltName=IP:{ip}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        commands = [
            [
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "90",
                "-keyout",
                "ca.key",
                "-out",
                "ca.crt",
                "-subj",
                "/CN=Delta Jenkins Internal CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            [
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                "server.key",
                "-out",
                "server.csr",
                "-subj",
                "/CN=" + ip,
            ],
            [
                "x509",
                "-req",
                "-in",
                "server.csr",
                "-CA",
                "ca.crt",
                "-CAkey",
                "ca.key",
                "-CAcreateserial",
                "-out",
                "server.crt",
                "-days",
                "30",
                "-extfile",
                "leaf.ext",
            ],
            [
                "pkcs12",
                "-export",
                "-inkey",
                "server.key",
                "-in",
                "server.crt",
                "-certfile",
                "ca.crt",
                "-out",
                "server.p12",
                "-passout",
                "env:DELTA_TLS_PASSWORD",
            ],
        ]
        for command in commands:
            subprocess.run(
                ["openssl", *command],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "DELTA_TLS_PASSWORD": password},
            )
        return base64.b64encode(
            (root / "ca.crt").read_bytes()
        ).decode(), base64.b64encode((root / "server.p12").read_bytes()).decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "connect"])
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    outputs = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    session = boto3.Session(profile_name=args.profile, region_name=outputs["region"])
    if session.client("sts").get_caller_identity()["Account"] != outputs["account_id"]:
        raise ValueError("AWS account mismatch")
    ssm = session.client("ssm")
    controller_name = "/delta-platform/jenkins/controller"
    if args.operation == "prepare":
        try:
            ssm.get_parameter(Name=controller_name)
            print("Existing controller secrets retained")
            return
        except ssm.exceptions.ParameterNotFound:
            pass
        address = urlsplit(outputs["jenkins_url"])
        ip = ipaddress.ip_address(address.hostname)
        if address.scheme != "https" or ip not in ipaddress.ip_network("10.80.1.0/24"):
            raise ValueError("Expected a private controller HTTPS address")
        password = secrets.token_urlsafe(24)
        ca, keystore = certificate(str(ip), password)
        value = {
            "url": outputs["jenkins_url"],
            "ca_base64": ca,
            "keystore_base64": keystore,
            "keystore_password": password,
            "admin_password": secrets.token_urlsafe(32),
            "platform_password": secrets.token_urlsafe(32),
        }
        if len(json.dumps(value).encode()) > 8192:
            raise ValueError("Controller runtime exceeds the Advanced parameter limit")
        ssm.put_parameter(
            Name=controller_name,
            Type="SecureString",
            Tier="Advanced",
            Value=json.dumps(value),
            Overwrite=False,
        )
        print("Controller credentials and 30-day IP certificate stored in SecureString")
        return
    instance = outputs["jenkins_instances"]["controller"]
    command = ssm.send_command(
        InstanceIds=[instance],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["python3 /opt/delta-jenkins/current/connect.py"]},
        Comment="Initialize private Jenkins API and agent connection",
    )["Command"]["CommandId"]
    for _ in range(24):
        try:
            response = ssm.get_command_invocation(
                CommandId=command, InstanceId=instance
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        if response["Status"] == "Success":
            connection = json.loads(response["StandardOutputContent"])
            break
        if response["Status"] in ("Failed", "TimedOut", "Cancelled"):
            raise SystemExit(
                "Controller connection failed; check readiness using operator access"
            )
        time.sleep(5)
    else:
        raise SystemExit("Controller connection timeout")
    if connection["url"] != outputs["jenkins_url"]:
        raise ValueError("Controller address mismatch")
    ssm.put_parameter(
        Name="/delta-platform/jenkins/agent",
        Type="SecureString",
        Overwrite=True,
        Value=json.dumps(
            {k: connection[k] for k in ("url", "agent_secret", "ca_base64")}
        ),
    )
    helper = Path(__file__).parents[1] / "configure-runtime.py"
    spec = importlib.util.spec_from_file_location("jenkins_runtime", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = {
        "PLATFORM_BUILD_PROVIDER": "jenkins",
        "PLATFORM_JENKINS_URL": connection["url"],
        "PLATFORM_JENKINS_JOB": "delta-platform-image",
        "PLATFORM_JENKINS_USER": "platform",
        "PLATFORM_JENKINS_TOKEN": connection["token"],
        "PLATFORM_JENKINS_CA_BASE64": connection["ca_base64"],
        "PLATFORM_BUILD_REPOSITORY": outputs["ecr_repository_url"] + "-builds",
        "PLATFORM_BUILD_REGION": outputs["region"],
    }
    name = outputs["runtime_parameter"]
    original = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    if original["Type"] != "SecureString":
        raise ValueError("Expected SecureString")
    merged = module.merged_runtime(
        json.loads(original["Value"]), values, outputs["account_id"]
    )
    if ssm.get_parameter(Name=name)["Parameter"]["Version"] != original["Version"]:
        raise ValueError("Concurrent runtime configuration update; retry")
    metadata = ssm.describe_parameters(
        ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]
    )["Parameters"][0]
    ssm.put_parameter(
        Name=name,
        Value=json.dumps(merged),
        Type="SecureString",
        KeyId=metadata["KeyId"],
        Tier="Advanced"
        if len(json.dumps(merged).encode()) > 4096
        else metadata["Tier"],
        Overwrite=True,
    )
    print(
        "Agent secret and platform connection saved; existing login/DB settings preserved"
    )


if __name__ == "__main__":
    main()
