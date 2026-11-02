"""`terraform show -json` 결과에서 매핑에 필요한 값만 꺼낸다.

정해 둔 속성만 복사한다. user_data 같은 값과 state 원본은 판단 서비스로 보내지 않는다.

    terraform show -json > show.json
    python scripts/extract_terraform.py --show show.json --service web --exclude-subnet -tgw --out request.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

ORIGIN = "terraform"
OS_PREFIXES = {"al2023-": "amazon_linux", "amzn2-": "amazon_linux", "ubuntu": "ubuntu", "rocky": "rocky",
               "rhel": "rhel", "RHEL": "rhel", "debian": "debian"}


def _walk(module: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from _walk(child)


def _of_type(resources: Sequence[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [r["values"] for r in resources if r["type"] == type_]


def _name(values: dict[str, Any], fallback: str) -> str:
    return (values.get("tags") or {}).get("Name") or values.get(fallback) or "unnamed"


def _public_tables(resources: Sequence[dict[str, Any]]) -> set[str]:
    routes = _of_type(resources, "aws_route")
    tables = {r["route_table_id"] for r in routes if str(r.get("gateway_id") or "").startswith("igw-")}
    for table in _of_type(resources, "aws_route_table"):
        if any(str(route.get("gateway_id") or "").startswith("igw-") for route in table.get("route") or ()):
            tables.add(table["id"])
    return tables


def _subnets(resources: Sequence[dict[str, Any]], exclude: Sequence[str]) -> list[dict[str, Any]]:
    public = _public_tables(resources)
    table_of = {a.get("subnet_id"): a["route_table_id"] for a in _of_type(resources, "aws_route_table_association")}
    subnets = []
    for values in _of_type(resources, "aws_subnet"):
        name = _name(values, "id")
        if any(text in name for text in exclude):
            continue
        table = table_of.get(values.get("id"))
        tier = None if table is None else ("public" if table in public else "private")
        subnets.append({"name": name, "cidr": values["cidr_block"], "tier": tier,
                        "zone": values.get("availability_zone_id"), "origin": ORIGIN})
    return subnets


def _os_of(ami_name: str | None) -> str | None:
    return next((os for prefix, os in OS_PREFIXES.items() if ami_name and ami_name.startswith(prefix)), None)


def _servers(resources: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    amis = {values.get("id"): values.get("name") for values in _of_type(resources, "aws_ami")}
    return [{"name": _name(values, "id"), "instance_type": values.get("instance_type"),
             "os": _os_of(amis.get(values.get("ami"))), "instance_profile": bool(values.get("iam_instance_profile")),
             "origin": ORIGIN}
            for values in _of_type(resources, "aws_instance")]


def _security(resources: Sequence[dict[str, Any]]) -> dict[str, Any]:
    detectors = _of_type(resources, "aws_guardduty_detector")
    if not detectors:  # Terraform 밖에서 켰을 수 있으니 꺼졌다고 쓰지 않는다
        return {"origin": ORIGIN}
    plans = [f["name"] for f in _of_type(resources, "aws_guardduty_detector_feature") if f.get("status") == "ENABLED"]
    return {"guardduty_enabled": any(d.get("enable") for d in detectors), "guardduty_plans": plans, "origin": ORIGIN}


def extract_mapping(show: dict[str, Any], exclude_subnets: Sequence[str] = ()) -> dict[str, Any]:
    resources = list(_walk(show.get("values", {}).get("root_module", {})))
    return {"subnets": _subnets(resources, exclude_subnets), "servers": _servers(resources), "security": _security(resources)}


def build_request(mapping: dict[str, Any], service: str) -> dict[str, Any]:
    return {"assessment_id": f"map-{service}", "kind": "migration_mapping", "service": {"service_id": service},
            "evidence": {"mapping": mapping}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show", required=True, type=Path)
    parser.add_argument("--service", required=True)
    parser.add_argument("--exclude-subnet", action="append", default=[])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    mapping = extract_mapping(json.loads(args.show.read_text(encoding="utf-8")), args.exclude_subnet)
    args.out.write_text(json.dumps(build_request(mapping, args.service), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
