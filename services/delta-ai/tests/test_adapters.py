from __future__ import annotations

import json

import pytest

from delta_ai.adapters import (
    cluster_facts,
    filter_log_signal,
    k3s_false_positives,
    kube_bench_checks,
    limit_packages,
    pf_firewall_rules,
    pf_self_block_label,
    probe_observed,
    traefik_log_signals,
    trivy_packages,
)
from delta_ai.contract import AssessmentRequest, PackageVulnerability

PF_BEFORE = """\
block drop in log on ! vlan04 inet from 10.10.40.0/24 to any
pass in log quick on vlan04 inet proto udp from any port = bootpc to 255.255.255.255 port = bootps keep state label "a"
pass in log quick on vlan04 proto udp from any port = bootpc to (self) port = bootps keep state label "b"
pass in quick on vlan04 inet from any to 10.10.40.1 flags S/SA keep state label "c"
block drop in quick on vlan04 inet from any to 10.0.0.0/8 label "d"
pass in quick on vlan04 inet from (vlan04:network) to any flags S/SA keep state label "e"
pass in quick on vlan03 inet from (vlan03:network) to any flags S/SA keep state label "f"
"""
PF_AFTER = """\
pass in quick on vlan04 inet proto tcp from any to 192.0.2.1 port = domain flags S/SA keep state label "a"
pass in quick on vlan04 inet proto udp from any to 192.0.2.1 port = ntp keep state label "b"
block drop in log quick on vlan04 inet from any to (self) label "c"
pass in quick on vlan04 inet from (vlan04:network) to any flags S/SA keep state label "d"
"""


def test_pf_rules_keep_only_interface_rules_and_skip_dhcp_and_antispoof():
    rules = pf_firewall_rules(PF_BEFORE, "vlan04")

    assert [(r.action, r.protocol, r.destination, r.port) for r in rules] == [
        ("pass", "any", "10.10.40.1", None),
        ("block", "any", "10.0.0.0/8", None),
        ("pass", "any", "any", None),
    ]


def test_pf_rules_parse_protocol_and_port():
    rules = pf_firewall_rules(PF_AFTER, "vlan04")

    assert [(r.action, r.protocol, r.destination, r.port) for r in rules][:3] == [
        ("pass", "tcp", "192.0.2.1", "domain"),
        ("pass", "udp", "192.0.2.1", "ntp"),
        ("block", "any", "(self)", None),
    ]


def test_trivy_vulnerabilities_are_grouped_by_package():
    report = {"ArtifactName": "app:1", "Results": [
        {"Vulnerabilities": [
            {"PkgName": "libcrypto3", "InstalledVersion": "3.3.2", "FixedVersion": "3.3.3",
             "Severity": "HIGH", "VulnerabilityID": "CVE-1"},
            {"PkgName": "libcrypto3", "InstalledVersion": "3.3.2", "FixedVersion": "3.3.7",
             "Severity": "CRITICAL", "VulnerabilityID": "CVE-2"},
            {"PkgName": "busybox", "InstalledVersion": "1.0", "Severity": "HIGH", "VulnerabilityID": "CVE-3"},
        ]},
        {"Target": "no vulnerabilities"},
    ]}

    packages = trivy_packages(report)

    assert [(p.package, p.severity, p.vulnerability_ids, p.fixed_versions) for p in packages] == [
        ("libcrypto3", "critical", ("CVE-1", "CVE-2"), ("3.3.3", "3.3.7")),
        ("busybox", "high", ("CVE-3",), ()),
    ]


def test_kube_bench_returns_only_failures_and_applies_verification():
    report = {"Controls": [{"tests": [{"results": [
        {"test_number": "4.2.4", "test_desc": "read-only", "status": "FAIL"},
        {"test_number": "4.2.6", "test_desc": "kernel", "status": "FAIL"},
        {"test_number": "1.1", "test_desc": "ok", "status": "PASS"},
        {"test_number": "1.2", "test_desc": "manual", "status": "WARN"},
    ]}]}]}

    checks = kube_bench_checks(report, {"4.2.4": "10255 닫힘"})

    assert [(c.check_id, c.status, c.verification) for c in checks] == [
        ("4.2.4", "false_positive", "10255 닫힘"), ("4.2.6", "fail", None)]


def _filter_line(label: str, action: str = "block", iface: str = "vlan04", dport: str = "22") -> str:
    return (f'<134>1 2026-11-02T00:44:26+09:00 fw filterlog 1 - [meta sequenceId="1"] 95,,,{label},{iface},match,'
            f"{action},in,4,0x0,,63,52223,0,DF,6,tcp,60,192.0.2.10,203.0.113.5,41000,{dport},0,S,1,,64240,,mss")


def test_filter_log_counts_matching_blocks_and_hides_destination():
    lines = [_filter_line("rule-1"), _filter_line("rule-1", dport="443"), _filter_line("other"),
             _filter_line("rule-1", action="pass"), "garbage line"]

    signal = filter_log_signal(lines, "rule-1", source="opnsense")

    assert (signal.signal, signal.count) == ("firewall_block", 2)
    assert "203.0.113.5" not in " ".join(signal.samples)
    assert "192.0.2.10 → firewall:22/tcp" in signal.samples


def _access(status: int, path: str) -> str:
    return json.dumps({"DownstreamStatus": status, "RequestPath": path, "RequestHost": "demo"})


def test_traefik_logs_become_attack_and_5xx_signals():
    lines = [_access(200, "/"), _access(200, "/?id=1'%20OR%20'1'='1"), _access(403, "/?q=%3Cscript%3E"),
             _access(502, "/api"), "not-json", _access(200, "/?f=../../etc/passwd")]

    signals = {s.signal: s for s in traefik_log_signals(lines, source="traefik")}

    assert signals["attack_request_allowed"].count == 2
    assert signals["attack_request_blocked"].count == 1
    assert signals["http_5xx"].count == 1
    assert signals["http_5xx"].total == 5


def test_adapter_output_fits_the_request_contract():
    req = AssessmentRequest.model_validate({
        "assessment_id": "a", "kind": "runtime_security", "service": {"service_id": "s"},
        "evidence": {"log_signals": [s.model_dump() for s in traefik_log_signals([_access(500, "/")], source="t")]},
    })

    assert req.evidence.log_signals[0].signal == "http_5xx"


KUBELET = {"readOnlyPort": 0, "tlsCertFile": "/c.crt", "tlsPrivateKeyFile": "/c.key",
           "authentication": {"anonymous": {"enabled": False}, "x509": {"clientCAFile": "/ca.crt"}},
           "authorization": {"mode": "Webhook"}}


def test_k3s_false_positives_need_effective_config_and_live_probe():
    verified = k3s_false_positives(KUBELET, "Running kube-apiserver --etcd-servers=unix://kine.sock",
                                   anonymous_status=401, read_only_open=False)

    assert set(verified) == {"1.2.14", "1.2.29", "4.2.1", "4.2.2", "4.2.3", "4.2.4", "4.2.10"}
    assert "401" in verified["4.2.1"]


def test_k3s_false_positive_is_withheld_when_measurement_disagrees():
    weak = {**KUBELET, "authentication": {"anonymous": {"enabled": True}}, "authorization": {"mode": "AlwaysAllow"}}

    verified = k3s_false_positives(weak, "--etcd-servers=https://etcd:2379 --disable-admission-plugins=ServiceAccount",
                                   anonymous_status=200, read_only_open=True)

    assert verified == {"4.2.10": verified["4.2.10"]}


def test_missing_apiserver_log_does_not_dismiss_admission_check():
    assert "1.2.14" not in k3s_false_positives(KUBELET, "", anonymous_status=401, read_only_open=False)


def test_route_to_is_not_read_as_destination():
    line = 'pass in quick on vlan04 route-to (vtnet1 192.0.2.1) inet from any to 198.51.100.1 port = https keep state\n'

    assert pf_firewall_rules(line, "vlan04")[0].destination == "198.51.100.1"


def test_pf_self_block_label_is_found_only_on_the_interface():
    assert pf_self_block_label(PF_AFTER, "vlan04") == "c"
    assert pf_self_block_label(PF_BEFORE, "vlan04") is None
    assert pf_self_block_label(PF_AFTER, "vlan03") is None


@pytest.mark.parametrize(("output", "observed"), [
    ("rc=0\n", "open"), ("rc=1\n", "closed"), ("rc=124\n", "closed"), ("rc=127\n", "unknown"), ("Error from server", "unknown"),
])
def test_probe_output_never_treats_tool_failure_as_closed(output, observed):
    assert probe_observed(output) == observed


def _workload(*env: dict) -> dict:
    return {"spec": {"template": {"spec": {"containers": [{"image": "app:1", "env": list(env)}]}}}}


def test_plaintext_secret_env_is_reported_by_name_only():
    facts = cluster_facts([], [_workload({"name": "DB_PASSWORD", "value": "hunter2"}, {"name": "PGDATA", "value": "/d"})])

    assert facts.plaintext_env == ("DB_PASSWORD",)
    assert facts.secrets_source == "env_plaintext"
    assert "hunter2" not in repr(facts)


def test_secret_reference_and_images_are_collected():
    ref = {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "p"}}}

    facts = cluster_facts([], [_workload(ref)])

    assert (facts.secrets_source, facts.images) == ("k8s_secret", ("app:1",))


@pytest.mark.parametrize(("ingresses", "tls"), [
    ([], None), ([{"spec": {}}], False), ([{"spec": {"tls": [{}]}}], True), ([{"spec": {"tls": [{}]}}, {"spec": {}}], False),
])
def test_ingress_tls_is_measured_from_every_ingress(ingresses, tls):
    assert cluster_facts(ingresses, []).ingress_tls is tls


def test_limit_packages_keeps_most_severe_and_reports_dropped():
    low = PackageVulnerability(image="a", package="l", severity="low")
    critical = PackageVulnerability(image="b", package="c", severity="critical")

    kept, dropped = limit_packages([low, critical], limit=1)

    assert ([p.package for p in kept], dropped) == (["c"], 1)
