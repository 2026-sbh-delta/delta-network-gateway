"""VM Compose 문서·DB 비밀·실행 이미지 관측. 동규님 platform/backend/tests/test_applications.py의 VM 시험 이식 포함."""

import json

import pytest

from release_support import ENVIRONMENT_ID, load

IMAGE = "asia-northeast3-docker.pkg.dev/fixture/app/frontend@sha256:" + "a" * 64
BACKEND = "asia-northeast3-docker.pkg.dev/fixture/app/backend@sha256:" + "b" * 64


def payload(cloud="aws"):
    logging = ({"driver": "awslogs", "options": {"awslogs-region": "ap-northeast-2",
                                                  "awslogs-group": "/delta/workload/" + ENVIRONMENT_ID,
                                                  "awslogs-create-group": "true", "mode": "non-blocking",
                                                  "tag": "{{.Name}}"}}
               if cloud == "aws" else
               {"driver": "gcplogs", "options": {"gcp-project": "fixture-project", "mode": "non-blocking",
                                                  "gcp-log-cmd": "false"}})
    return {
        "environment_id": ENVIRONMENT_ID,
        "cloud": cloud,
        "components": [
            {"name": "frontend", "container_port": 3000, "healthcheck_path": "/healthz"},
            {"name": "backend", "container_port": 8080, "healthcheck_path": "/healthz"},
        ],
        "images": [IMAGE, BACKEND],
        "memory_mb": 1664,
        "logging": logging,
        "database_secret": {"provider": "aws", "secret_id": "private"},
    }


def test_compose_keeps_two_images_private_backend_and_secret_out_of_config():
    module = load("vm_runtime")
    compose = module.compose_document(payload())
    frontend, backend = compose["services"]["frontend"], compose["services"]["backend"]
    assert frontend["environment"]["API_HOST"] == "backend"
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
    assert backend["ports"] == ["127.0.0.1:8089:8080"]
    assert frontend["ports"] == ["8088:3000"]
    assert backend["env_file"][0]["format"] == "raw"
    assert "database.env" not in json.dumps(frontend)
    assert "private" not in json.dumps(compose)


@pytest.mark.parametrize("cloud,driver", [("aws", "awslogs"), ("gcp", "gcplogs")])
def test_cloud_log_driver_and_hardening_on_every_service(cloud, driver):
    module = load("vm_runtime")
    for service in module.compose_document(payload(cloud))["services"].values():
        assert service["logging"]["driver"] == driver
        assert service["logging"]["options"]["mode"] == "non-blocking"
        assert service["cap_drop"] == ["ALL"] and service["pids_limit"] == 128
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pull_policy"] == "never"
    if cloud == "aws":
        options = module.compose_document(payload(cloud))["services"]["backend"]["logging"]["options"]
        assert options["awslogs-group"] == "/delta/workload/" + ENVIRONMENT_ID
        assert options["awslogs-create-group"] == "true"


@pytest.mark.parametrize("change", [
    lambda p: p.pop("logging"),
    lambda p: p["logging"].update(driver="json-file"),
    lambda p: p["logging"]["options"].update(mode="blocking"),
    lambda p: p["logging"]["options"].update({"awslogs-group": "/other"}),
])
def test_local_or_misdirected_logging_is_refused(change):
    module = load("vm_runtime")
    value = payload()
    change(value)
    with pytest.raises(ValueError):
        module.compose_document(value)


def test_database_credentials_are_read_on_host_not_embedded_in_compose(tmp_path, monkeypatch):
    module = load("vm_runtime")
    seen = []
    value = {"host": "database.internal", "dbname": "app", "username": "app",
             "password": "secret$literal", "ca_pem": "TEST CA"}

    def command(args):
        seen.append(args)
        return json.dumps({"SecretString": json.dumps(value)})

    monkeypatch.setattr(module, "command", command)
    module.write_database_secret(tmp_path, {"provider": "aws", "secret_id": "owned-db"}, "ap-northeast-2")
    assert "secret$literal" not in json.dumps(seen)
    assert seen[0][seen[0].index("--region") + 1] == "ap-northeast-2"
    env = tmp_path / "database.env"
    assert env.stat().st_mode & 0o777 == 0o600
    assert "PGPASSWORD=secret$literal\n" in env.read_text()
    assert "STORAGE_BACKEND=postgres" in env.read_text()
    value["password"] = "invalid\nINJECTED=true"
    with pytest.raises(ValueError):
        module.write_database_secret(tmp_path, {"provider": "aws", "secret_id": "owned-db"})



def test_gcp_database_env_uses_private_ip_and_keeps_dns_name_for_tls(tmp_path, monkeypatch):
    """컨테이너는 메타데이터 DNS 를 못 쓴다: 호스트가 Cloud SQL 이름을 사설 IP 로 풀어 넘기고 TLS 는 이름으로 확인한다."""
    import base64 as b64
    import io
    module = load("vm_runtime")
    value = {"host": "abc.region.sql.goog.", "dbname": "d", "username": "u", "password": "pw", "ca_pem": "TEST CA"}
    payloads = iter([json.dumps({"access_token": "t"}),
                     json.dumps({"payload": {"data": b64.b64encode(json.dumps(value).encode()).decode()}})])

    class Response(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, timeout=10: Response(next(payloads)))
    monkeypatch.setattr(module.socket, "gethostbyname", lambda name: "10.32.2.6" if name == "abc.region.sql.goog" else None)
    module.write_database_secret(tmp_path, {"provider": "gcp", "version": "projects/p/secrets/s/versions/1"})
    text = (tmp_path / "database.env").read_text()
    assert "PGHOST=10.32.2.6\n" in text and "PGTLS_SERVERNAME=abc.region.sql.goog.\n" in text


def docker(images, *, running=True, config_image=None, image_id="sha256:" + "1" * 64, digests=None):
    """docker compose ps / container inspect / image inspect 표준 JSON을 합성한다."""
    def run(args):
        if args[:2] == ["docker", "compose"]:
            return "cid-" + args[-1] + "\n"
        if args[:3] == ["docker", "container", "inspect"]:
            name = args[3].removeprefix("cid-")
            return json.dumps([{"State": {"Running": running}, "Image": image_id,
                                "Config": {"Image": config_image or images[name]}}])
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": image_id, "RepoDigests": digests if digests is not None else [args[3]]}])
        raise AssertionError(args)
    return run


HEALTH = {"status": "ok", "storage": "postgres", "version": "c" * 40, "environment": "production",
          "cloudProvider": "aws", "databaseVersionNum": 180001, "extra": "dropped"}


def test_observe_reports_running_digest_images_and_health():
    module = load("vm_runtime")
    images = {"frontend": IMAGE, "backend": BACKEND}
    result = module.observe(payload(), run=docker(images), fetch=lambda url: HEALTH)
    assert result["images"] == images
    assert result["health"]["databaseVersionNum"] == 180001 and "extra" not in result["health"]


@pytest.mark.parametrize("kwargs", [
    {"running": False},
    {"config_image": "asia-northeast3-docker.pkg.dev/fixture/app/frontend@sha256:" + "e" * 64},
    {"digests": []},
])
def test_observe_rejects_stopped_or_different_image(kwargs):
    module = load("vm_runtime")
    with pytest.raises(RuntimeError):
        module.observe(payload(), run=docker({"frontend": IMAGE, "backend": BACKEND}, **kwargs),
                       fetch=lambda url: HEALTH)


def test_ecr_login_uses_registry_region_and_password_stdin(monkeypatch):
    module = load("vm_runtime")
    calls = []

    def command(args, body=None):
        calls.append((args, body))
        return "fixture-token" if args[:2] == ["aws", "ecr"] else ""

    monkeypatch.setattr(module, "command", command)
    module.registry_login("/tmp/cfg", "111122223333.dkr.ecr.us-west-2.amazonaws.com/app@sha256:" + "a" * 64, "aws")
    assert calls[0][0][-1] == "us-west-2"
    assert calls[1][1] == "fixture-token" and "fixture-token" not in " ".join(calls[1][0])
    with pytest.raises(ValueError):
        module.registry_login("/tmp/cfg", IMAGE, "aws")
