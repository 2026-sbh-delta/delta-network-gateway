"""Build committed AI/orchestrator ARM64 sources into their existing ECR repositories."""
import datetime
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "616723161368.dkr.ecr.ap-northeast-2.amazonaws.com"


def main():
    if subprocess.check_output(["git", "status", "--porcelain", "--", "services"], cwd=ROOT):
        raise SystemExit("Commit exact service sources before building")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    endpoint = json.loads(subprocess.check_output(["docker", "context", "inspect"]))[0]["Endpoints"]["docker"]["Host"]
    result = {"source_commit": sha}
    with tempfile.TemporaryDirectory(prefix="delta-case-registry-") as folder:
        plugins = Path(folder) / "cli-plugins"
        plugins.mkdir()
        (plugins / "docker-buildx").symlink_to((Path.home() / ".docker/cli-plugins/docker-buildx").resolve())
        env = {**os.environ, "DOCKER_CONFIG": folder, "DOCKER_HOST": endpoint}
        password = subprocess.check_output(["aws", "ecr", "get-login-password", "--profile", "delta-finals", "--region", "ap-northeast-2"])
        subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", REGISTRY],
                       input=password, env=env, check=True, stdout=subprocess.DEVNULL)
        del password
        tag = "case-learning-" + sha[:12] + "-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        for name, repo, dockerfile, context in (
            ("ai", "delta-cp-ai/api", "services/delta-ai/Dockerfile", "services/delta-ai"),
            ("orchestrator", "delta-cp-orchestrator", "services/delta-orchestrator/Dockerfile", "services"),
        ):
            metadata = Path(folder) / (name + ".json")
            subprocess.run(["docker", "buildx", "build", "--platform", "linux/arm64", "--provenance=false", "--sbom=false",
                "--label", "org.opencontainers.image.revision=" + sha, "--metadata-file", str(metadata), "--push",
                "-f", str(ROOT / dockerfile), "-t", REGISTRY + "/" + repo + ":" + tag, str(ROOT / context)], env=env, check=True)
            digest = json.loads(metadata.read_bytes())["containerimage.digest"]
            result[name] = {"image": REGISTRY + "/" + repo + "@" + digest, "tag": tag}
    destination = ROOT.parent / "notes/case-learning-aws-20261106/images.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
