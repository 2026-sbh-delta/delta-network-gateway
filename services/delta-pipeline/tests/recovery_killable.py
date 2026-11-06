"""Subprocess graph crash test. Provider I/O is a durable local fake, not Kubernetes."""

import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from delta_pipeline.model import Options
from delta_pipeline.run import Run, latest_unfinished
from fakes import (
    COMMIT,
    DIGEST,
    IMAGE_REPO,
    REPO,
    FakeSast,
    FakeStaging,
    build_jenkins,
    deploy_jenkins,
)

base, point = Path(sys.argv[1]), sys.argv[2]
path = base / "jobs.json"
assisted = point in ("human_wait", "human_resume", "manual_after_create", "manual_after_wait")


class Source(FakeSast):
    def start(self, key, repo, commit):
        return super().start(str(uuid5(NAMESPACE_URL, key)), repo, commit)


class Native:
    def start(self, key, **binding):
        jobs = json.loads(path.read_text()) if path.exists() else {}
        if key not in jobs:
            jobs[key] = {"id": str(uuid5(NAMESPACE_URL, key)), **binding}
            path.write_text(json.dumps(jobs))
        if "recovery" in key and point == "after_create":
            os._exit(9)
        if point == "manual_after_create" and len(jobs) == 4:
            os._exit(9)
        return jobs[key]["id"]

    def get(self, rid):
        jobs = json.loads(path.read_text())
        job = next(j for j in jobs.values() if j["id"] == rid)
        retried = len(jobs) > 1 and job is list(jobs.values())[-1]
        if retried and point == "wait_retry":
            os._exit(9)
        if point == "manual_after_wait" and len(jobs) == 4:
            os._exit(9)
        failed = (assisted and not (base / "operator-fixed").exists()) or len(jobs) == 1 or point in ("exhausted", "finish_exhausted")
        return {"id": rid, "status": "failed" if failed else "succeeded", "kind": "dast_check",
                "current_step": "sandbox_cleanup", "error": {"code": "sandbox_health_failed"},
                "result": {"execution_cloud": "onprem", "runtime": "k3s", "cleanup": "stopped",
                           "runtime_check": "passed", "image": job["image"], "image_digest": DIGEST,
                           "commit_sha": COMMIT, "scanner_run_id": rid,
                           "sast_evidence_sha256": job["sast_evidence_sha256"],
                           "resource": "namespaces/delta-platform-sandbox/pods/delta-sbx-" + UUID(rid).hex,
                           "revision": "uid-1", "deployment_approved": False,
                           "dast_findings": [{"finding_id": "F1", "status": "NOT_REPRODUCED", "evidence_sha256": "a" * 64}],
                           "eligible_count": 1, "requires_review": False, "findings_truncated": False,
                           "coverage": "sast_xss_candidates_only", "dast_evidence_sha256": "b" * 64,
                           "dast_check": "completed"}}

    def retry_ready(self, rid, **binding):
        return True

    resume_ready = retry_ready
    observation_ready = retry_ready

    def inspect(self, rid):
        return {"facts": {"id": rid}, "limitations": ["durable fake provider"]}


def sleep(_):
    if point == "before_checkpoint":
        os._exit(9)


store = base / "graph"
saved = latest_unfinished(store) if store.exists() else None
rid, options = saved or (None, Options(REPO, COMMIT, execution_cloud="onprem", recovery=True, assistance=assisted))
run = Run(options, sast=Source(), dast=object(), build_jenkins=build_jenkins(),
          deploy_jenkins=deploy_jenkins(), staging=FakeStaging(), image_repo=IMAGE_REPO,
          store_dir=store, run_id=rid, onprem=Native(), sleep=sleep)
thread = threading.Thread(target=run.execute, daemon=True)
thread.start()
deadline = time.monotonic() + 10
while thread.is_alive() and time.monotonic() < deadline:
    waiting = next((n for n in run.snapshot()["nodes"] if n["decision"]), None)
    if waiting:
        if point in ("exhausted", "human_wait"):
            os._exit(9)
        if assisted:
            (base / "operator-fixed").write_text("fixed")
            run.answer("redeploy_sandbox", case_id=waiting["decision"].get("case_id"))
        else:
            run.answer("acknowledge")
    time.sleep(0.01)
thread.join(timeout=1)
assert not thread.is_alive()
print(json.dumps(run.snapshot(), ensure_ascii=False))
