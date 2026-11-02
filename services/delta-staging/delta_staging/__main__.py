"""스크립트용 진입점. 서버 없이 바로 배포하고 끝날 때까지 기다린다.

python -m delta_staging deploy --run-id r1 --image <이미지> [--commit SHA] [--env KEY=VALUE ...] [--replace]
python -m delta_staging stop --run-id r1
python -m delta_staging tools
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pydantic import ValidationError

from delta_staging.config import ConfigError
from delta_staging.contract import DeployRequest
from delta_staging.registry import Busy, Conflict, RequestRejected, NotFound, Unsupported
from delta_staging.tools import tool_catalog


def _env_pairs(pairs: list[str]) -> dict[str, str]:
    env = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--env 는 KEY=VALUE 형식이다: {pair}")
        env[key] = value
    return env


async def _deploy(args: argparse.Namespace) -> int:
    from delta_staging.api import registry_from_env

    req = DeployRequest(run_id=args.run_id, cloud=args.cloud, image=args.image, commit=args.commit,
                        env=_env_pairs(args.env), replace=args.replace, scanner_ip=args.scanner_ip)
    registry = registry_from_env()
    await registry.start(req)
    printed = 0
    task = asyncio.create_task(registry.wait(req.run_id))
    while True:
        steps = registry.get(req.run_id).steps
        for step in steps[printed:]:
            print(f"[{step.at:%H:%M:%S}] {step.name} {step.detail}", file=sys.stderr)
        printed = len(steps)
        if task.done():
            break
        await asyncio.sleep(1)
    result = await task
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.state == "ready" else 1


async def _stop(args: argparse.Namespace) -> int:
    from delta_staging.api import registry_from_env

    result = await registry_from_env().stop(args.run_id)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="delta_staging")
    sub = parser.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--run-id", required=True)
    deploy.add_argument("--image", required=True)
    deploy.add_argument("--cloud", default="aws")
    deploy.add_argument("--commit")
    deploy.add_argument("--env", action="append", default=[])
    deploy.add_argument("--replace", action="store_true")
    deploy.add_argument("--scanner-ip", help="공개 스테이징에 허용할 DAST 실행기 공인 IPv4. auto 면 지금 IP")
    stop = sub.add_parser("stop")
    stop.add_argument("--run-id", required=True)
    sub.add_parser("tools")
    args = parser.parse_args(argv)

    if args.command == "tools":
        print(json.dumps(tool_catalog(), ensure_ascii=False, indent=2))
        return 0
    try:
        return asyncio.run(_deploy(args) if args.command == "deploy" else _stop(args))
    except ValidationError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
    except (ConfigError, Unsupported, RequestRejected, Busy, Conflict, NotFound) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
