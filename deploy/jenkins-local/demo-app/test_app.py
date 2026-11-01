from __future__ import annotations

import unittest

from app import should_fail


class ShouldFailTest(unittest.TestCase):
    def test_never_fails_at_zero_rate(self) -> None:
        self.assertFalse(should_fail(0.0, 0.0))

    def test_fails_when_draw_is_below_rate(self) -> None:
        self.assertTrue(should_fail(0.5, 0.1))


if __name__ == "__main__":
    unittest.main()
