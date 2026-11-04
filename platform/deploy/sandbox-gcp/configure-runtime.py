"""기존 SSM 설정에 샌드박스 공개 설정만 병합한다. 비밀값을 출력하지 않는다."""

import argparse
import json
from pathlib import Path

import boto3


def merged_runtime(current, values, account):
    if set(values) != {"PLATFORM_GCP_SANDBOX_TARGET", "PLATFORM_GCP_CREDENTIALS_JSON"}:
        raise ValueError("Expected GCP target and federation configuration only")
    target = json.loads(values["PLATFORM_GCP_SANDBOX_TARGET"])
    credentials = json.loads(values["PLATFORM_GCP_CREDENTIALS_JSON"])
    if target["allowed_image_prefixes"] != [
        f"{account}.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@"
    ]:
        raise ValueError("Image repository/account mismatch")
    if credentials.get("type") != "external_account" or "private_key" in credentials:
        raise ValueError("Only keyless federation configuration is accepted")
    if any(c in value for value in values.values() for c in "\r\n"):
        raise ValueError("Configuration must be single-line JSON")
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
        region_name="ap-northeast-2",
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
