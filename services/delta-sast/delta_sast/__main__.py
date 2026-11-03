"""CLI

python -m delta_sast scan <소스 경로> --out result.sarif
python -m delta_sast tools
uvicorn delta_sast.api:app (HTTP 서버)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .classify import RuleCache
from .judge import client_from_env
from .locate import LocateError
from .scan import run_fast


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="delta_sast", description="Delta SAST 빠른 모드")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="소스를 점검해 SARIF를 쓴다")
    p.add_argument("root", type=Path)
    p.add_argument("--out", type=Path, default=Path("delta-sast.sarif"))
    p.add_argument("--run-id", default="local")
    p.add_argument("--commit")
    p.add_argument("--cache", type=Path, default=Path(".delta-sast-cache.json"))
    p.add_argument("--payload-mode", choices=["ai", "preset"], default="ai")
    p.add_argument("--no-llm", action="store_true", help="LLM 판단 없이 규칙·캐시까지만")
    sub.add_parser("tools", help="관리 에이전트용 도구 정의를 출력한다")
    args = parser.parse_args(argv)

    if args.cmd == "tools":
        from .tools import tool_catalog

        print(json.dumps(tool_catalog(), ensure_ascii=False, indent=2))
        return 0

    if not args.root.is_dir():
        print(f"소스 경로가 없다: {args.root}", file=sys.stderr)
        return 2
    client = None if args.no_llm else client_from_env()
    if client is None and not args.no_llm:
        print("LLM을 쓸 수 없어 규칙·캐시까지만 판정한다(SAST_LLM_BACKEND 확인)", file=sys.stderr)
    cache = RuleCache(args.cache)
    try:
        result = asyncio.run(run_fast(args.root, client=client, cache=cache, payload_mode=args.payload_mode))
    except LocateError as exc:
        print(f"위치 표시 실패: {exc}", file=sys.stderr)
        return 2
    cache.save()
    sarif = result.sarif(run_id=args.run_id, commit=args.commit, payload_mode=args.payload_mode)
    args.out.write_text(json.dumps(sarif, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    props = sarif["runs"][0]["properties"]
    print(json.dumps({"out": str(args.out), "counts": props["counts"], "dast_targets": props["dast_targets"],
                      "llm": props["llm"], "timings": props["timings"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
