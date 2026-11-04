import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("handoff", Path(__file__).resolve().parents[1] / "scripts/prepare_vpn_inputs.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def fixture():
    gateway = {"project_id": "term2-delta", "project_number": "123456789012", "router_asn": 65001, "interfaces": [{"id": 0, "ip_address": "1.1.1.1"}, {"id": 1, "ip_address": "8.8.8.8"}], "remote_cidrs": ["10.30.1.0/24"], "aws_cidrs": ["10.10.0.0/16"]}
    peers, instances = {}, []
    for interface in (0, 1):
        attributes = {"id": f"vpn-{interface}"}
        peer = {"gcp_interface": interface, "customer_gateway_ip": gateway["interfaces"][interface]["ip_address"], "customer_bgp_asn": 65001, "remote_cidrs": gateway["remote_cidrs"], "aws_cidrs_to_advertise": gateway["aws_cidrs"], "vpn_connection_id": attributes["id"], "tunnels": {}}
        for index in (1, 2):
            offset = interface * 8 + (index - 1) * 4
            outside = f"9.9.{interface}.{index}"
            attributes[f"tunnel{index}_address"] = outside
            attributes[f"tunnel{index}_preshared_key"] = f"synthetic-key-{interface}-{index}"
            peer["tunnels"][f"tunnel{index}"] = {"outside_ip": outside, "inside_cidr": f"169.254.10.{offset}/30", "aws_inside_ip": f"169.254.10.{offset+1}", "peer_inside_ip": f"169.254.10.{offset+2}", "aws_bgp_asn": 64512}
            attributes.update({f"tunnel{index}_inside_cidr": f"169.254.10.{offset}/30", f"tunnel{index}_vgw_inside_address": f"169.254.10.{offset+1}", f"tunnel{index}_cgw_inside_address": f"169.254.10.{offset+2}", f"tunnel{index}_bgp_asn": "64512"})
        peers[f"if{interface}"] = peer
        instances.append({"attributes": attributes})
    return gateway, peers, {"resources": [{"type": "aws_vpn_connection", "instances": instances}]}


class HandoffTests(unittest.TestCase):
    def test_complete_mapping(self):
        result = handoff.make_inputs(*fixture())
        self.assertEqual(sorted(t["aws_interface"] for t in result["aws_vpn_tunnels"].values()), [0, 1, 2, 3])
        self.assertEqual(len(result["vpn_shared_secrets"]), 4)
        self.assertNotIn("preshared_key", str(result["aws_vpn_tunnels"]))

    def test_rejects_wrong_project_or_gateway_or_prefix(self):
        for field, value in [("project_id", "previous-project"), ("router_asn", 65002), ("remote_cidrs", ["10.99.0.0/24"])]:
            gateway, peers, state = fixture()
            gateway[field] = value
            with self.assertRaises(ValueError):
                handoff.make_inputs(gateway, peers, state)

    def test_rejects_stale_aws_state(self):
        gateway, peers, state = fixture()
        state["resources"][0]["instances"][0]["attributes"]["tunnel1_address"] = "4.4.4.4"
        with self.assertRaises(ValueError):
            handoff.make_inputs(gateway, peers, state)

    def test_private_exclusive_file_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vpn.auto.tfvars.json"
            handoff.write_private(path, handoff.make_inputs(*fixture()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                handoff.write_private(path, {})
            link = Path(directory) / "link.tfvars.json"
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                handoff.write_private(link, {})

    def test_rejects_stale_inside_addresses(self):
        gateway, peers, state = fixture()
        state["resources"][0]["instances"][0]["attributes"]["tunnel1_vgw_inside_address"] = "169.254.99.1"
        with self.assertRaises(ValueError):
            handoff.make_inputs(gateway, peers, state)

    def test_rejects_private_outside_address(self):
        gateway, peers, state = fixture()
        peers["if0"]["tunnels"]["tunnel1"]["outside_ip"] = "10.0.0.1"
        state["resources"][0]["instances"][0]["attributes"]["tunnel1_address"] = "10.0.0.1"
        with self.assertRaises(ValueError):
            handoff.make_inputs(gateway, peers, state)


if __name__ == "__main__":
    unittest.main()
