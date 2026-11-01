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
from delta_ai.contract import AssessmentRequest, AttackResult, ClusterCheck, FirewallRule, LogSignal, ReachabilityProbe

KUBECTL = "sudo k3s kubectl"
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*")
BLOCKED_STATUS = {403, 406}
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
    if spec["source"] == "node":
        script = f"timeout 3 bash -c '</dev/tcp/{host}/{port}' 2>/dev/null; echo rc=$?"
        command = f"bash -c {shlex.quote(script)}"
    else:
        namespace, deployment = (safe(part) for part in spec["source"].removeprefix("pod:").split("/"))
        script = f"nc -z -w 3 {host} {port} >/dev/null 2>&1; echo rc=$?"
        command = f"{KUBECTL} exec -n {namespace} deploy/{deployment} -- sh -c {shlex.quote(script)}"
    try:
        observed = probe_observed(node.run(command, timeout=30))
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


def parity_request(config: Mapping[str, Any], facts: ClusterFacts | None, rules: Sequence[FirewallRule],
                   probes: Sequence[ReachabilityProbe], attacks: Sequence[AttackResult]) -> AssessmentRequest:
    declared = config["declared_target"]
    measured = {
        "secrets": {"source": facts.secrets_source if facts else None, "plaintext_env": facts.plaintext_env if facts else ()},
        "ingress": {"tls": facts.ingress_tls if facts else None},
        "network": {**declared.get("network", {}), "gateway_addresses": config["gateway_addresses"], "firewall_rules": rules},
    }
    return AssessmentRequest.model_validate({
        "assessment_id": f"onprem-parity-{config['service_id']}", "kind": "migration_parity",
        "service": {"service_id": config["service_id"]}, "source_env": config["source_env"],
        "target_env": {"provider": "onprem", **declared, **measured},
        "evidence": {"attack_results": attacks, "reachability": probes},
    })


def collect(config: Mapping[str, Any]) -> tuple[list[AssessmentRequest], list[str]]:
    node, firewall = Remote(config["node_ssh"]), Remote(config["firewall_ssh"])
    collector = Collector()
    pf_text = collector.attempt("방화벽 룰", lambda: firewall.run("/sbin/pfctl -sr"), "")
    rules = pf_firewall_rules(pf_text, config["interface"])
    probes = tuple(probe(node, spec) for spec in config["probes"])
    attacks = collector.attempt("공격 시나리오", lambda: tuple(attack(node, config["ingress_url"], s) for s in config["attacks"]), ())
    facts = collector.attempt("클러스터 manifest", lambda: cluster_facts(
        kubectl_items(node, "ingress"),
        [w for ns in config["namespaces"] for w in kubectl_items(node, "deploy,sts,ds", ns)]), None)
    packages = collector.attempt("이미지 스캔", lambda: scan_images(node, config["trivy_image"], facts.images), None) if facts else None
    checks = collector.attempt("kube-bench", lambda: cluster_checks(node, config["node_name"]), None)
    signals = (*collector.attempt("방화벽 로그", lambda: firewall_log(firewall, pf_self_block_label(pf_text, config["interface"])), ()),
               *collector.attempt("Traefik 로그", lambda: traefik_log(node), ()))
    requests = [parity_request(config, facts, rules, probes, attacks)]
    if packages is not None or checks is not None or signals:  # 모두 실패하면 런타임 판단을 받을 입력이 없다
        requests.append(AssessmentRequest.model_validate({
            "assessment_id": f"onprem-runtime-{config['service_id']}", "kind": "runtime_security",
            "service": {"service_id": config["service_id"]},
            "evidence": {"image_vulnerabilities": packages, "cluster_checks": checks, "log_signals": signals or None},
        }))
    return requests, collector.errors


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
        print(f"{request.kind}: {result['verdict']} {result['reasons']} finding {len(result['findings'])}건, "
              f"실측 통제 {len(result['verified_controls'])}건")
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
