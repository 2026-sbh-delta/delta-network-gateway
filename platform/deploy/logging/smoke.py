"""Send harmless cloud-native log markers; verify the deployed receiver end to end."""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
import google.auth
from google.auth.transport.requests import AuthorizedSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--profile", default="delta-platform")
    args = parser.parse_args()
    outputs = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    destination = outputs["aws_destination_arn"]
    region, account = destination.split(":")[3:5]
    session = boto3.Session(profile_name=args.profile, region_name=region)
    if session.client("sts").get_caller_identity()["Account"] != account:
        raise RuntimeError(
            "Smoke test must use the receiver account; it never modifies teammate groups"
        )
    logs = session.client("logs")
    marker = f"central-logs-{uuid.uuid4()}"
    source = f"/delta-platform/logging-smoke/{marker}"
    started = int(time.time() * 1000)
    created = False
    result = {
        "marker": marker,
        "destination_arn": destination,
        "aws_source_account": account,
        "aws_source_group": source,
        "gcp_source_project": "term2-delta",
        "verified": {},
        "cross_account_sender_verified": False,
    }
    try:
        logs.create_log_group(logGroupName=source)
        created = True
        logs.put_retention_policy(logGroupName=source, retentionInDays=1)
        logs.create_log_stream(logGroupName=source, logStreamName="smoke")
        logs.put_subscription_filter(
            logGroupName=source,
            filterName="delta-platform-central",
            filterPattern="",
            destinationArn=destination,
            distribution="Random",
        )
        logs.put_log_events(
            logGroupName=source,
            logStreamName="smoke",
            logEvents=[
                {"timestamp": started, "message": json.dumps({"marker": marker, "type": "smoke"})}
            ],
        )
        print("AWS: wrote a marker to a temporary CloudWatch source group", flush=True)
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/logging.write"]
        )
        with AuthorizedSession(credentials) as google_session:
            response = google_session.post(
                "https://logging.googleapis.com/v2/entries:write",
                json={
                    "logName": "projects/term2-delta/logs/delta-platform-log-smoke",
                    "resource": {"type": "global", "labels": {"project_id": "term2-delta"}},
                    "entries": [
                        {
                            "insertId": marker,
                            "timestamp": datetime.now(UTC).isoformat(),
                            "severity": "NOTICE",
                            "jsonPayload": {"marker": marker, "type": "smoke"},
                        }
                    ],
                },
                timeout=20,
            )
            response.raise_for_status()
        print("GCP: wrote the same marker to Cloud Logging", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and len(result["verified"]) < 2:
            for cloud, group in outputs["central_log_groups"].items():
                if cloud in result["verified"]:
                    continue
                response = logs.filter_log_events(
                    logGroupName=group, startTime=started - 10000, filterPattern=f'"{marker}"'
                )
                events = response.get("events", [])
                if not events:
                    continue
                value = json.loads(events[0]["message"])
                if value["cloud"] != cloud:
                    raise AssertionError("Unexpected cloud identity")
                if cloud == "aws":
                    assert value["account_id"] == account and value["log_group"] == source
                    assert value["region"] == region and marker in value["message"]
                else:
                    assert value["project_id"] == "term2-delta"
                    assert value["original"]["jsonPayload"]["marker"] == marker
                result["verified"][cloud] = {
                    "log_group": group,
                    "event_id": value["event_id"],
                    "source_timestamp": value["source_timestamp"],
                    "received_at": datetime.now(UTC).isoformat(),
                }
                print(
                    f"{cloud}: original marker and source identity verified in {group}", flush=True
                )
            if len(result["verified"]) < 2:
                time.sleep(5)
        if len(result["verified"]) != 2:
            raise RuntimeError(f"Timed out; verified only: {list(result['verified'])}")
    finally:
        if created:
            logs.delete_log_group(logGroupName=source)
            result["temporary_aws_source_deleted"] = True
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        "Both cloud-native paths passed. Cross-account sender attachment remains a separate check."
    )


if __name__ == "__main__":
    main()
