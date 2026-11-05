import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_sast_runtime import module


class DastRuntimeTest(unittest.TestCase):
    def configure(self):
        path = Path(__file__).parents[1] / "dast/configure.py"
        spec = importlib.util.spec_from_file_location("dast_configure", path)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value

    def test_target_is_the_private_platform_alb_only(self):
        configure = self.configure()
        target = configure.target(
            "http://internal-delta-platform-sandbox-123.ap-northeast-2.elb.amazonaws.com"
        )
        self.assertIsNone(target["repo"])
        for url in (
            "http://169.254.169.254",
            "http://example.com",
            target["origin"] + "/path",
            target["origin"] + "?a=1",
        ):
            with self.assertRaises(ValueError):
                configure.target(url)

    def test_token_survives_prepare_and_active_scans_block_changes(self):
        configure = self.configure()
        url = "http://internal-delta-platform-sandbox-123.ap-northeast-2.elb.amazonaws.com"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for _ in range(2):
                with patch.object(
                    configure.urllib.request,
                    "urlopen",
                    return_value=io.BytesIO(b'{"runs":[]}'),
                ):
                    configure.prepare(url, root)
                value = (root / "dast-platform.env").read_text()
                if _ == 0:
                    original = value
                self.assertEqual(value, original)
            self.assertEqual((root / "dast-platform.env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads((root / "platform-sandbox.json").read_text())["origin"], url
            )
            with (
                patch.object(
                    configure.urllib.request,
                    "urlopen",
                    return_value=io.BytesIO(b'{"runs":[{"status":"running"}]}'),
                ),
                self.assertRaises(ValueError),
            ):
                configure.prepare(url, root)
            self.assertEqual((root / "dast-platform.env").read_text(), original)

    def test_dast_runtime_merge_preserves_all_existing_values(self):
        connect = module("connect")
        original = {
            "db_password": "existing-db",
            "app_env": {
                "PLATFORM_SAST_TOKEN": "existing-sast",
                "PLATFORM_JENKINS_TOKEN": "existing-jenkins",
            },
        }
        fields = {
            "PLATFORM_DAST_URL": "https://172.31.25.180",
            "PLATFORM_DAST_TOKEN": "new-dast",
            "PLATFORM_DAST_CA_BASE64": "ca",
        }
        merged = connect.merge(original, fields, "DAST")
        self.assertEqual(merged["db_password"], original["db_password"])
        for key, value in original["app_env"].items():
            self.assertEqual(merged["app_env"][key], value)
        with self.assertRaises(ValueError):
            connect.merge(original, fields)
        nginx = module("install").nginx_config("172.31.25.180")
        self.assertIn("limit_except GET PUT", nginx)
        self.assertIn("proxy_set_header Host 127.0.0.1:8765", nginx)
        self.assertNotIn("location = /api/config", nginx)
