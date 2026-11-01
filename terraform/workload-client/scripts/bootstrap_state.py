#!/usr/bin/env python3
"""Bootstrap a private workload state bucket, after verifying the account.

Without --execute this only checks identity/bucket existence and prints the plan.
Never alters bucket policies, migrates state, or touches other buckets.
"""
import argparse
import json
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("account-id must contain 12 digits")

    def aws(*command, allow_missing=False):
        result = subprocess.run(
            ["aws", *command, "--profile", args.profile, "--region", args.region, "--output", "json"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            if allow_missing and "(404)" in result.stderr:
                return None
            raise SystemExit(result.stderr.strip())
        return json.loads(result.stdout) if result.stdout.strip() else {}

    account = aws("sts", "get-caller-identity")["Account"]
    if account != args.account_id:
        raise SystemExit("Authenticated account differs from account-id; no changes made")
    exists = aws("s3api", "head-bucket", "--bucket", args.bucket, "--expected-bucket-owner", account, allow_missing=True) is not None
    print(json.dumps({"account_id": account, "bucket": args.bucket, "exists": exists,
                      "actions": ["create if absent", "block all public access", "enforce bucket ownership", "enable versioning", "enable AES256 encryption"],
                      "execute": args.execute}))
    if not args.execute:
        return
    if not exists:
        command = ["s3api", "create-bucket", "--bucket", args.bucket]
        if args.region != "us-east-1":
            command += ["--create-bucket-configuration", "LocationConstraint=" + args.region]
        aws(*command)
    bucket_args = ["--bucket", args.bucket, "--expected-bucket-owner", account]
    aws("s3api", "put-public-access-block", *bucket_args, "--public-access-block-configuration",
        json.dumps({"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}))
    aws("s3api", "put-bucket-ownership-controls", *bucket_args, "--ownership-controls",
        json.dumps({"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}))
    aws("s3api", "put-bucket-versioning", *bucket_args, "--versioning-configuration", "Status=Enabled")
    aws("s3api", "put-bucket-encryption", *bucket_args, "--server-side-encryption-configuration",
        json.dumps({"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}))
    print(json.dumps({"public_access_block": aws("s3api", "get-public-access-block", *bucket_args),
                      "ownership": aws("s3api", "get-bucket-ownership-controls", *bucket_args),
                      "versioning": aws("s3api", "get-bucket-versioning", *bucket_args),
                      "encryption": aws("s3api", "get-bucket-encryption", *bucket_args)}))


if __name__ == "__main__":
    main()
