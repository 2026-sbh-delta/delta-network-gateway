#!/usr/bin/env python3
"""SARIF 에 탐지 결과가 있으면 사람 확인을 요구한다. 읽지 못하면 예외로 끝나 통과로 처리되지 않는다."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

EXIT_CLEAN = 0
EXIT_NEEDS_HUMAN = 20


def count_results(sarif: Mapping[str, Any]) -> int:
    return sum(len(run["results"]) for run in sarif["runs"])


def main(argv: list[str] | None = None) -> int:
    path = Path((argv if argv is not None else sys.argv[1:])[0])
    count = count_results(json.loads(path.read_text(encoding="utf-8")))
    print(f"SAST 탐지 {count}건")
    return EXIT_NEEDS_HUMAN if count else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
