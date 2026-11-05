"""gcloud 읽기 결과 → GCP 출발 인벤토리. 입력은 실제 term2-delta 응답에서 판단에 쓰는 필드만 남긴 것이다."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline" / "scripts"))

import gcp_inventory as gi  # noqa: E402
import migration_plan as mp  # noqa: E402

BASE = mp.read_json(ROOT / "pipeline" / "migration" / "inventory-gcp.json")
PREFIX = "https://www.googleapis.com/compute/v1/projects/term2-delta"


def instance(name="delta-lab-server", machine="e2-micro", license_="ubuntu-2404-lts", external=False,
             service_accounts=(), terraform=True, zone="asia-northeast3-a", subnet="delta-lab-workload"):
    nic = {"subnetwork": f"{PREFIX}/regions/asia-northeast3/subnetworks/{subnet}", "networkIP": "10.32.1.10"}
    if external:
        nic["accessConfigs"] = [{"natIP": "203.0.113.10"}]
    return {"name": name, "status": "RUNNING", "zone": f"{PREFIX}/zones/{zone}",
            "machineType": f"{PREFIX}/zones/{zone}/machineTypes/{machine}",
            "disks": [{"boot": True, "licenses": [f"https://www.googleapis.com/compute/v1/projects/x/global/licenses/{license_}"]}],
            "networkInterfaces": [nic], "serviceAccounts": [{"email": e} for e in service_accounts],
            "labels": {"goog-terraform-provisioned": "true"} if terraform else {}}


SUBNETS = [
    {"name": "delta-lab-workload", "ipCidrRange": "10.32.1.0/24", "region": f"{PREFIX}/regions/asia-northeast3"},
    {"name": "delta-lab-central", "ipCidrRange": "10.31.0.0/24", "region": f"{PREFIX}/regions/asia-northeast3"},
]
SERVICES = ["compute.googleapis.com", "logging.googleapis.com"]


def build(instances, policies=(), services=SERVICES):
    return gi.build_inventory(list(instances), SUBNETS, list(policies), list(services), base=BASE,
                              source_region="asia-northeast3")


def test_real_server_becomes_a_source_server_with_type_os_and_platform():
    inventory = build([instance()])

    server = inventory["mapping"]["servers"][0]
    assert server == {"name": "delta-lab-server", "instance_type": "e2-micro", "os": "ubuntu",
                      "service_account": False, "origin": "terraform"}
    assert inventory["services"] == [{"id": "delta-lab-server", "server": "delta-lab-server", "path": "/health",
                                      "source_platform": "linux/amd64"}]
    assert inventory["direction"] == "gcp-to-aws"


def test_only_subnets_in_use_are_listed_and_tier_follows_external_addresses():
    private = build([instance()])["mapping"]["subnets"]
    public = build([instance(external=True)])["mapping"]["subnets"]

    assert private == [{"name": "delta-lab-workload", "cidr": "10.32.1.0/24", "tier": "private", "origin": "terraform"}]
    assert public[0]["tier"] == "public"
    assert "target_cidr" not in private[0]  # 대상 대역은 delta-ai 가 기본 풀에서 고른다


def test_default_compute_account_is_not_a_service_account_of_its_own():
    default = build([instance(service_accounts=["123-compute@developer.gserviceaccount.com"])])
    custom = build([instance(service_accounts=["app@term2-delta.iam.gserviceaccount.com"])])

    assert default["mapping"]["servers"][0]["service_account"] is False
    assert default["source_env"]["identity"]["mechanism"] == "none"
    assert custom["mapping"]["servers"][0]["service_account"] is True
    assert custom["source_env"]["identity"]["mechanism"] == "service_account"


def test_scc_is_unknown_not_absent_when_the_project_api_is_off():
    """프로젝트가 조직 아래면 SCC 는 조직에서 켤 수 있다. API 가 꺼져 있다고 없는 것으로 보지 않는다."""
    inventory = build([instance()])

    assert inventory["mapping"]["security"]["scc_services"] is None


def test_waf_is_off_without_policies_and_categories_come_from_preconfigured_rules():
    off = build([instance()])
    rule = {"match": {"expr": {"expression": "evaluatePreconfiguredWaf('sqli-v422-stable', {'sensitivity': 1})"}},
            "action": "deny(403)"}
    on = build([instance()], policies=[{"name": "edge", "rules": [rule, {"action": "allow", "match": {}}]}])

    assert off["source_env"]["waf"] == {"enabled": False}
    assert off["source_edge"]["waf_capabilities"] == []
    assert on["source_env"]["waf"]["rule_groups"] == ["sqli-v422-stable"]
    assert on["source_edge"]["waf_capabilities"] == ["sqli"]


def test_unknown_image_and_arm_machines_are_kept_honest():
    inventory = build([instance(name="a", machine="t2a-standard-1", license_="something-else")])

    server = inventory["mapping"]["servers"][0]
    assert server["os"] is None
    assert inventory["services"][0]["source_platform"] == "linux/arm64"


def test_servers_outside_the_source_region_are_left_out():
    inventory = build([instance(), instance(name="far", zone="us-central1-a")])

    assert [s["name"] for s in inventory["mapping"]["servers"]] == ["delta-lab-server"]


def test_live_inventory_is_accepted_by_the_judgment(tmp_path):
    from delta_ai.contract import AssessmentRequest

    inventory = build([instance()])
    body = mp.mapping_request(inventory, mp.initial_state(None, []), "live-1")

    AssessmentRequest.model_validate(body)
