from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import vulnerable


class PingContractTest(unittest.TestCase):
    """패치가 함수를 지워 검사를 통과하는 일을 막는다."""

    def test_valid_host_still_returns_output(self) -> None:
        with mock.patch("subprocess.run", return_value=SimpleNamespace(stdout="pong")):
            self.assertEqual(vulnerable.ping("127.0.0.1"), "pong")
