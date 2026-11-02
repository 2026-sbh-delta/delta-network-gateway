from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "scripts"))

import fix_loop  # noqa: E402
import gate  # noqa: E402
import releases  # noqa: E402


def _sarif(*paths: str) -> dict[str, Any]:
    results = [{"locations": [{"physicalLocation": {"artifactLocation": {"uri": path}}}]} for path in paths]
    return {"runs": [{"results": results}]}


def _diff(path: str, old: str, new: str) -> dict[str, str]:
    return {"path": path, "diff": f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"}


def _fix_result(*diffs: dict[str, str]) -> dict[str, Any]:
    return {"verdict": "fix", "remediation": {"code_diffs": list(diffs)}}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    app.mkdir()
    (app / "bad.py").write_text("old\n", encoding="utf-8")
    (app / "other.py").write_text("keep\n", encoding="utf-8")
    (app / ".semgrep.yml").write_text("rules: []\n", encoding="utf-8")
    return tmp_path


def _tools(*, tests: bool = True, verdicts: tuple[dict[str, Any], ...] = ({"verdict": "pass"},),
           calls: list[str] | None = None) -> fix_loop.Tools:
    remaining = list(verdicts)
    seen = calls if calls is not None else []

    def run_tests(attempt: int) -> bool:
        seen.append(f"test-{attempt}")
        return tests

    def scan(attempt: int) -> dict[str, Any]:
        seen.append(f"scan-{attempt}")
        return _sarif("app/bad.py")

    def judge(attempt: int, sarif: dict[str, Any]) -> dict[str, Any]:
        seen.append(f"judge-{attempt}")
        return remaining.pop(0)

    return fix_loop.Tools(run_tests=run_tests, scan=scan, judge=judge)


def _run(workdir: Path, result: dict[str, Any], tools: fix_loop.Tools, max_attempts: int = 2) -> fix_loop.FixReport:
    return fix_loop.run_loop(workdir, result, _sarif("app/bad.py"), max_attempts=max_attempts, tools=tools)


def test_verified_patch_changes_the_copy_and_keeps_the_diff(workdir):
    report = _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")), _tools())

    assert report.outcome == "verified"
    assert report.attempts == 1
    assert "+new" in report.diff
    assert (workdir / "app/bad.py").read_text(encoding="utf-8") == "new\n"


def test_second_attempt_applies_the_next_patch(workdir):
    second = _fix_result(_diff("app/bad.py", "new", "newer"))
    report = _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")),
                  _tools(verdicts=(second, {"verdict": "pass"})))

    assert report.outcome == "verified"
    assert report.attempts == 2
    assert (workdir / "app/bad.py").read_text(encoding="utf-8") == "newer\n"


def test_stops_when_patches_do_not_converge(workdir):
    second = _fix_result(_diff("app/bad.py", "new", "newer"))
    calls: list[str] = []
    report = _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")), _tools(verdicts=(second,), calls=calls),
                  max_attempts=1)

    assert report.outcome == "stopped"
    assert "1회" in report.reason
    assert calls == ["test-1", "scan-1", "judge-1"]


@pytest.mark.parametrize("target,old", [("app/other.py", "keep"), ("app/.semgrep.yml", "rules: []")])
def test_patch_outside_scanner_findings_is_rejected(workdir, target, old):
    """검사 설정이나 다른 파일을 고쳐 통과하는 길을 막는다."""
    calls: list[str] = []
    report = _run(workdir, _fix_result(_diff(target, old, "changed")), _tools(calls=calls))

    assert report.outcome == "stopped"
    assert "지목하지 않은" in report.reason
    assert calls == []


def test_diff_header_cannot_hide_the_changed_file(workdir):
    disguised = {"path": "app/bad.py", "diff": _diff("app/other.py", "keep", "changed")["diff"]}

    assert _run(workdir, _fix_result(disguised), _tools()).outcome == "stopped"


def test_path_traversal_is_rejected(workdir):
    report = _run(workdir, _fix_result(_diff("../escape.py", "old", "new")), _tools())

    assert report.outcome == "stopped"
    assert not (workdir.parent / "escape.py").exists()


def test_diff_that_does_not_apply_is_rejected(workdir):
    report = _run(workdir, _fix_result(_diff("app/bad.py", "not-there", "new")), _tools())

    assert report.outcome == "stopped"
    assert "git apply" in report.reason


def test_new_symlink_is_rejected(workdir):
    link = {"path": "app/bad.py", "diff": (
                    "diff --git a/app/bad.py b/app/bad.py\ndeleted file mode 100644\n--- a/app/bad.py\n+++ /dev/null\n"
                    "@@ -1 +0,0 @@\n-old\n"
                    "diff --git a/app/bad.py b/app/bad.py\nnew file mode 120000\n--- /dev/null\n+++ b/app/bad.py\n"
                    "@@ -0,0 +1 @@\n+/etc/passwd\n\\ No newline at end of file\n")}

    assert _run(workdir, _fix_result(link), _tools()).outcome == "stopped"


def test_missing_patch_stops(workdir):
    assert _run(workdir, {"verdict": "fix"}, _tools()).outcome == "stopped"


def test_failed_sandbox_tests_stop_before_rescan(workdir):
    calls: list[str] = []
    report = _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")), _tools(tests=False, calls=calls))

    assert report.outcome == "stopped"
    assert calls == ["test-1"]


def test_needs_human_after_patch_is_not_a_pass(workdir):
    report = _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")),
                  _tools(verdicts=({"verdict": "needs_human"},)))

    assert report.outcome == "stopped"
    assert report.diff == ""


@pytest.mark.parametrize("error", [gate.GateUnavailable("down"), subprocess.CalledProcessError(2, "semgrep"),
                                   ValueError("bad json")])
def test_rescan_or_gate_errors_stop(workdir, error):
    def fail(attempt: int, *_: Any) -> dict[str, Any]:
        raise error

    tools = fix_loop.Tools(run_tests=lambda attempt: True, scan=lambda attempt: _sarif(), judge=fail)

    assert _run(workdir, _fix_result(_diff("app/bad.py", "old", "new")), tools).outcome == "stopped"


def test_snapshot_ignores_bytecode(workdir):
    cache = workdir / "app/__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"1")

    assert not [path for path in fix_loop.snapshot(workdir) if "__pycache__" in path]


def test_release_id_depends_on_code_and_config_not_bytecode(workdir):
    root = workdir / "app"
    base = releases.release_id(root, ("FAIL_RATE=0",))
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"1")

    assert releases.release_id(root, ("FAIL_RATE=0",)) == base
    assert releases.release_id(root, ("FAIL_RATE=0.5",)) != base
    (root / "bad.py").write_text("changed\n", encoding="utf-8")
    assert releases.release_id(root, ("FAIL_RATE=0",)) != base


def test_recorded_release_is_seen_until_cleared(tmp_path, capsys):
    state = tmp_path / "state/failed.json"
    check = ["check", "--state", str(state), "--release", "r1"]

    assert releases.main(check) == releases.EXIT_OK
    releases.main(["record", "--state", str(state), "--release", "r1", "--build", "7", "--reason", "traffic-10"])
    assert releases.main(check) == releases.EXIT_SEEN
    assert "#7" in capsys.readouterr().out
    assert releases.main([*check[:-1], "r2"]) == releases.EXIT_OK
    releases.main(["clear", "--state", str(state), "--release", "r1"])
    assert releases.main(check) == releases.EXIT_OK


def test_corrupt_state_raises_instead_of_allowing(tmp_path):
    state = tmp_path / "failed.json"
    state.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError):
        releases.main(["check", "--state", str(state), "--release", "r1"])


def test_read_logs_labels_each_file_and_keeps_order(tmp_path):
    first, second = tmp_path / "stage.log", tmp_path / "app.log"
    first.write_text("stage line", encoding="utf-8")
    second.write_text("app line", encoding="utf-8")

    text = gate.read_logs([first, second, tmp_path / "missing.log"])

    assert text.index("stage line") < text.index("app line")
    assert "app.log" in text
    assert "missing.log" not in text
