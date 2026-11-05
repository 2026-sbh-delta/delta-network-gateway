import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load(name, relative):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / relative
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GcpWorkerTests(unittest.TestCase):
    def test_ssh_uses_private_ephemeral_token_and_cleans_up_on_failure(self):
        module = load("gcp_vm", "workloads/gcp_vm.py")
        credentials = Mock(token="test-only-token")
        paths = []

        def fail(args, **kwargs):
            path = Path(
                next(
                    a.split("=", 1)[1]
                    for a in args
                    if a.startswith("--access-token-file=")
                )
            )
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), credentials.token)
            self.assertNotIn(credentials.token, " ".join(args))
            self.assertNotIn("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", kwargs["env"])
            raise RuntimeError("SSH failure")

        with (
            patch.object(
                module.google.auth, "default", return_value=(credentials, None)
            ),
            patch.dict(os.environ, {"CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": "/old"}),
            self.assertRaisesRegex(RuntimeError, "SSH failure"),
        ):
            module.ssh(["vm", "--command", "true"], "worker@example.test", fail)
        self.assertFalse(paths[0].exists())

    def test_binding_preserves_other_clouds_and_rejects_service_account_keys(self):
        module = load("gcp_configure", "workloads/runtime-gcp/configure.py")
        agent = {
            "deployment": {
                "connections": {"aws": {"existing": True}},
                "databases": {"aws-db": {}},
            },
            "agent_secret": "keep",
        }
        outputs = {
            "connection": {
                "identity": {"gcp_project": "term2-delta"},
                "terraform": {
                    "subnetwork": "projects/term2-delta/regions/asia-northeast3/subnetworks/delta-lab-app-backend"
                },
            },
            "credentials": {"type": "external_account"},
            "database_address": "10.32.3.2",
        }
        result = module.merge_agent(agent, outputs)
        self.assertEqual(result["agent_secret"], "keep")
        self.assertEqual(result["deployment"]["connections"]["aws"], {"existing": True})
        self.assertNotIn("gcp", agent["deployment"]["connections"])
        self.assertEqual(
            result["deployment"]["databases"]["gcp-postgres-prepared"]["connection"][
                "hostaddr"
            ],
            "10.32.3.2",
        )
        outputs["credentials"]["type"] = "service_account"
        with self.assertRaises(ValueError):
            module.merge_agent(agent, outputs)


class GcpTunnelTests(unittest.TestCase):
    def test_loopback_forwarding_and_cleanup_on_body_failure(self):
        module = load("gcp_tunnel", "workloads/gcp_vm.py")
        credentials = Mock(token="test-only-token")
        process = Mock(pid=12345)
        process.poll.return_value = None
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        token_path = None
        with (
            patch.object(
                module.google.auth, "default", return_value=(credentials, None)
            ),
            patch.object(module, "local_port", side_effect=[31001, 31002]),
            patch.object(module.subprocess, "Popen", return_value=process) as launch,
            patch.object(module.socket, "create_connection", return_value=connection),
            patch.object(module.os, "killpg") as kill,
            self.assertRaisesRegex(ValueError, "deployment failed"),
            module.forward(
                {"instance_name": "delta-app-test", "zone": "asia-northeast3-a"},
                {
                    "ssh_service_account": "worker@example.test",
                    "identity": {"gcp_project": "test-project"},
                },
                {"address": "10.32.3.2", "port": 5432},
            ) as ports,
        ):
            args = launch.call_args.args[0]
            self.assertIn("127.0.0.1:31001:127.0.0.1:8088", args)
            self.assertIn("127.0.0.1:31002:10.32.3.2:5432", args)
            self.assertIn("--tunnel-through-iap", args)
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            self.assertEqual(ports, {"health": 31001, "database": 31002})
            token_path = Path(
                next(
                    a.split("=", 1)[1]
                    for a in args
                    if a.startswith("--access-token-file=")
                )
            )
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            raise ValueError("deployment failed")
        kill.assert_called_once_with(process.pid, module.signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)
        self.assertFalse(token_path.exists())

    def test_failed_tunnel_does_not_yield_or_disclose_provider_output(self):
        module = load("gcp_tunnel_failure", "workloads/gcp_vm.py")
        process = Mock(pid=12345)
        process.poll.return_value = 1
        with (
            patch.object(
                module.google.auth,
                "default",
                return_value=(Mock(token="test-token"), None),
            ),
            patch.object(module.subprocess, "Popen", return_value=process),
            patch.object(module.os, "killpg"),
            self.assertRaisesRegex(RuntimeError, "IAP management tunnel failed"),
            module.forward(
                {"instance_name": "delta-app-test", "zone": "asia-northeast3-a"},
                {
                    "ssh_service_account": "worker@example.test",
                    "identity": {"gcp_project": "test-project"},
                },
            ),
        ):
            self.fail("A failed tunnel must not run deployment operations")
        process.wait.assert_called_once()

    def test_reject_metadata_public_address_and_unexpected_port(self):
        module = load("gcp_tunnel_validation", "workloads/gcp_vm.py")
        for endpoint in (
            {"address": "169.254.169.254", "port": 5432},
            {"address": "8.8.8.8", "port": 5432},
            {"address": "10.32.3.2", "port": 22},
        ):
            with (
                self.subTest(endpoint=endpoint),
                self.assertRaises(ValueError),
                module.forward({}, {}, endpoint),
            ):
                self.fail("Invalid endpoint accepted")
