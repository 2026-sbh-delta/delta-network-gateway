"""Create ignored local secrets. Never display passwords or tokens."""

import argparse
import os
import re
import secrets
from pathlib import Path


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    if not re.fullmatch(
        r"[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9/_.-]+",
        args.repository,
    ):
        raise ValueError("서울 리전의 ECR 저장소 URI가 필요합니다.")
    root = Path(__file__).resolve().parent
    (root / ".runtime").mkdir(mode=0o700, exist_ok=True)
    private_write(
        root / ".env",
        f"JENKINS_ADMIN_PASSWORD={secrets.token_urlsafe(32)}\nJENKINS_PLATFORM_PASSWORD={secrets.token_urlsafe(32)}\nDELTA_ECR_REPOSITORY={args.repository}\n",
    )
    print("로컬 .env 생성 완료. 기존 값은 덮어쓰지 않습니다.")


if __name__ == "__main__":
    main()
