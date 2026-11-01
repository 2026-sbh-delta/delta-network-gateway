"""스캐너·방화벽·로그 원본을 호출 계약으로 옮긴다. 파일이나 네트워크를 건드리지 않는 순수 변환이다."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote

from delta_ai.contract import (
    MAX_LOG_SAMPLE_CHARS,
    MAX_LOG_SAMPLES,
    MAX_RUNTIME_PACKAGES,
    ClusterCheck,
    FirewallRule,
    LogSignal,
    PackageVulnerability,
)
from delta_ai.secrets_pattern import redact

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")
_PF_ACTION = re.compile(r"^(pass|block)\b")
_PF_PROTO = re.compile(r"\bproto (\S+)")
_PF_FROM = re.compile(r"\bfrom (! )?(\S+)")
_PF_TO = re.compile(r"\bto (! )?(\S+)(?: port (?:= )?(\S+))?")
_PF_LABEL = re.compile(r'\blabel "([^"]+)"')
_PROBE_RC = re.compile(r"\brc=(\d+)")
_SENSITIVE_ENV = re.compile(r"PASSW|SECRET|TOKEN|CREDENTIAL|API_?KEY|PRIVATE_?KEY", re.IGNORECASE)
_ATTACK_PATTERN = re.compile(r"'\s*or\s|union\s+select|<script|\.\./|/etc/passwd", re.IGNORECASE)
# filterlog CSV 위치 (OPNsense IPv4)
_LOG_LABEL, _LOG_INTERFACE, _LOG_ACTION, _LOG_PROTOCOL, _LOG_SOURCE, _LOG_DEST_PORT = 3, 4, 6, 16, 18, 21


def pf_firewall_rules(pf_rules: str, interface: str) -> tuple[FirewallRule, ...]:
    """`pfctl -sr` 실효 룰에서 한 인터페이스의 quick 룰을 뽑는다. DHCP 자동 룰과 안티스푸핑(`on !`)은 뺀다."""
    rules = []
    for line in pf_rules.splitlines():
        action = _PF_ACTION.match(line)
        if action is None or f" on {interface} " not in line or " quick " not in line or "bootps" in line:
            continue
        protocol = _PF_PROTO.search(line)
        source = _PF_FROM.search(line)
        destination = _PF_TO.search(line, source.end()) if source else None  # route-to 를 목적지로 읽지 않는다
        if source is None or destination is None:
            continue
        rules.append(FirewallRule(interface=interface, action=action.group(1),
                                  protocol=protocol.group(1) if protocol else "any",
                                  source=(source.group(1) or "").strip() + source.group(2),
                                  destination=(destination.group(1) or "").strip() + destination.group(2),
                                  port=destination.group(3)))
    return tuple(rules)


def trivy_packages(report: Mapping[str, Any]) -> tuple[PackageVulnerability, ...]:
    """Trivy JSON 을 패키지별로 묶는다. 심각도 순으로 상한까지만 남긴다."""
    image = str(report.get("ArtifactName") or "unknown")
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for result in report.get("Results") or ():
        for item in result.get("Vulnerabilities") or ():
            grouped.setdefault((item["PkgName"], item.get("InstalledVersion", "")), []).append(item)
    packages = [_package(image, name, version, items) for (name, version), items in grouped.items()]
    return tuple(sorted(packages, key=_severity_rank))


def limit_packages(packages: Iterable[PackageVulnerability],
                   limit: int = MAX_RUNTIME_PACKAGES) -> tuple[tuple[PackageVulnerability, ...], int]:
    """심각한 순으로 상한까지 남기고, 버린 수를 돌려준다. 호출자가 버린 사실을 알릴 수 있어야 한다."""
    ordered = sorted(packages, key=_severity_rank)
    return tuple(ordered[:limit]), max(len(ordered) - limit, 0)


def _severity_rank(package: PackageVulnerability) -> int:
    return _SEVERITY_ORDER.index(package.severity)


def _package(image: str, name: str, version: str, items: Sequence[Mapping[str, Any]]) -> PackageVulnerability:
    severities = {str(i.get("Severity", "UNKNOWN")).lower() for i in items}
    severity = next((s for s in _SEVERITY_ORDER if s in severities), "unknown")
    fixed = sorted({v.strip() for i in items for v in str(i.get("FixedVersion") or "").split(",") if v.strip()})
    ids = tuple(dict.fromkeys(str(i["VulnerabilityID"]) for i in items))
    return PackageVulnerability(image=image, package=name, installed_version=version, severity=severity,
                                fixed_versions=tuple(fixed), vulnerability_ids=ids)


def kube_bench_checks(report: Mapping[str, Any], verified: Mapping[str, str]) -> tuple[ClusterCheck, ...]:
    """FAIL 만 옮긴다. verified 는 실측으로 오탐을 확인한 항목과 그 근거다."""
    results = (r for control in report.get("Controls", ()) for test in control.get("tests", ())
               for r in test.get("results", ()))
    return tuple(ClusterCheck(check_id=r["test_number"], title=r.get("test_desc", ""),
                              status="false_positive" if r["test_number"] in verified else "fail",
                              verification=verified.get(r["test_number"]))
                 for r in results if r.get("status") == "FAIL")


def k3s_false_positives(kubelet: Mapping[str, Any], apiserver_args: str, *, anonymous_status: int | None,
                        read_only_open: bool | None) -> dict[str, str]:
    """kube-bench 가 k3s 구조를 읽지 못해 낸 FAIL 중 실효 설정과 실측이 일치하는 것만 오탐으로 돌려준다."""
    authentication = kubelet.get("authentication") or {}
    checks = {
        "1.2.14": ("ServiceAccount 플러그인을 끄지 않음 (기본 활성)",
                   "kube-apiserver" in apiserver_args and "--disable-admission-plugins" not in apiserver_args),
        "1.2.29": ("저장소가 kine 유닉스 소켓이라 etcd TLS 인자가 적용되지 않음",
                   "--etcd-servers=unix://kine.sock" in apiserver_args),
        "4.2.1": (f"kubelet 설정 anonymous=false, 익명 요청 {anonymous_status}",
                  (authentication.get("anonymous") or {}).get("enabled") is False and anonymous_status == 401),
        "4.2.2": (f"kubelet 인가 모드 {(kubelet.get('authorization') or {}).get('mode')}",
                  (kubelet.get("authorization") or {}).get("mode") == "Webhook"),
        "4.2.3": ("kubelet 설정에 clientCAFile 있음", bool((authentication.get("x509") or {}).get("clientCAFile"))),
        "4.2.4": ("kubelet 설정 readOnlyPort=0, 10255 닫힘",
                  not kubelet.get("readOnlyPort") and read_only_open is False),
        "4.2.10": ("kubelet 설정에 TLS 인증서와 키 있음",
                   bool(kubelet.get("tlsCertFile") and kubelet.get("tlsPrivateKeyFile"))),
    }
    return {check_id: reason for check_id, (reason, holds) in checks.items() if holds}


def pf_self_block_label(pf_rules: str, interface: str) -> str | None:
    """방화벽 자신을 막는 룰의 라벨. 이 라벨로 차단 로그를 센다."""
    for line in pf_rules.splitlines():
        if f" on {interface} " in line and line.startswith("block") and " quick " in line and " to (self)" in line:
            label = _PF_LABEL.search(line)
            return label.group(1) if label else None
    return None


def probe_observed(output: str) -> Literal["open", "closed", "unknown"]:
    """연결 시험 출력의 rc= 를 읽는다. 시험 도구가 실패한 것을 닫힘으로 읽지 않는다."""
    code = _PROBE_RC.search(output)
    if code is None:
        return "unknown"
    return {"0": "open", "1": "closed", "124": "closed"}.get(code.group(1), "unknown")


@dataclass(frozen=True)
class ClusterFacts:
    ingress_tls: bool | None
    secrets_source: Literal["env_plaintext", "k8s_secret"] | None
    plaintext_env: tuple[str, ...]
    images: tuple[str, ...]


def cluster_facts(ingresses: Sequence[Mapping[str, Any]], workloads: Sequence[Mapping[str, Any]]) -> ClusterFacts:
    """Ingress·워크로드 manifest 에서 사실만 뽑는다. 환경 변수 값은 읽지 않는다."""
    containers = [c for w in workloads for c in w["spec"]["template"]["spec"]["containers"]]
    env = [e for c in containers for e in c.get("env") or ()]
    plaintext = tuple(e["name"] for e in env if "value" in e and _SENSITIVE_ENV.search(e["name"]))
    has_secret_ref = any((e.get("valueFrom") or {}).get("secretKeyRef") for e in env)
    source = "env_plaintext" if plaintext else "k8s_secret" if has_secret_ref else None
    tls = all((i.get("spec") or {}).get("tls") for i in ingresses) if ingresses else None
    return ClusterFacts(ingress_tls=tls, secrets_source=source, plaintext_env=plaintext,
                        images=tuple(dict.fromkeys(c["image"] for c in containers)))


def _samples(values: Iterable[str]) -> tuple[str, ...]:
    unique = dict.fromkeys(redact(value)[:MAX_LOG_SAMPLE_CHARS] for value in values)
    return tuple(unique)[:MAX_LOG_SAMPLES]


def filter_log_signal(lines: Iterable[str], label: str, *, source: str) -> LogSignal:
    """한 룰의 차단 로그를 센다. 목적지는 공인 IP 일 수 있어 표본에 넣지 않는다."""
    samples = []
    count = 0
    for line in lines:
        _, separator, csv = line.partition("] ")
        fields = csv.split(",") if separator else []
        if len(fields) <= _LOG_DEST_PORT or fields[_LOG_LABEL] != label or fields[_LOG_ACTION] != "block":
            continue
        count += 1
        samples.append(f"{fields[_LOG_SOURCE]} → firewall:{fields[_LOG_DEST_PORT]}/{fields[_LOG_PROTOCOL]}")
    return LogSignal(source=source, signal="firewall_block", count=count, samples=_samples(samples))


def traefik_log_signals(lines: Iterable[str], *, source: str) -> tuple[LogSignal, ...]:
    """Traefik JSON access log 에서 5xx 와 공격 형태 요청을 센다. 공격 판별은 시연용 문자열 패턴이다."""
    total = errors = 0
    allowed: list[str] = []
    blocked: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
            status = int(entry["DownstreamStatus"])
            path = unquote(str(entry.get("RequestPath", "")))
        except (ValueError, KeyError, TypeError):
            continue
        total += 1
        errors += status >= 500
        if _ATTACK_PATTERN.search(path):
            (allowed if status < 400 else blocked).append(path)
    return (LogSignal(source=source, signal="http_5xx", count=errors, total=total),
            LogSignal(source=source, signal="attack_request_allowed", count=len(allowed), samples=_samples(allowed)),
            LogSignal(source=source, signal="attack_request_blocked", count=len(blocked), samples=_samples(blocked)))
