"""검증한 플랫폼·PostgreSQL 이미지를 ECR에 게시하고 digest를 기록한다."""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

import boto3

parser = argparse.ArgumentParser()
parser.add_argument("--account-id", required=True)
parser.add_argument("--region", default="ap-northeast-2")
parser.add_argument("--repository", default="delta-platform")
parser.add_argument("--image", default="delta-platform:u1")
parser.add_argument(
    "--postgres-image",
    help="기존 ECR PostgreSQL digest를 유지한다. CI에서는 필수로 전달한다.",
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
s = boto3.Session(region_name=args.region)
if s.client("sts").get_caller_identity()["Account"] != args.account_id:
    raise SystemExit("AWS 계정 불일치")
ecr = s.client("ecr")
repo = ecr.describe_repositories(repositoryNames=[args.repository])["repositories"][0][
    "repositoryUri"
]
auth = ecr.get_authorization_token()["authorizationData"][0]
username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
subprocess.run(
    ["docker", "login", "--username", username, "--password-stdin", repo.split("/")[0]],
    input=password,
    text=True,
    stdout=subprocess.DEVNULL,
    check=True,
)
output = {}
images = [("platform", args.image)]
if args.postgres_image:
    if not re.fullmatch(re.escape(repo) + r"@sha256:[a-f0-9]{64}", args.postgres_image):
        raise SystemExit("같은 ECR 저장소의 검증한 PostgreSQL digest가 필요합니다.")
    ecr.describe_images(
        repositoryName=args.repository,
        imageIds=[{"imageDigest": args.postgres_image.split("@", 1)[1]}],
    )
    output["postgres"] = args.postgres_image
else:
    images.append(("postgres", "postgres:17-alpine"))
for name, image in images:
    image_id = (
        subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
        )
        .strip()
        .split(":")[1]
    )
    tag = f"{name}-{image_id[:16]}"
    uri = repo + ":" + tag
    try:
        ecr.describe_images(
            repositoryName=args.repository, imageIds=[{"imageTag": tag}]
        )
    except ecr.exceptions.ImageNotFoundException:
        subprocess.run(["docker", "tag", image, uri], check=True)
        with args.output.with_name(f"{name}-push.log").open("w") as log:
            subprocess.run(
                ["docker", "push", uri],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    digest = ecr.describe_images(
        repositoryName=args.repository, imageIds=[{"imageTag": tag}]
    )["imageDetails"][0]["imageDigest"]
    output[name] = repo + "@" + digest
    print(name, "published:", output[name], flush=True)
args.output.write_text(json.dumps(output, indent=2) + "\n")
