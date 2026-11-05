"""동일 호스트의 FE·BE 교체·관측·복귀. CLI worker가 SQLite와 flock을 소유한다."""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request

from delivery_contract import IMAGE_RE, canonical_hash, validate_bundle

COLUMNS = {"position", "id", "title", "content", "created_at"}
UNRESOLVED = ("running", "unknown", "rollback_failed")


def _secret_path(target):
    return target.get("db_secret_file", "/run/delta-compose/db_password")


def render_compose(target, bundle):
    validate_bundle(bundle, bundle["commit"])
    cloud = target["cloud"]
    services = {}
    for name in ("frontend", "backend"):
        if cloud == "gcp":
            logging = {"gcp-project": target["log_project"], "mode": "non-blocking"}
        else:
            group = target.get("log_groups", {}).get(name, target.get("log_group", ""))
            if not group.startswith("/delta/workload/"):
                raise ValueError("기업 AWS 로그 그룹이 필요하다")
            logging = {"awslogs-region": target["region"], "awslogs-group": group,
                       "awslogs-stream": target.get("log_prefix", target["compose_project"]) + "-" + name,
                       "mode": "non-blocking"}
        services[name] = {"image": bundle["images"][name], "restart": "unless-stopped", "init": True,
                          "environment": {"APP_ENV": "production", "CLOUD_PROVIDER": cloud,
                                          "APP_VERSION": bundle["commit"]},
                          "logging": {"driver": "gcplogs" if cloud == "gcp" else "awslogs", "options": logging},
                          "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"]}
    fe, be = services["frontend"], services["backend"]
    fe["environment"].update(PORT=str(target["frontend_port"]), API_HOST="backend",
                             API_PORT=str(target["backend_port"]), API_PROTOCOL="http")
    fe["ports"] = [f"{target.get('bind_ip', target.get('private_ip', '127.0.0.1'))}:{target['frontend_port']}:{target['frontend_port']}"]
    fe["depends_on"] = {"backend": {"condition": "service_healthy"}}
    be["environment"].update(PORT=str(target["backend_port"]), STORAGE_BACKEND="postgres",
                             PGHOST=target["db_host"], PGPORT=str(target.get("db_port", 5432)),
                             PGDATABASE=target["db_name"], PGUSER=target["db_user"],
                             PGSSLMODE=target.get("db_sslmode", "disable" if cloud == "gcp" else "verify-full"))
    be["entrypoint"] = ["/bin/sh", "/app/scripts/backend-entrypoint.sh"]
    be["command"] = ["node", "/app/server.mjs"]
    be["volumes"] = [{"type": "bind", "source": _secret_path(target),
                       "target": "/run/secrets/db_password", "read_only": True}]
    if target.get("db_ca_file"):
        be["volumes"].append({"type": "bind", "source": target["db_ca_file"], "target": "/run/db/ca.pem", "read_only": True})
        be["environment"]["PGSSLROOTCERT"] = "/run/db/ca.pem"
    if target.get("db_tls_servername"):
        be["environment"]["PGTLS_SERVERNAME"] = target["db_tls_servername"]
    if cloud == "gcp" and target.get("sql_proxy_image"):
        if not IMAGE_RE.fullmatch(target["sql_proxy_image"]):
            raise ValueError("SQL Proxy도 digest 고정 이미지여야 한다")
        services["proxy"] = {"image": target["sql_proxy_image"], "restart": "unless-stopped",
                             "network_mode": "service:backend", "depends_on": {"backend": {"condition": "service_started"}},
                             "command": ["--psc", "--address", "127.0.0.1", "--port", "5432", "--structured-logs",
                                         target["sql_connection_name"]],
                             "logging": {"driver": "gcplogs", "options": {"gcp-project": target["log_project"], "mode": "non-blocking"}}}
    return {"services": services}


def _db(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / "host.sqlite3"
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS deployments (id TEXT PRIMARY KEY, target TEXT NOT NULL, "
               "identity TEXT NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL, compose TEXT)")
    db.commit()
    path.chmod(0o600)
    return db


def _lock_path(state, target):
    return Path(state) / (canonical_hash(target) + ".lock")


def _save(state, request, result, compose=None):
    with _db(state) as db:
        db.execute("INSERT INTO deployments VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                   "state=excluded.state,result=excluded.result,compose=COALESCE(excluded.compose,deployments.compose)",
                   (request["deployment_id"], request["target"]["target_id"], canonical_hash(request), result["state"],
                    json.dumps(request), json.dumps(result), None if compose is None else json.dumps(compose)))


def query(deployment_id, state_dir):
    with _db(state_dir) as db:
        row = db.execute("SELECT target,state,result FROM deployments WHERE id=?", (deployment_id,)).fetchone()
    if row is None:
        return {"deployment_id": deployment_id, "state": "not_found"}
    if row[1] == "running":
        with _lock_path(state_dir, row[0]).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                # 실제 작업자가 없는 running 기록은 재부팅/중단 뒤에도 다음 배포를 차단한다.
                with _db(state_dir) as db:
                    db.execute("BEGIN IMMEDIATE")
                    latest = db.execute("SELECT state,result FROM deployments WHERE id=?", (deployment_id,)).fetchone()
                    if latest[0] == "running":
                        result = {**json.loads(latest[1]), "state": "unknown"}
                        db.execute("UPDATE deployments SET state='unknown',result=? WHERE id=?", (json.dumps(result), deployment_id))
                    else:
                        result = json.loads(latest[1])
                return result
    return json.loads(row[2])


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def _rows(value):
    if not value.strip():
        return []
    if value.lstrip().startswith("["):
        return json.loads(value)
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def _check_db(db, target):
    if (not isinstance(db, dict) or db.get("connected") is not True or db.get("database") != target["db_name"]
            or type(db.get("server_version_num")) is not int or db["server_version_num"] // 10000 != 18
            or db.get("schema_ok") is not True or db.get("schema") != "delta_notes" or db.get("table") != "notes"
            or not COLUMNS <= set(db.get("columns") or [])):
        raise ValueError("실제 PostgreSQL18 연결과 필수 스키마가 확인되지 않았다")


def _diagnostic(value, target, commit=None):
    if value.get("target_id") != target["target_id"] or value.get("environment") != "production":
        raise ValueError("호스트 진단 대상이 다르다")
    pre = value.get("preflight", {})
    if any(pre.get(k) is not True for k in ("disk_ok", "docker_ok", "compose_ok", "registry_ok")):
        raise ValueError("호스트 사전 진단 실패")
    _check_db(pre.get("db"), target)
    if commit is not None:
        fe, be = value.get("frontend", {}), value.get("backend", {})
        if (fe.get("healthy") is not True or be.get("healthy") is not True or be.get("storage") != "postgres"
                or fe.get("version") != commit or fe.get("cloudProvider") != target["cloud"]
                or ("commit" in fe and fe["commit"] != commit)
                or ("version" in be and be["version"] != commit)
                or ("cloudProvider" in be and be["cloudProvider"] != target["cloud"])):
            raise ValueError("FE·BE 실행 상태·버전·클라우드·postgres 저장소가 다르다")
        _check_db(be.get("db"), target)


def deploy(request, state_dir, *, runner=subprocess.run, probe=None):
    target, bundle = request["target"], request["bundle"]
    validate_bundle(bundle, request["commit_sha"])
    dep = request["deployment_id"]
    if (not isinstance(dep, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dep)
            or target.get("environment") != "production" or target.get("cloud") not in ("gcp", "aws")
            or target.get("db_major") != 18 or request.get("config_sha256") != canonical_hash(target)):
        raise ValueError("호스트 요청·대상·설정 해시가 올바르지 않다")
    document = render_compose(target, bundle)
    state_dir = Path(state_dir)
    with _db(state_dir) as db:
        existing = db.execute("SELECT identity FROM deployments WHERE id=?", (dep,)).fetchone()
    if existing:
        if existing[0] != canonical_hash(request):
            raise ValueError("같은 deployment_id의 요청이 다르다")
        return query(dep, state_dir)
    lock = _lock_path(state_dir, target["target_id"]).open("a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with _db(state_dir) as db:
                row = db.execute("SELECT id FROM deployments WHERE target=? AND state IN ('running','unknown','rollback_failed')",
                                 (target["target_id"],)).fetchone()
            return {"deployment_id": dep, "state": "blocked", "blocked_by": row[0] if row else "target-lock"}
        with _db(state_dir) as db:
            existing = db.execute("SELECT identity,result FROM deployments WHERE id=?", (dep,)).fetchone()
            pending = db.execute("SELECT id FROM deployments WHERE target=? AND state IN ('running','unknown','rollback_failed')",
                                 (target["target_id"],)).fetchone()
            previous = db.execute("SELECT request,compose FROM deployments WHERE target=? AND state='ready' ORDER BY rowid DESC LIMIT 1",
                                  (target["target_id"],)).fetchone()
        if existing:
            if existing[0] != canonical_hash(request):
                raise ValueError("같은 deployment_id의 요청이 다르다")
            return json.loads(existing[1])
        if pending:
            return {"deployment_id": dep, "state": "blocked", "blocked_by": pending[0]}
        result = {"deployment_id": dep, "target_id": target["target_id"], "environment": "production",
                  "commit_sha": request["commit_sha"], "commit": request["commit_sha"], "state": "running",
                  "config_sha256": request["config_sha256"]}
        _save(state_dir, request, result, document)
        with tempfile.TemporaryDirectory(prefix="docker-auth-", dir=state_dir) as auth:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "GOOGLE_", "CLOUDSDK_"))}
            env.update(DOCKER_CONFIG=auth, AWS_SHARED_CREDENTIALS_FILE="/dev/null", AWS_CONFIG_FILE="/dev/null", AWS_PAGER="")

            def run(argv, **kwargs):
                value = runner([str(v) for v in argv], capture_output=True, text=True, env=env, timeout=240, **kwargs)
                if value.returncode:
                    raise RuntimeError("호스트 명령 실패; 비밀이 포함될 수 있는 stderr 생략")
                return value

            compose_file = state_dir / (dep + ".compose.json")
            _write(compose_file, document)
            host_probe = probe or HostProbe(target, bundle, run, compose_file)

            def compose(file, *args):
                return run(["docker", "compose", "--project-name", target["compose_project"], "--file", file, *args])

            def observe(file, expected_images):
                images, digests, configs = {}, {}, {}
                for service in ("frontend", "backend"):
                    container_ids = compose(file, "ps", "-q", service).stdout.strip().splitlines()
                    if len(container_ids) != 1:
                        raise ValueError("서비스별 실행 컨테이너 하나가 필요하다")
                    container = json.loads(run(["docker", "container", "inspect", container_ids[0]]).stdout)[0]
                    image = json.loads(run(["docker", "image", "inspect", container["Image"]]).stdout)[0]
                    if (image.get("Id") != container["Image"] or container.get("State", {}).get("Status") != "running"
                            or container.get("State", {}).get("Health", {}).get("Status") != "healthy"):
                        raise ValueError("실행 image ID 또는 컨테이너 헬스가 다르다")
                    expected = expected_images[service]
                    if "@sha256:" in expected:
                        if expected not in image.get("RepoDigests", []):
                            raise ValueError("실제 컨테이너 image ID의 RepoDigests가 다르다")
                        images[service], digests[service] = expected, expected.rsplit("@", 1)[1]
                    elif expected != image.get("Id"):
                        raise ValueError("레거시 image ID 복귀가 다르다")
                    configs[service] = image["Id"]
                return images, digests, configs

            changed, was_running, old_doc, old_request = False, False, None, None
            try:
                _diagnostic(host_probe(target), target)
                if isinstance(host_probe, HostProbe):
                    host_probe.snapshot_service(state_dir / (dep + ".service.json"))
                running = _rows(compose(compose_file, "ps", "--all", "--format", "json").stdout)
                was_running = any(str(row.get("State", "")).lower() == "running" for row in running)
                if previous:
                    old_request, old_doc = json.loads(previous[0]), json.loads(previous[1])
                elif was_running:
                    # 기존 설치 파일을 보존하고 정규화한 설정·로컬 config ID만 복귀용으로 저장한다.
                    legacy = Path(target.get("legacy_compose_file", "/etc/delta-compose/compose.json" if target["cloud"] == "gcp"
                                              else "/opt/delta-compose/current/compose.yaml"))
                    if not legacy.is_file():
                        raise ValueError("현재 실행의 기존 Compose 설정을 보존할 수 없다")
                    argv = ["docker", "compose", "--project-name", target["compose_project"], "--file", legacy]
                    if target.get("legacy_env_file"):
                        argv += ["--env-file", target["legacy_env_file"]]
                    old_doc = json.loads(run([*argv, "config", "--format", "json"]).stdout)
                    for service in ("frontend", "backend"):
                        ids = compose(legacy, "ps", "-q", service).stdout.strip().splitlines()
                        old_doc["services"][service]["image"] = json.loads(run(["docker", "container", "inspect", ids[0]]).stdout)[0]["Image"]
                        old_doc["services"][service].pop("build", None)
                compose(compose_file, "config", "--quiet")
                compose(compose_file, "pull", "frontend", "backend", *(["proxy"] if "proxy" in document["services"] else []))
                changed = True
                compose(compose_file, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
                images, digests, configs = observe(compose_file, bundle["images"])
                diagnostic = host_probe(target)
                _diagnostic(diagnostic, target, request["commit_sha"])
                if isinstance(host_probe, HostProbe):
                    host_probe.activate_service(request, state_dir)
                    images, digests, configs = observe(compose_file, bundle["images"])
                    diagnostic = host_probe(target)
                    _diagnostic(diagnostic, target, request["commit_sha"])
                # Docker 저장 방식에 따라 .Id는 OCI config 또는 manifest일 수 있다.
                # .Image → inspect .Id → RepoDigests 연결만 요구하며 두 digest의 부등을 가정하지 않는다.
                result.update(bundle, images=images, image_digests=digests, observed_image_ids=configs, observed_config_ids=configs,
                              database_version_num=diagnostic["backend"]["db"]["server_version_num"], state="ready",
                              log_driver=target["log_driver"], log_delivery="unverified")
            except subprocess.TimeoutExpired:
                result.update(state="unknown", error="호스트 명령 응답 유실; 관측 후 복구 전까지 다른 배포 차단")
            except Exception:
                result.update(state="failed", error="운영 교체 또는 관측 검증 실패")
                if changed:
                    try:
                        if isinstance(host_probe, HostProbe):
                            host_probe.deactivate_new_service()
                            host_probe.restore_secret()
                        if was_running and old_doc:
                            rollback = state_dir / (dep + ".rollback.json")
                            _write(rollback, old_doc)
                            compose(rollback, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
                            old_images = {name: old_doc["services"][name]["image"] for name in ("frontend", "backend")}
                            observe(rollback, old_images)
                            if old_request:
                                old_probe = (HostProbe(old_request["target"], old_request["bundle"], run, rollback)
                                             if isinstance(host_probe, HostProbe) else host_probe)
                                _diagnostic(old_probe(old_request["target"]), old_request["target"], old_request["commit_sha"])
                            else:
                                if isinstance(host_probe, HostProbe):
                                    host_probe.compose_file = rollback
                                value = host_probe(target)
                                _diagnostic(value, target)
                                if not value.get("frontend", {}).get("healthy") or not value.get("backend", {}).get("healthy"):
                                    raise ValueError("레거시 앱 복귀 헬스 실패")
                        else:
                            compose(compose_file, "stop")
                        if isinstance(host_probe, HostProbe):
                            host_probe.restore_service()
                    except subprocess.TimeoutExpired:
                        result.update(state="unknown", error="복귀 명령 응답 유실; 다음 배포 차단")
                    except Exception:
                        result.update(state="rollback_failed", error="직전 실행 상태 복귀 실패; 다음 배포 차단")
                elif isinstance(host_probe, HostProbe):
                    try:
                        host_probe.restore_secret()
                    except Exception:
                        result.update(state="rollback_failed", error="사전 확인 전 비밀 파일 상태 복귀 실패")
            _save(state_dir, request, result)
            return result
    finally:
        lock.close()


DB_SQL = """SELECT json_build_object('connected',true,'database',current_database(),
'server_version_num',current_setting('server_version_num')::int,'schema','delta_notes','table','notes',
'columns',(SELECT json_agg(column_name) FROM information_schema.columns WHERE table_schema='delta_notes' AND table_name='notes'),
'schema_ok',to_regclass('delta_notes.notes') IS NOT NULL);"""


class HostProbe:
    """실제 VM에서만 호출하는 비밀·레지스트리·DB·HTTP 어댑터. stdout은 결과 dict뿐이다."""
    def __init__(self, target, bundle, run, compose_file=None):
        self.target, self.bundle, self.run = target, bundle, run
        self.prepared = False
        self.compose_file = compose_file
        self.service_snapshot = None
        self.secret_permissions = None
        self.secret_original = None
        self.secret_touched = False
        self.service_started = False

    @staticmethod
    def metadata_token():
        req = urllib.request.Request("http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
                                     headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)["access_token"]

    def verify_host(self, target):
        if target["cloud"] == "gcp":
            def metadata(path):
                req = urllib.request.Request("http://169.254.169.254/computeMetadata/v1/" + path,
                                             headers={"Metadata-Flavor": "Google"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.read().decode()
            if metadata("instance/name") != target["vm_name"] or metadata("project/project-id") != target["project"]:
                raise ValueError("요청 대상과 실제 GCP VM이 다르다")
        else:
            req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                         headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
            with urllib.request.urlopen(req, timeout=10) as response:
                token = response.read().decode()
            req = urllib.request.Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
                                         headers={"X-aws-ec2-metadata-token": token})
            with urllib.request.urlopen(req, timeout=10) as response:
                identity = json.load(response)
            if (identity.get("instanceId") != target["instance_id"] or identity.get("region") != target["region"]
                    or identity.get("accountId") != target["account_id"]
                    or identity.get("privateIp") != target["private_ip"]):
                raise ValueError("요청 대상과 실제 AWS VM이 다르다")

    def prepare(self, target, *, registry=True):
        if self.prepared:
            return
        self.verify_host(target)
        hosts = {ref.split("/", 1)[0] for ref in self.bundle["images"].values()}
        if target.get("sql_proxy_image"):
            hosts.add(target["sql_proxy_image"].split("/", 1)[0])
        allowed = set(target.get("registry_hosts", []))
        if not allowed:
            if target["cloud"] == "gcp":
                allowed = {target["region"] + "-docker.pkg.dev"}
            else:
                allowed = {target["account_id"] + ".dkr.ecr." + target["region"] + ".amazonaws.com"}
        if not hosts <= allowed:
            raise ValueError("인벤토리 registry_hosts 밖의 레지스트리다")
        if target["cloud"] == "gcp":
            reference = target["db_secret_ref"]
            if not reference.startswith("projects/"):
                reference = "projects/" + target["project"] + "/secrets/" + reference + "/versions/latest"
            elif "/versions/" not in reference:
                reference += "/versions/latest"
            if not re.fullmatch(r"projects/[A-Za-z0-9_-]+/secrets/[A-Za-z0-9_-]+/versions/(latest|[0-9]+)", reference):
                raise ValueError("GCP Secret Manager 참조가 올바르지 않다")
            token = self.metadata_token()
            req = urllib.request.Request("https://secretmanager.googleapis.com/v1/" + reference + ":access",
                                         headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=30) as response:
                secret = json.loads(base64.b64decode(json.load(response)["payload"]["data"]))
        else:
            if not re.fullmatch(r"arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+", target["db_secret_ref"]):
                raise ValueError("AWS Secrets Manager ARN이 필요하다")
            secret = json.loads(self.run(["aws", "secretsmanager", "get-secret-value", "--region", target["region"],
                                         "--secret-id", target["db_secret_ref"], "--query", "SecretString", "--output", "text"]).stdout)
        if secret.get("username") != target["db_user"] or not isinstance(secret.get("password"), str) or not secret["password"]:
            raise ValueError("앱 DB 자격 식별자가 다르다")
        path = Path(_secret_path(target))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            stat = path.stat()
            self.secret_permissions = (stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
            self.secret_original = path.read_text()
        self.secret_touched = True
        # 기존 mount의 inode를 유지한다. 비밀은 요청·Compose·일반 로그에 넣지 않는다.
        if not path.exists() or path.read_text() != secret["password"]:
            with path.open("w") as output:
                path.chmod(0o400)
                output.write(secret["password"])
        os.chown(path, target.get("db_secret_uid", 1000), target.get("db_secret_gid", 1000))
        for host in sorted(hosts) if registry else []:
            if host.endswith("-docker.pkg.dev"):
                password, username = self.metadata_token(), "oauth2accesstoken"
            elif re.fullmatch(r"[0-9]{12}[.]dkr[.]ecr[.][a-z0-9-]+[.]amazonaws[.]com", host):
                password, username = self.run(["aws", "ecr", "get-login-password", "--region", target["region"]]).stdout.strip(), "AWS"
            elif host == "gcr.io" and target.get("sql_proxy_image", "").startswith("gcr.io/cloud-sql-connectors/cloud-sql-proxy@") and all(not ref.startswith("gcr.io/") for ref in self.bundle["images"].values()):
                continue  # 허용된 공개 인프라 Proxy만 익명 pull한다.
            else:
                raise ValueError("단기 토큰을 지원하지 않는 레지스트리다")
            self.run(["docker", "login", "--username", username, "--password-stdin", host], input=password + "\n")
        self.prepared = True

    def __call__(self, target):
        self.prepare(target)
        # 기존 앱이 중지돼 있어도 DB 사전 확인은 기존 psql/DB 경로로 독립 수행한다.
        env = {**os.environ, "PGHOST": target.get("db_preflight_host", target["db_host"]), "PGPORT": str(target.get("db_preflight_port", target.get("db_port", 5432))),
               "PGDATABASE": target["db_name"], "PGUSER": target["db_user"],
               "PGPASSWORD": Path(_secret_path(target)).read_text(), "PGCONNECT_TIMEOUT": "10",
               "PGSSLMODE": target.get("db_preflight_sslmode", target.get("db_sslmode", "disable" if target["cloud"] == "gcp" else "verify-full")),
               "PGOPTIONS": "-c default_transaction_read_only=on"}
        if target.get("db_ca_file"):
            env["PGSSLROOTCERT"] = target["db_ca_file"]
        db = subprocess.run(["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", DB_SQL],
                            env=env, capture_output=True, text=True, check=True, timeout=25)
        diagnostic = json.loads(db.stdout)
        # 권한·네트워크·버전 실패는 위에서 예외로 닫힌다. 아래 true는 실제 명령 성공 뒤에만 설정한다.
        self.run(["docker", "info"])
        self.run(["docker", "compose", "version"])
        root = Path(target.get("install_dir", "/opt/delta-compose"))
        disk = shutil.disk_usage(root if root.exists() else root.parent)
        pre = {"disk_ok": disk.free >= 512 * 1024 * 1024, "docker_ok": True, "compose_ok": True,
               "registry_ok": self.prepared, "db": diagnostic}
        value = {"target_id": target["target_id"], "environment": "production", "preflight": pre,
                 "frontend": {"healthy": False}, "backend": {"healthy": False}}
        try:
            base = "http://" + target.get("bind_ip", target.get("private_ip", "127.0.0.1")) + ":" + str(target["frontend_port"])
            def http(path):
                req = urllib.request.Request(base + path, headers={"X-Request-Id": "delivery-" + self.bundle["bundle_sha256"][:24]})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)
            fe, be = http("/healthz"), http("/api/health")
            # BE 내부에서 앱과 같은 설정·비밀·TLS 연결로 SHOW와 스키마를 관측한다.
            js = ("import {readFileSync} from 'node:fs'; import pg from 'pg'; "
                  "import {postgresConfig} from '/app/lib/storage.mjs'; "
                  "const env={...process.env,PGPASSWORD:readFileSync('/run/secrets/db_password','utf8')};"
                  "const db=new pg.Client(postgresConfig(env)); await db.connect();"
                  "try{console.log(JSON.stringify((await db.query(" + json.dumps(DB_SQL) + ")).rows[0].json_build_object))}finally{await db.end()}")
            observed = self.run(["docker", "compose", "--project-name", target["compose_project"], "--file", self.compose_file,
                                 "exec", "-T", "backend", "node", "--input-type=module", "-e", js])
            fe["healthy"] = fe.get("status") == "ok"
            be["healthy"] = (be.get("status") == "ok" and bool(be.get("version"))
                             and be.get("databaseVersionNum") == json.loads(observed.stdout).get("server_version_num"))
            be["db"] = json.loads(observed.stdout)
            value.update(frontend=fe, backend=be)
        except Exception:
            pass  # preflight는 앱 중지를 허용한다. up 후에는 healthy=false가 반드시 실패한다.
        return value

    def snapshot_service(self, path):
        dropin = Path("/etc/systemd/system/delta-compose.service.d/delivery.conf")
        state = self.run(["systemctl", "show", "--property=LoadState,UnitFileState,ActiveState", "delta-compose.service"]).stdout
        if "LoadState=loaded" not in state:
            raise ValueError("기존 delta-compose systemd 서비스가 없다")
        self.service_snapshot = {"dropin": dropin.read_text() if dropin.exists() else None,
                                 "enabled": "UnitFileState=enabled" in state, "active": "ActiveState=active" in state}
        _write(path, self.service_snapshot)

    def activate_service(self, request, state_dir):
        # 기존 설치·설정은 보존하고 서비스의 실행 Compose만 신뢰된 새 파일로 연결한다.
        folder = Path(self.target.get("install_dir", "/opt/delta-compose")) / "delivery"
        request_path = folder / (request["deployment_id"] + ".request.json")
        _write(request_path, request)
        import shlex
        command = ["docker", "compose", "--project-name", self.target["compose_project"], "--file", str(self.compose_file)]
        dropin = Path("/etc/systemd/system/delta-compose.service.d/delivery.conf")
        dropin.parent.mkdir(parents=True, exist_ok=True)
        # 기존 ExecStartPre의 방화벽·준비 동작 뒤에 새 UID의 비밀 mount 준비를 추가한다.
        content = ("[Service]\nExecStart=\nExecStop=\nExecStartPre=" + shlex.join(["/usr/bin/python3", str(Path(__file__).resolve()), "prepare", "--request", str(request_path), "--state-dir", str(state_dir)])
                   + "\nExecStart=" + shlex.join([*command, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180"])
                   + "\nExecStop=" + shlex.join([*command, "stop"]) + "\n")
        dropin.write_text(content)
        dropin.chmod(0o644)
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "enable", "delta-compose.service"])
        if not self.service_snapshot["active"]:
            self.service_started = True
            self.run(["systemctl", "start", "delta-compose.service"])

    def deactivate_new_service(self):
        if self.service_started:
            self.run(["systemctl", "stop", "delta-compose.service"])

    def restore_service(self):
        if self.service_snapshot is not None:
            dropin = Path("/etc/systemd/system/delta-compose.service.d/delivery.conf")
            if self.service_snapshot["dropin"] is None:
                dropin.unlink(missing_ok=True)
            else:
                dropin.write_text(self.service_snapshot["dropin"])
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "enable" if self.service_snapshot["enabled"] else "disable", "delta-compose.service"])

    def restore_secret(self):
        if not self.secret_touched:
            return
        path = Path(_secret_path(self.target))
        if self.secret_permissions:
            mode, uid, gid = self.secret_permissions
            if path.read_text() != self.secret_original:
                path.write_text(self.secret_original)
            path.chmod(mode)
            os.chown(path, uid, gid)
        else:
            path.unlink(missing_ok=True)


def main(argv=None, *, runner=subprocess.run, probe=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "run", "deploy", "query", "prepare"))
    parser.add_argument("--request")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--deployment-id")
    args = parser.parse_args(argv)
    if args.action == "query":
        return query(args.deployment_id, Path(args.state_dir))
    if args.action == "prepare":
        request = json.loads(Path(args.request).read_text())
        validate_bundle(request["bundle"], request["commit_sha"])
        if request["config_sha256"] != canonical_hash(request["target"]):
            raise ValueError("부팅 준비 설정 해시가 다르다")
        HostProbe(request["target"], request["bundle"], None).prepare(request["target"], registry=False)
        return {"state": "prepared"}
    if args.action == "start":
        command = ["setsid", "--fork", sys.executable, str(Path(__file__).resolve()), "run",
                   "--request", args.request, "--state-dir", args.state_dir]
        value = runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if value.returncode:
            raise RuntimeError("원격 분리 worker 시작 실패")
        return {"state": "running", "deployment_id": json.loads(Path(args.request).read_text())["deployment_id"]}
    return deploy(json.loads(Path(args.request).read_text()), Path(args.state_dir), runner=runner, probe=probe)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception:
        print(json.dumps({"state": "unknown", "error": "호스트 작업 또는 상태 조회 실패"}))
        raise SystemExit(1)
