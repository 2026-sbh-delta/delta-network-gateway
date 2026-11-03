"""온프레미스 노드와 방화벽에서 증거를 읽기 전용으로 모아 판단 서비스에 보낸다.

    python scripts/collect_onprem.py --config <저장소 밖 설정.json> --out <결과 폴더> [--dry-run] [--url URL]

토큰은 DELTA_API_TOKEN 환경 변수로 받는다. 일부 수집에 실패해도 나머지로 판단을 받고, 종료 코드 2 로 알린다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from delta_ai.adapters import (
    ClusterFacts,
    cluster_facts,
    default_gateway,
    exposed_ports,
    filter_log_signal,
    host_firewall_state,
    k3s_false_positives,
    kube_bench_checks,
    limit_packages,
    namespaces_without_policy,
    node_has_public_ip,
    pf_firewall_rules,
    pf_self_block_label,
    probe_observed,
    traefik_log_signals,
    trivy_packages,
)
from delta_ai.contract import (
    AssessmentRequest,
    AttackResult,
    ClusterCheck,
    FirewallRule,
    LogSignal,
    NetworkPosture,
    ReachabilityProbe,
)
from delta_ai.posture import DEFAULT_GATEWAY_TARGET

KUBECTL = "sudo k3s kubectl"
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*")
BLOCKED_STATUS = {403, 406}
GATEWAY_PROBE_PORTS = (22, 80, 443)
DEFAULT_PROBE_IMAGE = "busybox:1.36"
KUBE_BENCH_VERSION = "0.16.0"
KUBE_BENCH_SCRIPT = f"""set -eu
D=$(mktemp -d); trap 'cd /; sudo rm -rf "$D"' EXIT; cd "$D"; V={KUBE_BENCH_VERSION}
B=https://github.com/aquasecurity/kube-bench/releases/download/v$V
curl -fsSL -o kb.tgz "$B/kube-bench_${{V}}_linux_amd64.tar.gz"
curl -fsSL -o sums "$B/kube-bench_${{V}}_checksums.txt"
[ "$(sha256sum kb.tgz | cut -d' ' -f1)" = "$(grep linux_amd64.tar.gz sums | cut -d' ' -f1)" ] || exit 3
tar xzf kb.tgz
sudo ./kube-bench run --benchmark k3s-cis-1.24 --config-dir cfg --config cfg/config.yaml --json --noremediations 2>/dev/null
"""


class Remote:
    def __init__(self, argv: Sequence[str]) -> None:
        self._argv = list(argv)

    def run(self, command: str, *, stdin: str = "", timeout: int = 60) -> str:
        """stdin 을 비워 두면 빈 입력을 보낸다. 실패는 CalledProcessError 로 올린다."""
        done = subprocess.run([*self._argv, command], input=stdin, capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise subprocess.CalledProcessError(done.returncode, command, done.stdout, done.stderr)
        return done.stdout


def safe(value: str) -> str:
    if not SAFE_NAME.fullmatch(value):
        raise ValueError(f"설정 값에 허용하지 않는 문자가 있다: {value!r}")
    return value


def first_json(text: str) -> dict[str, Any]:
    """앞에 다른 출력이 섞여도 첫 JSON 객체를 읽는다."""
    return json.JSONDecoder().raw_decode(text[text.index("{"):])[0]


def probe(node: Remote, spec: Mapping[str, Any]) -> ReachabilityProbe:
    host, port = safe(spec["host"]), int(spec["port"])
    nc_script = f"nc -z -w 3 {host} {port} >/dev/null 2>&1; echo rc=$?"
    if spec["source"] in ("node", "vantage"):
        script = f"timeout 3 bash -c '</dev/tcp/{host}/{port}' 2>/dev/null; echo rc=$?"
        command = f"bash -c {shlex.quote(script)}"
    elif spec["source"].startswith("temp-pod:"):
        namespace = safe(spec["source"].removeprefix("temp-pod:"))
        image = safe(spec.get("image", DEFAULT_PROBE_IMAGE))
        command = (f"{KUBECTL} run delta-probe-{os.getpid()}-{port} -n {namespace} --rm -i --quiet --restart=Never "
                   f"--image={image} --command -- sh -c {shlex.quote(nc_script)}")
    else:
        namespace, deployment = (safe(part) for part in spec["source"].removeprefix("pod:").split("/"))
        command = f"{KUBECTL} exec -n {namespace} deploy/{deployment} -- sh -c {shlex.quote(nc_script)}"
    try:
        observed = probe_observed(node.run(command, timeout=90))
    except (subprocess.SubprocessError, OSError):
        observed = "unknown"
    return ReachabilityProbe(environment="onprem", source=spec["source"], target=f"{spec['target']}({host})", port=port,
                             expected=spec["expected"], observed=observed)


def attack(node: Remote, base_url: str, spec: Mapping[str, Any]) -> AttackResult:
    url = shlex.quote(base_url + spec["path"])
    status = int(node.run(f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' {url}", timeout=20))
    return AttackResult(scenario=spec["scenario"], environment="onprem", http_status=status,
                        observed="blocked" if status in BLOCKED_STATUS else "allowed")


def kubectl_items(node: Remote, resource: str, namespace: str | None = None) -> list[dict[str, Any]]:
    scope = f"-n {safe(namespace)}" if namespace else "-A"
    return json.loads(node.run(f"{KUBECTL} get {resource} {scope} -o json"))["items"]


def scan_images(node: Remote, trivy_image: str, images: Sequence[str]) -> Any:
    packages = []
    for index, image in enumerate(images):
        command = (f"{KUBECTL} run trivy-scan-{os.getpid()}-{index} --rm -i --quiet --restart=Never "
                   f"--image={safe(trivy_image)} -- image --quiet --format json --scanners vuln {safe(image)}")
        packages.extend(trivy_packages(first_json(node.run(command, timeout=600))))
    kept, dropped = limit_packages(packages)
    if dropped:
        print(f"경고: 취약 패키지 {dropped}개는 상한을 넘어 심각도 낮은 순으로 뺐다", file=sys.stderr)
    return kept


def cluster_checks(node: Remote, node_name: str) -> tuple[ClusterCheck, ...]:
    report = node.run("bash -s", stdin=KUBE_BENCH_SCRIPT, timeout=300)
    kubelet = json.loads(node.run(f"{KUBECTL} get --raw /api/v1/nodes/{safe(node_name)}/proxy/configz"))["kubeletconfig"]
    apiserver = node.run("sudo journalctl -u k3s --no-pager | grep 'Running kube-apiserver' | tail -n 1")
    anonymous = node.run("curl -sk -m 3 -o /dev/null -w '%{http_code}' https://127.0.0.1:10250/pods")
    read_only = probe_observed(node.run("""bash -c 'timeout 2 bash -c "</dev/tcp/127.0.0.1/10255" 2>/dev/null; echo rc=$?'"""))
    verified = k3s_false_positives(kubelet, apiserver, anonymous_status=int(anonymous),
                                   read_only_open=None if read_only == "unknown" else read_only == "open")
    return kube_bench_checks(first_json(report), verified)


def firewall_log(firewall: Remote, label: str | None) -> tuple[LogSignal, ...]:
    if label is None:
        return ()
    lines = firewall.run(f"grep -F {shlex.quote(label)} /var/log/filter/latest.log", timeout=30).splitlines()
    return (filter_log_signal(lines, label, source="firewall"),)


def traefik_log(node: Remote) -> tuple[LogSignal, ...]:
    text = node.run(f"{KUBECTL} logs -n kube-system deploy/traefik --since=1h --tail=20000", timeout=30)
    return traefik_log_signals(text.splitlines(), source="traefik")


class Collector:
    """수집 실패를 모은다. 실패한 항목은 비워서 보내므로 서비스가 통과로 읽지 않는다."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def attempt(self, name: str, step: Callable[[], Any], default: Any) -> Any:
        try:
            return step()
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as error:
            detail = error.stderr.strip()[:200] if isinstance(error, subprocess.CalledProcessError) else str(error)[:200]
            self.errors.append(f"{name}: {type(error).__name__} {detail}")
            print(f"수집 실패 {self.errors[-1]}", file=sys.stderr)
            return default


def network_probe_specs(node: Remote, config: Mapping[str, Any], collector: Collector) -> list[dict[str, Any]]:
    """설정한 대상과, 요청하면 기본 게이트웨이만 시험한다. 주변 기기를 찾아 스캔하지 않는다."""
    specs = list(config.get("probes", ()))
    if not config.get("probe_default_gateway"):
        return specs
    gateway = collector.attempt("기본 게이트웨이", lambda: default_gateway(node.run("ip route")), None)
    if gateway is None:
        return specs
    return [*specs, *({"source": "node", "target": DEFAULT_GATEWAY_TARGET, "host": gateway, "port": port, "expected": "closed"}
                      for port in GATEWAY_PROBE_PORTS)]


def inbound_probes(vantage: Remote, config: Mapping[str, Any]) -> tuple[ReachabilityProbe, ...]:
    return tuple(probe(vantage, {**spec, "source": "vantage"}) for spec in config.get("inbound_probes", ()))


def network_posture(node: Remote, config: Mapping[str, Any], probes: Sequence[ReachabilityProbe],
                    collector: Collector) -> NetworkPosture:
    namespaces = config["namespaces"]
    return NetworkPosture(
        upstream_firewall=config.get("upstream_firewall"),
        node_public_ip=collector.attempt("노드 주소", lambda: node_has_public_ip(node.run("ip -o addr")), None),
        host_firewall=collector.attempt("호스트 방화벽", lambda: host_firewall_state(
            node.run("sudo iptables -S INPUT"), node.run("sudo ip6tables -S INPUT")), None),
        exposed_ports=collector.attempt("열린 포트", lambda: exposed_ports(
            node.run("sudo ss -tlnH"), kubectl_items(node, "svc")), None),
        network_probes=probes,
        inbound_probes=inbound_probes(Remote(config["vantage_ssh"]), config) if config.get("vantage_ssh") else (),
        cluster_probes=tuple(probe(node, spec) for spec in config.get("cluster_probes", ())),
        namespaces_without_policy=collector.attempt("NetworkPolicy", lambda: namespaces_without_policy(
            namespaces, kubectl_items(node, "networkpolicy")), None),
    )


def parity_request(config: Mapping[str, Any], facts: ClusterFacts | None, rules: Sequence[FirewallRule] | None,
                   probes: Sequence[ReachabilityProbe], attacks: Sequence[AttackResult]) -> AssessmentRequest:
    declared = config["declared_target"]
    measured = {
        "secrets": {"source": facts.secrets_source if facts else None, "plaintext_env": facts.plaintext_env if facts else ()},
        "ingress": {"tls": facts.ingress_tls if facts else None},
        "network": {**declared.get("network", {}), "gateway_addresses": config.get("gateway_addresses", ()), "firewall_rules": rules},
    }
    return AssessmentRequest.model_validate({
        "assessment_id": f"onprem-parity-{config['service_id']}", "kind": "migration_parity",
        "service": {"service_id": config["service_id"]}, "source_env": config["source_env"],
        "target_env": {"provider": "onprem", **declared, **measured},
        "evidence": {"attack_results": attacks, "reachability": probes},
    })


def collect(config: Mapping[str, Any]) -> tuple[list[AssessmentRequest], list[str]]:
    node = Remote(config["node_ssh"])
    firewall = Remote(config["firewall_ssh"]) if config.get("firewall_ssh") else None
    collector = Collector()
    # 방화벽 접근이 없는 환경은 룰 점검을 건너뛴다. 격리 여부는 태세의 실측 시험으로 판정한다
    pf_text = collector.attempt("방화벽 룰", lambda: firewall.run("/sbin/pfctl -sr"), "") if firewall else ""
    rules = pf_firewall_rules(pf_text, config["interface"]) if firewall else None
    probes = tuple(probe(node, spec) for spec in network_probe_specs(node, config, collector))
    attacks = collector.attempt("공격 시나리오", lambda: tuple(attack(node, config["ingress_url"], s) for s in config["attacks"]), ())
    facts = collector.attempt("클러스터 manifest", lambda: cluster_facts(
        kubectl_items(node, "ingress"),
        [w for ns in config["namespaces"] for w in kubectl_items(node, "deploy,sts,ds", ns)]), None)
    packages = collector.attempt("이미지 스캔", lambda: scan_images(node, config["trivy_image"], facts.images), None) if facts else None
    checks = collector.attempt("kube-bench", lambda: cluster_checks(node, config["node_name"]), None)
    firewall_signals = collector.attempt("방화벽 로그", lambda: firewall_log(
        firewall, pf_self_block_label(pf_text, config["interface"])), ()) if firewall else ()
    signals = (*firewall_signals, *collector.attempt("Traefik 로그", lambda: traefik_log(node), ()))
    runtime = AssessmentRequest.model_validate({
        "assessment_id": f"onprem-runtime-{config['service_id']}", "kind": "runtime_security",
        "service": {"service_id": config["service_id"]},
        "evidence": {"image_vulnerabilities": packages, "cluster_checks": checks, "log_signals": signals or None,
                     "network_posture": network_posture(node, config, probes, collector)},
    })
    return [parity_request(config, facts, rules, probes, attacks), runtime], collector.errors


def post(url: str, token: str, request: AssessmentRequest) -> dict[str, Any]:
    body = json.dumps(request.model_dump(mode="json")).encode()
    call = urllib.request.Request(f"{url}/v1/assessments", body,
                                  {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(call, timeout=240) as response:  # noqa: S310 - URL 은 실행자가 지정한다
        return json.load(response)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true", help="수집만 하고 서비스에 보내지 않는다")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    requests, errors = collect(json.loads(args.config.read_text(encoding="utf-8")))
    for request in requests:
        write_json(args.out / f"{request.kind}.request.json", request.model_dump(mode="json"))
        if args.dry_run:
            continue
        result = post(args.url, os.environ["DELTA_API_TOKEN"], request)
        write_json(args.out / f"{request.kind}.result.json", result)
        posture = (result.get("posture") or {}).get("overall", "-")
        print(f"{request.kind}: {result['verdict']} {result['reasons']} finding {len(result['findings'])}건, "
              f"실측 통제 {len(result['verified_controls'])}건, 태세 {posture}")
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
