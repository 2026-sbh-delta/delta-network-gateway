from __future__ import annotations

from delta_ai.contract import Finding
from delta_ai.verdict import decide


def _finding(**overrides) -> Finding:
    base = dict(finding_id="f1", rule_id="PAR-LOG-001", category="logging", severity="medium", status="fail",
                autofix="allowed", title="t", location={}, evidence="e", has_doc=True,
                ops=({"op": "replace", "path": "/logging/central", "value": True},))
    return Finding.model_validate({**base, **overrides})


def test_no_findings_passes():
    assert decide((), ()) == ("pass", ())


def test_low_only_passes():
    assert decide((_finding(severity="low", status="unknown", ops=()),), ()) == ("pass", ())


def test_dismissed_finding_does_not_block():
    assert decide((_finding(status="dismissed", ops=()),), ()) == ("pass", ())


def test_fixable_fail_returns_fix():
    assert decide((_finding(),), ()) == ("fix", ())


def test_unknown_requires_human():
    verdict, reasons = decide((_finding(status="unknown", ops=()),), ())

    assert verdict == "needs_human"
    assert "EVIDENCE_UNKNOWN" in reasons


def test_missing_doc_requires_human():
    verdict, reasons = decide((_finding(has_doc=False),), ())

    assert verdict == "needs_human"
    assert "NO_EVIDENCE_DOC" in reasons


def test_forbidden_autofix_requires_human():
    verdict, reasons = decide((_finding(autofix="forbidden", ops=()),), ())

    assert verdict == "needs_human"
    assert "AUTOFIX_FORBIDDEN" in reasons


def test_fail_without_remediation_requires_human():
    verdict, reasons = decide((_finding(ops=()),), ())

    assert verdict == "needs_human"
    assert "PATCH_MISSING" in reasons


def test_extra_reason_blocks_even_when_fixable():
    verdict, reasons = decide((_finding(),), ("LLM_OUTPUT_INVALID",))

    assert verdict == "needs_human"
    assert reasons == ("LLM_OUTPUT_INVALID",)
