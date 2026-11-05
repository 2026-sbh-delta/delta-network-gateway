from __future__ import annotations

from delta_pipeline.clients import ServiceUnavailable

COMMIT = "943ccb991b6e7aebfaaabea4eff7178032440747"
REPO = "https://github.com/2026-sbh-delta/delta-sample-app"
DIGEST = "sha256:" + "a" * 64
IMAGE_REPO = "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/delta-sample-app"


class FakeSast:
    def __init__(self, *, state="done", targets=2, secrets=0, commit=COMMIT, fail=False):
        self.state, self.targets, self.secrets, self.commit, self.fail = state, targets, secrets, commit, fail
        self.started = []

    def start(self, run_id, repo, commit):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.started.append(run_id)
        return {"run_id": run_id, "state": "running"}

    def scan(self, run_id):
        return {"run_id": run_id, "state": self.state, "commit": self.commit, "error": None if self.state == "done" else "x",
                "summary": {"dast_targets": self.targets, "counts": {"secrets": self.secrets}}}

    def sarif(self, run_id):
        return {"version": "2.1.0", "runs": [{"properties": {"run_id": run_id}}]}


class FakeDast:
    def __init__(self, statuses=("NOT_REPRODUCED",), *, status="done"):
        self.statuses, self.status, self.started = list(statuses), status, []

    def start(self, report, *, target_id, sast_run_id, planner):
        assert report["version"] == "2.1.0" and target_id == "staging-gcp"
        self.started.append(sast_run_id)
        return f"dast-{len(self.started)}"

    def run(self, run_id):
        return {"status": self.status, "error_kind": None if self.status == "done" else "failed", "counts": {},
                "results": [{"finding_id": f"F{i}", "title": "XSS", "status": s, "message": "m"}
                            for i, s in enumerate(self.statuses)]}


class FakeJenkins:
    """작업 하나(delta-build 또는 delta-deploy). result.json 은 작업마다 다르게 준다."""

    def __init__(self, result, final="SUCCESS"):
        self.result, self.final, self.keys, self.params = result, final, [], []

    def trigger(self, params, run_key):
        self.keys.append(run_key)
        self.params.append(dict(params))
        return len(self.keys)

    def find_build(self, run_key):
        return self.keys.index(run_key) + 1 if run_key in self.keys else None

    def status(self, build):
        return self.final

    def file(self, build, name):
        return self.result if name == "result.json" else None

    def console_url(self, build):
        return f"http://jenkins/{build}"


def build_jenkins(**kw):
    return FakeJenkins({"commit": COMMIT, "image_ref": f"{IMAGE_REPO}@{DIGEST}", "image_digest": DIGEST}, **kw)


def deploy_jenkins(*, rows="0", digest=DIGEST, **kw):
    steps = [{"name": "image_digest", "detail": digest}]
    if rows is not None:
        steps.append({"name": "data_reset", "detail": f"delta-lab-staging-db-reset: 남은 행 {rows}"})
    return FakeJenkins({"state": "ready", "url": "http://136.82.112.37", "image_digest": digest, "revision": "r1",
                        "scanner_ip": "3.35.109.106", "steps": steps}, **kw)
