from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def demo_code_files() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in (FIXTURES / "demo-app").glob("*.js")}


@pytest.fixture
def parity_waf_missing() -> dict[str, Any]:
    return load_fixture("parity-waf-missing.json")


@pytest.fixture
def parity_ok() -> dict[str, Any]:
    return load_fixture("parity-ok.json")


@pytest.fixture
def security_request() -> dict[str, Any]:
    return {
        "assessment_id": "as-security-1",
        "kind": "code_security",
        "service": {"service_id": "demo-app", "commit": "abc123"},
        "evidence": {"sarif": load_fixture("demo-app.sarif.json"), "code_files": demo_code_files()},
    }


@pytest.fixture
def feedback_request() -> dict[str, Any]:
    return load_fixture("feedback-report.json")
