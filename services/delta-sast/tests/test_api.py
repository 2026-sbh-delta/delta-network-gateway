import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from delta_sast.api import create_app
from delta_sast.judge import FakeClient
from delta_sast.registry import SastRegistry
from delta_sast.source import SourcePolicy, SourceRejected, fetch, scan_root
from delta_sast.tools import tool_catalog

needs_opengrep = pytest.mark.skipif(shutil.which("opengrep") is None, reason="opengrep 없음")
TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CLEAN = "shop.filters.clean"
VULNERABLE = {"verdict": "vulnerable", "reason": "script 태그만 지운다.",
              "cited": [{"path": "shop/filters.py", "line": 6}], "payload_template": "img_onerror"}


@pytest.fixture
def llm():
    return FakeClient({CLEAN: VULNERABLE})


@pytest.fixture
def client(fixtures, tmp_path, llm):
    registry = SastRegistry(tmp_path / "state", SourcePolicy(("https://github.com/2026-sbh-delta/",), (fixtures,)),
                            lambda: llm)
    with TestClient(create_app(registry=registry, token=TOKEN)) as c:
        yield c


def wait_done(client, run_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scan = client.get(f"/v1/sast/scans/{run_id}", headers=AUTH).json()
        if scan["state"] != "running":
            return scan
        time.sleep(0.2)
    raise AssertionError("점검이 끝나지 않았다")


def test_app_refuses_to_start_without_token(tmp_path):
    with pytest.raises(ValueError):
        create_app(registry=SastRegistry(tmp_path, SourcePolicy((), ()), lambda: None), token="")


def test_endpoints_require_token(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/v1/tools").status_code == 401
    assert client.post("/v1/sast/scans", json={"run_id": "r", "repo": "x"}).status_code == 401


def test_tool_catalog_is_llm_tool_shaped(client):
    catalog = client.get("/v1/tools", headers=AUTH).json()
    assert catalog == tool_catalog()
    assert [t["name"] for t in catalog["tools"]] == ["sast_scan", "sast_status", "sast_sarif", "sast_dast_feedback"]
    assert all(set(t) == {"name", "description", "input_schema"} for t in catalog["tools"])
    scan = catalog["tools"][0]["input_schema"]
    assert scan["additionalProperties"] is False and set(scan["required"]) == {"run_id", "repo"}
    assert set(catalog["tools"][3]["input_schema"]["required"]) == {"run_id", "results"}
    assert set(catalog["routes"]) == {t["name"] for t in catalog["tools"]}


@needs_opengrep
def test_scan_lifecycle_and_sarif(client, fixtures, llm):
    body = {"run_id": "r1", "repo": str(fixtures / "board")}
    started = client.post("/v1/sast/scans", json=body, headers=AUTH)
    assert started.status_code == 202 and started.json()["state"] == "running"
    assert client.post("/v1/sast/scans", json=body, headers=AUTH).status_code == 202  # 같은 요청은 그대로
    other = {"run_id": "r1", "repo": str(fixtures / "misc")}
    assert client.post("/v1/sast/scans", json=other, headers=AUTH).status_code == 409

    scan = wait_done(client, "r1")
    assert scan["state"] == "done", scan
    assert scan["summary"]["dast_targets"] == 3
    assert [s["name"] for s in scan["steps"]] == ["accepted", "fetch", "scan", "judge", "done"]
    sarif = client.get("/v1/sast/scans/r1/sarif", headers=AUTH).json()
    assert sarif["runs"][0]["properties"]["run_id"] == "r1"
    assert llm.calls == 0  # 게시판 예제는 규칙만으로 판정된다


def test_rejects_full_mode_and_unknown_scan(client, fixtures):
    full = {"run_id": "r2", "repo": str(fixtures / "board"), "mode": "full"}
    assert client.post("/v1/sast/scans", json=full, headers=AUTH).status_code == 501
    assert client.get("/v1/sast/scans/none", headers=AUTH).status_code == 404
    assert client.get("/v1/sast/scans/none/sarif", headers=AUTH).status_code == 404


def test_disallowed_source_fails_the_scan(client):
    body = {"run_id": "r3", "repo": "https://github.com/someone-else/app", "commit": "abc1234"}
    assert client.post("/v1/sast/scans", json=body, headers=AUTH).status_code == 202
    scan = wait_done(client, "r3")
    assert scan["state"] == "failed" and "허용된" in scan["error"]
    assert client.get("/v1/sast/scans/r3/sarif", headers=AUTH).status_code == 409


@needs_opengrep
def test_dast_feedback_turns_llm_judgment_into_rule(client, fixtures, llm):
    client.post("/v1/sast/scans", json={"run_id": "f1", "repo": str(fixtures / "misc")}, headers=AUTH)
    assert wait_done(client, "f1")["state"] == "done"
    results = client.get("/v1/sast/scans/f1/sarif", headers=AUTH).json()["runs"][0]["results"]
    by_llm = next(r for r in results if r["properties"]["judgment"]["by"] == "llm")
    by_rule = next(r for r in results if r["properties"]["judgment"]["by"] == "rule" and r["kind"] == "fail"
                   and r["properties"]["dast"]["verifiable"])
    fb = {"results": [
        {"fingerprint": by_llm["partialFingerprints"]["deltaFlow/v1"], "status": "REPRODUCED"},
        {"fingerprint": by_rule["partialFingerprints"]["deltaFlow/v1"], "status": "REPRODUCED"},
        {"fingerprint": "0" * 32, "status": "REPRODUCED"},
    ]}
    outcomes = client.post("/v1/sast/scans/f1/dast-results", json=fb, headers=AUTH).json()
    assert [o["outcome"] for o in outcomes] == ["ineffective", "skipped", "unknown"]

    calls = llm.calls
    client.post("/v1/sast/scans", json={"run_id": "f2", "repo": str(fixtures / "misc")}, headers=AUTH)
    assert wait_done(client, "f2")["state"] == "done"
    results = client.get("/v1/sast/scans/f2/sarif", headers=AUTH).json()["runs"][0]["results"]
    same = next(r for r in results if r["partialFingerprints"] == by_llm["partialFingerprints"])
    assert same["properties"]["judgment"]["by"] == "cache"
    assert same["properties"]["judgment"]["verdict"] == "vulnerable"
    assert llm.calls == calls  # 규칙으로 굳은 필터는 다시 묻지 않는다


def test_source_policy(tmp_path, fixtures):
    policy = SourcePolicy(("https://github.com/2026-sbh-delta/",), (fixtures,))
    for repo, commit in [("https://github.com/other/app", "abc1234"),
                         ("http://github.com/2026-sbh-delta/app", "abc1234"),
                         ("https://user:pw@github.com/2026-sbh-delta/app", "abc1234"),
                         ("https://github.com/2026-sbh-delta/app", None),
                         (str(tmp_path), None)]:
        with pytest.raises(SourceRejected):
            fetch(repo, commit, tmp_path / "w", policy)
    base, cleanup = fetch(str(fixtures / "board"), None, tmp_path / "w", policy)
    assert base == (fixtures / "board").resolve() and cleanup is False
    with pytest.raises(SourceRejected):
        scan_root(base, "../misc")
    assert scan_root(base, "app") == base / "app"


def test_local_commit_is_extracted(tmp_path, fixtures):
    repo = tmp_path / "repo"
    shutil.copytree(fixtures / "board", repo)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    sha = subprocess.run([*git[:3], "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    (repo / "app" / "board.py").write_text("# 커밋 뒤 수정\n", encoding="utf-8")

    base, cleanup = fetch(str(repo), sha, tmp_path / "w", SourcePolicy((), (tmp_path,)))
    assert cleanup is True
    assert "save_post" in (base / "app" / "board.py").read_text(encoding="utf-8")


@needs_opengrep
def test_baseline_comparison_for_patch_round(client, fixtures):
    """패치 회차(B)를 패치 전(A)과 비교한다. escape를 넣은 body는 resolved, 새 탐지는 0."""
    patched = fixtures / "board_patched"
    if not patched.exists():
        shutil.copytree(fixtures / "board", patched)
        board = patched / "app" / "board.py"
        board.write_text(board.read_text(encoding="utf-8").replace(
            'body = post["body"]', 'body = escape(post["body"])'), encoding="utf-8")

    no_base = {"run_id": "b0", "repo": str(fixtures / "board"), "baseline_run_id": "missing"}
    assert client.post("/v1/sast/scans", json=no_base, headers=AUTH).status_code == 400

    client.post("/v1/sast/scans", json={"run_id": "run-42-1", "repo": str(fixtures / "board")}, headers=AUTH)
    assert wait_done(client, "run-42-1")["state"] == "done"
    body = {"run_id": "run-42-2", "repo": str(patched), "baseline_run_id": "run-42-1"}
    assert client.post("/v1/sast/scans", json=body, headers=AUTH).status_code == 202
    scan = wait_done(client, "run-42-2")
    base = scan["summary"]["baseline"]
    assert (base["run_id"], base["new_findings"], base["resolved"], base["still_open"]) == ("run-42-1", 0, 1, 2)

    results = client.get("/v1/sast/scans/run-42-2/sarif", headers=AUTH).json()["runs"][0]["results"]
    body_result = next(r for r in results if r["properties"]["dast"].get("field") == "body")
    assert (body_result["kind"], body_result["baselineState"]) == ("pass", "updated")
    assert body_result["partialFingerprints"]["deltaFlow/v1"] in base["resolved_fingerprints"]
    assert {r["baselineState"] for r in results} <= {"unchanged", "updated"}
