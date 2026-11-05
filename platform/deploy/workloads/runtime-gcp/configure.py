"""Bind existing GCP networking to the dedicated AWS deployment worker.

Does not enable user-facing deployment until connectivity has been verified.
Terraform outputs contain identity configuration, never service-account keys.
"""

import argparse
import json
from pathlib import Path

import boto3


def merge_agent(agent, outputs):
    result = json.loads(json.dumps(agent))
    connection = json.loads(json.dumps(outputs["connection"]))
    connection["management_transport"] = "iap"
    if (
        connection["identity"]["gcp_project"] != "term2-delta"
        or not connection["terraform"]["subnetwork"].endswith(
            "/subnetworks/delta-lab-app-backend"
        )
        or outputs["credentials"].get("type") != "external_account"
    ):
        raise ValueError("Unexpected deployment project, subnet or credential type")
    result["gcp_credentials"] = outputs["credentials"]
    result["deployment"]["connections"]["gcp"] = connection
    result["deployment"]["databases"]["gcp-postgres-prepared"] = {
        "revision": "v1",
        "clouds": ["gcp"],
        "modes": ["new"],
        "server_mode": "prepared",
        "secret_provider": "gcp",
        "admin_secret_id": "projects/term2-delta/secrets/delta-platform-demo-target-admin",
        "connection": {"hostaddr": outputs["database_address"]},
        "iap_endpoint": {
            "address": outputs["database_address"],
            "port": 5432,
        },
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True, type=Path)
    parser.add_argument("--profile", default="delta-platform")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name="ap-northeast-2")
    if session.client("sts").get_caller_identity()["Account"] != "616723161368":
        raise ValueError("Unexpected AWS account")
    ssm = session.client("ssm")
    outputs = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    name = "/delta-platform/deployment/agent"
    original = json.loads(
        ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    )
    merged = merge_agent(original, outputs)
    value = json.dumps(merged)
    if len(value.encode()) > 8192:
        raise ValueError("Deployment agent configuration exceeds SSM limit")
    if args.apply:
        ssm.put_parameter(
            Name=name, Value=value, Type="SecureString", Tier="Advanced", Overwrite=True
        )
    print(
        json.dumps(
            {
                "applied": args.apply,
                "cloud": "gcp",
                "subnet": merged["deployment"]["connections"]["gcp"]["terraform"][
                    "subnetwork"
                ],
                "database": "10.32.3.2",
                "user_facing_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
