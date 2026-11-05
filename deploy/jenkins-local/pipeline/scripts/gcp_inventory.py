"""gcloud 읽기 결과로 GCP 출발 인벤토리를 만든다. 표준 라이브러리만 쓴다.

gcloud 는 이 스크립트 밖에서 읽기 전용 명령으로 돌리고, 결과 JSON 이 든 폴더만 받는다.
  instances.json          gcloud compute instances list --format=json
  subnets.json            gcloud compute networks subnets list --format=json
  security-policies.json  gcloud compute security-policies list --format=json
  services.txt            gcloud services list --enabled --format='value(config.name)'

GCP API 로 알 수 없는 값(시크릿 저장소·중앙 로그·TLS·검사 경로)은 base 인벤토리 값을 그대로 두고 _note 에 적는다.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

# 이미지 라이선스 이름 앞부분 → delta-ai 가 아는 OS 이름
OS_PREFIXES = (("ubuntu", "ubuntu"), ("debian", "debian"), ("rhel", "rhel"), ("rocky", "rocky"), ("cos", "cos"))
ARM_FAMILIES = ("t2a", "c4a")  # Arm 머신 계열. 나머지 GCP 계열은 x86 이다
DEFAULT_COMPUTE_ACCOUNT = "-compute@developer.gserviceaccount.com"
PRECONFIGURED = re.compile(r"evaluatePreconfiguredWaf\('([a-z0-9-]+)'")
CATEGORY = re.compile(r"(-v\d+)?-(stable|canary)$")
OBSERVED_NOTE = ("실제 GCP 프로젝트를 gcloud 로 읽어 만든 인벤토리다. 서버·서브넷·WAF·워크로드 권한 방식은 읽은 값이고, "
                 "secrets·logging·ingress·network 는 GCP API 로 알 수 없어 base 인벤토리 값을 그대로 뒀다. "
                 "SCC 는 프로젝트 API 가 꺼져 있어도 조직에서 켤 수 있어 모른다(None)로 둔다")


def _basename(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _region_of_zone(zone: str) -> str:
    return _basename(zone).rsplit("-", 1)[0]


def _origin(instance: Mapping[str, Any]) -> str:
    return "terraform" if instance.get("labels", {}).get("goog-terraform-provisioned") == "true" else "operator"


def _os(instance: Mapping[str, Any]) -> str | None:
    licenses = [_basename(lic) for disk in instance.get("disks", []) if disk.get("boot") for lic in disk.get("licenses", [])]
    for name in licenses:
        for prefix, os_name in OS_PREFIXES:
            if name.startswith(prefix):
                return os_name
    return None  # 모르면 비워 둔다. delta-ai 가 unknown 으로 사람에게 넘긴다


def _platform(machine_type: str) -> str:
    return "linux/arm64" if machine_type.split("-", 1)[0] in ARM_FAMILIES else "linux/amd64"


def _has_own_account(instance: Mapping[str, Any]) -> bool:
    return any(not sa.get("email", "").endswith(DEFAULT_COMPUTE_ACCOUNT) for sa in instance.get("serviceAccounts", []))


def _is_public(instance: Mapping[str, Any]) -> bool:
    return any(nic.get("accessConfigs") for nic in instance.get("networkInterfaces", []))


def _server(instance: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": instance["name"], "instance_type": _basename(instance["machineType"]), "os": _os(instance),
            "service_account": _has_own_account(instance), "origin": _origin(instance)}


def _subnets(instances: Sequence[Mapping[str, Any]], subnets: Iterable[Mapping[str, Any]],
             region: str) -> list[dict[str, Any]]:
    """서버가 쓰는 서브넷만 낸다. GCP 서브넷엔 공개·사설 구분이 없어 그 안 서버의 외부 IP 로 정한다."""
    users: dict[str, list[Mapping[str, Any]]] = {}
    for instance in instances:
        for nic in instance.get("networkInterfaces", []):
            users.setdefault(_basename(nic["subnetwork"]), []).append(instance)
    rows = []
    for subnet in subnets:
        members = users.get(subnet["name"])
        if not members or _basename(subnet["region"]) != region:
            continue
        rows.append({"name": subnet["name"], "cidr": subnet["ipCidrRange"],
                     "tier": "public" if any(_is_public(i) for i in members) else "private",
                     "origin": _origin(members[0])})
    return rows


def _waf(policies: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    groups = sorted({m.group(1) for policy in policies for rule in policy.get("rules", [])
                     if str(rule.get("action", "")).startswith("deny")
                     for m in [PRECONFIGURED.search(rule.get("match", {}).get("expr", {}).get("expression", ""))] if m})
    if not groups:
        return {"enabled": False}, []
    waf = {"enabled": True, "product": "cloud_armor", "mode": "block", "rule_groups": groups}
    return waf, sorted({CATEGORY.sub("", group) for group in groups})


def build_inventory(instances: Sequence[Mapping[str, Any]], subnets: Sequence[Mapping[str, Any]],
                    policies: Sequence[Mapping[str, Any]], enabled_services: Sequence[str], *,
                    base: Mapping[str, Any], source_region: str) -> dict[str, Any]:
    """enabled_services 는 지금 판단에 쓰지 않지만 SCC 를 모른다고 둔 근거로 _evidence 에 남긴다."""
    in_region = [i for i in instances if _region_of_zone(i["zone"]) == source_region]
    if not in_region:
        raise ValueError(f"{source_region} 에 서버가 없다")
    waf, capabilities = _waf(policies)
    own_account = any(_has_own_account(i) for i in in_region)
    source_env = {**copy.deepcopy(base["source_env"]), "provider": "gcp", "waf": waf,
                  "identity": {"mechanism": "service_account" if own_account else "none"}}
    mapping = {**copy.deepcopy(base["mapping"]), "source_region": source_region,
               "subnets": _subnets(in_region, subnets, source_region),
               "servers": [_server(i) for i in in_region],
               "security": {"scc_services": None, "origin": "operator"}}
    return {"_note": OBSERVED_NOTE,
            "_evidence": {"securitycenter_api": "securitycenter.googleapis.com" in enabled_services},
            "direction": "gcp-to-aws", "service_id": base["service_id"],
            "services": [{"id": i["name"], "server": i["name"], "path": "/health",
                          "source_platform": _platform(_basename(i["machineType"]))} for i in in_region],
            "databases": [], "mapping": mapping, "source_env": source_env,
            "source_edge": {"waf_capabilities": capabilities},
            "target_baseline": copy.deepcopy(base["target_baseline"])}


def _read(folder: Path) -> tuple[list[Any], list[Any], list[Any], list[str]]:
    def load(name: str) -> list[Any]:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    services = (folder / "services.txt").read_text(encoding="utf-8").split()
    return load("instances.json"), load("subnets.json"), load("security-policies.json"), services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, type=Path, help="gcloud 결과 JSON 폴더")
    parser.add_argument("--base", required=True, type=Path, help="값을 빌려 올 GCP 인벤토리")
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        instances, subnets, policies, services = _read(args.dir)
        base = json.loads(args.base.read_text(encoding="utf-8"))
        inventory = build_inventory(instances, subnets, policies, services, base=base, source_region=args.region)
    except (OSError, ValueError, KeyError) as error:
        print(f"인벤토리를 만들 수 없다: {error}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
