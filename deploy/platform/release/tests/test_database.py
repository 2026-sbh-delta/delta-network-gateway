"""앱 DB 준비: 관리자 권한 없는 앱 역할 확인, SHOW server_version_num 관측, GCP 기록 비밀 어댑터."""

import base64
import json

import pytest

from release_support import ENVIRONMENT_ID, SERVICE_ID, load


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, log, version="180001", privileges=(False, False, False, False)):
        self.log, self.version, self.privileges = log, version, privileges

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else repr(query)
        self.log.append(text)
        if "rolsuper" in text:
            return Cursor(self.privileges)
        if text == "SHOW server_version_num":
            return Cursor((self.version,))
        return Cursor(None)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Temp:
    def cleanup(self):
        pass


class Store:
    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self, source):
        self.source, self.created = source, {}

    def describe_secret(self, SecretId):
        if SecretId not in self.created:
            raise self.exceptions.ResourceNotFoundException()
        return {"Tags": self.created[SecretId]["Tags"]}

    def get_secret_value(self, SecretId):
        if SecretId in self.created:
            return {"SecretString": self.created[SecretId]["SecretString"]}
        return {"SecretString": json.dumps(self.source)}

    def create_secret(self, Name, SecretString, Tags):
        self.created[Name] = {"SecretString": SecretString, "Tags": Tags}


def request():
    return {"environment_id": ENVIRONMENT_ID, "service_id": SERVICE_ID, "target": {"cloud": "aws"},
            "database": {"mode": "new"}}


def source():
    return {"host": "db.internal", "port": 5432, "dbname": "postgres", "username": "platform_admin",
            "password": "fixture-admin", "ca_pem": "CA"}


def test_prepare_returns_observed_postgres_version_from_app_connection():
    module = load("database")
    log, users = [], []

    def connector(value, **kwargs):
        users.append(value["username"])
        return Connection(log), Temp()

    store = Store(source())
    name, value, version = module.prepare(request(), {"admin_secret_id": "admin"}, client=store, connector=connector)
    assert version == 180001
    assert name == "delta-platform/customer-db/" + ENVIRONMENT_ID
    assert value["username"].startswith("u_") and users[-1] == value["username"]
    assert log[-1] == "SHOW server_version_num"
    # 재실행은 같은 앱 자격 증명을 재사용한다.
    again = module.prepare(request(), {"admin_secret_id": "admin"}, client=store, connector=connector)
    assert again[1] == value


def test_admin_privileged_app_role_is_rejected():
    module = load("database")

    def connector(value, **kwargs):
        return Connection([], privileges=(True, False, False, False)), Temp()

    with pytest.raises(ValueError, match="administrative"):
        module.prepare(request(), {"admin_secret_id": "admin"}, client=Store(source()), connector=connector)


class Response:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")


class Session:
    def __init__(self):
        self.secrets, self.versions = {}, {}

    def get(self, url, timeout):
        name = url.split("/secrets/")[1].split("/")[0]
        if name not in self.secrets:
            return Response(404)
        if url.endswith(":access"):
            return Response(200, {"payload": {"data": self.versions[name]}})
        return Response(200, {"labels": self.secrets[name]})

    def post(self, url, params=None, json=None, timeout=None):
        if params:
            self.secrets[params["secretId"]] = json["labels"]
            return Response(200)
        name = url.split("/secrets/")[1].split(":")[0]
        self.versions[name] = json["payload"]["data"]
        return Response(200)


def test_gcp_secret_store_round_trips_owner_tags_in_target_project():
    module = load("database")
    session = Session()
    store = module.GcpSecretStore("fixture-project", session)
    name = "delta-platform/customer-db/" + ENVIRONMENT_ID
    with pytest.raises(store.exceptions.ResourceNotFoundException):
        store.describe_secret(SecretId=name)
    tags = [{"Key": "ManagedBy", "Value": "delta-platform"}, {"Key": "Environment", "Value": ENVIRONMENT_ID},
            {"Key": "ServiceId", "Value": SERVICE_ID}, {"Key": "DatabaseMode", "Value": "new"}]
    store.create_secret(Name=name, SecretString='{"host":"x"}', Tags=tags)
    secret_id = "delta-platform-customer-db-" + ENVIRONMENT_ID.replace("-", "")
    assert set(session.secrets[secret_id]) == {"managed-by", "environment", "service-id", "database-mode"}
    described = {t["Key"]: t["Value"] for t in store.describe_secret(SecretId=name)["Tags"]}
    assert described == {t["Key"]: t["Value"] for t in tags}
    assert store.get_secret_value(SecretId=name)["SecretString"] == '{"host":"x"}'
    assert base64.b64decode(session.versions[secret_id]) == b'{"host":"x"}'
    with pytest.raises(ValueError):
        store.describe_secret(SecretId="other/secret")

