"""Administrator-owned entry point, outside the customer checkout."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import build_common


def validated(env):
    values = {
        k: env[k]
        for k in (
            "DELTA_RUN_ID",
            "DELTA_SOURCE",
            "DELTA_COMMIT",
            "DELTA_CONTEXT",
            "DELTA_DOCKERFILE",
            "DELTA_ECR_REPOSITORY",
            "AWS_REGION",
        )
    }
    if str(UUID(values["DELTA_RUN_ID"])) != values["DELTA_RUN_ID"]:
        raise ValueError("Invalid run UUID")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}",
        values["DELTA_SOURCE"],
    ):
        raise ValueError("Only public GitHub repository URLs are allowed")
    if not re.fullmatch(r"[a-f0-9]{40}", values["DELTA_COMMIT"]):
        raise ValueError("A pinned commit is required")
    if not re.fullmatch(r"[a-z0-9-]+", values["AWS_REGION"]) or not re.fullmatch(
        rf"[0-9]{{12}}\.dkr\.ecr\.{re.escape(values['AWS_REGION'])}\.amazonaws\.com/[a-z0-9][a-z0-9/_.-]+",
        values["DELTA_ECR_REPOSITORY"],
    ):
        raise ValueError("Invalid ECR repository or region")
    for key in ("DELTA_CONTEXT", "DELTA_DOCKERFILE"):
        v = values[key]
        if (
            not v
            or len(v) > 256
            or Path(v).is_absolute()
            or ".." in Path(v).parts
            or any(ord(c) < 32 for c in v)
        ):
            raise ValueError("Invalid build path")
    return values


def main():
    value = validated(os.environ)
    output = Path.cwd() / "result.json"
    output.unlink(missing_ok=True)
    run_id = value["DELTA_RUN_ID"]
    repository = value["DELTA_ECR_REPOSITORY"]
    # Temporary checkout is separate from trusted scripts and the archived result.
    with tempfile.TemporaryDirectory(prefix="delta-source-") as directory:
        source = Path(directory)
        git = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "http.followRedirects=false",
            "-C",
            str(source),
        ]
        subprocess.run([*git, "init"], check=True, timeout=30)
        subprocess.run(
            [
                *git,
                "fetch",
                "--depth=1",
                "--",
                value["DELTA_SOURCE"] + ".git",
                value["DELTA_COMMIT"],
            ],
            check=True,
            timeout=180,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        subprocess.run(
            [*git, "checkout", "--detach", "FETCH_HEAD"], check=True, timeout=30
        )
        os.environ.update(
            {
                "CODEBUILD_SRC_DIR": str(source),
                "CODEBUILD_RESOLVED_SOURCE_VERSION": value["DELTA_COMMIT"],
                "DELTA_REPOSITORY": repository,
            }
        )
        try:
            build_common.main()
            images = json.loads(
                subprocess.run(
                    [
                        "aws",
                        "ecr",
                        "describe-images",
                        "--region",
                        value["AWS_REGION"],
                        "--repository-name",
                        repository.split("/", 1)[1],
                        "--image-ids",
                        f"imageTag=run-{run_id}",
                        "--output",
                        "json",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                ).stdout
            )["imageDetails"]
            if len(images) != 1 or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", images[0].get("imageDigest", "")
            ):
                raise ValueError("Published image digest is missing")
            digest = images[0]["imageDigest"]
            output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": run_id,
                        "commit_sha": value["DELTA_COMMIT"],
                        "platform": "linux/amd64",
                        "image": repository + "@" + digest,
                        "image_digest": digest,
                    }
                )
            )
        finally:
            subprocess.run(
                ["docker", "image", "rm", "-f", repository + ":run-" + run_id],
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    main()
