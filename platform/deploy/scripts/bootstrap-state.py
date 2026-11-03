"""플랫폼 전용 Terraform 상태 버킷을 준비한다. AWS 인증은 실행 환경에서 받는다."""

import argparse
import json
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

parser = argparse.ArgumentParser()
parser.add_argument("--account-id", required=True)
parser.add_argument("--region", default="ap-northeast-2")
args = parser.parse_args()
session = boto3.Session(region_name=args.region)
actual = session.client("sts").get_caller_identity()["Account"]
if actual != args.account_id:
    raise SystemExit("AWS 계정이 일치하지 않습니다.")
bucket = f"delta-platform-tf-{actual}"
s3 = session.client("s3")
try:
    s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=actual)
except ClientError as exc:
    if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
        raise
    s3.create_bucket(
        Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": args.region}
    )
s3.put_public_access_block(
    Bucket=bucket,
    PublicAccessBlockConfiguration={
        "BlockPublicAcls": True,
        "IgnorePublicAcls": True,
        "BlockPublicPolicy": True,
        "RestrictPublicBuckets": True,
    },
)
s3.put_bucket_ownership_controls(
    Bucket=bucket,
    OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
)
s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
s3.put_bucket_encryption(
    Bucket=bucket,
    ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
    },
)
s3.put_bucket_policy(
    Bucket=bucket,
    Policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                }
            ],
        }
    ),
)
s3.put_bucket_tagging(
    Bucket=bucket,
    Tagging={
        "TagSet": [
            {"Key": "Project", "Value": "delta-platform"},
            {"Key": "Purpose", "Value": "terraform-state"},
        ]
    },
)
path = Path(__file__).resolve().parents[1] / "terraform/backend.hcl"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(
        f'bucket = "{bucket}"\nkey = "platform/terraform.tfstate"\nregion = "{args.region}"\nencrypt = true\nuse_lockfile = true\nallowed_account_ids = ["{actual}"]\n'
    )
print("Private versioned state bucket ready:", bucket)
