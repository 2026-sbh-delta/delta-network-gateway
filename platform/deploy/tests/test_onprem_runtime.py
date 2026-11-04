import importlib.util
import unittest
from pathlib import Path


def render():
    path = Path(__file__).parents[1] / "onprem/render.py"
    spec = importlib.util.spec_from_file_location("onprem_render", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class OnpremRuntimeTest(unittest.TestCase):
    def test_only_scanner_and_worker_may_reach_one_bounded_sandbox(self):
        objects = render().resources(["10.80.1.70", "172.31.25.180"])["items"]
        self.assertFalse(any(o["kind"].startswith("Cluster") for o in objects))
        for obj in objects:
            if obj["kind"] != "Namespace":
                self.assertEqual(obj["metadata"]["namespace"], "delta-platform-sandbox")
        ns = next(o for o in objects if o["kind"] == "Namespace")
        self.assertEqual(
            ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted"
        )
        policy = next(o["spec"] for o in objects if o["kind"] == "NetworkPolicy")
        self.assertEqual(policy["egress"], [])
        self.assertEqual(
            policy["ingress"][0]["from"],
            [
                {"ipBlock": {"cidr": "10.80.1.70/32"}},
                {"ipBlock": {"cidr": "172.31.25.180/32"}},
            ],
        )
        quota = next(o["spec"]["hard"] for o in objects if o["kind"] == "ResourceQuota")
        self.assertEqual(
            (quota["pods"], quota["secrets"], quota["persistentvolumeclaims"]),
            ("1", "1", "0"),
        )
        service = next(o["spec"] for o in objects if o["kind"] == "Service")
        self.assertEqual(service["externalTrafficPolicy"], "Local")
        self.assertEqual(service["ports"][0]["nodePort"], 32080)

    def test_scanner_cannot_mutate_or_read_credentials(self):
        objects = render().resources(["10.80.1.70"])["items"]
        scanner = next(
            o
            for o in objects
            if o["kind"] == "Role" and o["metadata"]["name"] == "scanner"
        )
        for rule in scanner["rules"]:
            self.assertEqual(rule["verbs"], ["get"])
            self.assertNotIn("secrets", rule["resources"])
        for sa in (o for o in objects if o["kind"] == "ServiceAccount"):
            self.assertFalse(sa["automountServiceAccountToken"])

    def test_public_wildcard_metadata_and_loopback_sources_are_rejected(self):
        for sources in (
            [],
            ["0.0.0.0"],
            ["8.8.8.8"],
            ["169.254.169.254"],
            ["127.0.0.1"],
            ["10.0.0.0/8"],
        ):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                render().resources(sources)
        with self.assertRaises(ValueError):
            render().resources(["10.80.1.70"], 80)
