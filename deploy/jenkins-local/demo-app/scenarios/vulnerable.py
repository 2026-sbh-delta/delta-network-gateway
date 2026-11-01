"""SAST 시험용 취약 코드. vulnerable-code 시나리오에서만 복사된다."""

from __future__ import annotations

import subprocess


def ping(host: str) -> str:
    result = subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True, text=True, check=False)
    return result.stdout
