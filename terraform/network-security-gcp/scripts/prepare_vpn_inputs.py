#!/usr/bin/env python3
"""Read AWS state in memory and write guarded ephemeral GCP VPN inputs."""
import argparse
import ipaddress
import json
import os
import subprocess
from pathlib import Path


def make_inputs(gateway, handoff, state, secret_version=1):
    if gateway.get("project_id") != "term2-delta" or not str(gateway.get("project_number", "")).isdigit():
        raise ValueError("Unexpected GCP project identity")
    if secret_version < 1:
        raise ValueError("Secret version must be positive")
    if len(handoff) != 2 or {item["gcp_interface"] for item in handoff.values()} != {0, 1}:
        raise ValueError("Exactly two AWS peers for GCP interfaces 0 and 1 are required")
    interfaces = {int(item["id"]): item["ip_address"] for item in gateway["interfaces"]}
    vpn_state = {
        instance["attributes"]["id"]: instance["attributes"]
        for resource in state.get("resources", []) if resource.get("type") == "aws_vpn_connection"
        for instance in resource.get("instances", [])
    }
    tunnels, secrets = {}, {}
    for peer in handoff.values():
        interface = peer["gcp_interface"]
        if peer["customer_gateway_ip"] != interfaces[interface] or peer["customer_bgp_asn"] != gateway["router_asn"]:
            raise ValueError("AWS peer does not match the current GCP gateway")
        if set(peer["remote_cidrs"]) != set(gateway["remote_cidrs"]) or set(peer["aws_cidrs_to_advertise"]) != set(gateway["aws_cidrs"]):
            raise ValueError("Gateway prefix contract mismatch")
        attributes = vpn_state.get(peer["vpn_connection_id"])
        if not attributes:
            raise ValueError("Handoff VPN is absent from the selected AWS state")
        for index in (1, 2):
            name = f"if{interface}t{index}"
            tunnel = peer["tunnels"][f"tunnel{index}"]
            if attributes.get(f"tunnel{index}_address") != tunnel["outside_ip"]:
                raise ValueError("Stale AWS tunnel handoff")
            for field, expected in (("inside_cidr", tunnel["inside_cidr"]), ("vgw_inside_address", tunnel["aws_inside_ip"]), ("cgw_inside_address", tunnel["peer_inside_ip"])):
                if attributes.get(f"tunnel{index}_{field}") != expected:
                    raise ValueError("Stale AWS inside tunnel addresses")
            if int(attributes.get(f"tunnel{index}_bgp_asn", -1)) != int(tunnel["aws_bgp_asn"]):
                raise ValueError("Stale AWS BGP ASN")
            if not ipaddress.ip_address(tunnel["outside_ip"]).is_global:
                raise ValueError("AWS outside address must be globally routable")
            secret = attributes.get(f"tunnel{index}_preshared_key")
            if not isinstance(secret, str) or len(secret) < 8:
                raise ValueError("AWS Standard VPN state has no usable secret")
            tunnels[name] = {
                "gcp_interface": interface, "aws_interface": interface * 2 + index - 1,
                "outside_ip": tunnel["outside_ip"], "inside_cidr": tunnel["inside_cidr"],
                "gcp_inside_ip": tunnel["peer_inside_ip"], "aws_inside_ip": tunnel["aws_inside_ip"],
                "aws_asn": int(tunnel["aws_bgp_asn"]), "secret_version": secret_version,
            }
            secrets[name] = secret
    return {"project_id": gateway["project_id"], "project_number": str(gateway["project_number"]), "aws_vpn_tunnels": tunnels, "vpn_shared_secrets": secrets}


def write_private(path, data):
    if not path.name.endswith(".tfvars.json"):
        raise ValueError("Use a Git-ignored .tfvars.json destination")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def read_command(command):
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-root", type=Path, required=True)
    parser.add_argument("--gateway-handoff", type=Path, required=True)
    parser.add_argument("--central-account-id", required=True)
    parser.add_argument("--network-key", default="gcp")
    parser.add_argument("--secret-version", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output already exists; refusing to overwrite secrets")
    try:
        identity = read_command(["aws", "sts", "get-caller-identity", "--output", "json"])
        root = str(args.aws_root.resolve())
        account = read_command(["terraform", f"-chdir={root}", "output", "-json", "central_account_id"])
        if identity["Account"] != args.central_account_id or account != args.central_account_id:
            raise ValueError("AWS profile and Terraform central account must match the explicit account")
        gateway = json.loads(args.gateway_handoff.read_text())
        all_handoff = read_command(["terraform", f"-chdir={root}", "output", "-json", "vpn_handoff"])
        handoff = {key: value for key, value in all_handoff.items() if value["network_key"] == args.network_key}
        state = read_command(["terraform", f"-chdir={root}", "state", "pull"])
        inputs = make_inputs(gateway, handoff, state, args.secret_version)
        write_private(args.output, inputs)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        # No command stdout/stderr or state is printed on failure.
        parser.exit(2, f"VPN input preparation failed ({type(error).__name__}). Check account, gateway and current handoff.\n")
    print(f"Wrote {len(inputs['aws_vpn_tunnels'])} tunnels to {args.output}; mode 0600; secrets omitted from output")


if __name__ == "__main__":
    main()
