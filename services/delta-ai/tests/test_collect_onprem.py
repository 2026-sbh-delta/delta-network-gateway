from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

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
    assert [r.kind for r in requests] == ["migration_parity"]
    assert requests[0].target_env.network.firewall_rules == ()
    assert {p.observed for p in requests[0].evidence.reachability} == {"unknown"}


@pytest.mark.parametrize("value", ["a; rm -rf /", "$(id)", "-x", "a b", ""])
def test_unsafe_config_value_is_rejected(value):
    with pytest.raises(ValueError):
        collector.safe(value)


def test_first_json_skips_leading_output():
    assert collector.first_json('warn\n{"a": 1}\ntrailer') == {"a": 1}
