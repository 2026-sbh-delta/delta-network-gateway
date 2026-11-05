import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "jenkins_relocate", Path(__file__).resolve().parents[1] / "jenkins/aws/relocate.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.controller = {
            "admin_password": "admin-secret",
            "platform_password": "user-secret",
            "keystore_password": "tls-secret",
            "url": "https://10.80.1.20",
        }
        self.agent = {"agent_secret": "agent-secret", "url": "https://10.80.1.20"}
        self.platform = {
            "db_password": "db-secret",
            "app_env": {
                "PLATFORM_JENKINS_TOKEN": "api-secret",
                "PLATFORM_LOGIN_ID": "same-user",
                "PLATFORM_DAST_URL": "https://scanner.example",
            },
        }

    def test_only_address_and_certificate_change(self):
        result = module.replacements(
            self.controller,
            self.agent,
            self.platform,
            "https://10.80.10.20",
            "new-ca",
            "new-keystore",
        )
        self.assertEqual(
            result[module.CONTROLLER]["admin_password"],
            self.controller["admin_password"],
        )
        self.assertEqual(
            result[module.CONTROLLER]["platform_password"],
            self.controller["platform_password"],
        )
        self.assertEqual(
            result[module.AGENT]["agent_secret"], self.agent["agent_secret"]
        )
        after = result[module.PLATFORM]
        self.assertEqual(after["db_password"], self.platform["db_password"])
        for key, value in self.platform["app_env"].items():
            self.assertEqual(after["app_env"][key], value)
        self.assertEqual(
            after["app_env"]["PLATFORM_JENKINS_URL"], "https://10.80.10.20"
        )
        self.assertNotIn("PLATFORM_JENKINS_CA_BASE64", self.platform["app_env"])
        self.assertEqual(self.controller["url"], "https://10.80.1.20")

    def test_unapproved_address_and_missing_tokens_fail_before_update(self):
        with self.assertRaises(ValueError):
            module.replacements(
                self.controller,
                self.agent,
                self.platform,
                "https://example.com",
                "ca",
                "key",
            )
        with self.assertRaises(ValueError):
            module.replacements(
                self.controller, {}, self.platform, "https://10.80.10.20", "ca", "key"
            )
