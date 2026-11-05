import base64
import gzip
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


if __name__ == "__main__":
    unittest.main()
