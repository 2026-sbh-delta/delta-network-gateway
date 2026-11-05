"""Copy CloudWatch subscription envelopes without losing source identity."""

import base64
import gzip
import hashlib
import io
import json
import os
import time

from collector import MAX_BATCH_BYTES, MAX_EVENT_BYTES, CloudWatchWriter

MAX_ENVELOPE_BYTES = 8 * 1024 * 1024


# 플랫폼 자신의 로그(중앙 그룹·플랫폼 서비스 그룹)는 다시 받지 않는다. 예전 이름도 함께 막는다.
PLATFORM_GROUP_PREFIXES = ("/delta-cp/", "/delta-platform/central/")


def decode(record, allowed_accounts, stream_arn, collector_prefix):
    if record.get("eventSourceARN") != stream_arn or record.get("eventSource") != "aws:kinesis":
        raise ValueError("Unexpected source stream")
    compressed = base64.b64decode(record["kinesis"]["data"], validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
        raw = source.read(MAX_ENVELOPE_BYTES + 1)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("Oversized envelope")
    envelope = json.loads(raw)
    if envelope.get("messageType") == "CONTROL_MESSAGE":
        return None
    if (
        envelope.get("messageType") != "DATA_MESSAGE"
        or envelope.get("owner") not in allowed_accounts
    ):
        raise ValueError("Unexpected message type or source account")
    group = envelope["logGroup"]
    if group.startswith(PLATFORM_GROUP_PREFIXES) or group.startswith(
        f"/aws/lambda/{collector_prefix}"
    ):
        # Stop accidental recursion. The sender must also exclude these groups.
        return None
    if not isinstance(envelope["logEvents"], list):
        raise ValueError("Invalid event list")
    return envelope


def batches(envelope, region, now_ms):
    events, size = [], 0
    for original in envelope["logEvents"]:
        identity = "\n".join(
            [envelope["owner"], region, envelope["logGroup"], envelope["logStream"], original["id"]]
        )
        text = json.dumps(
            {
                "cloud": "aws",
                "account_id": envelope["owner"],
                "region": region,
                "log_group": envelope["logGroup"],
                "log_stream": envelope["logStream"],
                "event_id": hashlib.sha256(identity.encode()).hexdigest(),
                "source_timestamp": original["timestamp"],
                "collected_at_ms": now_ms,
                "message": original["message"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        event_size = len(text.encode("utf-8")) + 26
        if event_size > MAX_EVENT_BYTES:
            raise ValueError("Oversized event; retain envelope in failure destination")
        if events and (size + event_size > MAX_BATCH_BYTES or len(events) >= 10000):
            yield events
            events, size = [], 0
        events.append({"timestamp": now_ms, "message": text})
        size += event_size
    if events:
        yield events


def process(records, client, group, allowed_accounts, stream_arn, collector_prefix, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    failures, stored, skipped = [], 0, 0
    for record in records:
        # Missing Kinesis metadata is an invalid invocation, not an acknowledged record.
        sequence = record["kinesis"]["sequenceNumber"]
        try:
            envelope = decode(record, allowed_accounts, stream_arn, collector_prefix)
            if envelope is None:
                skipped += 1
                continue
            region = stream_arn.split(":")[3]
            source = "\n".join(
                [envelope["owner"], region, envelope["logGroup"], envelope["logStream"]]
            )
            stream = f"{envelope['owner']}/{hashlib.sha256(source.encode()).hexdigest()}"
            writer = CloudWatchWriter(client, group, stream)
            for batch in batches(envelope, region, now_ms):
                if writer.put(batch):
                    raise RuntimeError("CloudWatch rejected records")
                stored += len(batch)
        except Exception as error:
            # Never print original customer content or exception messages.
            print(json.dumps({"type": "ingest_failure", "error_type": type(error).__name__}))
            failures.append({"itemIdentifier": sequence})
            break
    print(
        json.dumps(
            {
                "type": "ingest_summary",
                "stored": stored,
                "skipped": skipped,
                "failed": len(failures),
            }
        )
    )
    return {"batchItemFailures": failures}


def handler(event, context):
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "logs",
        config=Config(
            connect_timeout=3, read_timeout=5, retries={"max_attempts": 2, "mode": "standard"}
        ),
    )
    return process(
        event["Records"],
        client,
        os.environ["DESTINATION_LOG_GROUP"],
        set(os.environ["ALLOWED_SOURCE_ACCOUNTS"].split(",")),
        os.environ["SOURCE_STREAM_ARN"],
        os.environ["COLLECTOR_NAME_PREFIX"],
    )
