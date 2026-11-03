from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from delta_ai.contract import NetworkPosture
from delta_ai.posture import check_posture

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "collect_onprem.py"
spec = importlib.util.spec_from_file_location("collect_onprem", SCRIPT)
collector = importlib.util.module_from_spec(spec)
sys.modules["collect_onprem"] = collector
spec.loader.exec_module(collector)

EXAMPLE = json.loads((SCRIPT.parent / "collect_onprem.example.json").read_text(encoding="utf-8"))


class DownRemote:
    """모든 명령이 실패하는 원격 호스트."""

    def __init__(self, argv: Any) -> None:
        pass

    def run(self, command: str, **_: Any) -> str:
        raise subprocess.CalledProcessError(255, command, "", "connection refused")


def test_unreachable_hosts_are_reported_and_never_read_as_safe(monkeypatch):
    monkeypatch.setattr(collector, "Remote", DownRemote)

    requests, errors = collector.collect(EXAMPLE)

    assert errors
    assert [r.kind for r in requests] == ["migration_parity", "runtime_security"]
    posture = requests[1].evidence.network_posture
    assert (posture.node_public_ip, posture.host_firewall, posture.namespaces_without_policy) == (None, None, None)
    assert requests[0].target_env.network.firewall_rules == ()
    assert {p.observed for p in requests[0].evidence.reachability} == {"unknown"}


def test_environment_without_firewall_access_skips_firewall_and_reports_no_failure_for_it(monkeypatch):
    monkeypatch.setattr(collector, "Remote", DownRemote)
    config = {key: value for key, value in EXAMPLE.items() if key != "firewall_ssh"} | {"upstream_firewall": "unmanaged"}

    requests, errors = collector.collect(config)

    assert requests[0].target_env.network.firewall_rules is None
    assert requests[1].evidence.network_posture.upstream_firewall == "unmanaged"
    assert not any("방화벽 룰" in error for error in errors)


def test_default_gateway_probes_are_added_only_when_asked(monkeypatch):
    class Router(DownRemote):
        def run(self, command: str, **_: Any) -> str:
            if command == "ip route":
                return "default via 192.0.2.1 dev eth0\n"
            return "rc=1\n" if "/dev/tcp" in command else super().run(command)

    monkeypatch.setattr(collector, "Remote", Router)

    without = collector.network_probe_specs(collector.Remote([]), {"probes": []}, collector.Collector())
    with_gateway = collector.network_probe_specs(collector.Remote([]), {"probes": [], "probe_default_gateway": True},
                                                 collector.Collector())

    assert without == []
    assert {(s["host"], s["port"], s["expected"]) for s in with_gateway} == {
        ("192.0.2.1", 22, "closed"), ("192.0.2.1", 80, "closed"), ("192.0.2.1", 443, "closed")}


@pytest.mark.parametrize("value", ["a; rm -rf /", "$(id)", "-x", "a b", ""])
def test_unsafe_config_value_is_rejected(value):
    with pytest.raises(ValueError):
        collector.safe(value)


def test_first_json_skips_leading_output():
    assert collector.first_json('warn\n{"a": 1}\ntrailer') == {"a": 1}


def test_inbound_probes_run_from_the_vantage_host():
    class Vantage:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def run(self, command: str, **_: Any) -> str:
            self.commands.append(command)
            return "rc=0\n"

    vantage = Vantage()

    probes = collector.inbound_probes(vantage, {"inbound_probes": [
        {"target": "node", "host": "192.0.2.10", "port": 31968, "expected": "closed"}]})

    assert "/dev/tcp/192.0.2.10/31968" in vantage.commands[0]
    assert (probes[0].source, probes[0].observed) == ("vantage", "open")


def test_collected_gateway_probes_do_not_prove_lateral_isolation():
    class Closed:
        def run(self, command: str, **_: Any) -> str:
            return "rc=1\n"

    spec = {"source": "node", "target": collector.DEFAULT_GATEWAY_TARGET, "host": "192.0.2.1", "port": 22, "expected": "closed"}
    posture = NetworkPosture(node_public_ip=False, network_probes=(collector.probe(Closed(), spec),))

    assert check_posture(posture).result.layers[0].state == "unknown"
