import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "preflight", Path(__file__).parents[1] / "workloads/preflight.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.data = {"database": {"profile_id": "gcp-prepared"}}
        self.config = {
            "databases": {
                "gcp-prepared": {
                    "server_mode": "prepared",
                    "preflight_endpoint": {"address": "10.32.3.2", "port": 5432},
                }
            }
        }

    def test_missing_route_fails_with_actionable_nonsecret_error(self):
        with (
            patch.object(
                module.socket,
                "create_connection",
                side_effect=TimeoutError("private provider detail"),
            ),
            self.assertRaises(module.DeploymentPreflightError) as error,
        ):
            module.check_prepared_database(self.data, self.config)
        self.assertIn("VPN routes/firewall", str(error.exception))
        self.assertNotIn("private provider detail", str(error.exception))

    def test_reachable_endpoint_passes_and_closes_connection(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            self.config["databases"]["gcp-prepared"]["preflight_endpoint"] = {
                "address": "127.0.0.1",
                "port": server.getsockname()[1],
            }
            module.check_prepared_database(self.data, self.config)
            client, _ = server.accept()
            with client:
                self.assertEqual(client.recv(1), b"")

    def test_new_server_and_no_database_do_not_probe_uncreated_resource(self):
        with patch.object(module.socket, "create_connection") as connect:
            module.check_prepared_database({}, self.config)
            self.config["databases"]["gcp-prepared"]["server_mode"] = "create"
            module.check_prepared_database(self.data, self.config)
            connect.assert_not_called()

    def test_iap_mode_does_not_require_private_route_from_aws(self):
        self.data["target"] = {"cloud": "gcp"}
        self.config["connections"] = {"gcp": {"management_transport": "iap"}}
        self.config["databases"]["gcp-prepared"]["iap_endpoint"] = {
            "address": "10.32.3.2",
            "port": 5432,
        }
        with patch.object(module.socket, "create_connection") as connect:
            module.check_prepared_database(self.data, self.config)
            connect.assert_not_called()
        del self.config["databases"]["gcp-prepared"]["iap_endpoint"]
        with self.assertRaisesRegex(module.DeploymentPreflightError, "not configured"):
            module.check_prepared_database(self.data, self.config)
