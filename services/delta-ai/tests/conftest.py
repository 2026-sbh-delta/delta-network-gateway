from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def parity_waf_missing() -> dict[str, Any]:
    return load_fixture("parity-waf-missing.json")


@pytest.fixture
def parity_ok() -> dict[str, Any]:
    return load_fixture("parity-ok.json")


@pytest.fixture
def feedback_request() -> dict[str, Any]:
    return load_fixture("feedback-report.json")
