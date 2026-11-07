"""Contract-level tests through the real API/normalizer/mapper, without cloud writes or an LLM bill."""

import pytest
from test_api import HEADERS, FakeReader, _body, _client


def resource(type_, **attributes):
    return {"mode": "managed", "type": type_, "instances": [{"attributes": attributes}]}


def state(*resources):
    return {"version": 4, "resources": list(resources)}


def request(tmp_path, snapshot, **overrides):
    return _client(tmp_path, FakeReader(snapshot)).post(
        "/v1/diffs", json=_body(**overrides), headers=HEADERS
    )


LB = resource(
    "aws_lb", name="web", arn="lb-arn", load_balancer_type="application", internal=False
)
LISTENER = resource(
    "aws_lb_listener", load_balancer_arn="lb-arn", protocol="HTTPS", port=443
)
DB = resource(
    "aws_db_instance",
    identifier="notes",
    engine="postgres",
    engine_version="16.4",
    allocated_storage=20,
    multi_az=True,
    password="SECRET-DB-PASSWORD",
    username="SECRET-USER",
)
YAML = """
apiVersion: v1
kind: Secret
metadata: {name: db-password}
stringData: {password: SECRET-YAML-PASSWORD}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: notes, namespace: delta-notes}
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          volumeMounts: [{name: data, mountPath: /var/lib/postgresql/data}]
          env: [{name: PASSWORD, value: SECRET-ENV-VALUE}]
          resources: {requests: {cpu: "250m", memory: "256Mi"}}
  volumeClaimTemplates:
    - metadata: {name: data}
      spec:
        storageClassName: local-path
        accessModes: [ReadWriteOnce]
        resources: {requests: {storage: 20Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: postgres, namespace: delta-notes}
spec:
  ports: [{port: 5432}]
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: web, namespace: delta-notes}
spec:
  tls: [{hosts: [notes.example], secretName: tls}]
  rules: []
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: default-deny, namespace: delta-notes}
spec: {podSelector: {}, policyTypes: [Ingress, Egress]}
"""


def rows(response):
    assert response.status_code == 200, response.text
    return response.json()["assessment"]["mapping"]["rows"]


def test_aws_lb_and_postgres_only_state_is_mappable_and_never_returns_secrets(tmp_path):
    result = request(tmp_path, state(LB, LISTENER, DB))
    mapped = rows(result)
    assert (
        next(r for r in mapped if r["resource"] == "load_balancer")["target"]
        == "Application Load Balancer"
    )
    database = next(r for r in mapped if r["resource"] == "database")
    assert "16.4" in database["source"]
    assert database["target"] == "Cloud SQL for PostgreSQL"
    assert "SECRET" not in result.text
    assert result.json()["mapping_input"]["databases"][0]["storage_gib"] == 20


def test_lb_without_listener_is_unknown(tmp_path):
    mapped = rows(request(tmp_path, state(LB)))
    assert mapped[0]["grade"] == "unknown"


def test_cloud_sql_and_gcp_lb_state_are_mapped_in_reverse(tmp_path):
    snapshot = state(
        resource(
            "google_sql_database_instance",
            name="notes",
            database_version="POSTGRES_16",
            settings=[{"disk_size": 30, "availability_type": "REGIONAL"}],
        ),
        resource("google_compute_target_https_proxy", self_link="https-proxy"),
        resource(
            "google_compute_global_forwarding_rule",
            name="public",
            target="https-proxy",
            port_range="443",
            load_balancing_scheme="EXTERNAL_MANAGED",
        ),
    )
    result = request(
        tmp_path,
        snapshot,
        source={
            "provider": "gcp",
            "region": "asia-northeast3",
            "state": {"bucket": "tf-bucket", "key": "delta/lab/gcp.tfstate"},
        },
        target={"provider": "aws", "region": "ap-northeast-2"},
    )
    mapped = rows(result)
    assert any(r["target"] == "RDS PostgreSQL" for r in mapped)
    assert any(r["target"] == "ALB" for r in mapped)
    assert "16" == result.json()["mapping_input"]["databases"][0]["version"]


def test_k3s_source_yaml_maps_all_requested_kinds_and_does_not_read_s3(tmp_path):
    reader = FakeReader(None)
    result = _client(tmp_path, reader).post(
        "/v1/diffs",
        headers=HEADERS,
        json=_body(
            source={"provider": "onprem", "manifests": YAML}, target={"provider": "gcp"}
        ),
    )
    mapped = rows(result)
    assert {"workload", "storage", "database", "load_balancer", "security"} <= {
        r["resource"] for r in mapped
    }
    assert reader.reads == []
    assert result.json()["source"]["kind"] == "kubernetes_yaml"
    assert len(result.json()["source"]["sha256"]) == 64
    assert "SECRET" not in result.text
    assert "stringData" not in result.text
    assert "env" not in result.json()["mapping_input"]["kubernetes"]["workloads"][0]


@pytest.mark.parametrize(
    "manifest,expected",
    [
        (YAML, "gap"),  # Managed multi-AZ cannot become a single PostgreSQL pod.
        (YAML.replace("postgres:16-", "postgres:15-"), "gap"),
        (YAML.replace("20Gi", "2Gi"), "gap"),
        (None, "unknown"),
    ],
)
def test_aws_to_k3s_checks_database_version_storage_and_ha(
    tmp_path, manifest, expected
):
    target = {"provider": "onprem"}
    if manifest is not None:
        target["manifests"] = manifest
    mapped = rows(request(tmp_path, state(DB), target=target))
    assert next(r for r in mapped if r["resource"] == "database")["grade"] == expected


def test_aurora_is_a_gap_not_generic_postgresql(tmp_path):
    snapshot = state(
        resource(
            "aws_rds_cluster",
            cluster_identifier="db",
            engine="aurora-postgresql",
            engine_version="16.4",
        )
    )
    assert (
        next(
            r for r in rows(request(tmp_path, snapshot)) if r["resource"] == "database"
        )["grade"]
        == "gap"
    )


@pytest.mark.parametrize(
    "yaml_text",
    [
        "kind: Secret\nmetadata: {name: password}\nstringData: {x: SECRET}",
        "kind: Pod\nkind: Deployment\nmetadata: {name: SECRET}",
        "kind: Pod\nmetadata: &x {name: SECRET}\nspec: *x",
        '!!python/object/apply:os.system ["echo SECRET"]',
        "kind: Pod\nmetadata: {name: SECRET}\nspec: {containers: 1}",
    ],
)
def test_invalid_or_unmappable_yaml_is_safe_422(tmp_path, yaml_text):
    result = request(
        tmp_path, None, source={"provider": "onprem", "manifests": yaml_text}
    )
    assert result.status_code == 422
    assert "SECRET" not in result.text


def test_invalid_request_never_echoes_yaml_secret(tmp_path):
    result = request(tmp_path, None, source={"provider": "aws", "manifests": YAML})
    assert result.status_code == 422
    assert "SECRET" not in result.text


def test_chunked_body_cannot_bypass_size_limit(tmp_path):
    def chunks():
        yield b'{"source":"'
        yield b"x" * 65_000
        yield b'"}'

    result = _client(tmp_path, FakeReader()).post(
        "/v1/diffs", content=chunks(), headers=HEADERS
    )
    assert result.status_code == 413


def test_postgres_pvc_must_actually_be_mounted(tmp_path):
    manifest = YAML.replace(
        "          volumeMounts: [{name: data, mountPath: /var/lib/postgresql/data}]\n",
        "",
    )
    result = request(
        tmp_path, None, source={"provider": "onprem", "manifests": manifest}
    )
    rows(result)
    assert result.json()["mapping_input"]["databases"][0]["storage_gib"] is None


def test_digest_only_postgres_image_does_not_invent_a_version(tmp_path):
    result = request(
        tmp_path,
        None,
        source={
            "provider": "onprem",
            "manifests": YAML.replace(
                "postgres:16-alpine", "postgres@sha256:" + "1" * 64
            ),
        },
    )
    rows(result)
    assert result.json()["mapping_input"]["databases"][0]["version"] is None


def test_pvc_after_deployment_is_resolved_without_manifest_order_dependency(tmp_path):
    manifest = """apiVersion: apps/v1
kind: Deployment
metadata: {name: db}
spec:
  template:
    spec:
      containers:
      - name: postgres
        image: postgres:16
        volumeMounts: [{name: storage, mountPath: /var/lib/postgresql/data}]
      volumes: [{name: storage, persistentVolumeClaim: {claimName: pg-data}}]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: pg-data}
spec:
  resources: {requests: {storage: 5Gi}}
"""
    result = request(
        tmp_path, None, source={"provider": "onprem", "manifests": manifest}
    )
    rows(result)
    assert result.json()["mapping_input"]["databases"][0]["storage_gib"] == 5


def test_empty_kubernetes_inventory_cannot_produce_a_pass():
    from delta_ai.contract import MappingInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MappingInput.model_validate(
            {"source_provider": "onprem", "target_provider": "gcp", "kubernetes": {}}
        )
