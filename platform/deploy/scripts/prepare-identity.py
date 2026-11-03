"""AWS·GitHub를 조회해 OIDC 부트스트랩 입력을 만든다. 원격 설정은 변경하지 않는다."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def write_private(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--repository", default="2026-sbh-delta/delta-network-gateway")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{12}", args.account_id):
        raise SystemExit("AWS 계정은 12자리 숫자여야 합니다.")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", args.region):
        raise SystemExit("AWS 리전 형식이 올바르지 않습니다.")
    session = boto3.Session(region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.account_id:
        raise SystemExit("AWS 계정이 일치하지 않습니다.")
    s3, iam = session.client("s3"), session.client("iam")
    bucket = f"delta-platform-tf-{args.account_id}"

    def state(key, optional=False):
        try:
            return json.loads(
                s3.get_object(
                    Bucket=bucket, Key=key, ExpectedBucketOwner=args.account_id
                )["Body"].read()
            )
        except ClientError as exc:
            if optional and exc.response["Error"]["Code"] == "NoSuchKey":
                return {"resources": []}
            raise

    platform = state("platform/terraform.tfstate")
    identity = state("platform/identity.tfstate", optional=True)
    if (
        platform["outputs"]["account_id"]["value"] != args.account_id
        or platform["outputs"]["region"]["value"] != args.region
    ):
        raise SystemExit("기존 플랫폼 state의 계정·리전이 일치하지 않습니다.")
    resources = {
        f"{r['type']}.{r['name']}": r["instances"][0]["attributes"]
        for r in platform["resources"]
        if r.get("instances") and not r.get("module")
    }
    owned = any(
        r["type"] == "aws_iam_openid_connect_provider"
        and r["name"] == "github"
        and r.get("instances")
        for r in identity["resources"]
    )
    try:
        provider = iam.get_open_id_connect_provider(
            OpenIDConnectProviderArn=f"arn:aws:iam::{args.account_id}:oidc-provider/token.actions.githubusercontent.com"
        )
        if "sts.amazonaws.com" not in provider["ClientIDList"]:
            raise SystemExit(
                "기존 GitHub provider에 sts.amazonaws.com audience가 없습니다."
            )
        create_provider = owned
    except iam.exceptions.NoSuchEntityException:
        create_provider = True
    oidc = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/{args.repository}/actions/oidc/customization/sub"],
            text=True,
        )
    )
    if not oidc.get("use_default") or not oidc.get("use_immutable_subject"):
        raise SystemExit("이 저장소의 기본 immutable OIDC subject를 확인해야 합니다.")
    inputs = {
        "account_id": args.account_id,
        "region": args.region,
        "github_subject_prefix": oidc["sub_claim_prefix"],
        "create_oidc_provider": create_provider,
        "state_bucket": bucket,
        "cloudfront_prefix_list_id": resources[
            "aws_ec2_managed_prefix_list.cloudfront"
        ]["id"],
        "origin_request_policy_id": resources[
            "aws_cloudfront_origin_request_policy.platform"
        ]["id"],
        "platform_vpc_id": resources["aws_vpc.platform"]["id"],
        "platform_subnet_ids": [
            resources[f"aws_subnet.{name}"]["id"] for name in ["public", "private"]
        ],
    }
    root = Path(__file__).resolve().parents[1] / "identity"
    write_private(root / "terraform.tfvars.json", json.dumps(inputs, indent=2) + "\n")
    write_private(
        root / "backend.hcl",
        f'bucket = "{bucket}"\nkey = "platform/identity.tfstate"\nregion = "{args.region}"\nencrypt = true\nuse_lockfile = true\nallowed_account_ids = ["{args.account_id}"]\n',
    )
    print(
        json.dumps(
            {
                "account_id": args.account_id,
                "create_provider": create_provider,
                "subject": oidc["sub_claim_prefix"] + ":ref:refs/heads/main",
                "github_variables_changed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
