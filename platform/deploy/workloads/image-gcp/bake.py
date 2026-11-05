"""Build an immutable VM image, then remove all temporary compute/network resources."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path


def execute(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).parent
    digest = hashlib.sha256(
        (source / "bootstrap.sh").read_bytes() + (source / "main.tf").read_bytes()
    ).hexdigest()[:12]
    name = "delta-runtime-" + digest
    gcloud = ["gcloud", "--project=" + args.project, "--quiet"]
    images = json.loads(
        execute(
            [
                *gcloud,
                "compute",
                "images",
                "list",
                "--no-standard-images",
                "--filter=name=" + name,
                "--format=json(name)",
            ]
        )
    )
    if images:
        print("projects/" + args.project + "/global/images/" + name)
        return
    args.workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for filename in ("main.tf", "bootstrap.sh", ".terraform.lock.hcl"):
        shutil.copyfile(source / filename, args.workdir / filename)
    (args.workdir / "inputs.auto.tfvars.json").write_text(
        json.dumps({"project_id": args.project, "name": name})
    )
    terraform = ["terraform", "-chdir=" + str(args.workdir)]
    execute([*terraform, "init", "-input=false", "-lockfile=readonly"])
    try:
        execute([*terraform, "apply", "-input=false", "-auto-approve"])
        for _ in range(120):
            serial = execute(
                [
                    *gcloud,
                    "compute",
                    "instances",
                    "get-serial-port-output",
                    name,
                    "--zone=asia-northeast3-a",
                ]
            )
            if "DELTA_RUNTIME_IMAGE_READY" in serial:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Runtime image build did not finish")
        execute(
            [*gcloud, "compute", "instances", "stop", name, "--zone=asia-northeast3-a"]
        )
        execute(
            [
                *gcloud,
                "compute",
                "images",
                "create",
                name,
                "--source-disk=" + name,
                "--source-disk-zone=asia-northeast3-a",
                "--labels=managed-by=delta-platform,purpose=customer-runtime",
            ]
        )
        print("projects/" + args.project + "/global/images/" + name)
    finally:
        execute([*terraform, "destroy", "-input=false", "-auto-approve"])


if __name__ == "__main__":
    main()
