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
            "worker_database_address": "10.31.0.2",
        }
        result = module.merge_agent(agent, outputs)
        self.assertEqual(result["agent_secret"], "keep")
        self.assertEqual(result["deployment"]["connections"]["aws"], {"existing": True})
        self.assertNotIn("gcp", agent["deployment"]["connections"])
        self.assertEqual(
            result["deployment"]["databases"]["gcp-postgres-prepared"]["connection"][
                "hostaddr"
            ],
            "10.31.0.2",
        )
        outputs["credentials"]["type"] = "service_account"
        with self.assertRaises(ValueError):
            module.merge_agent(agent, outputs)
