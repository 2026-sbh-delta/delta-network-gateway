#!/usr/bin/env python3
"""운영 대상 아키텍처의 단일 이미지를 빌드하고 식별값을 남긴다. 표준 라이브러리만 쓴다.

멀티 아키텍처 인덱스와 첨부 증명이 끼면 플랫폼별 digest 가 어긋나 스테이징과 대조할 수 없다.
image_id 는 로컬 값이다. 저장소 digest 는 이미지를 올린 뒤에 생긴다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

DEFAULT_PLATFORM = "linux/amd64"
EXIT_BUILD_FAILED = 1
EXIT_WRONG_PLATFORM = 3
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def build_command(tag: str, context: str, platform: str) -> list[str]:
    # 구식 빌더는 containerd 이미지 저장소에서 다른 아키텍처를 빌드하지 못한다(Docker Desktop 에서 재현). buildx 만 쓴다
    return ["docker", "buildx", "build", "--platform", platform, "--provenance=false", "--sbom=false", "--load",
            "-q", "-t", tag, context]


def build(tag: str, context: str, platform: str, out: Path, run: Runner = _run) -> int:
    done = run(build_command(tag, context, platform))
    if done.returncode != 0:
        print(done.stderr, file=sys.stderr)
        return EXIT_BUILD_FAILED
    inspected = run(["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", tag])
    fields = inspected.stdout.split()
    if inspected.returncode != 0 or len(fields) != 2:
        print(f"빌드한 이미지를 확인하지 못했다: {tag}", file=sys.stderr)
        return EXIT_BUILD_FAILED
    image_id, actual = fields
    if actual != platform:
        # 조용히 다른 아키텍처를 올리면 같은 이미지를 두 클라우드에 쓸 수 없다
        print(f"요청한 플랫폼 {platform} 과 빌드 결과 {actual} 이 다르다", file=sys.stderr)
        return EXIT_WRONG_PLATFORM
    out.write_text(json.dumps({"tag": tag, "image_id": image_id, "platform": actual}) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    return build(args.tag, args.context, args.platform, args.out)


if __name__ == "__main__":
    sys.exit(main())
