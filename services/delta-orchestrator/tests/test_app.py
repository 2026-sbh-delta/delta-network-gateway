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
        return {"state": "done", "commit": COMMIT, "summary": {"dast_targets": 1, "counts": {"secrets": 0},
                "scope": {"commit": COMMIT, "files": 1}}}

    def sarif(self, run_id):
        return {"version": "2.1.0"}


class _Dast:
    def submit(self, run_id, binding, report, reverify_of=None):
        return {}

    def view(self, run_id):
        return {"status": "done", "finding_ids": ["F"], "results": [{"finding_id": "F", "status": "REPRODUCED"}]}


class _Staging:
    def release(self, cloud, holder):
        return True


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
               staging=_Staging(), image_repo="repo/app", store_dir=store_dir, sleep=lambda _: None, run_id=run_id)


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
        # 커밋을 고르기 전(빈 값)이나 브랜치 이름은 실행 기록을 만들기 전에 거른다
        for bad in ("", "main"):
            assert client.post("/api/pipeline/runs", json={"repo": REPO, "commit": bad}, headers={"Origin": PUBLIC}).status_code == 400
        assert client.get("/api/pipeline/runs").json()["runs"] == []
        bad_project = {**start, "project_id": "not-a-project"}
        assert client.post("/api/pipeline/runs", json=bad_project, headers={"Origin": PUBLIC}).status_code == 400
        # 실행을 시작한 플랫폼 프로젝트는 실행 기록에 남아 목록에서 보인다(실행 동작에는 쓰지 않는다)
        project = "61b078c3-f956-4b9b-b394-6c43324dc2af"
        r = client.post("/api/pipeline/runs", json={**start, "project_id": project}, headers={"Origin": PUBLIC})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        assert client.get("/api/pipeline/runs").json()["runs"][0]["options"]["project_id"] == project
        assert client.post("/api/pipeline/runs", json=start, headers={"Origin": PUBLIC}).status_code == 409

        def wait(cond):
            for _ in range(200):
                run = client.get(f"/api/pipeline/runs/{run_id}").json()
                if cond(run):
                    return run
                time.sleep(0.02)
            raise AssertionError(run)

        # DAST 재현 → 사람 확인 대기. 잘못된 답은 거부, 승인하면 끝까지 가되 '보안 미해결'로 끝난다
        wait(lambda run: any(n["decision"] for n in run["nodes"]))
        assert client.post(f"/api/pipeline/runs/{run_id}/answer", json={"value": "maybe"},
                           headers={"Origin": PUBLIC}).status_code == 409
        assert client.post(f"/api/pipeline/runs/{run_id}/answer", json={"value": "approve"},
                           headers={"Origin": PUBLIC}).status_code == 200
        assert wait(lambda run: run["outcome"])["outcome"] == "unresolved"
        listed = client.get("/api/pipeline/runs").json()["runs"][0]
        assert listed["run_id"] == run_id and listed["built"] is True
        assert client.get("/api/pipeline/runs/nope").status_code == 404
        assert client.get("/api/pipeline/runs/..%2Fx").status_code == 404


def test_localhost_host_header_from_a_remote_peer_is_refused(monkeypatch):
    app = make(monkeypatch)
    with TestClient(app, base_url="http://localhost:8080", client=("10.90.10.7", 40000)) as client:
        assert client.get("/api/status").status_code == 403
        assert client.post("/api/pipeline/runs", json={}).status_code == 403
    with TestClient(app, base_url="http://localhost:8080", client=("127.0.0.1", 40000)) as client:
        assert client.get("/api/status").status_code == 200


def test_cross_site_link_navigation_opens_but_cross_site_posts_and_fetches_do_not(monkeypatch):
    """11/5 실제: Slack·다른 탭 링크로 들어오면 브라우저가 Sec-Fetch-Site: cross-site 를 붙여 화면이 403 이었다."""
    app = make(monkeypatch)
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as client:
        assert client.get("/login", headers=nav).status_code == 200
        assert client.get("/", headers=nav).headers["location"] == "/login"
        assert client.post("/login", data={"password": "x"},
                           headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}).status_code == 403
        assert client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}).status_code == 403


def test_domain_revert_needs_login_and_explicit_approval(monkeypatch):
    """도메인 되돌리기는 로그인한 사람이 approve: true 로 승인해야 시작한다."""
    started = []
    monkeypatch.setenv("DELTA_CUTOVER", '{"zone_id": "' + "a" * 32 + '", "record": "app.a.site", "green": "aws.a.site", '
                       '"blue": "gcp.a.site", "blue_origin": "https://gcp.a.site"}')
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(orch.pipelines_mod.domain, "current_target", lambda cfg, cf: "gcp.a.site")
    monkeypatch.setattr(orch.pipelines_mod.domain, "revert", lambda cfg, cf, **kw: started.append(cfg.green))
    app = make(monkeypatch)
    with TestClient(app, base_url=PUBLIC, follow_redirects=False) as client:
        assert client.post("/api/domain/revert", json={"approve": True}, headers={"Origin": PUBLIC}).status_code == 401
        client.post("/login", data={"password": "orchestrator-password-123"}, headers={"Origin": PUBLIC})
        view = client.get("/api/domain").json()
        assert view["configured"] and view["target"] == "gcp.a.site" and view["green"] == "aws.a.site"
        assert client.post("/api/domain/revert", json={}, headers={"Origin": PUBLIC}).status_code == 400
        assert client.post("/api/domain/revert", json={"approve": True}, headers={"Origin": PUBLIC}).status_code == 202
    for _ in range(50):
        if started:
            break
        __import__("time").sleep(0.02)
    assert started == ["aws.a.site"]
