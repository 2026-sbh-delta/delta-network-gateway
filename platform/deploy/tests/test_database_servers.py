import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

spec = importlib.util.spec_from_file_location(
    "database_server", Path(__file__).parents[1] / "workloads/database_server.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DatabaseServerTest(unittest.TestCase):
    def data(self, cloud="aws"):
        return {
            "target": {"cloud": cloud},
            "database": {"mode": "new"},
            "connection": {"account_id": "111122223333", "gcp_project": "test-project"},
            "environment_id": str(uuid4()),
            "service_id": str(uuid4()),
        }

    def test_existing_server_replacement_is_refused_before_apply(self):
        calls = []

        def execute(command, **kwargs):
            calls.append(command)
            return json.dumps(
                {"resource_changes": [{"change": {"actions": ["delete", "create"]}}]}
            )

        with (
            TemporaryDirectory() as root,
            self.assertRaisesRegex(ValueError, "must not update"),
        ):
            module.provision(
                self.data(),
                {"terraform": {"account_id": "111122223333"}},
                {"state_root": root},
                execute,
            )
        self.assertFalse(any(command[1] == "apply" for command in calls))

    def test_gcp_password_is_ephemeral_and_stable_across_retry(self):
        passwords = []
        data = self.data("gcp")

        def execute(command, **kwargs):
            passwords.append(kwargs["env"]["TF_VAR_admin_password"])
            if command[1] == "show":
                return json.dumps(
                    {"resource_changes": [{"change": {"actions": ["create"]}}]}
                )
            if command[1] == "output":
                return json.dumps(
                    {
                        "host": {"value": "db.sql.goog"},
                        "private_ip": {"value": "10.32.3.2"},
                        "admin_secret_id": {
                            "value": "projects/test-project/secrets/admin"
                        },
                    }
                )
            return ""

        with TemporaryDirectory() as root:
            for _ in range(2):
                result = module.provision(
                    data,
                    {
                        "terraform": {"project_id": "test-project"},
                        "ca_pem": "PUBLIC CA",
                    },
                    {"state_root": root},
                    execute,
                )
            directory = Path(root) / "databases" / data["environment_id"]
            self.assertEqual(len(set(passwords)), 1)
            self.assertNotIn(
                passwords[0], (directory / "server.auto.tfvars.json").read_text()
            )
            self.assertEqual(
                (directory / "admin-password").stat().st_mode & 0o777, 0o600
            )
            self.assertNotIn(passwords[0], json.dumps(result))
            self.assertEqual(result["secret_provider"], "gcp")
            self.assertEqual(
                result["iap_endpoint"], {"address": "10.32.3.2", "port": 5432}
            )

    def test_wrong_account_and_existing_database_cannot_provision(self):
        data = self.data()
        with TemporaryDirectory() as root:
            for profile in ({"terraform": {"account_id": "999999999999"}},):
                with self.assertRaisesRegex(ValueError, "selected cloud account"):
                    module.provision(data, profile, {"state_root": root}, None)
            data["database"]["mode"] = "existing"
            with self.assertRaisesRegex(ValueError, "new database"):
                module.provision(data, {}, {"state_root": root}, None)
