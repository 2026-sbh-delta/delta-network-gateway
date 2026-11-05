"""AWS 관리형 규칙 그룹 ↔ 공격 범주 ↔ Cloud Armor 사전 구성 규칙.

두 제품의 규칙은 1:1 이 아니다. 여기 매핑은 "같은 범주를 켠다"는 뜻이지 같은 탐지를 보장하지 않는다 —
동등성 판단은 PAR-WAF-002 의 실제 공격 결과로 한다.
출처: AWS WAF managed rule groups, Google Cloud Armor preconfigured WAF rules (2026-10-31 확인).
"""

from __future__ import annotations

import re

from delta_ai.contract import WafRule

SENSITIVITY = 1  # 높은 신뢰도 시그니처만 — 오탐을 줄이고, 공격 시험으로 부족한지 확인한다
BASE_PRIORITY = 1000
PRIORITY_STEP = 10
CLOUD_ARMOR_CRS_SUFFIX = "-v422-stable"

AWS_GROUP_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "AWSManagedRulesSQLiRuleSet": ("sqli",),
    "AWSManagedRulesCommonRuleSet": ("xss", "lfi", "rfi"),
    "AWSManagedRulesKnownBadInputsRuleSet": ("java", "cve"),
    "AWSManagedRulesLinuxRuleSet": ("lfi",),
    "AWSManagedRulesUnixRuleSet": ("rce",),
    "AWSManagedRulesPHPRuleSet": ("php",),
}
# 범주 외 기능이 있어 일부만 옮겨지는 그룹 — 안내 문구로만 남긴다
AWS_GROUP_PARTIAL_NOTES: dict[str, str] = {
    "AWSManagedRulesCommonRuleSet": "크기 제한·EC2 메타데이터 SSRF·제한 확장자 규칙은 대응 규칙을 정하지 않았다",
    "AWSManagedRulesKnownBadInputsRuleSet": "localhost Host 헤더·PROPFIND 규칙은 대응 규칙을 정하지 않았다",
}
CLOUD_ARMOR_CANARY_ONLY = {"cve": "cve-canary"}
_CLOUD_ARMOR_VERSION = re.compile(r"(-v\d+)?-(stable|canary)$")
_CLOUD_ARMOR_ALIASES = {"cve": "cve", "generic": "nodejs"}


def source_capabilities(rule_groups: tuple[str, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    """(옮길 수 있는 범주, 매핑이 없는 그룹)."""
    capabilities = {c for group in rule_groups for c in AWS_GROUP_CAPABILITIES.get(group, ())}
    unmapped = tuple(group for group in rule_groups if group not in AWS_GROUP_CAPABILITIES)
    return frozenset(capabilities), unmapped


def cloud_armor_capabilities(rule_names: tuple[str, ...]) -> frozenset[str]:
    names = {_CLOUD_ARMOR_VERSION.sub("", name) for name in rule_names}
    return frozenset(_CLOUD_ARMOR_ALIASES.get(name, name) for name in names)


def _rule_name(capability: str) -> str:
    return CLOUD_ARMOR_CANARY_ONLY.get(capability, f"{capability}{CLOUD_ARMOR_CRS_SUFFIX}")


def cloud_armor_rule_names(capabilities: frozenset[str]) -> tuple[str, ...]:
    return tuple(_rule_name(c) for c in sorted(capabilities))


def cloud_armor_rules(capabilities: frozenset[str]) -> tuple[WafRule, ...]:
    return tuple(
        WafRule(
            priority=BASE_PRIORITY + PRIORITY_STEP * index,
            action="deny(403)",
            expression=f"evaluatePreconfiguredWaf('{_rule_name(capability)}', {{'sensitivity': {SENSITIVITY}}})",
            capability=capability,
        )
        for index, capability in enumerate(sorted(capabilities))
    )


# GCP → AWS: 범주마다 켤 AWS 관리형 그룹. lfi 는 Linux 그룹도 있지만 xss·rfi 와 같이 Common 이 덮는다
AWS_CAPABILITY_GROUP: dict[str, str] = {
    "sqli": "AWSManagedRulesSQLiRuleSet",
    "xss": "AWSManagedRulesCommonRuleSet",
    "lfi": "AWSManagedRulesCommonRuleSet",
    "rfi": "AWSManagedRulesCommonRuleSet",
    "java": "AWSManagedRulesKnownBadInputsRuleSet",
    "cve": "AWSManagedRulesKnownBadInputsRuleSet",
    "rce": "AWSManagedRulesUnixRuleSet",
    "php": "AWSManagedRulesPHPRuleSet",
}
AWS_MANAGED_GROUP_ACTION = "managed_group"  # WebACL 에 관리형 그룹을 넣는다. 차단은 그룹의 규칙이 한다


def cloud_armor_source_capabilities(rule_names: tuple[str, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    """GCP 출발: (AWS 로 옮길 수 있는 범주, 대응 그룹이 없는 규칙)."""
    capabilities = {name: next(iter(cloud_armor_capabilities((name,)))) for name in rule_names}
    movable = frozenset(c for c in capabilities.values() if c in AWS_CAPABILITY_GROUP)
    unmapped = tuple(name for name, c in capabilities.items() if c not in AWS_CAPABILITY_GROUP)
    return movable, unmapped


def aws_group_capabilities(rule_groups: tuple[str, ...]) -> frozenset[str]:
    return source_capabilities(rule_groups)[0]


def aws_group_names(capabilities: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted({AWS_CAPABILITY_GROUP[c] for c in capabilities}))


def aws_waf_rules(capabilities: frozenset[str]) -> tuple[WafRule, ...]:
    """범주마다 한 줄을 낸다. 같은 그룹이 여러 범주를 덮어도 파이프라인이 범주별로 확인하려는 것이다."""
    return tuple(
        WafRule(priority=BASE_PRIORITY + PRIORITY_STEP * index, action=AWS_MANAGED_GROUP_ACTION,
                expression=f"AWS/{AWS_CAPABILITY_GROUP[capability]}", capability=capability)
        for index, capability in enumerate(sorted(capabilities))
    )
