"""기존 SSM 설정에 샌드박스 공개 설정만 병합한다. 비밀값을 출력하지 않는다."""

import argparse
import json
import re
from pathlib import Path

import boto3


def merged_runtime(current, values, account):
    if set(values) != {"PLATFORM_SANDBOX_TARGET"}:
        raise ValueError("샌드박스 설정 한 값만 허용합니다.")
    target = json.loads(values["PLATFORM_SANDBOX_TARGET"])
    region = target["region"]
    if target["account_id"] != account or not re.fullmatch(r"[a-z0-9-]+", region):
        raise ValueError("샌드박스 계정·리전이 일치하지 않습니다.")
    if any(
        target[k] != "delta-platform-sandbox"
        for k in ("cluster", "service", "task_family")
    ):
        raise ValueError("전용 플랫폼 실행 공간만 허용합니다.")
    prefix = f"{account}.dkr.ecr.{region}.amazonaws.com/delta-platform-builds@"
    if target["allowed_image_prefixes"] != [prefix] or target["dynamic_scanner_access"]:
        raise ValueError("빌드 저장소와 비공개 실행 설정을 확인하세요.")
    if not target["target_group_arn"].startswith(
        f"arn:aws:elasticloadbalancing:{region}:{account}:targetgroup/delta-platform-sandbox/"
    ):
        raise ValueError("샌드박스 대상 그룹을 확인하세요.")
    if any(c in values["PLATFORM_SANDBOX_TARGET"] for c in "\r\n"):
        raise ValueError("설정은 한 줄 JSON이어야 합니다.")
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
        profile_name=args.profile,
        region_name=json.loads(values["PLATFORM_SANDBOX_TARGET"])["region"],
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
    print(f"샌드박스 설정 병합 완료. SSM 버전: {response['Version']}")


if __name__ == "__main__":
    main()
