import base64
import gzip
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

import aws_collector as ingest

STREAM = "arn:aws:kinesis:ap-northeast-2:111122223333:stream/test"
ACCOUNT = "111122223333"


def envelope(**changes):
    return {
        "messageType": "DATA_MESSAGE",
        "owner": ACCOUNT,
        "logGroup": "/service/test",
        "logStream": "app",
        "logEvents": [{"id": "source-1", "timestamp": 1, "message": "원본 로그"}],
        **changes,
    }


def record(body=None, sequence="100"):
    return {
        "eventSource": "aws:kinesis",
        "eventSourceARN": STREAM,
        "kinesis": {
            "sequenceNumber": sequence,
            "data": base64.b64encode(
                gzip.compress(json.dumps(body or envelope()).encode())
            ).decode(),
        },
    }


class AwsIngestTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.put_log_events.return_value = {}

    def process(self, records):
        return ingest.process(
            records, self.client, "central", {ACCOUNT}, STREAM, "delta-platform-logs", 1000
        )

    def test_preserves_original_identity_and_replay_id(self):
        self.assertEqual(self.process([record()]), {"batchItemFailures": []})
        saved = json.loads(self.client.put_log_events.call_args.kwargs["logEvents"][0]["message"])
        self.assertEqual(saved["message"], "원본 로그")
        self.assertEqual(saved["source_timestamp"], 1)
        self.assertEqual(saved["account_id"], ACCOUNT)
        self.assertEqual(saved["region"], "ap-northeast-2")
        later = next(ingest.batches(envelope(), "ap-northeast-2", 9999))[0]
        self.assertEqual(json.loads(later["message"])["event_id"], saved["event_id"])

    def test_untrusted_account_and_stream_are_not_accepted(self):
        wrong_stream = record()
        wrong_stream["eventSourceARN"] = STREAM + "-other"
        for item in (record(envelope(owner="999999999999")), wrong_stream):
            self.assertEqual(self.process([item])["batchItemFailures"], [{"itemIdentifier": "100"}])
        self.client.put_log_events.assert_not_called()

    def test_control_messages_and_recursive_groups_do_not_loop(self):
        for body in (
            envelope(messageType="CONTROL_MESSAGE"),
            envelope(logGroup="/delta-cp/central/aws"),
            envelope(logGroup="/delta-cp/platform/dast"),
            envelope(logGroup="/delta-platform/central/aws"),
            envelope(logGroup="/aws/lambda/delta-platform-logs-aws"),
        ):
            self.assertEqual(self.process([record(body)])["batchItemFailures"], [])
        self.client.put_log_events.assert_not_called()

    def test_cloudwatch_partial_rejection_is_retried(self):
        self.client.put_log_events.return_value = {
            "rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0}
        }
        self.assertEqual(self.process([record()])["batchItemFailures"], [{"itemIdentifier": "100"}])

    def test_write_failure_stops_at_failed_sequence(self):
        self.client.put_log_events.side_effect = OSError(
            "customer content must not appear in runtime logs"
        )
        with patch("builtins.print") as output:
            result = self.process([record(), record(sequence="200")])
        self.assertEqual(result["batchItemFailures"], [{"itemIdentifier": "100"}])
        self.assertEqual(self.client.put_log_events.call_count, 1)
        self.assertNotIn("customer content", str(output.call_args_list))

    def test_batch_byte_limit_counts_utf8(self):
        original = {"id": "large", "timestamp": 1, "message": "한" * 200000}
        chunks = list(
            ingest.batches(envelope(logEvents=[original, original]), "ap-northeast-2", 1000)
        )
        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(
                sum(len(e["message"].encode()) + 26 for e in chunk), ingest.MAX_BATCH_BYTES
            )

    def test_oversized_event_is_not_silently_truncated(self):
        original = {"id": "huge", "timestamp": 1, "message": "x" * ingest.MAX_EVENT_BYTES}
        self.assertEqual(
            self.process([record(envelope(logEvents=[original]))])["batchItemFailures"],
            [{"itemIdentifier": "100"}],
        )
        self.client.put_log_events.assert_not_called()

    def test_decompression_is_bounded(self):
        item = record()
        item["kinesis"]["data"] = base64.b64encode(gzip.compress(b"x" * 1025)).decode()
        with patch.object(ingest, "MAX_ENVELOPE_BYTES", 1024):
            self.assertEqual(self.process([item])["batchItemFailures"], [{"itemIdentifier": "100"}])

    def test_malformed_data_and_missing_metadata_cannot_ack(self):
        item = record()
        item["kinesis"]["data"] = "broken!"
        self.assertEqual(self.process([item])["batchItemFailures"], [{"itemIdentifier": "100"}])
        with self.assertRaises(KeyError):
            self.process([{}])

    def relay(self, group="/delta/security/gcp-firewall", **changes):
        name = ingest.GCP_RELAY_LOGS[group]
        value = {
            "cloud": "gcp",
            "project_id": "term2-delta",
            "log_name": name,
            "insert_id": "gcp-1",
            "source_timestamp": "2026-11-07T00:00:00Z",
            "event_id": hashlib.sha256(f"term2-delta\n{name}\ngcp-1".encode()).hexdigest(),
            "original": {
                "logName": name,
                "insertId": "gcp-1",
                "timestamp": "2026-11-07T00:00:00Z",
                "jsonPayload": {"marker": "synthetic"},
            },
            **changes,
        }
        body = envelope(
            owner=ingest.GCP_RELAY_ACCOUNT,
            logGroup=group,
            logEvents=[{"id": "cw-1", "timestamp": 1, "message": json.dumps(value)}],
        )
        return body, value

    def process_relay(self, body, destination="central-gcp"):
        return ingest.process(
            [record(body)],
            self.client,
            "central-aws",
            {ACCOUNT, ingest.GCP_RELAY_ACCOUNT},
            STREAM,
            "delta-platform-logs",
            1000,
            destination,
        )

    def test_two_gcp_routes_preserve_schema_and_stable_source_id(self):
        for group in ingest.GCP_RELAY_LOGS:
            body, original = self.relay(group)
            self.assertEqual(self.process_relay(body), {"batchItemFailures": []})
            call = self.client.put_log_events.call_args.kwargs
            self.assertEqual(call["logGroupName"], "central-gcp")
            saved = json.loads(call["logEvents"][0]["message"])
            self.assertEqual({key: saved[key] for key in original}, original)
            self.assertEqual(saved["relay"]["account_id"], ingest.GCP_RELAY_ACCOUNT)
            self.assertEqual(saved["relay"]["log_group"], group)
            later = next(ingest.gcp_batches(body, "ap-northeast-2", 2000))[0]
            self.assertEqual(json.loads(later["message"])["event_id"], original["event_id"])

    def test_wrong_account_cannot_claim_the_gcp_relay_group(self):
        body, _ = self.relay()
        body["owner"] = ACCOUNT
        self.assertEqual(self.process_relay(body)["batchItemFailures"], [{"itemIdentifier": "100"}])
        self.client.put_log_events.assert_not_called()

    def test_gcp_identity_mismatch_is_retried_without_storage(self):
        for changes in (
            {"project_id": "other"},
            {"cloud": "aws"},
            {"event_id": "forged"},
            {"insert_id": "wrong"},
            {"source_timestamp": "wrong"},
            {"log_name": ingest.GCP_RELAY_LOGS["/delta/security/gcp-firewall-threat"]},
        ):
            body, _ = self.relay(**changes)
            self.assertEqual(
                self.process_relay(body)["batchItemFailures"], [{"itemIdentifier": "100"}]
            )
        self.client.put_log_events.assert_not_called()

    def test_unconfigured_gcp_destination_does_not_fall_back_to_aws(self):
        body, _ = self.relay()
        self.assertEqual(
            self.process_relay(body, None)["batchItemFailures"], [{"itemIdentifier": "100"}]
        )
        self.client.put_log_events.assert_not_called()

    def test_arbitrary_aws_json_does_not_become_a_gcp_record(self):
        body, original = self.relay()
        body["logGroup"] = "/service/test"
        self.assertEqual(self.process_relay(body), {"batchItemFailures": []})
        call = self.client.put_log_events.call_args.kwargs
        self.assertEqual(call["logGroupName"], "central-aws")
        self.assertEqual(json.loads(call["logEvents"][0]["message"])["cloud"], "aws")

    def test_gcp_destination_rejection_is_not_acknowledged(self):
        body, _ = self.relay()
        self.client.put_log_events.return_value = {
            "rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0}
        }
        self.assertEqual(self.process_relay(body)["batchItemFailures"], [{"itemIdentifier": "100"}])


if __name__ == "__main__":
    unittest.main()
