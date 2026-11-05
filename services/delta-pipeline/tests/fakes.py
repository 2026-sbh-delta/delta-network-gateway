from __future__ import annotations

from delta_pipeline.clients import ServiceUnavailable

COMMIT = "943ccb991b6e7aebfaaabea4eff7178032440747"
REPO = "https://github.com/2026-sbh-delta/delta-sample-app"
DIGEST = "sha256:" + "a" * 64
IMAGE_REPO = "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/delta-sample-app"


class FakeSast:
    def __init__(self, *, state="done", targets=2, secrets=0, commit=COMMIT, fail=False, files=5, scope_commit=None,
                 no_summary=False):
        self.state, self.targets, self.secrets, self.commit, self.fail = state, targets, secrets, commit, fail
        self.files, self.scope_commit, self.no_summary = files, scope_commit, no_summary
        self.started = []

    def start(self, run_id, repo, commit):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.started.append(run_id)
        return {"run_id": run_id, "state": "running"}

    def scan(self, run_id):
        summary = {"dast_targets": self.targets, "counts": {"secrets": self.secrets},
                   "scope": {"commit": self.scope_commit or self.commit, "files": self.files, "languages": {"python": self.files}}}
        return {"run_id": run_id, "state": self.state, "commit": self.commit, "error": None if self.state == "done" else "x",
                "summary": None if self.no_summary else summary}

    def sarif(self, run_id):
        return {"version": "2.1.0", "runs": [{"properties": {"run_id": run_id}}]}


class FakeDast:
    """DAST 플랫폼 API 흉내. 같은 ID·같은 입력은 같은 점검, 다른 입력은 409 와 같은 오류."""

    def __init__(self, statuses=("NOT_REPRODUCED",), *, status="done", expected=None, eligible=None):
        self.statuses, self.status, self.started, self.bindings = list(statuses), status, [], {}
        self.expected = len(self.statuses) if expected is None else expected
        self.eligible = self.expected if eligible is None else eligible

    def submit(self, run_id, binding, report):
        from delta_pipeline.clients import evidence_hash

        assert report["version"] == "2.1.0" and binding["sast_evidence_sha256"] == evidence_hash(report)
        assert binding["execution_cloud"] == "gcp" and binding["image"].endswith(binding["image_digest"])
        if run_id in self.bindings:
            if self.bindings[run_id] != binding:
                raise ServiceUnavailable("HTTP 409")
            return self.view(run_id)
        self.bindings[run_id] = binding
        self.started.append(binding["sast_run_id"])
        return self.view(run_id)

    def view(self, run_id):
        return {"run_id": run_id, "status": self.status, "finding_ids": [f"F{i}" for i in range(self.expected)],
                "eligible_count": self.eligible,
                "results": [{"finding_id": f"F{i}", "status": s} for i, s in enumerate(self.statuses)]}


class FakeStaging:
    def __init__(self, fail=False):
        self.released, self.fail = [], fail

    def release(self, cloud, holder):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.released.append((cloud, holder))
        return True


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
