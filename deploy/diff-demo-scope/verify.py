"""Read-only Diff API acceptance; no app deployment, pipeline or DNS call."""

import json
import os
import sys
import urllib.error
import urllib.request

port = int(sys.argv[1])
body = {"diff_id": f"iam-lambda-scope-{port}", "service_id": "delta-sample-app",
        "source": {"provider": "aws", "region": "ap-northeast-2", "state": {
            "bucket": "sbh-delta-workload-tf-616723161368", "key": "delta/lab/workload-client/terraform.tfstate"}},
        "target": {"provider": "gcp", "region": "asia-northeast3"}}


def post(data, authorized=True):
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = "Bearer " + os.environ["DELTA_DIFF_TOKEN"]
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/diffs", json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=80) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        return e.code, None


assert post(body, False)[0] == 401
other = json.loads(json.dumps(body))
other["source"]["state"]["key"] += ".other.tfstate"
assert post(other)[0] == 403
status, data = post(body)
assert status == 200
mapping = data["mapping_input"]
permissions = mapping["servers"][0]["permissions"]
assert set(permissions) == {"s3:GetObject", "secretsmanager:GetSecretValue", "logs:CreateLogStream",
                            "logs:DescribeLogStreams", "logs:PutLogEvents"}
assert set(data["source"]["excluded_capabilities"]) == {"LAMBDA_NETWORK_LOGS"}
assert "LAMBDA_NETWORK_LOGS" not in mapping["security"]["guardduty_plans"]
assert {"RDS_LOGIN_EVENTS", "S3_DATA_EVENTS"} <= set(mapping["security"]["guardduty_plans"])
assert not mapping["accepted_gaps"] and mapping["scc_tier"] is None
rows = data["assessment"]["mapping"]["rows"]
identity = [r for r in rows if r["resource"] == "identity"]
assert len(identity) == 1 and identity[0]["grade"] == "gap"
assert {"RDS_LOGIN_EVENTS", "S3_DATA_EVENTS"} <= {r["source"] for r in rows if r["grade"] == "gap"}
assert not any(r["source"] == "LAMBDA_NETWORK_LOGS" for r in rows)
subnets = [r["source"] for r in rows if r["resource"] == "subnet"]
assert len(subnets) == 8 and not any(s.endswith("-tgw") for s in subnets)
assert any(r["resource"] == "security" and r["grade"] == "unknown" for r in rows)
assert data["assessment"]["verdict"] == "needs_human" and data["assessment"]["meta"]["llm_used"]
print(json.dumps({"port": port, "http": status, "state_serial": data["source"]["serial"],
                  "permissions": permissions, "identity_grade": identity[0]["grade"], "subnets": len(subnets),
                  "excluded_capabilities": data["source"]["excluded_capabilities"], "rds_s3_checks_retained": True,
                  "scc_still_unknown": True, "verdict": data["assessment"]["verdict"], "llm_used": True}))
