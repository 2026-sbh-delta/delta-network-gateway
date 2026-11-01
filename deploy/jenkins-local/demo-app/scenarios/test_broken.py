from __future__ import annotations

import unittest


class BrokenTest(unittest.TestCase):
    def test_fails_on_purpose(self) -> None:
        self.fail("failing-test 시나리오: 의도한 테스트 실패")
