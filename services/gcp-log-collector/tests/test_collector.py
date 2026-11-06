import base64
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

import collector


def message(index=1, project="term2-delta", payload=None):
    entry = payload if payload is not None else {
        "logName": f"projects/{project}/logs/networksecurity.googleapis.com%2Ffirewall_threat",
        "insertId": f"entry-{index}", "timestamp": "2026-11-04T00:00:00Z",
        "jsonPayload": {"action": "ALERT", "request_id": f"request-{index}"},
    }
    return {"ackId": str(index), "message": {"messageId": str(index), "data": base64.b64encode(json.dumps(entry).encode()).decode()}}


class IngestTests(unittest.TestCase):
    def test_routed_groups_store_before_ack_and_preserve_identity(self):
        calls = []
        client, pubsub = Mock(), Mock()
        client.put_log_events.side_effect = lambda **kwargs: calls.append(("store", kwargs)) or {}
        pubsub.ack.side_effect = lambda ids: calls.append(("ack", ids))
        firewall = "projects/term2-delta/logs/compute.googleapis.com%2Ffirewall"
        threat = "projects/term2-delta/logs/networksecurity.googleapis.com%2Ffirewall_threat"
        writer = collector.RoutedCloudWatchWriter(client, {firewall: "/delta/security/gcp-firewall", threat: "/delta/security/gcp-firewall-threat"}, "test")
        entry = {"logName": firewall, "insertId": "firewall-2", "timestamp": "2026-11-07T00:00:00Z"}
        result = collector.process_messages([message(1), message(2, payload=entry)], pubsub, writer, "term2-delta", 1000)
        self.assertEqual(result, {"pulled": 2, "stored": 2, "acked": 2, "failed": 0})
        self.assertEqual([kind for kind, _ in calls], ["store", "store", "ack"])
        self.assertEqual({item[1]["logGroupName"] for item in calls[:2]}, set(writer.routes.values()))
        self.assertEqual(json.loads(calls[1][1]["logEvents"][0]["message"])["original"], entry)

    def test_one_route_failure_does_not_ack_that_route(self):
        client, pubsub = Mock(), Mock()
        threat = "projects/term2-delta/logs/networksecurity.googleapis.com%2Ffirewall_threat"
        firewall = "projects/term2-delta/logs/compute.googleapis.com%2Ffirewall"
        client.put_log_events.side_effect = [OSError("storage unavailable"), {}]
        writer = collector.RoutedCloudWatchWriter(client, {threat: "/delta/security/threat", firewall: "/delta/security/firewall"}, "test")
        result = collector.process_messages([message(1), message(2, payload={"logName": firewall})], pubsub, writer, "term2-delta", 1000)
        self.assertEqual(result, {"pulled": 2, "stored": 1, "acked": 1, "failed": 1})
        pubsub.ack.assert_called_once_with(["2"])

    def test_unknown_route_never_uses_a_default_group(self):
        client, pubsub = Mock(), Mock()
        writer = collector.RoutedCloudWatchWriter(client, {"projects/term2-delta/logs/other": "/delta/security/other"}, "test")
        result = collector.process_messages([message()], pubsub, writer, "term2-delta", 1000)
        self.assertEqual(result["failed"], 1)
        client.put_log_events.assert_not_called()
        pubsub.ack.assert_not_called()

    def test_invalid_route_configuration_fails_closed(self):
        for routes in ({}, [], {"projects/other/logs/test": "/delta/security/test"}, {"projects/term2-delta/logs/test": "/delta-cp/central/gcp"}):
            with self.assertRaises(ValueError):
                collector.RoutedCloudWatchWriter(Mock(), routes, "test")

    def test_drains_partial_pull_batches_until_empty(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = set()
        pubsub.pull.side_effect = [[message(1)], [message(2), message(3)], []]
        result = collector.drain_messages(pubsub, writer, "term2-delta", lambda: 50000)
        self.assertEqual(result, {"pulled": 3, "stored": 3, "acked": 3, "failed": 0})
        self.assertEqual(pubsub.ack.call_count, 2)

    def test_stops_extra_pulls_when_lambda_budget_is_low(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = set()
        pubsub.pull.return_value = [message()]
        result = collector.drain_messages(pubsub, writer, "term2-delta", lambda: 39000)
        self.assertEqual(result["acked"], 1)
        self.assertEqual(pubsub.pull.call_count, 1)

    def test_stops_extra_pulls_after_intake_window(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = set()
        pubsub.pull.return_value = [message()]
        with patch("collector.time.monotonic", side_effect=[0, 11]):
            result = collector.drain_messages(pubsub, writer, "term2-delta", lambda: 50000)
        self.assertEqual(result["acked"], 1)
        self.assertEqual(pubsub.pull.call_count, 1)

    def test_stops_draining_after_storage_failure(self):
        writer, pubsub = Mock(), Mock()
        writer.put.side_effect = OSError("unavailable")
        pubsub.pull.return_value = [message()]
        result = collector.drain_messages(pubsub, writer, "term2-delta", lambda: 50000)
        self.assertEqual(result["failed"], 1)
        pubsub.ack.assert_not_called()
        self.assertEqual(pubsub.pull.call_count, 1)

    def test_stores_original_before_ack(self):
        calls = []
        writer, pubsub = Mock(), Mock()
        writer.put.side_effect = lambda events: calls.append(("store", events)) or set()
        pubsub.ack.side_effect = lambda ids: calls.append(("ack", ids))
        stats = collector.process_messages([message()], pubsub, writer, "term2-delta", 1234)
        self.assertEqual([item[0] for item in calls], ["store", "ack"])
        record = json.loads(calls[0][1][0]["message"])
        self.assertEqual(record["original"]["jsonPayload"]["request_id"], "request-1")
        self.assertEqual(calls[0][1][0]["timestamp"], 1234)
        self.assertEqual(record["source_timestamp"], "2026-11-04T00:00:00Z")
        self.assertEqual(stats["acked"], 1)

    def test_store_failure_never_acks(self):
        writer, pubsub = Mock(), Mock()
        writer.put.side_effect = OSError("unavailable")
        result = collector.process_messages([message()], pubsub, writer, "term2-delta")
        pubsub.ack.assert_not_called()
        self.assertEqual(result["failed"], 1)

    def test_partial_cloudwatch_rejection_only_acks_accepted(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = {1}
        result = collector.process_messages([message(1), message(2)], pubsub, writer, "term2-delta")
        pubsub.ack.assert_called_once_with(["1"])
        self.assertEqual(result["failed"], 1)

    def test_wrong_project_and_malformed_messages_remain_for_dlq(self):
        writer, pubsub = Mock(), Mock()
        bad = message(); bad["message"]["data"] = "not-base64"
        result = collector.process_messages([message(project="other-project"), bad, message(payload=[1])], pubsub, writer, "term2-delta")
        writer.put.assert_not_called()
        pubsub.ack.assert_not_called()
        self.assertEqual(result["failed"], 3)

    def test_ack_failure_can_redeliver_with_stable_event_id(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = set()
        pubsub.ack.side_effect = OSError("timeout")
        result = collector.process_messages([message()], pubsub, writer, "term2-delta", 1000)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(result["acked"], 0)
        first, _ = collector.convert(message(), "term2-delta", 1000)
        second, _ = collector.convert(message(), "term2-delta", 2000)
        self.assertEqual(json.loads(first["message"])["event_id"], json.loads(second["message"])["event_id"])

    def test_large_batches_are_split_below_cloudwatch_limit(self):
        writer, pubsub = Mock(), Mock()
        writer.put.return_value = set()
        entry = {"logName": "projects/term2-delta/logs/test", "textPayload": "x" * 600000}
        result = collector.process_messages([message(1, payload=entry), message(2, payload=entry)], pubsub, writer, "term2-delta")
        self.assertEqual(writer.put.call_count, 2)
        self.assertEqual(result["acked"], 2)

    def test_oversized_record_is_not_truncated_or_acknowledged(self):
        writer, pubsub = Mock(), Mock()
        entry = {"logName": "projects/term2-delta/logs/test", "textPayload": "x" * (collector.MAX_EVENT_BYTES + 1)}
        result = collector.process_messages([message(payload=entry)], pubsub, writer, "term2-delta")
        writer.put.assert_not_called()
        pubsub.ack.assert_not_called()
        self.assertEqual(result["failed"], 1)

    def test_cloudwatch_rejection_indices(self):
        client = Mock()
        client.put_log_events.return_value = {"rejectedLogEventsInfo": {"tooOldLogEventEndIndex": 0, "tooNewLogEventStartIndex": 3}}
        writer = collector.CloudWatchWriter(client, "group", "stream")
        self.assertEqual(writer.put([{}] * 4), {0, 3})

    def test_pubsub_path_rejects_other_project_or_url(self):
        for path in ("projects/other/subscriptions/name", "https://example.com", "projects/term2-delta/subscriptions/../escape"):
            with self.assertRaises(ValueError):
                collector.PubSub(Mock(), path)

    def test_pull_error_is_not_treated_as_empty_success(self):
        session = Mock()
        session.post.return_value.raise_for_status.side_effect = OSError("403")
        pubsub = collector.PubSub(session, "projects/term2-delta/subscriptions/delta-collector")
        with self.assertRaises(OSError):
            pubsub.pull()

    def test_real_google_sdk_signs_lambda_credentials_without_metadata(self):
        audience = "//iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/delta-lab-aws/providers/aws-collector"
        environment = {
            "AWS_ACCESS_KEY_ID": "synthetic-access-key", "AWS_SECRET_ACCESS_KEY": "synthetic-secret-key",
            "AWS_SESSION_TOKEN": "synthetic-session-token", "AWS_REGION": "ap-northeast-2",
            "WIF_AUDIENCE": audience, "PUBSUB_SUBSCRIPTION": "projects/term2-delta/subscriptions/delta-collector",
            "DESTINATION_LOG_GROUP": "group",
        }
        with patch.dict(os.environ, environment), patch("boto3.client"):
            _, writer, session = collector.runtime()
            try:
                self.assertEqual(writer.stream, "gcp-network-security")
                request = Mock(side_effect=AssertionError("Unexpected network or metadata access"))
                token = json.loads(unquote(session.credentials.retrieve_subject_token(request)))
                request.assert_not_called()
                headers = {header["key"].lower(): header["value"] for header in token["headers"]}
                self.assertEqual(headers["x-goog-cloud-target-resource"], audience)
                self.assertIn("sts.ap-northeast-2.amazonaws.com", token["url"])
                self.assertNotIn("synthetic-secret-key", json.dumps(token))
            finally:
                session.close()
        with patch.dict(os.environ, {**environment, "DESTINATION_LOG_STREAM": "gcp-cloud-logging"}), patch("boto3.client"):
            _, writer, session = collector.runtime()
            try:
                self.assertEqual(writer.stream, "gcp-cloud-logging")
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
