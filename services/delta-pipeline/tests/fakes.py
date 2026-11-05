from __future__ import annotations

from delta_pipeline.clients import ServiceUnavailable

COMMIT = "943ccb991b6e7aebfaaabea4eff7178032440747"
FIX_COMMIT = "1f" * 20
REPO = "https://github.com/2026-sbh-delta/delta-sample-app"
DIGEST = "sha256:" + "a" * 64
IMAGE_REPO = "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/delta-sample-app"


class FakeSast:
    def __init__(self, *, state="done", targets=2, secrets=0, commit=None, fail=False, files=5, scope_commit=None,
                 no_summary=False):
        # commit: 점검했다고 답할 커밋(주지 않으면 요청받은 커밋)
        self.state, self.targets, self.secrets, self.commit, self.fail = state, targets, secrets, commit, fail
        self.files, self.scope_commit, self.no_summary = files, scope_commit, no_summary
        self.started, self.commits = [], {}

    def start(self, run_id, repo, commit):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.started.append(run_id)
        self.commits[run_id] = commit
        return {"run_id": run_id, "state": "running"}

    def scan(self, run_id):
        commit = self.commit or self.commits.get(run_id, COMMIT)
        summary = {"dast_targets": self.targets, "counts": {"secrets": self.secrets},
                   "scope": {"commit": self.scope_commit or commit, "files": self.files, "languages": {"python": self.files}}}
        return {"run_id": run_id, "state": self.state, "commit": commit, "error": None if self.state == "done" else "x",
                "summary": None if self.no_summary else summary}

    def sarif(self, run_id):
        return {"version": "2.1.0", "runs": [{"properties": {"run_id": run_id}}]}


class FakeDast:
    """DAST 플랫폼 API 흉내. 같은 ID·같은 입력은 같은 점검, 다른 입력은 409 와 같은 오류."""

    def __init__(self, statuses=("NOT_REPRODUCED",), *, status="done", expected=None, eligible=None,
                 after_fix=("NOT_REPRODUCED",), fix_status="ready", pr_commit=FIX_COMMIT):
        self.statuses, self.status, self.started, self.bindings = list(statuses), status, [], {}
        self.expected = len(self.statuses) if expected is None else expected
        self.eligible = self.expected if eligible is None else eligible
        # 조치 루프: 재검증(reverify_of 있음) 판정, 수정안 상태, PR 이 올린 커밋
        self.after_fix, self.fix_status, self.pr_commit = list(after_fix), fix_status, pr_commit
        self.reverify, self.prs, self.pr_bases = {}, [], []

    def submit(self, run_id, binding, report, reverify_of=None):
        from delta_pipeline.clients import evidence_hash

        assert report["version"] == "2.1.0" and binding["sast_evidence_sha256"] == evidence_hash(report)
        assert binding["execution_cloud"] == "gcp" and binding["image"].endswith(binding["image_digest"])
        if run_id in self.bindings:
            if self.bindings[run_id] != binding:
                raise ServiceUnavailable("HTTP 409")
            return self.view(run_id)
        self.bindings[run_id] = binding
        if reverify_of:
            self.reverify[run_id] = reverify_of
        self.started.append(binding["sast_run_id"])
        return self.view(run_id)

    def view(self, run_id):
        statuses = self.after_fix if run_id in self.reverify else self.statuses
        expected = len(statuses) if run_id in self.reverify else self.expected
        return {"run_id": run_id, "status": self.status, "finding_ids": [f"F{i}" for i in range(expected)],
                "eligible_count": expected if run_id in self.reverify else self.eligible,
                "results": [{"finding_id": f"F{i}", "status": s} for i, s in enumerate(statuses)]}

    def fix(self, run_id, finding_id):
        return {"status": self.fix_status, "patch": "diff --git a/board.py b/board.py\n" if self.fix_status == "ready" else None,
                "files": ["board.py"], "summary": "출력 인코딩", "trace": [], "changes": []}

    def open_pr(self, run_id, finding_id, base=None):
        self.prs.append((run_id, finding_id))
        self.pr_bases.append(base)
        return {"status": "created", "number": 7, "url": "https://github.com/x/pull/7", "branch": "dast/fix",
                "commit_sha": self.pr_commit}


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
        if name != "result.json":
            return None
        # 빌드 결과는 요청받은 커밋을 그대로 돌려준다(2차는 수정 커밋)
        if "commit" in self.result and len(self.params) >= build and "COMMIT" in self.params[build - 1]:
            return {**self.result, "commit": self.params[build - 1]["COMMIT"]}
        return self.result

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
