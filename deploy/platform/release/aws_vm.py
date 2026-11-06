"""AWS 대상 계정 관리: delta-prod-deploy 위임 세션, SSM 온라인 대기, DB 준비용 SSM 포트 포워딩.

GCP의 IAP 포워딩(gcp_vm.forward)과 같은 역할이다. 컨트롤러 VPC에서 대상 RDS로 직접 경로를 요구하지 않는다.
위임 자격 증명은 이 프로세스 안의 boto3 세션과 포워딩 자식 프로세스 환경에만 둔다.
"""

import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import time
from contextlib import contextmanager

ROLE_RE = re.compile(r"arn:aws:iam::([0-9]{12}):role/[A-Za-z0-9+=,.@_/-]{1,128}")
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


def session(selected, *, factory=None):
    """컨트롤러 자신의 역할로 대상 계정 역할을 ExternalId와 함께 위임받는다."""
    import boto3

    factory = factory or boto3.Session
    match = ROLE_RE.fullmatch(str(selected.get("assume_role_arn", "")))
    if not match or match.group(1) != selected["identity"]["account_id"]:
        raise ValueError("AWS deploy role must belong to the selected account")
    if not isinstance(selected.get("external_id"), str) or not selected["external_id"]:
        raise ValueError("AWS deploy role external ID is not configured")
    region = selected["region"]
    credentials = factory(region_name=region).client("sts").assume_role(
        RoleArn=selected["assume_role_arn"],
        RoleSessionName="delta-release",
        ExternalId=selected["external_id"],
        DurationSeconds=3600,
    )["Credentials"]
    target = factory(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )
    if target.client("sts").get_caller_identity()["Account"] != selected["identity"]["account_id"]:
        raise ValueError("AWS identity mismatch")
    return target


def wait_online(aws, instance_id, *, sleep=time.sleep, attempts=60):
    ssm = aws.client("ssm")
    for _ in range(attempts):
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )["InstanceInformationList"]
        if info and info[0]["PingStatus"] == "Online":
            return ssm
        sleep(5)
    raise RuntimeError("SSM unavailable")


def run_command(ssm, instance_id, command, *, sleep=time.sleep):
    """AWS-RunShellScript 결과 stdout만 돌려준다. 실패 시 출력은 버린다."""
    reply = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command], "executionTimeout": ["900"]},
    )
    for _ in range(190):
        try:
            result = ssm.get_command_invocation(
                CommandId=reply["Command"]["CommandId"], InstanceId=instance_id
            )
        except ssm.exceptions.InvocationDoesNotExist:
            sleep(5)
            continue
        if result["Status"] == "Success":
            return result.get("StandardOutputContent", "")
        if result["Status"] not in ("Pending", "InProgress", "Delayed"):
            raise RuntimeError("VM deployment failed")
        sleep(5)
    raise RuntimeError("VM deployment timeout")


def local_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _valid_endpoint(host, port):
    if port != 5432:
        raise ValueError("SSM database endpoint must be PostgreSQL 5432")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not HOST_RE.fullmatch(str(host)):
            raise ValueError("SSM database endpoint host is invalid") from None
        return str(host)
    if address.version != 4 or not address.is_private or address.is_link_local:
        raise ValueError("SSM database endpoint must be a private address")
    return str(address)


@contextmanager
def forward(aws, instance_id, host, port=5432, *, popen=subprocess.Popen):
    """새 VM을 거쳐 DB 주소로 SSM 포트 포워딩한다. 로컬 루프백만 연다."""
    host = _valid_endpoint(host, port)
    local = local_port()
    frozen = aws.get_credentials().get_frozen_credentials()
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env.update(
        AWS_ACCESS_KEY_ID=frozen.access_key,
        AWS_SECRET_ACCESS_KEY=frozen.secret_key,
        AWS_SESSION_TOKEN=frozen.token,
        AWS_REGION=aws.region_name,
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
        AWS_CONFIG_FILE="/dev/null",
        AWS_PAGER="",
    )
    args = [
        "aws", "ssm", "start-session", "--region", aws.region_name, "--target", instance_id,
        "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
        "--parameters", json.dumps({"host": [host], "portNumber": [str(port)], "localPortNumber": [str(local)]}),
    ]
    process = popen(
        args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("AWS SSM management tunnel failed")
            try:
                with socket.create_connection(("127.0.0.1", local), timeout=1):
                    pass
                break
            except OSError:
                time.sleep(0.25)
        else:
            raise RuntimeError("AWS SSM management tunnel did not become ready")
        yield local
    finally:
        stop(process)
