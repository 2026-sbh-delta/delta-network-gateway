"""메인 페이지: 공개 주소는 로그인해야 보이고, 상태 API는 서비스 목록을 돌려준다."""

from fastapi.testclient import TestClient

from delta_orchestrator import app as orch

PUBLIC = "https://d222.cloudfront.net"


def make(monkeypatch):
    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", PUBLIC)
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "orchestrator-password-123")
    monkeypatch.setenv("ORCH_SESSION_SECRET", "s" * 40)

    async def fake_collect():
        return {"services": [{"id": "sast", "state": "up"}], "checked_at": "2026-11-05T00:00:00Z"}
    monkeypatch.setattr(orch, "collect", fake_collect)
    return orch.create_app()


def test_health_and_login_gate(monkeypatch):
    app = make(monkeypatch)
    with TestClient(app, base_url="http://10.90.10.5:8080") as client:
        assert client.get("/healthz").text == "ok"
        assert client.get("/api/status").status_code == 403
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/").headers["location"] == "/login"
        ok = client.post("/login", data={"password": "orchestrator-password-123"}, headers={"Origin": PUBLIC})
        assert ok.status_code == 303
        assert client.get("/api/status").json()["services"][0]["id"] == "sast"



COMMIT = "943ccb991b6e7aebfaaabea4eff7178032440747"
REPO = "https://github.com/2026-sbh-delta/delta-sample-app"
DIGEST = "sha256:" + "a" * 64


class _Sast:
    def start(self, run_id, repo, commit):
        return {}

    def scan(self, run_id):
        return {"state": "done", "commit": COMMIT, "summary": {"dast_targets": 1, "counts": {"secrets": 0}}}

    def sarif(self, run_id):
        return {"version": "2.1.0"}


class _Dast:
    def start(self, report, **kw):
        return "d1"

    def run(self, run_id):
        return {"status": "done", "results": [{"finding_id": "F", "status": "REPRODUCED"}]}


class _Jenkins:
    def __init__(self, result):
        self.result, self.keys = result, []

    def trigger(self, params, run_key):
        self.keys.append(run_key)
        return len(self.keys)

    def find_build(self, run_key):
        return self.keys.index(run_key) + 1 if run_key in self.keys else None

    def status(self, build):
        return "SUCCESS"

    def file(self, build, name):
        return self.result

    def console_url(self, build):
        return "http://jenkins"


def fake_run(options, store_dir, run_id):
    from delta_pipeline.run import Run
    return Run(options, sast=_Sast(), dast=_Dast(),
               build_jenkins=_Jenkins({"commit": COMMIT, "image_ref": "repo/app@" + DIGEST}),
               deploy_jenkins=_Jenkins({"state": "ready", "url": "http://s", "image_digest": DIGEST,
                                        "steps": [{"name": "data_reset", "detail": "job: 남은 행 0"}]}),
               image_repo="repo/app", store_dir=store_dir, sleep=lambda _: None, run_id=run_id)


def test_pipeline_api_needs_login_runs_and_takes_a_human_answer(monkeypatch, tmp_path):
    import time

    from delta_orchestrator.pipelines import Pipelines

    monkeypatch.setenv("ORCH_PUBLIC_ORIGIN", PUBLIC)
    monkeypatch.setenv("ORCH_ADMIN_PASSWORD", "orchestrator-password-123")
    monkeypatch.setenv("ORCH_SESSION_SECRET", "s" * 40)
    app = orch.create_app(Pipelines(tmp_path, fake_run))
    start = {"repo": REPO, "commit": COMMIT}
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as client:
        assert client.post("/api/pipeline/runs", json=start, headers={"Origin": PUBLIC}).status_code == 401
        client.post("/login", data={"password": "orchestrator-password-123"}, headers={"Origin": PUBLIC})
        # 다른 출처에서 온 실행 요청은 로그인돼 있어도 거부
        assert client.post("/api/pipeline/runs", json=start, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/pipeline/runs", json={"repo": REPO}, headers={"Origin": PUBLIC}).status_code == 400
        r = client.post("/api/pipeline/runs", json=start, headers={"Origin": PUBLIC})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        assert client.post("/api/pipeline/runs", json=start, headers={"Origin": PUBLIC}).status_code == 409

        def wait(cond):
            for _ in range(200):
                run = client.get(f"/api/pipeline/runs/{run_id}").json()
                if cond(run):
                    return run
                time.sleep(0.02)
            raise AssertionError(run)

        # DAST 재현 → 사람 확인 대기. 잘못된 답은 거부, 승인하면 끝까지 간다
        wait(lambda run: any(n["decision"] for n in run["nodes"]))
        assert client.post(f"/api/pipeline/runs/{run_id}/answer", json={"value": "maybe"},
                           headers={"Origin": PUBLIC}).status_code == 409
        assert client.post(f"/api/pipeline/runs/{run_id}/answer", json={"value": "approve"},
                           headers={"Origin": PUBLIC}).status_code == 200
        assert wait(lambda run: run["outcome"])["outcome"] == "verified"
        assert client.get("/api/pipeline/runs").json()["runs"][0]["run_id"] == run_id
        assert client.get("/api/pipeline/runs/nope").status_code == 404
        assert client.get("/api/pipeline/runs/..%2Fx").status_code == 404
