#!/usr/bin/env python3
"""운영 지표 기준을 넘겨 롤백된 릴리스를 기억해, 같은 릴리스가 사람 확인 없이 다시 트래픽을 받지 않게 한다.

릴리스는 코드 내용과 런타임 설정을 합친 해시다. 같은 코드라도 설정이 다르면 다른 릴리스다.
판단 서비스 장애로 멈춘 빌드는 릴리스 문제가 아니므로 기록하지 않는다(호출자 책임).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_SEEN = 4
SKIPPED_DIRS = {"__pycache__"}


def release_id(root: Path, extras: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not SKIPPED_DIRS.intersection(p.parts)):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    for extra in sorted(extras):
        digest.update(extra.encode() + b"\0")
    return digest.hexdigest()[:16]


def load(state: Path) -> dict[str, Any]:
    """파일이 없으면 기록이 없는 것이다. 깨진 파일은 예외로 올려 호출자가 멈추게 한다."""
    return json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}


def _save(state: Path, records: dict[str, Any]) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, state)


def record(state: Path, release: str, *, build: str, reason: str) -> None:
    entry = {"build": build, "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save(state, {**load(state), release: entry})


def clear(state: Path, release: str) -> None:
    records = load(state)
    if release in records:
        _save(state, {key: value for key, value in records.items() if key != release})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("id", "check", "record", "clear"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--state", type=Path)
    parser.add_argument("--release")
    parser.add_argument("--build", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.command == "id":
        print(release_id(args.root, tuple(args.extra)))
    elif args.command == "check":
        previous = load(args.state).get(args.release)
        if previous:
            print(f"이전 실패: 빌드 #{previous['build']} {previous['reason']} ({previous['at']})")
            return EXIT_SEEN
    elif args.command == "record":
        record(args.state, args.release, build=args.build, reason=args.reason)
    else:
        clear(args.state, args.release)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
