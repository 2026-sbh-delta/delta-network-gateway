"""A failed candidate/swap must leave the previous Diff running."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("fail_at", ["candidate", "live", None])
def test_reversible_demo_update_preserves_previous_container_and_state(tmp_path, monkeypatch, fail_at):
    path = Path(__file__).resolve().parents[3] / "deploy/diff-demo-scope/deploy.py"
    spec = importlib.util.spec_from_file_location("demo_diff_deploy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = tmp_path / "release"
    release.mkdir()
    files = {}
    for name in ("diff.py", "terraform.py", "contract.py", "iam.py"):
        (release / name).write_text(name)
        files[name] = hashlib.sha256(name.encode()).hexdigest()
    (release / "manifest.json").write_text(json.dumps({"release": "test", "files": files}))
    state = tmp_path / "state"
    state.mkdir()
    (state / "keep").write_text("existing rule state")
    root = tmp_path / "host"
    root.mkdir()
    old = {"Id": "old", "State": {"Running": True}, "Config": {"Image": module.BASE,
           "Env": ["DELTA_DIFF_DEMO_WORKLOAD_SCOPE=1"]}, "HostConfig": {"NetworkMode": "host"},
           "Mounts": [{"Source": str(state), "Destination": "/state"}]}
    containers = {"delta-diff": old}
    events = []

    def inspect(name):
        return containers.get(name) or next((v for v in containers.values() if v["Id"] == name), None)

    def create(previous, name, *args, **kwargs):
        value = {**previous, "Id": name, "State": {"Running": False}}
        containers[name] = value
        return name

    def docker(*args, **kwargs):
        events.append(args)
        action, name = args[0], args[-1]
        if action == "rename":
            value = inspect(args[1])
            original = next(k for k, v in containers.items() if v is value)
            containers[name] = containers.pop(original)
        elif action == "rm":
            value = inspect(name)
            containers.pop(next(k for k, v in containers.items() if v is value))
        else:
            inspect(name)["State"]["Running"] = action == "start"
        return ""

    def ready(name, *args):
        if (fail_at == "candidate" and "candidate" in name) or (fail_at == "live" and name == "delta-diff"):
            raise RuntimeError("acceptance failed")

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "inspect", inspect)
    monkeypatch.setattr(module, "create", create)
    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module, "ready", ready)
    monkeypatch.setattr(module, "hashes", lambda *_: module.EXPECTED)
    if fail_at:
        with pytest.raises(RuntimeError, match="acceptance failed"):
            module.main(release)
        assert inspect("delta-diff")["Id"] == "old" and old["State"]["Running"]
        if fail_at == "candidate":
            assert not any(e[0] == "stop" for e in events)
    else:
        module.main(release)
        assert inspect("delta-diff")["Id"] != "old"
        assert inspect("delta-diff-before-iam-lambda-test")["Id"] == "old"
    assert (state / "keep").read_text() == "existing rule state"
    assert (release / "state-before/keep").read_text() == "existing rule state"
