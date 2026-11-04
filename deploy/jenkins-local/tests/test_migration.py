"""이전 파이프라인 스크립트. 판단은 실제 delta-ai 판정 코드(LLM 없음)로 받는다."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline" / "scripts"))
sys.path.insert(0, str(ROOT / "traffic-control"))

import attack_probe  # noqa: E402
import cutover  # noqa: E402
import edge_waf  # noqa: E402
import migration_plan as mp  # noqa: E402
import traffic  # noqa: E402
from control import ControlError, Controller  # noqa: E402
from delta_ai.contract import AssessmentRequest  # noqa: E402
from delta_ai.service import assess  # noqa: E402
from delta_ai.store import RuleStore  # noqa: E402
from test_traffic_control import FakeHaproxy  # noqa: E402

INVENTORY = mp.read_json(ROOT / "pipeline" / "migration" / "inventory-aws.json")
INVENTORY_GCP = mp.read_json(ROOT / "pipeline" / "migration" / "inventory-gcp.json")
SERVICES = cutover.load_services(ROOT / "pipeline" / "migration" / "services.json")


@pytest.fixture
def judge(tmp_path):
    def run(body: dict[str, Any]) -> dict[str, Any]:
        request = AssessmentRequest.model_validate(body)
        result = asyncio.run(assess(request, llm=None, store=RuleStore(tmp_path / "rules.sqlite3")))
        return json.loads(result.model_dump_json())
    return run


def settle_mapping(judge, inventory, state, answers=None, rounds=4):
    """Jenkinsfile.migration 의 이전 판단 단계와 같은 순서다."""
    for _ in range(rounds):
        result = judge(mp.mapping_request(inventory, state, "m"))
        if result["verdict"] == "pass":
            return result, state
        fixes = mp.classify_fixes(result, inventory)
        if any(f["kind"] != "unsupported" for f in fixes):
            state = mp.record_fixes(state, fixes, ["build_arch", "set"])
            continue
        questions = mp.human_questions(result, inventory, state)
        if questions["resolved"]:
            return result, state
        state = mp.decide(state, questions, answers or {})
    raise AssertionError("이전 판단이 끝나지 않았다")


def test_inventory_requests_match_the_service_contract():
    inventory = mp.apply_scenario(INVENTORY, "good")
    state = mp.initial_state("premium", [])
    AssessmentRequest.model_validate(mp.mapping_request(inventory, state, "m"))
    plan = {"target_env": INVENTORY["target_baseline"]}
    AssessmentRequest.model_validate(mp.parity_request(inventory, plan, [], "p"))


def test_arm_servers_are_fixed_by_building_for_the_target_then_rejudged(judge):
    inventory = mp.apply_scenario(INVENTORY, "good")
    state = mp.initial_state("premium", [])

    first = judge(mp.mapping_request(inventory, state, "m1"))
    fixes = mp.classify_fixes(first, inventory)

    assert first["verdict"] == "fix"
    assert {(f["kind"], f["server"]) for f in fixes} == {("build_arch", "delta-web"), ("build_arch", "delta-api")}
    second = judge(mp.mapping_request(inventory, mp.record_fixes(state, fixes, ["build_arch"]), "m2"))
    assert second["verdict"] == "pass"


def test_plan_takes_machine_type_and_platform_from_the_judgment(judge):
    inventory = mp.apply_scenario(INVENTORY, "good")
    result, state = settle_mapping(judge, inventory, mp.initial_state("premium", []))

    plan = mp.target_plan(inventory, result, state)

    assert [(s["id"], s["machine_type"], s["target_platform"]) for s in plan["services"]] == [
        ("web", "e2-small", "linux/amd64"), ("api", "e2-medium", "linux/amd64")]
    assert mp.target_tfvars(plan) == {"region": "asia-northeast3"}
    gaps = mp.terraform_gaps(plan)
    assert gaps["root"] == "terraform/network-security-gcp"
    missing = gaps["missing_variables"]
    assert missing["machine_type"] == {"fixed": "e2-micro", "needed": {"web": "e2-small", "api": "e2-medium"}}
    assert missing["server_count"] == {"fixed": 1, "needed": 2}
    assert missing["cloud_armor_rules"]["attachable"] is False  # 앞단이 패스스루 NLB 다
    assert missing["subnets"] == plan["subnets"]  # subnets 는 central 과 같이 받는 객체라 workload 만 채우지 않는다


def test_standard_scc_gaps_are_not_crossed_without_a_person(judge):
    inventory = mp.apply_scenario(INVENTORY, "scc-standard")
    state = mp.initial_state("standard", [])
    with pytest.raises(mp.PlanError):
        settle_mapping(judge, inventory, state)  # 승인 답이 없으면 decide 가 멈춘다

    answers = {"ACCEPT_container_threats": True, "ACCEPT_vm_threats": True, "ACCEPT_log_threats": True}
    result, state = settle_mapping(judge, inventory, state, answers)
    assert result["verdict"] == "pass"
    assert state["accepted_gaps"] == ["container_threats", "log_threats", "vm_threats"]
    assert {r["grade"] for r in result["mapping"]["rows"] if r["resource"] == "security"} == {"gap"}


def test_unmapped_permissions_need_explicit_approval_and_are_recorded(judge):
    inventory = mp.apply_scenario(INVENTORY, "iam-gap")
    state = mp.initial_state(None, [])
    with pytest.raises(mp.PlanError, match="승인하지 않았다"):
        settle_mapping(judge, inventory, state, {"SCC_TIER": "premium"})

    result, state = settle_mapping(judge, inventory, state, {"SCC_TIER": "premium", "APPROVE_REMAINING": True})

    assert result["verdict"] == "needs_human"  # 판정은 바뀌지 않는다. 사람 승인으로만 진행한다
    assert len(state["approved_findings"]) == 1
    plan = mp.target_plan(inventory, result, state)
    assert any(g["rule_id"] == "MAP-IAM-001" for g in plan["remaining_gaps"])


def test_unknown_fix_paths_go_to_a_person():
    result = {"findings": [{"finding_id": "f", "rule_id": "MAP-X", "severity": "high", "status": "fail",
                            "ops": [{"op": "replace", "path": "/mapping/region", "value": "x"}]}]}
    assert mp.classify_fixes(result, INVENTORY)[0]["kind"] == "unsupported"


def test_plan_refuses_an_unsettled_judgment():
    result = {"verdict": "needs_human", "findings": [{"finding_id": "f", "rule_id": "MAP-IAM-001", "severity": "high",
                                                      "status": "fail", "evidence": "x"}], "mapping": {"rows": []}}
    with pytest.raises(mp.PlanError):
        mp.target_plan(INVENTORY, result, mp.initial_state("premium", []))


def test_waf_fix_is_applied_to_the_plan_and_passes_once_attacks_are_blocked(judge):
    inventory = mp.apply_scenario(INVENTORY, "good")
    result, state = settle_mapping(judge, inventory, mp.initial_state("premium", []))
    plan = mp.target_plan(inventory, result, state)
    capabilities = inventory["source_edge"]["waf_capabilities"]

    first = judge(mp.parity_request(inventory, plan, [], "p1"))
    plan, summary = mp.apply_parity_fixes(plan, first)

    assert first["verdict"] == "fix"
    assert plan["target_env"]["waf"]["enabled"] is True
    assert mp.edge_capabilities(plan) == sorted(capabilities)
    assert summary["human"] == []
    blocked = [{"scenario": f"{c}-probe", "environment": env, "observed": "blocked", "http_status": 403}
               for env in ("aws", "gcp") for c in capabilities]
    source, target = blocked[:len(capabilities)], blocked[len(capabilities):]
    assert judge(mp.parity_request(inventory, plan, mp.controlled_attacks(source, target), "p2"))["verdict"] == "pass"


# ---- GCP → AWS ----

def test_gcp_inventory_requests_match_the_service_contract():
    inventory = mp.apply_scenario(INVENTORY_GCP, "good")
    state = mp.initial_state("premium", [])  # AWS 대상이면 SCC 등급은 보내지 않는다
    request = mp.mapping_request(inventory, state, "m")
    assert "scc_tier" not in request["evidence"]["mapping"]
    AssessmentRequest.model_validate(request)
    AssessmentRequest.model_validate(mp.parity_request(inventory, {"target_env": inventory["target_baseline"]}, [], "p"))


def test_gcp_to_aws_needs_no_rebuild_and_asks_no_tier(judge):
    inventory = mp.apply_scenario(INVENTORY_GCP, "good")
    state = mp.initial_state(None, [])

    first = judge(mp.mapping_request(inventory, state, "m1"))

    assert first["verdict"] == "pass"  # E2 와 t3 는 둘 다 x86 이고 GuardDuty 는 등급이 없다
    assert mp.human_questions(first, inventory, state)["needs_scc_tier"] is False
    plan = mp.target_plan(inventory, first, state)
    assert [(s["id"], s["machine_type"], s["target_platform"]) for s in plan["services"]] == [
        ("web", "t3.small", "linux/amd64"), ("api", "t3.medium", "linux/amd64")]
    assert plan["security"] == ["GuardDuty 기본 탐지", "RUNTIME_MONITORING"]
    assert mp.target_tfvars(plan) == {"region": "ap-northeast-2"}


def test_aws_root_gaps_name_what_workload_server_cannot_take_yet(judge):
    inventory = mp.apply_scenario(INVENTORY_GCP, "good")
    result, state = settle_mapping(judge, inventory, mp.initial_state(None, []))

    gaps = mp.terraform_gaps(mp.target_plan(inventory, result, state))

    assert gaps["root"] == "terraform/workload-server"
    missing = gaps["missing_variables"]
    assert missing["instance_type"]["needed"] == {"web": "t3.small", "api": "t3.medium"}
    assert missing["ami_architecture"]["needed"] == {"web": "linux/amd64", "api": "linux/amd64"}
    assert missing["guardduty_features"] == ["RUNTIME_MONITORING"]


def test_gcp_security_gap_needs_a_person(judge):
    inventory = mp.apply_scenario(INVENTORY_GCP, "security-gap")
    state = mp.initial_state(None, [])
    with pytest.raises(mp.PlanError, match="Security Health Analytics"):
        settle_mapping(judge, inventory, state)

    result, state = settle_mapping(judge, inventory, state, {"ACCEPT_Security Health Analytics": True})

    assert result["verdict"] == "pass"
    assert state["accepted_gaps"] == ["Security Health Analytics"]


def test_gcp_iam_gap_needs_explicit_approval(judge):
    inventory = mp.apply_scenario(INVENTORY_GCP, "iam-gap")
    with pytest.raises(mp.PlanError, match="승인하지 않았다"):
        settle_mapping(judge, inventory, mp.initial_state(None, []))

    result, state = settle_mapping(judge, inventory, mp.initial_state(None, []), {"APPROVE_REMAINING": True})

    assert result["verdict"] == "needs_human"
    assert any(g["rule_id"] == "MAP-IAM-001" for g in mp.target_plan(inventory, result, state)["remaining_gaps"])


def test_gcp_to_aws_waf_fix_uses_aws_groups_and_keeps_every_category(judge):
    inventory = mp.apply_scenario(INVENTORY_GCP, "good")
    result, state = settle_mapping(judge, inventory, mp.initial_state(None, []))
    plan = mp.target_plan(inventory, result, state)
    capabilities = inventory["source_edge"]["waf_capabilities"]

    first = judge(mp.parity_request(inventory, plan, [], "p1"))
    plan, summary = mp.apply_parity_fixes(plan, first)

    assert first["verdict"] == "fix"
    assert plan["target_env"]["waf"]["product"] == "aws_waf"
    assert plan["target_env"]["waf"]["rule_groups"] == ["AWSManagedRulesCommonRuleSet",
                                                        "AWSManagedRulesKnownBadInputsRuleSet",
                                                        "AWSManagedRulesSQLiRuleSet"]
    assert mp.edge_capabilities(plan) == sorted(capabilities)
    blocked = [{"scenario": f"{c}-probe", "environment": env, "observed": "blocked", "http_status": 403}
               for env in ("gcp", "aws") for c in capabilities]
    source, target = blocked[:len(capabilities)], blocked[len(capabilities):]
    assert judge(mp.parity_request(inventory, plan, mp.controlled_attacks(source, target), "p2"))["verdict"] == "pass"


def test_target_attack_is_not_trusted_when_the_source_did_not_block_it():
    source = [{"scenario": "sqli-probe", "environment": "aws", "observed": "allowed"}]
    target = [{"scenario": "sqli-probe", "environment": "gcp", "observed": "blocked"}]
    assert mp.controlled_attacks(source, target)[-1]["observed"] == "unknown"


def test_waf_patterns_cover_every_probe_and_survive_runtime_escaping():
    import re

    for capability, payload in attack_probe.PAYLOADS.items():
        assert re.search(edge_waf.PATTERNS[capability], payload, re.IGNORECASE), capability
    commands = edge_waf.commands("gcp", ["sqli"])
    assert commands[0] == "clear acl /usr/local/etc/haproxy/waf/gcp.acl"
    assert commands[1].endswith(r"\\x27\\s*or\\s|union\\s+select|sleep\\(")
    with pytest.raises(edge_waf.UnsupportedCapability):
        edge_waf.patterns_for(["graphql"])


def test_normal_requests_do_not_match_the_waf_patterns():
    import re

    for path in ("/", "/api/hello", "/api/hello?id=1", "/health"):
        assert not any(re.search(p, path, re.IGNORECASE) for p in edge_waf.PATTERNS.values())


@pytest.mark.parametrize("status,observed", [(403, "blocked"), (200, "allowed"), (404, "allowed"), (502, "unknown"),
                                             (0, "unknown")])
def test_probe_reads_only_403_as_blocked(status, observed):
    results = attack_probe.probe("http://edge/api/hello", "gcp", ["sqli"], fetch=lambda url: status)
    assert results[0]["observed"] == observed


# ---- 단계 전환 ----

class DirectControl:
    """cutover.Control 대신 실제 Controller 를 부른다. HTTP 만 뺐다."""

    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.ui_actions: list = []

    def state(self):
        return self.controller.snapshot()

    def events(self, after):
        if self.ui_actions:
            self.ui_actions.pop(0)(self.controller)
        return self.controller.events_after(after)

    def post(self, path, body):
        c = self.controller
        try:
            if path == "/api/run":
                c.start_run(body["run_id"], body["mode"], body["source"], body["target"])
                return {"ok": True}
            if path == "/api/run/end":
                c.end_run(body["run_id"], body["result"])
                return {"ok": True}
            service_id, action = path.split("/")[3:5]
            if action == "weight":
                return c.set_weight(service_id, body["target_percent"], "pipeline")
            return c.report(service_id, body["state"], body["detail"], body["percent"])
        except ControlError as exc:
            raise cutover.ControlRejected(exc.status, exc.message) from exc


def _settings(tmp_path):
    return cutover.Settings(run_id="build-9", source="aws", target="gcp", version="gcp-b9", gateway="http://gw",
                            requests=40, max_p95_ms=2000, hold_seconds=0, out_dir=tmp_path)


def _scripted(outcomes):
    calls = []

    def measurer(service, percent, settings):
        calls.append((service["id"], percent))
        return outcomes.get((service["id"], percent), cutover.Outcome("passed", "ok"))
    return measurer, calls


def test_steps_must_rise_and_end_at_100():
    assert cutover.parse_steps("web=10,50,100;api=25,100", SERVICES) == [("web", [10, 50, 100]), ("api", [25, 100])]
    for bad in ("web=50,10,100", "web=10,50", "db=100", "web=0,100", "web=100;web=100"):
        with pytest.raises(ValueError):
            cutover.parse_steps(bad, SERVICES)


def test_auto_moves_each_service_through_its_steps(tmp_path):
    haproxy = FakeHaproxy()
    control = DirectControl(Controller(list(SERVICES.values()), ("aws", "gcp"), haproxy))
    measurer, calls = _scripted({})

    code = cutover.run_auto(control, SERVICES, cutover.parse_steps("web=10,100;api=50,100", SERVICES), _settings(tmp_path),
                            tmp_path / "state.json", measurer=measurer, sleep=lambda _: None)

    assert code == cutover.EXIT_DONE
    assert calls == [("web", 10), ("web", 100), ("api", 50), ("api", 100)]
    assert haproxy.backends["mig_web"] == haproxy.backends["mig_api"] == {"aws": 0, "gcp": 100}
    assert json.loads((tmp_path / "state.json").read_text())["services"] == {"web": "completed", "api": "completed"}


def test_auto_rolls_back_only_the_failing_service_and_stops(tmp_path):
    haproxy = FakeHaproxy()
    control = DirectControl(Controller(list(SERVICES.values()), ("aws", "gcp"), haproxy))
    measurer, calls = _scripted({("api", 10): cutover.Outcome("rolled_back", "지표 기준 초과")})

    code = cutover.run_auto(control, SERVICES, cutover.parse_steps("web=100;api=10,100", SERVICES), _settings(tmp_path),
                            tmp_path / "state.json", measurer=measurer, sleep=lambda _: None)

    assert code == cutover.EXIT_FAILED
    assert calls == [("web", 100), ("api", 10)]
    assert haproxy.backends["mig_web"] == {"aws": 0, "gcp": 100}  # 통과한 서비스는 그대로
    assert haproxy.backends["mig_api"] == {"aws": 100, "gcp": 0}
    assert control.controller.snapshot()["run"]["result"] == "failed"


def test_manual_judges_each_change_from_the_screen_and_ends_partial(tmp_path):
    haproxy = FakeHaproxy()
    control = DirectControl(Controller(list(SERVICES.values()), ("aws", "gcp"), haproxy))
    control.ui_actions = [lambda c: c.set_weight("web", 25, "ui"), lambda c: None,
                          lambda c: c.set_weight("web", 100, "ui"), lambda c: c.set_weight("api", 10, "ui"),
                          lambda c: None, lambda c: c.finish()]
    measurer, calls = _scripted({("api", 10): cutover.Outcome("rolled_back", "판단 fix")})

    code = cutover.run_manual(control, SERVICES, _settings(tmp_path), tmp_path / "state.json", timeout=60,
                              measurer=measurer, sleep=lambda _: None)

    assert calls == [("web", 25), ("web", 100), ("api", 10)]
    assert code == cutover.EXIT_PARTIAL
    assert haproxy.backends["mig_web"] == {"aws": 0, "gcp": 100}
    assert haproxy.backends["mig_api"] == {"aws": 100, "gcp": 0}
    assert json.loads((tmp_path / "state.json").read_text())["services"] == {"web": "completed", "api": "rolled_back"}


def test_manual_times_out_as_a_failure(tmp_path):
    control = DirectControl(Controller(list(SERVICES.values()), ("aws", "gcp"), FakeHaproxy()))
    ticks = iter(range(0, 1000, 10))
    code = cutover.run_manual(control, SERVICES, _settings(tmp_path), tmp_path / "state.json", timeout=30,
                              measurer=_scripted({})[0], sleep=lambda _: None, clock=lambda: next(ticks))
    assert code == cutover.EXIT_FAILED
    assert control.controller.snapshot()["run"]["result"] == "timeout"


def test_rollback_skips_completed_services_and_needs_no_screen(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"services": {"web": "completed", "api": "measuring"}}))
    applied = []
    rolled = cutover.rollback_unfinished(SERVICES, tmp_path / "state.json", "aws", "gcp",
                                         apply=lambda backend, weights: applied.append((backend, weights)))
    assert rolled == ["api"]
    assert applied == [("mig_api", {"aws": 100, "gcp": 0})]


def test_rollback_without_a_state_file_returns_everything_to_the_source(tmp_path):
    rolled = cutover.rollback_unfinished(SERVICES, tmp_path / "missing.json", "aws", "gcp", apply=lambda *_: None)
    assert rolled == ["web", "api"]


def _samples(version, status, count):
    return [traffic.Sample(version, status, 0.01) for _ in range(count)]


def test_measure_rolls_back_on_metric_breach_without_asking_the_judge(tmp_path):
    def judge(_):
        raise AssertionError("기준 초과면 판단을 기다리지 않는다")

    outcome = cutover.measure(SERVICES["api"], 10, _settings(tmp_path),
                              sampler=lambda url, n: _samples("gcp-b9", 500, 30) + _samples("aws-src", 200, 10), judge=judge)
    assert outcome.state == "rolled_back"


def test_measure_maps_verdicts_and_treats_no_answer_as_rollback(tmp_path, judge):
    sampler = lambda url, n: _samples("gcp-b9", 200, 40) + _samples("aws-src", 200, 360)  # noqa: E731
    assert cutover.measure(SERVICES["web"], 10, _settings(tmp_path), sampler=sampler, judge=judge).state == "passed"
    few = lambda url, n: _samples("gcp-b9", 200, 5)  # noqa: E731
    assert cutover.measure(SERVICES["web"], 10, _settings(tmp_path), sampler=few, judge=judge).state == "needs_ack"

    def down(_):
        raise cutover.gate.GateUnavailable("URLError")
    assert cutover.measure(SERVICES["web"], 10, _settings(tmp_path), sampler=sampler, judge=down).state == "rolled_back"


def test_parity_fixes_are_applied_before_asking_a_person(tmp_path):
    """WAF 를 켜기 전 공격 시험은 앱 오류로 unknown 이 나올 수 있다. 고친 뒤 다시 시험해야 한다."""
    result = {"findings": [
        {"finding_id": "a", "rule_id": "PAR-WAF-001", "severity": "high", "status": "fail", "evidence": "WAF 없음",
         "ops": [{"op": "replace", "path": "/waf/enabled", "value": True}], "waf_rules": []},
        {"finding_id": "b", "rule_id": "PAR-WAF-002", "severity": "high", "status": "unknown", "evidence": "500",
         "ops": [], "waf_rules": []}]}
    plan = {"target_env": {"provider": "gcp", "waf": {"enabled": False}}, "waf_rules": []}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "result.json").write_text(json.dumps(result))
    code = mp.main(["parity-fixes", "--plan", str(tmp_path / "plan.json"), "--result", str(tmp_path / "result.json"),
                    "--out", str(tmp_path / "fixes.json")])
    assert code == 0
    assert mp.read_json(tmp_path / "plan.json")["target_env"]["waf"]["enabled"] is True

    (tmp_path / "result.json").write_text(json.dumps({"findings": [result["findings"][1]]}))
    code = mp.main(["parity-fixes", "--plan", str(tmp_path / "plan.json"), "--result", str(tmp_path / "result.json"),
                    "--out", str(tmp_path / "fixes.json")])
    assert code == 20
