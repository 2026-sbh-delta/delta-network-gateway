"""Render operator-owned YAML with registered source addresses, without applying it."""

import argparse
import ipaddress
import json
from pathlib import Path

import yaml

NAMESPACE = "delta-platform-sandbox"
ROOT = Path(__file__).parent / "manifests"


def resources(sources, node_port=32080):
    if not sources or len(sources) > 8 or not 30000 <= node_port <= 32767:
        raise ValueError("Explicit scanner/worker addresses and NodePort required")
    for source in sources:
        ip = ipaddress.ip_address(source)
        if (
            ip.version != 4
            or not ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
        ):
            raise ValueError("Only registered private IPv4 sources")
    items = []
    for file in (ROOT / "base/sandbox.yaml", ROOT / "sandbox/network.yaml"):
        items.extend(yaml.safe_load_all(file.read_text()))
    for item in items:
        if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "sandbox":
            item["spec"]["ingress"] = [
                {
                    "from": [{"ipBlock": {"cidr": s + "/32"}} for s in sources],
                    "ports": [{"protocol": "TCP", "port": "http"}],
                }
            ]
        if item["kind"] == "Service" and item["metadata"]["name"] == "sandbox":
            item["spec"]["ports"][0]["nodePort"] = node_port
    return {"apiVersion": "v1", "kind": "List", "items": items}


def main():
    parser = argparse.ArgumentParser(description="k3s 샌드박스 YAML 렌더링")
    parser.add_argument("--source", required=True, action="append")
    parser.add_argument("--node-port", type=int, default=32080)
    parser.add_argument("--yaml", action="store_true")
    args = parser.parse_args()
    result = resources(args.source, args.node_port)
    print(
        yaml.safe_dump_all(result["items"], sort_keys=False)
        if args.yaml
        else json.dumps(result)
    )


if __name__ == "__main__":
    main()
