"""Owned, loopback-only demonstration; run with the platform backend's Python environment."""

import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from delta_platform.core.store import digest
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "platform/.runtime/migration-demo"
ENV = WORK / ".env"
BASE = [
    "docker",
    "compose",
    "--env-file",
    str(ENV),
    "-f",
    str(ROOT / "platform/migration/compose.demo.yaml"),
]


def run(*args, input=None, capture=False):
    return subprocess.run(
        [*BASE, *args], input=input, check=True, stdout=subprocess.PIPE if capture else None
    ).stdout


def ingress(target):
    if target == "maintenance":
        body = "add_header Retry-After 2 always; return 503;"
    else:
        body = f"proxy_set_header Host $http_host; proxy_pass http://{target}:8080;"
    text = "server { listen 80; keepalive_timeout 0; location / { " + body + " } }\n"
    temporary = WORK / "nginx/default.conf.tmp"
    temporary.write_text(text)
    temporary.replace(WORK / "nginx/default.conf")


def wait_file(name, process):
    deadline = time.monotonic() + 120
    while not (WORK / name).exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Browser did not reach " + name)
        time.sleep(0.2)


def main():
    # Preserve private 0600 files while letting the invoking Linux user read them.
    os.environ["DEMO_RUNNER_UID"] = str(os.getuid())
    os.environ["DEMO_RUNNER_GID"] = str(os.getgid())
    WORK.mkdir(parents=True, exist_ok=True)
    if (WORK / "work/plan.json").exists():
        raise SystemExit("Existing demo plan: inspect it before a new run; do not overwrite.")
    (WORK / "nginx").mkdir(exist_ok=True)
    (WORK / "work").mkdir(exist_ok=True)
    if not ENV.exists():
        values = {
            key: secrets.token_urlsafe(32)
            for key in (
                "DB_ADMIN_PASSWORD",
                "APP_DB_PASSWORD",
                "APP_SECRET_KEY",
                "REPLICATION_PASSWORD",
                "DEMO_PASSWORD",
            )
        }
        descriptor = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("\n".join(key + "=" + value for key, value in values.items()) + "\n")
    values = dict(line.split("=", 1) for line in ENV.read_text().splitlines())
    ingress("source-app")
    run("build", "source-app", "runner")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "delta-migration-session-demo:local", "--format", "{{.Id}}"],
        text=True,
    ).strip()
    override = WORK / "pinned-app.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    name: {"image": image_id, "pull_policy": "never"}
                    for name in ("source-app", "target-app")
                }
            }
        )
    )
    BASE.extend(["-f", str(override)])
    run("up", "-d", "source", "target", "source-app", "ingress")
    run("exec", "-T", "source-app", "python", "manage.py", "migrate", "--noinput")
    user_setup = 'from django.contrib.auth.models import User; import sys; u,_=User.objects.get_or_create(username="migration-demo"); u.set_password(sys.stdin.read()); u.save()'
    run(
        "exec",
        "-T",
        "source-app",
        "python",
        "manage.py",
        "shell",
        "-c",
        user_setup,
        input=values["DEMO_PASSWORD"].encode(),
    )
    ddl = run(
        "exec",
        "-T",
        "source",
        "pg_dump",
        "-U",
        "delta_admin",
        "-d",
        "app",
        "--schema-only",
        "--no-owner",
        "--no-privileges",
        "--schema=public",
        capture=True,
    )
    run(
        "exec",
        "-T",
        "target",
        "psql",
        "-U",
        "delta_admin",
        "-d",
        "app",
        "-v",
        "ON_ERROR_STOP=1",
        input=b"DROP SCHEMA public;\n" + ddl,
    )
    for database in ("source", "target"):
        statements = (
            sql.SQL(
                "CREATE ROLE replicator LOGIN REPLICATION PASSWORD {}; GRANT USAGE ON SCHEMA public TO app,replicator; GRANT SELECT ON ALL TABLES IN SCHEMA public TO replicator; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO app; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO app;"
            )
            .format(sql.Literal(values["REPLICATION_PASSWORD"]))
            .as_string()
        )
        run(
            "exec",
            "-T",
            database,
            "psql",
            "-U",
            "delta_admin",
            "-d",
            "app",
            "-v",
            "ON_ERROR_STOP=1",
            input=statements.encode(),
        )
    browser_log = (WORK / "browser.log").open("w")
    browser = subprocess.Popen(
        ["node", str(ROOT / "platform/migration/browser-demo.cjs")],
        stdout=browser_log,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_file("browser-ready", browser)
        source_env, target_env, workflow, execution = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
        base = ["run", "--rm", "-T", "runner", "/app/platform/backend/.venv/bin/platform-db-move"]
        run(
            *base,
            "plan",
            "--local-testing",
            "--plan",
            "/work/plan.json",
            "--source-environment-id",
            source_env,
            "--target-environment-id",
            target_env,
            "--writer-role",
            "app",
        )
        plan = json.loads((WORK / "work/plan.json").read_text())
        hashed = digest(plan)
        options = [
            "--local-testing",
            "--plan",
            "/work/plan.json",
            "--journal",
            "/work/journal.json",
            "--workflow-id",
            workflow,
            "--run-id",
            execution,
        ]
        run(*base, "start", *options, "--approved-hash", hashed)
        run(*base, "wait", *options)
        (WORK / "copy-ready").touch()
        wait_file("browser-cdc-ready", browser)
        probe_result = []
        stop = threading.Event()

        def probe():
            with httpx.Client(timeout=2, trust_env=False) as client:
                while not stop.is_set():
                    try:
                        status = client.get("http://127.0.0.1:18096/health").status_code
                    except httpx.HTTPError:
                        status = None
                    probe_result.append({"at": time.time(), "status": status})
                    stop.wait(0.2)

        sampler = threading.Thread(target=probe)
        sampler.start()
        try:
            time.sleep(0.5)
            ingress("maintenance")
            run("exec", "-T", "ingress", "nginx", "-s", "reload")
            with httpx.Client(
                timeout=2, trust_env=False, headers={"Connection": "close"}
            ) as probe_client:
                deadline = time.monotonic() + 15
                while probe_client.get("http://127.0.0.1:18096/health").status_code != 503:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Ingress maintenance was not activated")
                    time.sleep(0.1)
            paused_at = time.time()
            run(*base, "promote", *options, "--approved-hash", hashed)
            run("up", "-d", "target-app")
            deadline = time.monotonic() + 60
            while True:
                try:
                    run(
                        "exec",
                        "-T",
                        "target-app",
                        "python",
                        "-c",
                        "import urllib.request,sys\ntry: urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2)\nexcept Exception: sys.exit(1)",
                        capture=True,
                    )
                    break
                except subprocess.CalledProcessError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.5)
            ingress("target-app")
            run("exec", "-T", "ingress", "nginx", "-s", "reload")
            with httpx.Client(
                timeout=2, trust_env=False, headers={"Connection": "close"}
            ) as probe_client:
                deadline = time.monotonic() + 20
                consecutive = 0
                while consecutive < 3:
                    response = probe_client.get("http://127.0.0.1:18096/health")
                    healthy = (
                        response.status_code == 200
                        and response.json().get("environment") == "local-target"
                    )
                    consecutive = consecutive + 1 if healthy else 0
                    if time.monotonic() > deadline:
                        raise RuntimeError("New ingress route did not become healthy")
                    time.sleep(0.2)
            resumed_at = time.time()
            (WORK / "cutover-done").touch()
            browser.wait(timeout=90)
            if browser.returncode:
                raise RuntimeError("Browser verification failed; inspect private browser.log")
            time.sleep(0.5)
        finally:
            stop.set()
            sampler.join()
        run(*base, "cleanup", *options)
        run("stop", "source-app")
        proof = json.loads((WORK / "browser-proof.json").read_text())
        app_containers = (
            run("ps", "-a", "-q", "source-app", "target-app", capture=True).decode().split()
        )
        observed_images = [
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.Image}}", identifier], text=True
            ).strip()
            for identifier in app_containers
        ]
        if len(observed_images) != 2 or any(image != image_id for image in observed_images):
            raise RuntimeError("Source/target app image identity mismatch")
        proof.update(
            app_image_id=image_id,
            same_app_image=True,
            workflow_id=workflow,
            run_id=execution,
            plan_hash=hashed,
            maintenance_seconds=round(resumed_at - paused_at, 3),
            health_probes=probe_result,
            database_phase=json.loads((WORK / "work/journal.json").read_text())["phase"],
        )
        (WORK / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        print(
            "Actual local app+DB migration verified; proof saved under platform/.runtime/migration-demo."
        )
    finally:
        if browser.poll() is None:
            browser.terminate()
            try:
                browser.wait(timeout=10)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait(timeout=5)
        browser_log.close()


if __name__ == "__main__":
    main()
