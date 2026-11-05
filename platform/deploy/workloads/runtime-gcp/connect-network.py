"""Add approved prefixes to existing GCP routing without touching remote TF state.

The owning network-security-gcp Terraform root contains the same desired routes.
This operator step changes only the named route filters and advertisements.
"""

import argparse
import json
import subprocess

PROJECT = "term2-delta"
REGION = "asia-northeast3"
DATABASE = "10.32.3.0/24"
WORKER_DATABASE = "10.31.0.2/32"
PLATFORM = "10.80.0.0/16"


def call(args):
    return subprocess.check_output(
        ["gcloud", *args, "--project=" + PROJECT, "--format=json", "--quiet"],
        text=True,
    )


def commands():
    router = json.loads(
        call(["compute", "routers", "describe", "delta-lab-aws", "--region=" + REGION])
    )
    vpc = json.loads(
        call(
            [
                "network-connectivity",
                "spokes",
                "describe",
                "delta-lab-workload",
                "--global",
            ]
        )
    )
    vpn = json.loads(
        call(
            [
                "network-connectivity",
                "spokes",
                "describe",
                "delta-lab-aws",
                "--region=" + REGION,
            ]
        )
    )
    export = set(vpc["linkedVpcNetwork"]["includeExportRanges"])
    imports = set(vpn["linkedVpnTunnels"]["includeImportRanges"])
    remote = set(vpn["linkedVpnTunnels"]["includeExportRanges"])
    result = []
    if DATABASE not in export:
        result.append(
            [
                "network-connectivity",
                "spokes",
                "linked-vpc-network",
                "update",
                "delta-lab-workload",
                "--global",
                "--include-export-ranges=" + ",".join(sorted(export | {DATABASE})),
            ]
        )
    if DATABASE not in imports or PLATFORM not in remote:
        result.append(
            [
                "network-connectivity",
                "spokes",
                "linked-vpn-tunnels",
                "update",
                "delta-lab-aws",
                "--region=" + REGION,
                "--include-import-ranges=" + ",".join(sorted(imports | {DATABASE})),
                "--include-export-ranges=" + ",".join(sorted(remote | {PLATFORM})),
            ]
        )
    if router["bgp"].get("advertiseMode") != "CUSTOM":
        raise ValueError(
            "Unexpected router mode; inspect before changing advertisements"
        )
    missing = {DATABASE, WORKER_DATABASE} - {
        r["range"] for r in router["bgp"].get("advertisedIpRanges", [])
    }
    if missing:
        result.append(
            [
                "compute",
                "routers",
                "update",
                "delta-lab-aws",
                "--region=" + REGION,
                "--add-advertisement-ranges=" + ",".join(sorted(missing)),
            ]
        )
    for peer in router["bgpPeers"]:
        if peer.get("advertiseMode") != "CUSTOM":
            raise ValueError(
                "Unexpected peer mode; inspect before changing advertisements"
            )
        missing = {DATABASE, WORKER_DATABASE} - {
            r["range"] for r in peer.get("advertisedIpRanges", [])
        }
        if missing:
            result.append(
                [
                    "compute",
                    "routers",
                    "update-bgp-peer",
                    "delta-lab-aws",
                    "--region=" + REGION,
                    "--peer-name=" + peer["name"],
                    "--add-advertisement-ranges=" + ",".join(sorted(missing)),
                ]
            )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for command in commands():
        print(" ".join(command), flush=True)
        if args.apply:
            call(command)
