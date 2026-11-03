"""Trusted CodeBuild entry point, embedded by Terraform rather than read from the repo."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID


def run(args, **kwargs):
    return subprocess.run(args, check=True, timeout=780, **kwargs)


def paths(root, context, dockerfile):
    root = root.resolve(strict=True)
    for value in (context, dockerfile):
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("빌드 경로는 저장소 내부 상대 경로여야 합니다.")
    ctx, df = (
        (root / context).resolve(strict=True),
        (root / dockerfile).resolve(strict=True),
    )
    if (
        not ctx.is_relative_to(root)
        or not df.is_relative_to(ctx)
        or not ctx.is_dir()
        or not df.is_file()
    ):
        raise ValueError("Dockerfile/context가 저장소 내부에 있지 않습니다.")
    # Reject symlink components, even aliases that happen to resolve inside the checkout.
    for value in (context, dockerfile):
        part = root
        for component in Path(value).parts:
            part /= component
            if part.is_symlink():
                raise ValueError("빌드 경로에 심볼릭 링크를 사용할 수 없습니다.")
    return ctx, df


def main():
    env = os.environ
    root = Path(env["CODEBUILD_SRC_DIR"])
    sha, run_id, repository = (
        env["DELTA_COMMIT"],
        str(UUID(env["DELTA_RUN_ID"])),
        env["DELTA_REPOSITORY"],
    )
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("고정된 Git 커밋이 필요합니다.")
    if not re.fullmatch(
        r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9][a-z0-9/_.-]+",
        repository,
    ):
        raise ValueError("허용된 ECR 저장소가 아닙니다.")
    actual = run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if actual != sha or env.get("CODEBUILD_RESOLVED_SOURCE_VERSION") != sha:
        raise ValueError("체크아웃 커밋이 요청과 다릅니다.")
    ctx, df = paths(root, env["DELTA_CONTEXT"], env["DELTA_DOCKERFILE"])
    tag = f"{repository}:run-{run_id}"
    # Block container forwarding to local credential endpoints. Never enable host networking.
    for cidr in ("169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        run(["iptables", "-I", "DOCKER-USER", "-d", cidr, "-j", "REJECT"])
    # DOCKER-USER also sees replies addressed to the private container IP.
    # Let established replies through before rejecting NEW private destinations.
    resolvers = [
        line.split()[1]
        for line in Path("/etc/resolv.conf").read_text().splitlines()
        if line.startswith("nameserver ") and len(line.split()) >= 2
    ]
    for resolver in resolvers:
        if ":" in resolver:
            continue
        for protocol in ("udp", "tcp"):
            run(
                [
                    "iptables",
                    "-I",
                    "DOCKER-USER",
                    "-d",
                    resolver,
                    "-p",
                    protocol,
                    "--dport",
                    "53",
                    "-j",
                    "ACCEPT",
                ]
            )
    run(
        [
            "iptables",
            "-I",
            "DOCKER-USER",
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="delta-docker-") as config:
        docker_env = {**env, "DOCKER_CONFIG": config}
        run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64",
                "--provenance=false",
                "--sbom=false",
                "--load",
                "--file",
                str(df),
                "--tag",
                tag,
                str(ctx),
            ],
            env=docker_env,
        )
        architecture = run(
            ["docker", "image", "inspect", tag, "--format", "{{json .}}"],
            env=docker_env,
            capture_output=True,
            text=True,
        )
        image = json.loads(architecture.stdout)
        if image["Architecture"] != "amd64" or image["Os"] != "linux":
            raise ValueError("이미지는 linux/amd64여야 합니다.")
        # Authenticate only after the untrusted Dockerfile has finished. Token is stdin only.
        password = run(
            ["aws", "ecr", "get-login-password", "--region", env["AWS_REGION"]],
            capture_output=True,
        ).stdout
        run(
            [
                "docker",
                "login",
                "--username",
                "AWS",
                "--password-stdin",
                repository.split("/")[0],
            ],
            input=password,
            env=docker_env,
        )
        run(["docker", "push", tag], env=docker_env)
    print("이미지 게시 완료. 플랫폼에서 ECR digest를 확인합니다.", flush=True)


if __name__ == "__main__":
    main()
