#!/usr/bin/env python3
"""이전 판단 결과를 실행할 일로 바꾼다. 판정은 하지 않는다(판단 서비스 몫). 표준 라이브러리만 쓴다.

상태는 파일로만 넘긴다. 함수는 입력을 바꾸지 않고 새 값을 돌려준다.
- inventory: 출발 환경 인벤토리(시나리오 적용 후)
- state: 파이프라인이 적용한 수정안과 사람 결정. 매 판단마다 inventory + state 로 입력을 새로 만든다
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

DIRECTIONS: dict[str, tuple[str, str]] = {"aws-to-gcp": ("aws", "gcp"), "gcp-to-aws": ("gcp", "aws")}
PLATFORMS = {"x86_64": "linux/amd64", "arm64": "linux/arm64"}
ARCH_OP = re.compile(r"^/mapping/servers/(\d+)/multi_arch_image$")
CIDR_OP = re.compile(r"^/mapping/subnets/(\d+)/target_cidr$")
CLOUD_ARMOR_VERSION = re.compile(r"(-v\d+)?-(stable|canary)$")
SCC_TIERS = ("premium", "standard")
# 대상 root. 로컬 실행기는 Terraform 을 돌리지 않고, 채울 값과 root 에 없는 변수만 낸다
TERRAFORM_ROOTS = {"gcp": "terraform/gcp-server", "aws": "terraform/workload-server"}
AWS_ROOT_FIXED = {"instance_type": "t4g.nano", "architecture": "arm64"}  # workload-server 에 박혀 있는 값
# AWS 관리형 그룹 → 범주. delta-ai waf_mapping 과 같은 표다(이 스크립트는 표준 라이브러리만 쓴다)
AWS_GROUP_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "AWSManagedRulesSQLiRuleSet": ("sqli",),
    "AWSManagedRulesCommonRuleSet": ("xss", "lfi", "rfi"),
    "AWSManagedRulesKnownBadInputsRuleSet": ("java", "cve"),
    "AWSManagedRulesLinuxRuleSet": ("lfi",),
    "AWSManagedRulesUnixRuleSet": ("rce",),
    "AWSManagedRulesPHPRuleSet": ("php",),
}
EMPTY_STATE: dict[str, Any] = {"applied_ops": [], "scc_tier": None, "accepted_gaps": [], "approved_findings": []}


class PlanError(ValueError):
    """판단 결과를 실행 계획으로 옮길 수 없다. 추측하지 않고 멈춘다."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- 시나리오 ----

def _source(inventory: Mapping[str, Any]) -> str:
    return DIRECTIONS[inventory["direction"]][0]


def _target(inventory: Mapping[str, Any]) -> str:
    return DIRECTIONS[inventory["direction"]][1]


def _scenario_iam_gap(inventory: dict[str, Any]) -> None:
    """옮길 수 있는 권한 하나와 대응표에 없는 권한을 섞는다."""
    server = inventory["mapping"]["servers"][1]
    if _source(inventory) == "aws":
        server.update(instance_profile=True, permissions=["secretsmanager:GetSecretValue", "ssm:GetParameter",
                                                          "ecr:GetAuthorizationToken"])
    else:
        server.update(service_account=True, permissions=["roles/secretmanager.secretAccessor",
                                                         "roles/pubsub.publisher"])


def _scenario_security_gap(inventory: dict[str, Any]) -> None:
    """대응을 정하지 않은 보안 기능을 하나 켠다. 사람이 잃어도 되는지 정해야 넘어간다."""
    security = inventory["mapping"]["security"]
    if _source(inventory) == "aws":
        security["guardduty_plans"] = [*security["guardduty_plans"], "S3_DATA_EVENTS"]
    else:
        security["scc_services"] = [*security["scc_services"], "Security Health Analytics"]


def _scenario_bad_target(inventory: dict[str, Any]) -> None:
    inventory["services"][1]["target_fail_rate"] = 0.5


SCENARIOS = {"good": None, "scc-standard": None, "gate-down": None, "iam-gap": _scenario_iam_gap,
             "security-gap": _scenario_security_gap, "bad-target": _scenario_bad_target}


def apply_scenario(inventory: Mapping[str, Any], name: str) -> dict[str, Any]:
    if name not in SCENARIOS:
        raise PlanError(f"모르는 시나리오: {name}")
    result = copy.deepcopy(dict(inventory))
    change = SCENARIOS[name]
    if change:
        change(result)
    return result


# ---- 이전 판단 입력 ----

def initial_state(scc_tier: str | None, accepted: Iterable[str]) -> dict[str, Any]:
    if scc_tier not in (None, *SCC_TIERS):
        raise PlanError(f"모르는 SCC 등급: {scc_tier}")
    return {**copy.deepcopy(EMPTY_STATE), "scc_tier": scc_tier, "accepted_gaps": sorted(set(accepted))}


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split("/") if p]
    target: Any = document
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target.setdefault(part, {})
    last = parts[-1]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value


def apply_ops(document: Mapping[str, Any], ops: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """add·replace 만 다룬다. remove 나 다른 연산은 받지 않는다."""
    result = copy.deepcopy(dict(document))
    for op in ops:
        if op.get("op") not in ("add", "replace"):
            raise PlanError(f"적용하지 않는 연산: {op.get('op')} {op.get('path')}")
        _set_path(result, op["path"], copy.deepcopy(op.get("value")))
    return result


def mapping_input(inventory: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    source, target = DIRECTIONS[inventory["direction"]]
    evidence = apply_ops({"mapping": inventory["mapping"]}, state["applied_ops"])
    mapping = {**evidence["mapping"], "source_provider": source, "target_provider": target,
               "accepted_gaps": list(state["accepted_gaps"])}
    if state.get("scc_tier") and target == "gcp":  # GuardDuty 는 등급이 없어 AWS 대상에는 보내지 않는다
        mapping["scc_tier"] = state["scc_tier"]
    return mapping


def mapping_request(inventory: Mapping[str, Any], state: Mapping[str, Any], assessment_id: str) -> dict[str, Any]:
    return {"assessment_id": assessment_id, "kind": "migration_mapping",
            "service": {"service_id": inventory["service_id"]},
            "evidence": {"mapping": mapping_input(inventory, state)}}


def _blocking(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [f for f in result.get("findings", []) if f["severity"] != "low" and f["status"] != "dismissed"]


def classify_fixes(result: Mapping[str, Any], inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    """수정안 하나하나를 파이프라인이 할 일로 나눈다. 모르는 경로는 unsupported 로 사람에게 넘긴다."""
    servers = inventory["mapping"]["servers"]
    fixes = []
    for finding in _blocking(result):
        for op in finding.get("ops", []):
            entry = {"rule_id": finding["rule_id"], "finding_id": finding["finding_id"], "op": op}
            arch = ARCH_OP.match(op["path"])
            if arch and op.get("value") is True and int(arch.group(1)) < len(servers):
                entry |= {"kind": "build_arch", "server": servers[int(arch.group(1))]["name"]}
            elif CIDR_OP.match(op["path"]):
                entry |= {"kind": "set"}
            else:
                entry |= {"kind": "unsupported"}
            fixes.append(entry)
    return fixes


def record_fixes(state: Mapping[str, Any], fixes: Sequence[Mapping[str, Any]], kinds: Iterable[str]) -> dict[str, Any]:
    wanted = set(kinds)
    applied = [f["op"] for f in fixes if f["kind"] in wanted]
    if not applied:
        raise PlanError("적용할 수정안이 없다")
    return {**copy.deepcopy(dict(state)), "applied_ops": [*state["applied_ops"], *applied]}


def human_questions(result: Mapping[str, Any], inventory: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """사람이 정할 것만 모은다. 고칠 수 있는 수정안이 남아 있으면 그것부터 한다(fixable > 0)."""
    security = inventory["mapping"].get("security") or {}
    blocking = _blocking(result)
    # SCC 등급은 GCP 대상에서만 묻는다. 출발이 GuardDuty 를 쓸 때만 위협 탐지 등급이 의미가 있다
    fixable = [f for f in blocking if f.get("ops")]
    gaps = [f for f in blocking if f["rule_id"] == "MAP-SEC-001" and f["status"] == "fail"
            and f.get("location", {}).get("capability")]
    needs_tier = _target(inventory) == "gcp" and bool(security.get("guardduty_enabled")) and not state.get("scc_tier")
    tier_findings = [f for f in blocking if f["rule_id"] == "MAP-SEC-001" and f["status"] == "unknown"] if needs_tier else []
    handled = {f["finding_id"] for f in (*fixable, *gaps, *tier_findings)}
    others = [f for f in blocking if f["finding_id"] not in handled]
    unapproved = [f for f in others if f["finding_id"] not in state["approved_findings"]]
    return {
        "fixable": len(fixable),
        "needs_scc_tier": needs_tier,
        "acceptable_gaps": [{"capability": f["location"]["capability"], "reason": f["evidence"]} for f in gaps],
        "others": [{"finding_id": f["finding_id"], "rule_id": f["rule_id"], "evidence": f["evidence"]} for f in unapproved],
        "resolved": not fixable and not needs_tier and not gaps and not unapproved,
    }


def question_text(questions: Mapping[str, Any]) -> str:
    lines = []
    if questions["needs_scc_tier"]:
        lines.append("- 대상 프로젝트의 Security Command Center 등급을 고른다 (standard 면 일부 위협 탐지를 잃는다)")
    lines += [f"- 잃어도 되는 능력인가: {g['capability']} — {g['reason']}" for g in questions["acceptable_gaps"]]
    lines += [f"- 자동으로 옮기지 않는 차이 [{o['rule_id']}]: {o['evidence']}" for o in questions["others"]]
    return "\n".join(lines) or "- 정할 것 없음"


def decide(state: Mapping[str, Any], questions: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """사람 답을 상태에 넣는다. 승인하지 않은 차이가 남으면 진행할 수 없다."""
    result = copy.deepcopy(dict(state))
    if questions["needs_scc_tier"]:
        tier = answers.get("SCC_TIER")
        if tier not in SCC_TIERS:
            raise PlanError("SCC 등급을 고르지 않았다")
        result["scc_tier"] = tier
    accepted = {g["capability"] for g in questions["acceptable_gaps"] if answers.get(f"ACCEPT_{g['capability']}") is True}
    declined = [g["capability"] for g in questions["acceptable_gaps"] if g["capability"] not in accepted]
    if declined:
        # 잃을 수 없는 능력이면 대상 설계(Premium 등)를 바꿔 다시 시작해야 한다. 같은 질문을 되풀이하지 않는다
        raise PlanError(f"잃어도 된다고 승인하지 않은 능력: {', '.join(declined)}")
    result["accepted_gaps"] = sorted(set(result["accepted_gaps"]) | accepted)
    if questions["others"]:
        if answers.get("APPROVE_REMAINING") is not True:
            raise PlanError("자동으로 옮기지 않는 차이를 승인하지 않았다")
        result["approved_findings"] = sorted({*result["approved_findings"], *(o["finding_id"] for o in questions["others"])})
    return result


# ---- 대상 계획 ----

def _rows(result: Mapping[str, Any], resource: str) -> dict[str, dict[str, Any]]:
    """행의 source 는 '<서버>: <값>' 꼴이다. 서브넷 행은 이름만 있다."""
    rows = {}
    for row in result["mapping"]["rows"]:
        if row["resource"] == resource:
            rows[row["source"].split(":", 1)[0].strip()] = row
    return rows


def _target_of(rows: Mapping[str, Mapping[str, Any]], server: str, label: str) -> str:
    row = rows.get(server)
    if row is None or not row.get("target"):
        raise PlanError(f"{server} 의 대상 {label} 이 판단 결과에 없다")
    return row["target"]


def target_plan(inventory: Mapping[str, Any], result: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("verdict") != "pass" and not human_questions(result, inventory, state)["resolved"]:
        raise PlanError(f"이전 판단이 끝나지 않았다: {result.get('verdict')}")
    source, target = DIRECTIONS[inventory["direction"]]
    compute, os_rows = _rows(result, "compute"), _rows(result, "os")
    platforms = dict(target_platforms(inventory, result))
    services = [{**copy.deepcopy(service), "machine_type": _target_of(compute, service["server"], "머신 타입"),
                 "target_platform": platforms[service["id"]], "os": _target_of(os_rows, service["server"], "OS")}
                for service in inventory["services"]]
    subnets = [{"source": name, "target_cidr": row["target"].split(":", 1)[0].strip()}
               for name, row in _rows(result, "subnet").items() if row.get("target")]
    gaps = [{"source": r["source"], "rule_id": r["rule_id"], "differences": r["differences"]}
            for r in result["mapping"]["rows"] if r["grade"] in ("gap", "unknown")]
    security = [part.strip() for r in result["mapping"]["rows"]
                if r["resource"] == "security" and r["grade"] in ("exact", "approximate", "split") and r.get("target")
                for part in r["target"].split(",")]
    return {"direction": inventory["direction"], "source": source, "target": target,
            "region": result["mapping"]["region"], "services": services, "subnets": subnets,
            "security": security, "target_env": copy.deepcopy(inventory["target_baseline"]), "waf_rules": [],
            "decisions": {"scc_tier": state.get("scc_tier"), "accepted_gaps": state["accepted_gaps"],
                          "approved_findings": state["approved_findings"]},
            "remaining_gaps": gaps}


def target_tfvars(plan: Mapping[str, Any]) -> dict[str, Any]:
    """대상 root 에 이미 있는 변수만 채운다. 없는 변수는 terraform_gaps 가 낸다."""
    values: dict[str, Any] = {"region": plan["region"]}
    if plan["target"] == "gcp" and plan["subnets"]:
        values["server_subnet_cidr"] = plan["subnets"][0]["target_cidr"]
    # workload-server 의 azs 는 AZ 두 개와 TGW 서브넷까지 받아서 서브넷 하나로는 채우지 않는다
    return values


def _aws_root_gaps(plan: Mapping[str, Any]) -> dict[str, Any]:
    types = {s["id"]: s["machine_type"] for s in plan["services"] if s["machine_type"] != AWS_ROOT_FIXED["instance_type"]}
    archs = {s["id"]: s["target_platform"] for s in plan["services"]
             if s["target_platform"] != f"linux/{AWS_ROOT_FIXED['architecture']}"}
    features = [name for name in plan.get("security", []) if name.isupper()]  # GuardDuty 기능 이름만 대문자다
    return {"instance_type": {"fixed": AWS_ROOT_FIXED["instance_type"], "needed": types},
            "ami_architecture": {"fixed": AWS_ROOT_FIXED["architecture"], "needed": archs},
            "wafv2_web_acl_rules": plan["waf_rules"], "guardduty_features": features,
            "subnets": plan["subnets"]}


def terraform_gaps(plan: Mapping[str, Any]) -> dict[str, Any]:
    target = plan["target"]
    if target == "aws":
        missing = _aws_root_gaps(plan)
    else:
        missing = {"machine_type": {s["id"]: s["machine_type"] for s in plan["services"]},
                   "security_policy_rules": plan["waf_rules"]}
    return {"root": TERRAFORM_ROOTS[target], "missing_variables": missing}


# ---- 동등성 ----

def waf_capabilities(rule_names: Iterable[str]) -> list[str]:
    """Cloud Armor 규칙 이름이나 AWS 관리형 그룹 이름을 범주로 바꾼다."""
    capabilities: set[str] = set()
    for name in rule_names:
        capabilities.update(AWS_GROUP_CAPABILITIES.get(name, (CLOUD_ARMOR_VERSION.sub("", name),)))
    return sorted(capabilities)


def edge_capabilities(plan: Mapping[str, Any]) -> list[str]:
    waf = plan["target_env"].get("waf", {})
    if not waf.get("enabled"):
        return []
    return sorted({*waf_capabilities(waf.get("rule_groups") or ()), *(r["capability"] for r in plan["waf_rules"])})


def controlled_attacks(source: Sequence[Mapping[str, Any]], target: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """출발 환경이 막지 못한 시험은 시험 자체를 믿을 수 없으므로 대상 결과를 unknown 으로 낸다(양성 대조)."""
    source_blocked = {a["scenario"] for a in source if a["observed"] == "blocked"}
    checked = []
    for attack in target:
        entry = dict(attack)
        if attack["scenario"] not in source_blocked:
            entry["observed"] = "unknown"
        checked.append(entry)
    return [*map(dict, source), *checked]


def parity_request(inventory: Mapping[str, Any], plan: Mapping[str, Any], attacks: Sequence[Mapping[str, Any]],
                   assessment_id: str) -> dict[str, Any]:
    return {"assessment_id": assessment_id, "kind": "migration_parity",
            "service": {"service_id": inventory["service_id"]},
            "source_env": inventory["source_env"], "target_env": plan["target_env"],
            "evidence": {"attack_results": list(attacks)}}


def apply_parity_fixes(plan: Mapping[str, Any], result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """수정안이 있는 차이만 대상 계획에 넣는다. 나머지는 사람 몫으로 돌려준다."""
    blocking = _blocking(result)
    # 여러 차이(WAF 없음, 공격 통과)가 같은 경로를 고치라고 한다. 경로마다 마지막 값 하나만 넣는다
    ops = list({op["path"]: op for f in blocking for op in f.get("ops", [])}.values())
    rules = {r["capability"]: r for r in plan["waf_rules"]}
    rules |= {r["capability"]: r for f in blocking for r in f.get("waf_rules", [])}
    new_plan = {**copy.deepcopy(dict(plan)), "target_env": apply_ops(plan["target_env"], ops),
                "waf_rules": sorted(rules.values(), key=lambda r: r["priority"])}
    human = [{"rule_id": f["rule_id"], "evidence": f["evidence"]} for f in blocking
             if not f.get("ops") and not f.get("waf_rules")]
    return new_plan, {"applied_ops": ops, "waf_rules": len(new_plan["waf_rules"]), "human": human}


# ---- CLI ----

def _cmd_init(args: argparse.Namespace) -> int:
    inventory = apply_scenario(read_json(args.inventory), args.scenario)
    tier = None if args.scc_tier == "ask" else args.scc_tier
    write_json(args.out_inventory, inventory)
    write_json(args.out_state, initial_state(tier, [g for g in args.accept.split(",") if g]))
    return 0


def _cmd_mapping_request(args: argparse.Namespace) -> int:
    write_json(args.out, mapping_request(read_json(args.inventory), read_json(args.state), args.id))
    return 0


def _cmd_fixes(args: argparse.Namespace) -> int:
    fixes = classify_fixes(read_json(args.result), read_json(args.inventory))
    write_json(args.out, fixes)
    for fix in fixes:
        print(f"{fix['kind']}: [{fix['rule_id']}] {fix['op']['op']} {fix['op']['path']}={json.dumps(fix['op'].get('value'))}")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    write_json(args.state, record_fixes(read_json(args.state), read_json(args.fixes), args.kinds.split(",")))
    return 0


def _cmd_questions(args: argparse.Namespace) -> int:
    questions = human_questions(read_json(args.result), read_json(args.inventory), read_json(args.state))
    write_json(args.out, {**questions, "text": question_text(questions)})
    print(question_text(questions))
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    questions = read_json(args.questions)
    write_json(args.state, decide(read_json(args.state), questions, read_json(args.answers)))
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    plan = target_plan(read_json(args.inventory), read_json(args.result), read_json(args.state))
    write_json(args.out, plan)
    write_json(args.tfvars, target_tfvars(plan))
    for service in plan["services"]:
        print(f"{service['id']}: {service['machine_type']} {service['target_platform']} {service['os']}")
    return 0


def _cmd_parity_request(args: argparse.Namespace) -> int:
    source = read_json(args.attacks_source) if args.attacks_source else []
    target = read_json(args.attacks_target) if args.attacks_target else []
    write_json(args.out, parity_request(read_json(args.inventory), read_json(args.plan), controlled_attacks(source, target),
                                        args.id))
    return 0


def _cmd_parity_fixes(args: argparse.Namespace) -> int:
    plan, summary = apply_parity_fixes(read_json(args.plan), read_json(args.result))
    write_json(args.plan, plan)
    write_json(args.out, summary)
    print(f"적용한 수정안 {len(summary['applied_ops'])}개, WAF 규칙 {summary['waf_rules']}개, 사람 확인 {len(summary['human'])}개")
    # 고친 것이 있으면 다시 시험·판단한다. 공격 시험 결과처럼 WAF 를 켠 뒤 바뀌는 차이가 있다
    return 0 if summary["applied_ops"] or not summary["human"] else 20


def target_platforms(inventory: Mapping[str, Any], result: Mapping[str, Any]) -> list[tuple[str, str]]:
    arch = _rows(result, "cpu_arch")
    platforms = []
    for service in inventory["services"]:
        target = _target_of(arch, service["server"], "CPU 구조")
        if target not in PLATFORMS:
            raise PlanError(f"{service['server']} 의 대상 CPU 구조를 빌드할 수 없다: {target}")
        platforms.append((service["id"], PLATFORMS[target]))
    return platforms


def input_spec(questions: Mapping[str, Any]) -> list[str]:
    """Jenkins 입력 단계를 만들 줄 목록. Groovy 샌드박스에서 JSON 을 읽지 않으려고 쓴다."""
    lines = ["TIER"] if questions["needs_scc_tier"] else []
    lines += [f"GAP {g['capability']}" for g in questions["acceptable_gaps"]]
    return [*lines, *(["OTHERS"] if questions["others"] else [])]


def _cmd_get(args: argparse.Namespace) -> int:
    """Groovy 가 읽기 쉬운 한 줄 형식으로 낸다."""
    what = args.what
    if what == "edge-capabilities":
        print(",".join(edge_capabilities(read_json(args.plan))))
    elif what == "terraform-gaps":
        write_json(args.out, terraform_gaps(read_json(args.plan)))
    elif what == "services":
        for s in read_json(args.inventory)["services"]:
            print(f"{s['id']} {s['source_platform']} {s.get('target_fail_rate', 0)}")
    elif what == "source-capabilities":
        print(",".join(sorted(read_json(args.inventory)["source_edge"]["waf_capabilities"])))
    elif what == "databases":
        print(len(read_json(args.inventory).get("databases", [])))
    elif what == "target-platforms":
        for service_id, platform in target_platforms(read_json(args.inventory), read_json(args.result)):
            print(f"{service_id} {platform}")
    elif what == "input-spec":
        print("\n".join(input_spec(read_json(args.questions))))
    elif what == "question-text":
        print(question_text(read_json(args.questions)))
    elif what == "resolved":
        return 0 if read_json(args.questions)["resolved"] else 3
    elif what == "autofixable":
        return 0 if any(f["kind"] != "unsupported" for f in read_json(args.fixes)) else 3
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init")
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--scenario", default="good")
    p.add_argument("--scc-tier", choices=("ask", *SCC_TIERS), default="ask")
    p.add_argument("--accept", default="")
    p.add_argument("--out-inventory", type=Path, required=True)
    p.add_argument("--out-state", type=Path, required=True)
    p.set_defaults(run=_cmd_init)
    p = sub.add_parser("mapping-request")
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=_cmd_mapping_request)
    p = sub.add_parser("fixes")
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=_cmd_fixes)
    p = sub.add_parser("record")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--fixes", type=Path, required=True)
    p.add_argument("--kinds", required=True)
    p.set_defaults(run=_cmd_record)
    p = sub.add_parser("questions")
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=_cmd_questions)
    p = sub.add_parser("decide")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--answers", type=Path, required=True)
    p.set_defaults(run=_cmd_decide)
    p = sub.add_parser("plan")
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--tfvars", type=Path, required=True)
    p.set_defaults(run=_cmd_plan)
    p = sub.add_parser("parity-request")
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--attacks-source", type=Path)
    p.add_argument("--attacks-target", type=Path)
    p.add_argument("--id", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=_cmd_parity_request)
    p = sub.add_parser("parity-fixes")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(run=_cmd_parity_fixes)
    p = sub.add_parser("get")
    p.add_argument("what", choices=("edge-capabilities", "terraform-gaps", "services", "source-capabilities",
                                    "databases", "target-platforms", "input-spec", "question-text", "resolved",
                                    "autofixable"))
    for name in ("plan", "inventory", "result", "questions", "fixes", "out"):
        p.add_argument(f"--{name}", type=Path)
    p.set_defaults(run=_cmd_get)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.run(args)
    except (PlanError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"이전 계획 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
