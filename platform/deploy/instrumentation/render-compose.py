"""Attach a prepared agent directory read-only to an existing VM container."""

import argparse
import copy
import json
from pathlib import Path

import yaml
from profiles import MOUNT, NODE_IMAGE, environment


def render(document, service, agent_path, target, version, *, metrics=True):
    result = copy.deepcopy(document)
    app = result["services"][service]
    if not app.get("image") or "build" in app:
        raise ValueError(
            "Deploy an existing image; this tool never builds customer code"
        )
    path = Path(agent_path).resolve()
    if json.loads((path / "delta-agent.json").read_text()).get("image") != NODE_IMAGE:
        raise ValueError("Prepare the pinned agent artifact first")
    current = app.get("environment", {})
    if not isinstance(current, dict) or app.get("env_file"):
        raise ValueError(
            "Explicit environment mapping required to detect duplicate instrumentation"
        )
    updates = environment(target, service, version, existing=current, metrics=metrics)
    for volume in app.get("volumes", []):
        if MOUNT in str(volume):
            raise ValueError("Instrumentation mount already exists")
    app["environment"] = {**current, **updates}
    app.setdefault("volumes", []).append(
        {"type": "bind", "source": str(path), "target": MOUNT, "read_only": True}
    )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="VM용 Compose에 자동 계측 설정 추가")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--service", required=True)
    p.add_argument("--agent-path", required=True)
    p.add_argument("--collector-endpoint", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--traces-only", action="store_true")
    a = p.parse_args()
    a.output.write_text(
        yaml.safe_dump(
            render(
                yaml.safe_load(a.input.read_text()),
                a.service,
                a.agent_path,
                a.collector_endpoint,
                a.commit,
                metrics=not a.traces_only,
            ),
            sort_keys=False,
        )
    )
