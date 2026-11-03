"""기존 SSM 설정에 빌드용 공개 설정만 병합한다. 비밀값을 출력하지 않는다."""

import argparse
import json
import re
from pathlib import Path

import boto3


def merged_runtime(current, values, account):
    expected = {
        "PLATFORM_BUILD_PROJECT",
        "PLATFORM_BUILD_REPOSITORY",
        "PLATFORM_BUILD_REGION",
    }
    if set(values) != expected or not all(isinstance(v, str) for v in values.values()):
        raise ValueError("빌드 설정 세 값만 허용합니다.")
    if not re.fullmatch(r"[a-z0-9-]+", values["PLATFORM_BUILD_REGION"]):
        raise ValueError("리전 형식 오류")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{1,149}", values["PLATFORM_BUILD_PROJECT"]
    ):
        raise ValueError("프로젝트 이름 오류")
    prefix = f"{account}.dkr.ecr.{values['PLATFORM_BUILD_REGION']}.amazonaws.com/"
    if not values["PLATFORM_BUILD_REPOSITORY"].startswith(prefix):
        raise ValueError("현재 계정·리전의 ECR만 사용합니다.")
    return {**current, "app_env": {**current["app_env"], **values}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--parameter", default="/delta-platform/runtime")
    args = parser.parse_args()
    values = json.loads(args.outputs.read_text())["runtime_env"]["value"]
    session = boto3.Session(
        profile_name=args.profile, region_name=values["PLATFORM_BUILD_REGION"]
    )
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.account_id:
        raise ValueError("AWS 계정이 일치하지 않습니다.")
    ssm = session.client("ssm")
    current = ssm.get_parameter(Name=args.parameter, WithDecryption=True)["Parameter"]
    if current["Type"] != "SecureString":
        raise ValueError("기존 SecureString 설정만 갱신합니다.")
    body = json.loads(current["Value"])
    merged = merged_runtime(body, values, account)
    if merged == body:
        print("설정 변경 없음")
        return
    metadata = ssm.describe_parameters(
        ParameterFilters=[
            {"Key": "Name", "Option": "Equals", "Values": [args.parameter]}
        ]
    )["Parameters"][0]
    # Refuse a detected concurrent update; operators must serialize this bootstrap operation.
    if (
        ssm.get_parameter(Name=args.parameter)["Parameter"]["Version"]
        != current["Version"]
    ):
        raise RuntimeError("동시에 설정이 변경됐습니다. 다시 실행하세요.")
    response = ssm.put_parameter(
        Name=args.parameter,
        Value=json.dumps(merged),
        Type="SecureString",
        KeyId=metadata["KeyId"],
        Overwrite=True,
    )
    print(f"빌드 설정 세 값 병합 완료. SSM 버전: {response['Version']}")


if __name__ == "__main__":
    main()
