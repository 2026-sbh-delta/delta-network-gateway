"""Merge approved Jenkins settings into existing SSM runtime without printing secrets."""

import argparse
import base64
import json
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import boto3


def merged_runtime(current, values, account):
    expected = {
        "PLATFORM_BUILD_PROVIDER",
        "PLATFORM_BUILD_REPOSITORY",
        "PLATFORM_BUILD_REGION",
        "PLATFORM_JENKINS_URL",
        "PLATFORM_JENKINS_JOB",
        "PLATFORM_JENKINS_USER",
        "PLATFORM_JENKINS_TOKEN",
    }
    optional = {"PLATFORM_JENKINS_CA_BASE64"}
    if (
        not expected <= set(values)
        or set(values) - expected - optional
        or any(
            not isinstance(v, str) or not v or "\n" in v or "\r" in v
            for v in values.values()
        )
    ):
        raise ValueError("지정된 Jenkins 설정만 허용합니다.")
    if "PLATFORM_JENKINS_CA_BASE64" in values:
        if len(values["PLATFORM_JENKINS_CA_BASE64"]) > 32768:
            raise ValueError("CA 인증서가 너무 큽니다.")
        ssl.create_default_context(
            cadata=base64.b64decode(
                values["PLATFORM_JENKINS_CA_BASE64"], validate=True
            ).decode("ascii")
        )
    url = urlsplit(values["PLATFORM_JENKINS_URL"])
    if (
        values["PLATFORM_BUILD_PROVIDER"] != "jenkins"
        or url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or "%" in url.path
        or ".." in url.path.split("/")
    ):
        raise ValueError(
            "운영 Jenkins는 인증 정보를 포함하지 않은 HTTPS 주소가 필요합니다."
        )
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", values["PLATFORM_JENKINS_JOB"]
    ):
        raise ValueError("Job 이름을 확인하세요.")
    if not re.fullmatch(r"[a-z0-9-]+", values["PLATFORM_BUILD_REGION"]) or not values[
        "PLATFORM_BUILD_REPOSITORY"
    ].startswith(f"{account}.dkr.ecr.{values['PLATFORM_BUILD_REGION']}.amazonaws.com/"):
        raise ValueError("현재 계정의 ECR 저장소가 필요합니다.")
    return {**current, "app_env": {**current["app_env"], **values}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--parameter", default="/delta-platform/runtime")
    args = parser.parse_args()
    values = json.loads(args.config.read_text())
    session = boto3.Session(
        profile_name=args.profile, region_name=values["PLATFORM_BUILD_REGION"]
    )
    if session.client("sts").get_caller_identity()["Account"] != args.account_id:
        raise ValueError("계정 불일치")
    ssm = session.client("ssm")
    parameter = ssm.get_parameter(Name=args.parameter, WithDecryption=True)["Parameter"]
    if parameter["Type"] != "SecureString":
        raise ValueError("기존 SecureString만 수정합니다.")
    original = json.loads(parameter["Value"])
    merged = merged_runtime(original, values, args.account_id)
    if merged == original:
        print("설정 변경 없음")
        return
    metadata = ssm.describe_parameters(
        ParameterFilters=[
            {"Key": "Name", "Option": "Equals", "Values": [args.parameter]}
        ]
    )["Parameters"][0]
    if (
        ssm.get_parameter(Name=args.parameter)["Parameter"]["Version"]
        != parameter["Version"]
    ):
        raise ValueError("동시 설정 변경 감지. 다시 확인하세요.")
    result = ssm.put_parameter(
        Name=args.parameter,
        Value=json.dumps(merged),
        Type="SecureString",
        KeyId=metadata["KeyId"],
        Tier="Advanced"
        if len(json.dumps(merged).encode()) > 4096
        else metadata["Tier"],
        Overwrite=True,
    )
    print(
        f"Jenkins 설정 병합 완료. SSM 버전: {result['Version']}. 앱 배포는 별도로 수행합니다."
    )


if __name__ == "__main__":
    main()
