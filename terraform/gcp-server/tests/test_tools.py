import importlib.util
import json
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff", ROOT / "scripts/import_aws_vpn.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def fixture():
    peers, instances = {}, []
    for interface in (0, 1):
        attributes = {"id": f"vpn-{interface}", "customer_gateway_id": f"cgw-{interface}"}
        tunnels = {}
        for index in (1, 2):
            host = (interface * 2 + index - 1) * 4
            prefix = f"tunnel{index}"
            tunnels[prefix] = {"outside_ip": f"3.1.1.{interface * 2 + index}", "inside_cidr": f"169.254.10.{host}/30",
                               "aws_inside_ip": f"169.254.10.{host + 1}", "peer_inside_ip": f"169.254.10.{host + 2}", "aws_bgp_asn": 64512}
            attributes[prefix + "_address"] = tunnels[prefix]["outside_ip"]
            attributes[prefix + "_inside_cidr"] = tunnels[prefix]["inside_cidr"]
            attributes[prefix + "_preshared_key"] = "MockPrivateValueNotReal"
        peers[str(interface)] = {"network_key": "gcp", "gcp_interface": interface, "vpn_connection_id": attributes["id"],
                                 "customer_gateway_id": attributes["customer_gateway_id"], "tunnels": tunnels}
        instances.append({"attributes": attributes})
    outputs = {"central_account_id": {"value": "482840608926"}, "region": {"value": "ap-northeast-2"}, "vpn_handoff": {"value": peers}}
    state = {"resources": [{"mode": "managed", "type": "aws_vpn_connection", "instances": instances}]}
    return outputs, state


class HandoffTests(unittest.TestCase):
    def test_mapping_and_secret_separation(self):
        regular, secret = handoff.build_inputs(*fixture(), "482840608926")
        self.assertEqual([t["aws_interface"] for t in regular["vpn_tunnels"].values()], [0, 1, 2, 3])
        self.assertNotIn("MockPrivateValue", json.dumps(regular))
        self.assertEqual(len(secret["tunnel_shared_secrets"]), 4)

    def test_wrong_account_rejected(self):
        with self.assertRaises(ValueError):
            handoff.build_inputs(*fixture(), "999999999999")

    def test_rotation_version_forwarded(self):
        regular, _ = handoff.build_inputs(*fixture(), "482840608926", secret_version=2)
        self.assertTrue(all(t["secret_version"] == 2 for t in regular["vpn_tunnels"].values()))
        with self.assertRaises(ValueError):
            handoff.build_inputs(*fixture(), "482840608926", secret_version=0)

    def test_stale_handoff_rejected(self):
        outputs, state = fixture()
        state["resources"][0]["instances"][0]["attributes"]["tunnel1_address"] = "3.9.9.9"
        with self.assertRaises(ValueError):
            handoff.build_inputs(outputs, state, "482840608926")

    def test_missing_psk_rejected(self):
        outputs, state = fixture()
        del state["resources"][0]["instances"][0]["attributes"]["tunnel1_preshared_key"]
        with self.assertRaises(ValueError):
            handoff.build_inputs(outputs, state, "482840608926")

    def test_private_files_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vpn-secrets.auto.tfvars.json"
            handoff.write_private_new(path, {"test": "fixture"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                handoff.write_private_new(path, {})
            link = Path(folder) / "link"
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                handoff.write_private_new(link, {})


class ServerTests(unittest.TestCase):
    def test_both_control_listeners_respond(self):
        import socket
        with socket.socket() as probe1, socket.socket() as probe2:
            probe1.bind(("127.0.0.1", 8080))
            probe2.bind(("127.0.0.1", 8081))
        process = subprocess.Popen([sys.executable, str(ROOT / "app/server.py"), "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for port in (8080, 8081):
                for attempt in range(50):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            data = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or attempt == 49:
                            self.fail("Local test server did not start.")
                        time.sleep(0.05)
                self.assertEqual(data["port"], port)
                self.assertEqual(data["cloud"], "gcp")
                self.assertEqual(data["path"], "/health")
        finally:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
