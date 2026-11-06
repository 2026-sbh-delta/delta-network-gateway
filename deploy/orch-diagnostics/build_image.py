"""Build the committed ARM64 orchestrator into its existing ECR; temporary registry login."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[2]
repository = "616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-cp-orchestrator"
region = "ap-northeast-2"

def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def aws(*args):
    p = command(["aws", *args, "--profile", "delta-finals", "--region", region,
        "--output", "json", "--no-cli-pager"], capture_output=True, text=True)
    return json.loads(p.stdout)

if __name__ == "__main__":
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "services"], cwd=repo)
    if dirty:
        raise SystemExit("Commit the exact service snapshot before building")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    tag = "orch-diagnostics-" + sha[:12] + "-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    image = repository + ":" + tag
    endpoint = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0]["Endpoints"]["docker"]["Host"]
    with tempfile.TemporaryDirectory(prefix="delta-cp-registry-") as directory:
        # Keep plugin discovery while registry credentials stay in the temporary config.
        plugins = Path(directory) / "cli-plugins"
        plugins.mkdir()
        buildx = Path.home() / ".docker/cli-plugins/docker-buildx"
        if buildx.is_file():
            (plugins / "docker-buildx").symlink_to(buildx.resolve())
        env = {**os.environ, "DOCKER_CONFIG": directory, "DOCKER_HOST": endpoint}
        auth = subprocess.check_output(["aws", "ecr", "get-login-password", "--profile", "delta-finals",
            "--region", region])
        command(["docker", "login", "--username", "AWS", "--password-stdin", repository.split("/")[0]],
            input=auth, env=env, stdout=subprocess.DEVNULL)
        del auth
        command(["docker", "buildx", "build", "--platform", "linux/arm64", "--provenance=false", "--sbom=false",
            "-f", str(repo / "services/delta-orchestrator/Dockerfile"), "-t", image, "--push", str(repo / "services")], env=env)
    digest = aws("ecr", "describe-images", "--repository-name", "delta-cp-orchestrator",
        "--image-ids", "imageTag=" + tag)["imageDetails"][0]["imageDigest"]
    result = {"source_commit": sha, "tag": tag, "image": repository + "@" + digest}
    (repo / "deploy/orch-diagnostics/image.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
