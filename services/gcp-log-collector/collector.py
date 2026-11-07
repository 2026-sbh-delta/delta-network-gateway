"""Pull GCP LogEntry messages and ack only records accepted by CloudWatch."""
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

MAX_EVENT_BYTES = 900 * 1024
MAX_BATCH_BYTES = 1000 * 1024


def convert(message, project_id, now_ms):
    entry = json.loads(base64.b64decode(message["message"]["data"], validate=True))
    if not isinstance(entry, dict) or not str(entry.get("logName", "")).startswith(f"projects/{project_id}/logs/"):
        raise ValueError("Unexpected source project or LogEntry format")
    log_name = entry["logName"]
    insert_id = entry.get("insertId") or message["message"]["messageId"]
    event_id = hashlib.sha256(f"{project_id}\n{log_name}\n{insert_id}".encode()).hexdigest()
    record = {
        "cloud": "gcp", "project_id": project_id,
        "log_name": log_name, "insert_id": insert_id, "event_id": event_id,
        "source_timestamp": entry.get("timestamp"),
        "published_at": message["message"].get("publishTime"),
        "collected_at": datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
        "original": entry,
    }
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    size = len(text.encode("utf-8")) + 26
    if size > MAX_EVENT_BYTES:
        raise ValueError("Oversized entry retained in Pub/Sub for dead lettering")
    # Preserve source time in JSON; ingestion time avoids age-related CW rejection.
    return {"timestamp": now_ms, "message": text}, size


def process_messages(messages, pubsub, writer, project_id, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    stats = {"pulled": len(messages), "stored": 0, "acked": 0, "failed": 0}
    batches, current, size = [], [], 0
    for message in messages:
        try:
            event, event_size = convert(message, project_id, now_ms)
            ack_id = message["ackId"]
        except (ValueError, KeyError, TypeError, UnicodeError):
            stats["failed"] += 1
            continue
        if current and size + event_size > MAX_BATCH_BYTES:
            batches.append(current)
            current, size = [], 0
        current.append((ack_id, event))
        size += event_size
    if current:
        batches.append(current)
    for batch in batches:
        try:
            rejected = writer.put([item[1] for item in batch])
        except Exception:
            stats["failed"] += len(batch)
            continue
        accepted = [ack_id for index, (ack_id, _) in enumerate(batch) if index not in rejected]
        stats["stored"] += len(accepted)
        stats["failed"] += len(rejected)
        if accepted:
            try:
                pubsub.ack(accepted)
                stats["acked"] += len(accepted)
            except Exception:
                # Already stored entries can redeliver; event_id remains stable.
                stats["failed"] += len(accepted)
    return stats


def drain_messages(pubsub, writer, project_id, remaining_ms):
    stats = {"pulled": 0, "stored": 0, "acked": 0, "failed": 0}
    intake_deadline = time.monotonic() + 10
    for index in range(10):
        # A pull can return fewer than maxMessages despite a nonempty backlog.
        # Leave room for its 30s long poll and the final write/ack operations.
        if index and (time.monotonic() >= intake_deadline or remaining_ms() < 40000):
            break
        messages = pubsub.pull()
        if not messages:
            break
        batch = process_messages(messages, pubsub, writer, project_id)
        for key in stats:
            stats[key] += batch[key]
        if batch["failed"]:
            break
    return stats


class CloudWatchWriter:
    def __init__(self, client, group, stream):
        self.client, self.group, self.stream = client, group, stream

    def put(self, events):
        try:
            self.client.create_log_stream(logGroupName=self.group, logStreamName=self.stream)
        except self.client.exceptions.ResourceAlreadyExistsException:
            pass
        response = self.client.put_log_events(logGroupName=self.group, logStreamName=self.stream, logEvents=events)
        info = response.get("rejectedLogEventsInfo", {})
        rejected = set()
        for key in ("tooOldLogEventEndIndex", "expiredLogEventEndIndex"):
            if key in info:
                rejected.update(range(min(info[key] + 1, len(events))))
        if "tooNewLogEventStartIndex" in info:
            rejected.update(range(info["tooNewLogEventStartIndex"], len(events)))
        return rejected


class RoutedCloudWatchWriter:
    """Keep failures local to a destination and never fall back for unknown logs."""

    def __init__(self, client, routes, stream):
        if not isinstance(routes, dict) or not routes or not all(
            isinstance(name, str) and name.startswith("projects/term2-delta/logs/")
            and isinstance(group, str) and group.startswith("/delta/security/")
            for name, group in routes.items()
        ):
            raise ValueError("Invalid source-to-group mapping")
        self.client, self.routes, self.stream = client, routes, stream

    def put(self, events):
        destinations, rejected = {}, set()
        for index, event in enumerate(events):
            name = json.loads(event["message"])["log_name"]
            group = self.routes.get(name)
            if group is None:
                rejected.add(index)
            else:
                destinations.setdefault(group, []).append((index, event))
        for group, items in destinations.items():
            try:
                failed = CloudWatchWriter(self.client, group, self.stream).put([event for _, event in items])
            except Exception:
                rejected.update(index for index, _ in items)
            else:
                rejected.update(items[index][0] for index in failed)
        return rejected


class PubSub:
    def __init__(self, session, subscription):
        if not re.fullmatch(r"projects/term2-delta/subscriptions/[A-Za-z][A-Za-z0-9._~+%-]{2,254}", subscription):
            raise ValueError("Unexpected subscription path")
        self.session = session
        self.url = "https://pubsub.googleapis.com/v1/" + subscription

    def pull(self):
        response = self.session.post(self.url + ":pull", json={"maxMessages": 100}, timeout=(3, 30))
        response.raise_for_status()
        return response.json().get("receivedMessages", [])

    def ack(self, ids):
        response = self.session.post(self.url + ":acknowledge", json={"ackIds": ids}, timeout=(3, 5))
        response.raise_for_status()


def runtime():
    import boto3
    from botocore.config import Config
    from google.auth import aws
    from google.auth.transport.requests import AuthorizedSession
    audience = os.environ["WIF_AUDIENCE"]
    if not re.fullmatch(r"//iam.googleapis.com/projects/[0-9]+/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/aws-collector", audience):
        raise ValueError("Unexpected WIF audience")
    credentials = aws.Credentials.from_info({
        "type": "external_account", "audience": audience,
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "credential_source": {
            "environment_id": "aws1",
            "region_url": "http://169.254.169.254/latest/meta-data/placement/availability-zone",
            "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials",
            "regional_cred_verification_url": "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
        },
    }, scopes=["https://www.googleapis.com/auth/pubsub"])
    session = AuthorizedSession(credentials, refresh_timeout=5, max_refresh_attempts=1)
    pubsub = PubSub(session, os.environ["PUBSUB_SUBSCRIPTION"])
    logs = boto3.client("logs", config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"}))
    stream = os.environ.get("DESTINATION_LOG_STREAM", "gcp-network-security")
    routes = os.environ.get("DESTINATION_LOG_GROUPS")
    writer = RoutedCloudWatchWriter(logs, json.loads(routes), stream) if routes is not None else CloudWatchWriter(logs, os.environ["DESTINATION_LOG_GROUP"], stream)
    return pubsub, writer, session


def handler(event, context):
    session = None
    try:
        pubsub, writer, session = runtime()
        stats = drain_messages(pubsub, writer, "term2-delta", context.get_remaining_time_in_millis)
        print(json.dumps({"type": "ingest_summary", **stats}))
        if stats["failed"]:
            raise RuntimeError("Some messages remain unacknowledged; inspect DLQ and ingest counters")
        return stats
    finally:
        if session is not None:
            session.close()
