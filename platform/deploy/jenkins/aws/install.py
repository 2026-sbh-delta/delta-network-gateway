"""Install one trusted Jenkins component on its dedicated VM; never print secrets."""

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path


def write(path, value, *, owner=None):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(value if isinstance(value, bytes) else value.encode())
    if owner:
        os.chown(path, *owner)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--component", choices=["controller", "agent"], required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--account-id", required=True)
    args = p.parse_args()
    repository = f"{args.account_id}.dkr.ecr.{args.region}.amazonaws.com/delta-platform"
    if not re.fullmatch(re.escape(repository) + r"@sha256:[a-f0-9]{64}", args.image):
        raise ValueError("Only a pinned platform tooling image is allowed")
    if not Path("/opt/delta-jenkins/bootstrap-ready").is_file():
        raise ValueError("VM bootstrap has not completed")
    config = json.loads(
        subprocess.check_output(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                args.region,
                "--name",
                f"/delta-platform/jenkins/{args.component}",
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ]
        )
    )
    root = Path("/etc/delta-jenkins")
    write(root / "ca.crt", base64.b64decode(config["ca_base64"], validate=True))
    env = {}
    name = "delta-jenkins-" + args.component
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--restart",
        "unless-stopped",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=3",
    ]
    if args.component == "controller":
        subprocess.run(["mountpoint", "-q", "/srv/delta-jenkins"], check=True)
        dropin = Path("/etc/systemd/system/docker.service.d")
        dropin.mkdir(parents=True, exist_ok=True)
        write(
            dropin / "jenkins-data.conf",
            "[Unit]\nRequiresMountsFor=/srv/delta-jenkins\n",
        )
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        write(
            root / "tls.p12",
            base64.b64decode(config["keystore_base64"], validate=True),
            owner=(1000, 1000),
        )
        write(root / "controller-config.json", json.dumps(config))
        env.update(
            {
                "JENKINS_ADMIN_PASSWORD": config["admin_password"],
                "JENKINS_PLATFORM_PASSWORD": config["platform_password"],
                "JAVA_OPTS": "-Djenkins.install.runSetupWizard=false -Xmx768m",
                "JENKINS_OPTS": "--httpPort=-1 --httpsPort=8443 --httpsKeyStore=/run/secrets/tls.p12 --httpsKeyStorePassword="
                + config["keystore_password"],
            }
        )
        if config.get("deployment_password"):
            for filename in ("deployment-casc.yaml", "deployment.Jenkinsfile"):
                if not (root / filename).is_file():
                    raise ValueError("Trusted deployment controller files are missing")
            env["JENKINS_DEPLOYMENT_PASSWORD"] = config["deployment_password"]
            command += [
                "--volume",
                str(root / "deployment-casc.yaml") + ":/opt/delta/casc.yaml:ro",
                "--volume",
                str(root / "deployment.Jenkinsfile")
                + ":/opt/delta/workloads/Jenkinsfile:ro",
            ]
        command += [
            "--memory",
            "1536m",
            "--publish",
            "443:8443",
            "--volume",
            "/srv/delta-jenkins:/var/jenkins_home",
            "--volume",
            str(root / "tls.p12") + ":/run/secrets/tls.p12:ro",
        ]
    else:
        write(root / "agent-secret", config["agent_secret"])
        env.update(
            {
                "JENKINS_URL": config["url"],
                "JENKINS_CA_FILE": "/run/secrets/ca.crt",
                "DELTA_ECR_REPOSITORY": repository + "-builds",
                "AWS_REGION": args.region,
                "DOCKER_TLS_CERTDIR": "",
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            }
        )
        command += [
            "--privileged",
            "--memory",
            "3g",
            "--pids-limit",
            "2048",
            "--volume",
            "delta-jenkins-agent-docker:/var/lib/docker",
            "--volume",
            str(root / "agent-secret") + ":/run/secrets/agent_secret:ro",
            "--volume",
            str(root / "ca.crt") + ":/run/secrets/ca.crt:ro",
        ]
    if any("\n" in v or "\r" in v for v in env.values()):
        raise ValueError("Unexpected newline in runtime setting")
    write(root / "runtime.env", "".join(f"{k}={v}\n" for k, v in env.items()))
    password = subprocess.check_output(
        ["aws", "ecr", "get-login-password", "--region", args.region]
    )
    subprocess.run(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            repository.split("/")[0],
        ],
        input=password,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["docker", "pull", args.image], check=True)
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        [*command, "--env-file", str(root / "runtime.env"), args.image], check=True
    )
    write(root / "image.txt", args.image + "\n")
    print(args.component, "installed")


if __name__ == "__main__":
    main()
