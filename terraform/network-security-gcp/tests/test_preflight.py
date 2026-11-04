import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("preflight", Path(__file__).resolve().parents[1] / "scripts/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def response(data, code=200):
    result = Mock(status_code=code)
    result.json.return_value = data
    return result


class PreflightTests(unittest.TestCase):
    def test_denied_project_is_not_ready_and_stops_inventory(self):
        session = Mock()
        session.get.return_value = response({"error": {"message": "private error body"}}, 403)
        result = preflight.inspect("term2-delta", "asia-northeast3", session)
        self.assertFalse(result["ready"])
        self.assertEqual(session.get.call_count, 1)
        session.post.assert_not_called()
        self.assertNotIn("private error body", str(result))

    def test_partial_inventory_is_not_marked_verified(self):
        session = Mock()
        def get(url, **kwargs):
            if '/v1/projects/term2-delta' in url and 'cloudresourcemanager' in url:
                return response({"projectId": "term2-delta", "projectNumber": "123456789012"})
            if url.endswith('/billingInfo'):
                return response({"billingEnabled": True})
            if 'serviceusage' in url:
                return response({"state": "ENABLED"})
            if url.endswith('/global/networks'):
                return response({"items": [], "nextPageToken": "another-page"})
            if url.endswith('/aggregated/subnetworks'):
                return response({"items": {}})
            return response({"name": "asia-northeast3", "status": "UP", "quotas": []})
        session.get.side_effect = get
        session.post.return_value = response({"permissions": list(preflight.PERMISSIONS)})
        result = preflight.inspect("term2-delta", "asia-northeast3", session)
        self.assertFalse(result["inventory_verified"])
        self.assertFalse(result["ready"])

    def test_failed_permission_probe_is_not_reported_as_denied_permissions(self):
        session = Mock()
        session.post.return_value = response({"error": {"message": "Invalid permission probe"}}, 400)
        session.get.side_effect = [
            response({"projectId": "term2-delta", "projectNumber": "123456789012"}),
            response({"billingEnabled": True}),
            *[response({"state": "ENABLED"}) for _ in preflight.APIS],
            response({"items": []}), response({"items": {}}), response({"status": "UP"}),
        ]
        result = preflight.inspect("term2-delta", "asia-northeast3", session)
        self.assertFalse(result["permissions"]["checked"])
        self.assertIsNone(result["permissions"]["missing"])
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
