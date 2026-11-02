from __future__ import annotations

import unittest
from pathlib import Path


class RequiresShellTest(unittest.TestCase):
    """bad-patch 시나리오용. 패치가 이 테스트를 깨면 샌드박스에서 멈추는지 본다."""

    def test_source_keeps_shell_call(self) -> None:
        source = Path(__file__).with_name("vulnerable.py").read_text(encoding="utf-8")
        self.assertIn("shell=True", source)
