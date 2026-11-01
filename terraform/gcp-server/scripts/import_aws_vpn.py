#!/usr/bin/env python3
"""Read existing central Terraform outputs/state and prepare GCP tunnel inputs.

Never changes AWS or GCP. PSKs are captured in memory and written only into an
ignored 0600 ephemeral input file. Nothing from raw state is printed.
"""
import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path


def build_inputs(outputs, state, expected_account, secret_version=1):
    if not isinstance(secret_version, int) or secret_version < 1:
        raise ValueError("secret_version must be a positive integer.")
    if outputs.get("central_account_id", {}).get("value") != expected_account:
        raise ValueError("Central account output does not match the authorized account.")
    if outputs.get("region", {}).get("value") != "ap-northeast-2":
        raise ValueError("Unexpected central region.")
    handoff = {k: p for k, p in outputs.get("vpn_handoff", {}).get("value", {}).items() if p.get("network_key") == "gcp"}
    if len(handoff) != 2 or {p.get("gcp_interface") for p in handoff.values()} != {0, 1}:
        raise ValueError("Expected exactly two GCP peers on interfaces 0 and 1.")
    resources = {instance.get("attributes", {}).get("id"): instance.get("attributes", {})
                 for resource in state.get("resources", [])
                 if resource.get("mode") == "managed" and resource.get("type") == "aws_vpn_connection" and not resource.get("module")
                 for instance in resource.get("instances", [])}
    tunnels, secrets = {}, {}
    for peer in handoff.values():
        interface = peer["gcp_interface"]
        attributes = resources.get(peer.get("vpn_connection_id"), {})
        if not attributes or attributes.get("customer_gateway_id") != peer.get("customer_gateway_id"):
            raise ValueError("VPN handoff and current state identity do not match.")
        for tunnel_index in (1, 2):
            prefix = f"tunnel{tunnel_index}"
            tunnel = peer["tunnels"][prefix]
            inside = ipaddress.ip_network(tunnel["inside_cidr"], strict=True)
            if inside.prefixlen != 30 or not inside.subnet_of(ipaddress.ip_network("169.254.0.0/16")):
                raise ValueError("Invalid tunnel inside network.")
            if attributes.get(prefix + "_address") != tunnel["outside_ip"] or attributes.get(prefix + "_inside_cidr") != str(inside):
                raise ValueError("Tunnel handoff is stale relative to state.")
            if tunnel["aws_inside_ip"] != str(inside[1]) or tunnel["peer_inside_ip"] != str(inside[2]):
                raise ValueError("Unexpected inside host mapping.")
            psk = attributes.get(prefix + "_preshared_key")
            if not psk:
                raise ValueError("Missing Standard PSK in the selected AWS VPN state.")
            key = f"aws-if{interface}-t{tunnel_index}"
            tunnels[key] = {"gcp_interface": interface, "aws_interface": interface * 2 + tunnel_index - 1,
                            "aws_outside_ip": tunnel["outside_ip"], "inside_cidr": str(inside),
                            "gcp_inside_ip": tunnel["peer_inside_ip"], "aws_inside_ip": tunnel["aws_inside_ip"],
                            "aws_bgp_asn": int(tunnel["aws_bgp_asn"]), "secret_version": secret_version}
            secrets[key] = psk
    if len({t["aws_outside_ip"] for t in tunnels.values()}) != 4 or len({t["inside_cidr"] for t in tunnels.values()}) != 4:
        raise ValueError("Duplicate AWS endpoint or inside network.")
    return {"vpn_tunnels": tunnels}, {"tunnel_shared_secrets": secrets}


def write_private_new(path, contents):
    # Refuse overwrites/symlinks. Operators explicitly remove stale inputs first.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(contents, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-root", type=Path, required=True)
    parser.add_argument("--central-account-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--secret-version", type=int, default=1, help="Increment when rotating AWS PSKs.")
    args = parser.parse_args()
    if os.environ.get("AWS_PROFILE") != "sbh-delta-security":
        raise ValueError("Use AWS_PROFILE=sbh-delta-security.")
    def read(*command):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ValueError("Read-only Terraform command failed. Raw output is suppressed.")
        return json.loads(result.stdout)
    outputs = read("terraform", f"-chdir={args.aws_root.resolve()}", "output", "-json")
    state = read("terraform", f"-chdir={args.aws_root.resolve()}", "state", "pull")
    regular, secret = build_inputs(outputs, state, args.central_account_id, args.secret_version)
    destination = args.output_dir.resolve()
    if not destination.is_dir():
        raise ValueError("output-dir must already exist.")
    files = [destination / "vpn.auto.tfvars.json", destination / "vpn-secrets.auto.tfvars.json"]
    if any(path.exists() or path.is_symlink() for path in files):
        raise ValueError("Refusing to overwrite existing tunnel input files.")
    write_private_new(files[0], regular)
    try:
        write_private_new(files[1], secret)
    except BaseException:
        files[0].unlink()
        raise
    print(f"Prepared {len(regular['vpn_tunnels'])} tunnel contracts and private ephemeral input files in {destination}. No PSKs printed.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        # Values in parsed state must never become an error message.
        if isinstance(error, (KeyError, json.JSONDecodeError)):
            print("Invalid state/output structure. Raw data is suppressed.", file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        sys.exit(2)
