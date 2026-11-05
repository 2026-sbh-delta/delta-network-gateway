"""PostgreSQL provisioning on operator-approved servers; secrets never enter job parameters."""

import base64
import ipaddress
import json
import secrets
import tempfile
from pathlib import Path
from uuid import UUID

import boto3
import google.auth
import psycopg
from google.auth.transport.requests import AuthorizedSession
from psycopg import sql

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS delta_notes;
CREATE TABLE IF NOT EXISTS delta_notes.notes (
 position bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 id uuid NOT NULL UNIQUE,
 title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
 content text NOT NULL DEFAULT '' CHECK (length(content) <= 2000),
 created_at timestamptz NOT NULL
);
"""


def profile_for(data, config):
    choice = data.get("database")
    if not choice:
        return None
    profile = config.get("databases", {}).get(choice["profile_id"])
    if (
        not profile
        or profile["revision"] != choice["revision"]
        or choice["engine"] != "postgres"
        or choice["mode"] not in profile["modes"]
        or data["target"]["cloud"] not in profile["clouds"]
        or len(data["components"]) != 2
    ):
        raise ValueError("Database profile changed or unavailable")
    return profile


def connect(value, *, autocommit=False):
    # Always verify the server CA and hostname; caller closes before temp directory exits.
    temp = tempfile.TemporaryDirectory(prefix="delta-db-ca-")
    path = Path(temp.name) / "ca.pem"
    path.write_text(value["ca_pem"])
    path.chmod(0o600)
    try:
        conn = psycopg.connect(
            host=value["host"],
            port=int(value.get("port", 5432)),
            dbname=value["dbname"],
            user=value["username"],
            password=value["password"],
            sslmode="verify-full",
            sslrootcert=str(path),
            connect_timeout=10,
            autocommit=autocommit,
        )
    except (psycopg.Error, ValueError, KeyError):
        temp.cleanup()
        raise RuntimeError("PostgreSQL connection failed") from None
    return conn, temp


def prepare(data, profile, *, client=None):
    """Create only a dedicated logical DB/role; never modify an existing app's schema."""
    client = client or boto3.client("secretsmanager", region_name="ap-northeast-2")
    secret_key = (
        "admin_secret_id" if data["database"]["mode"] == "new" else "app_secret_id"
    )
    source = json.loads(
        client.get_secret_value(SecretId=profile[secret_key])["SecretString"]
    )
    environment = str(UUID(data["environment_id"]))
    name = "delta-platform/customer-db/" + environment
    tags = {
        "ManagedBy": "delta-platform",
        "Environment": environment,
        "ServiceId": data["service_id"],
        "DatabaseMode": data["database"]["mode"],
    }
    try:
        metadata = client.describe_secret(SecretId=name)
        if {t["Key"]: t["Value"] for t in metadata.get("Tags", [])} != tags:
            raise ValueError("Database secret ownership mismatch")
        value = json.loads(client.get_secret_value(SecretId=name)["SecretString"])
    except client.exceptions.ResourceNotFoundException:
        value = dict(source)
        if data["database"]["mode"] == "new":
            value.update(
                dbname="d_" + UUID(environment).hex,
                username="u_" + UUID(environment).hex,
                password=secrets.token_urlsafe(36),
            )
        client.create_secret(
            Name=name,
            SecretString=json.dumps(value),
            Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
        )
    if data["database"]["mode"] == "new":
        conn, ca = connect(source, autocommit=True)
        try:
            conn.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 11))", (environment,)
            )
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (value["username"],)
            ).fetchone():
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                    ).format(
                        sql.Identifier(value["username"]),
                        sql.Literal(value["password"]),
                    )
                )
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (value["dbname"],)
            ).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(
                        sql.Identifier(value["dbname"])
                    )
                )
            conn.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(value["dbname"])
                )
            )
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(value["dbname"]), sql.Identifier(value["username"])
                )
            )
        finally:
            conn.close()
            ca.cleanup()
        admin = {**source, "dbname": value["dbname"]}
        conn, ca = connect(admin)
        try:
            with conn:
                conn.execute(SCHEMA)
                conn.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA delta_notes TO {}").format(
                        sql.Identifier(value["username"])
                    )
                )
                conn.execute(
                    sql.SQL(
                        "GRANT SELECT, INSERT, DELETE ON TABLE delta_notes.notes TO {}"
                    ).format(sql.Identifier(value["username"]))
                )
                conn.execute(
                    sql.SQL(
                        "GRANT USAGE ON SEQUENCE delta_notes.notes_position_seq TO {}"
                    ).format(sql.Identifier(value["username"]))
                )
        finally:
            conn.close()
            ca.cleanup()
    conn, ca = connect(value)
    try:
        with conn:
            privileges = conn.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            if not privileges or any(privileges):
                raise ValueError(
                    "Application database role must not have administrative privileges"
                )
            conn.execute("SELECT id FROM delta_notes.notes LIMIT 0")
    finally:
        conn.close()
        ca.cleanup()
    return name, value


def deliver(data, selected, secret_id, value):
    cloud = data["target"]["cloud"]
    if cloud == "aws":
        # Instance role reads only platform-owned DB secrets; containers cannot use IMDS.
        return {"provider": "aws", "secret_id": secret_id}
    if cloud == "onprem":
        return {"provider": "kubernetes", "value": value}
    project = data["connection"]["gcp_project"]
    name = "delta-db-" + UUID(data["environment_id"]).hex
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    base = f"https://secretmanager.googleapis.com/v1/projects/{project}"
    resource = f"projects/{project}/secrets/{name}"
    labels = {
        "managed-by": "delta-platform",
        "environment": data["environment_id"],
        "service-id": data["service_id"],
    }
    response = session.get(f"{base}/secrets/{name}", timeout=20)
    if response.status_code == 404:
        response = session.post(
            f"{base}/secrets",
            params={"secretId": name},
            json={"replication": {"automatic": {}}, "labels": labels},
            timeout=20,
        )
    if (
        response.status_code not in (200, 201)
        or response.json().get("labels") != labels
    ):
        raise RuntimeError("GCP database secret ownership mismatch")
    # A dedicated secret is owned by this environment; avoid overwriting other IAM bindings.
    response = session.get(
        f"https://secretmanager.googleapis.com/v1/{resource}:getIamPolicy", timeout=20
    )
    response.raise_for_status()
    policy = response.json()
    member = "serviceAccount:" + selected["terraform"]["service_account"]
    bindings = policy.setdefault("bindings", [])
    if not any(
        b.get("role") == "roles/secretmanager.secretAccessor"
        and member in b.get("members", [])
        for b in bindings
    ):
        bindings.append(
            {"role": "roles/secretmanager.secretAccessor", "members": [member]}
        )
        response = session.post(
            f"https://secretmanager.googleapis.com/v1/{resource}:setIamPolicy",
            json={"policy": policy},
            timeout=20,
        )
        response.raise_for_status()
    response = session.post(
        f"https://secretmanager.googleapis.com/v1/{resource}:addVersion",
        json={
            "payload": {"data": base64.b64encode(json.dumps(value).encode()).decode()}
        },
        timeout=20,
    )
    response.raise_for_status()
    return {"provider": "gcp", "version": response.json()["name"]}


def kubernetes_secret(data, selected, value):
    name = "db-" + UUID(data["environment_id"]).hex
    env = environment(value)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name,
            "namespace": selected["namespace"],
            "labels": {
                "app.kubernetes.io/managed-by": "delta-platform",
                "delta-platform/environment-id": data["environment_id"],
                "delta-platform/service-id": data["service_id"],
            },
            "annotations": {"delta-platform/plan-hash": data["plan_hash"]},
        },
        "stringData": {**env, "ca.pem": value["ca_pem"]},
    }


def environment(value):
    result = {
        "STORAGE_BACKEND": "postgres",
        "PGHOST": value["host"],
        "PGPORT": str(value.get("port", 5432)),
        "PGDATABASE": value["dbname"],
        "PGUSER": value["username"],
        "PGPASSWORD": value["password"],
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "/run/delta-db/ca.pem",
    }
    if any(
        not isinstance(v, str) or any(c in v for c in "\r\n\x00")
        for v in result.values()
    ):
        raise ValueError("Invalid PostgreSQL configuration")
    return result


def egress(profile):
    # Only the approved DB addresses are allowed through the application policy.
    return {
        "to": [
            {"ipBlock": {"cidr": str(ipaddress.ip_network(c))}}
            for c in profile["egress_cidrs"]
        ],
        "ports": [{"protocol": "TCP", "port": int(profile.get("port", 5432))}],
    }
