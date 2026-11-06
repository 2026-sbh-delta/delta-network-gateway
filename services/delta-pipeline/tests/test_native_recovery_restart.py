import json,subprocess,sys
from pathlib import Path
import pytest
@pytest.mark.parametrize("point", ["before_checkpoint", "after_create", "wait_retry"])
def test_process_restart_does_not_duplicate_redeployment(tmp_path, point):
    helper = Path(__file__).with_name("recovery_killable.py")
    first = subprocess.run([sys.executable, str(helper), str(tmp_path), point],
                           capture_output=True, text=True, timeout=20, check=False)
    assert first.returncode == 9, first.stderr
    done = subprocess.run([sys.executable, str(helper), str(tmp_path), "never"],
                          capture_output=True, text=True, timeout=20, check=False)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.splitlines()[-1])
    assert result["outcome"] == "verified", done.stdout
    jobs = json.loads((tmp_path / "jobs.json").read_text())
    assert len(jobs) == 2
    recovery = next(n for n in result["nodes"] if n["id"] == "recovery")
    assert len(recovery["detail"]["events"]) == 1


def test_restart_at_human_gate_keeps_exhausted_budget(tmp_path):
    helper = Path(__file__).with_name("recovery_killable.py")
    first = subprocess.run([sys.executable, str(helper), str(tmp_path), "exhausted"],
                           capture_output=True, text=True, timeout=20, check=False)
    assert first.returncode == 9, first.stderr
    done = subprocess.run([sys.executable, str(helper), str(tmp_path), "finish_exhausted"],
                          capture_output=True, text=True, timeout=20, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.splitlines()[-1])["outcome"] == "stopped"
    assert len(json.loads((tmp_path / "jobs.json").read_text())) == 3


